"""Turn mechanics of the web layer (docs/12 §3, §5).

The properties tested here are the reason this layer exists at all:

* the line is emitted **before** the narrative tick, so the tick's ~8.6s no longer
  sits in front of the player (docs/09 §4's latency table);
* a turn is serialised end to end, so the two writers of world state never overlap
  (``WorldStateManager`` is not thread-safe and a tick always writes ``advance_turn``);
* a failure is reported per *stage*, because a failed line loses the turn while a
  failed tick only costs the next turn its setup.

Everything runs on ``MockLLMClient`` — ``conftest``'s autouse fixture pins
``USE_MOCK_LLM=1``, so no request leaves the machine.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

# The web interface is an optional extra (``pip install -e ".[dev,web]"``), so a
# dev-only checkout skips these rather than failing to collect — the same courtesy the
# embedding extra gets. ``session`` imports FastAPI transitively via the app package.
pytest.importorskip("fastapi", reason="needs the 'web' extra")

from ai_native_rpg.web.events import EventType
from ai_native_rpg.web.session import Session, SessionError, list_saves, load_save

SCENARIO = "village_disappearance"


@pytest.fixture
def session(tmp_path):
    """A live session writing its traces and saves into temp dirs, not the repo's."""
    s = Session(
        session_id="test_session",
        scenario=SCENARIO,
        trace_dir=tmp_path,
        save_dir=tmp_path / "saves",
    )
    yield s
    s.close()


def _types(session: Session) -> list[str]:
    return [e.type.value for e in session._history]


# --- ordering --------------------------------------------------------------


def test_dialogue_is_emitted_before_the_narrative_tick(session):
    """The whole point of the layer: the player reads the line, then the tick lands."""
    session.submit_turn("那天晚上你看到了什么？")
    session.join(timeout=30)

    types = _types(session)
    assert types.index(EventType.DIALOGUE.value) < types.index(EventType.NARRATIVE_TICK.value)


def test_turn_accepted_precedes_the_line(session):
    """The echo lands first so the front end never inserts the player's text itself."""
    session.submit_turn("你好")
    session.join(timeout=30)

    types = _types(session)
    assert types[0] == EventType.TURN_ACCEPTED.value
    assert types.index(EventType.TURN_ACCEPTED.value) < types.index(EventType.DIALOGUE.value)


def test_every_turn_closes_with_a_tick(session):
    """A tick always runs, even a quiet one: pacing rules reason about adjacency.

    ``advance_turn`` is submitted on every tick including ``relieve``, so a turn that
    left no trace would make a beat five turns back look like it just happened.
    """
    before = session.manager.snapshot().story_beats.turn
    session.submit_turn("你好")
    session.join(timeout=30)

    assert session.manager.snapshot().story_beats.turn == before + 1
    assert EventType.NARRATIVE_TICK.value in _types(session)


def test_state_snapshots_follow_each_stage(session):
    """Scene and panel refresh after the line and again after the tick (docs/12 §3.3)."""
    session.submit_turn("你好")
    session.join(timeout=30)

    types = _types(session)
    assert types.count(EventType.SCENE.value) == 2
    assert types.count(EventType.PANEL.value) == 2


# --- serialisation ---------------------------------------------------------


def test_second_turn_is_refused_while_one_is_in_flight(session):
    """A turn stays in flight until its *tick* finishes, not just its line.

    The tick writes world state, so admitting the next turn alongside it is exactly
    the race the single-thread executor exists to prevent (docs/12 §5.3).
    """
    before = session.manager.snapshot().story_beats.turn

    session.submit_turn("第一句")

    with pytest.raises(SessionError, match="in flight"):
        session.submit_turn("第二句")

    session.join(timeout=30)
    # Once drained, the session accepts work again.
    session.submit_turn("第三句")
    session.join(timeout=30)
    # +2, not the literal count: the fixture's session has already run its opening tick
    # (docs/12 §4.2's "M1's line is in the first hello" fix), so `turn` starts above 0.
    assert session.manager.snapshot().story_beats.turn == before + 2


def test_busy_stays_true_until_the_tick_completes(session, monkeypatch):
    """Regression: ``busy`` must not clear when the dialogue job finishes.

    Deriving it from ``queue.unfinished_tasks`` looked right and was not — that counter
    drops as the last job is *taken*, leaving a window where the line was done, the
    tick was still writing, and a second turn was admitted.
    """
    tick_entered = threading.Event()
    release_tick = threading.Event()
    real_tick = session.engine.tick

    def slow_tick(**kwargs):
        tick_entered.set()
        release_tick.wait(timeout=10)
        return real_tick(**kwargs)

    monkeypatch.setattr(session.engine, "tick", slow_tick)

    session.submit_turn("你好")
    assert tick_entered.wait(timeout=20), "tick never started"

    # The line has been emitted, the tick is mid-flight: still busy.
    assert EventType.DIALOGUE.value in _types(session)
    assert session.busy is True
    with pytest.raises(SessionError, match="in flight"):
        session.submit_turn("插一句")

    release_tick.set()
    session.join(timeout=30)
    assert session.busy is False


def test_concurrent_submissions_serialise(session):
    """Many threads posting at once must not interleave writes.

    Whatever is admitted runs one at a time, so the turn counter equals the number of
    accepted turns — never a lost or doubled increment.
    """
    before = session.manager.snapshot().story_beats.turn
    accepted = []
    errors = []

    def submit():
        try:
            accepted.append(session.submit_turn("同时说话"))
        except SessionError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    session.join(timeout=30)

    assert len(accepted) >= 1
    assert len(accepted) + len(errors) == 6
    # The decisive assertion: exactly one advance_turn per accepted turn, on top of
    # whatever the session's opening tick already contributed before this ran.
    assert session.manager.snapshot().story_beats.turn == before + len(accepted)


def test_empty_input_is_rejected(session):
    with pytest.raises(SessionError, match="empty"):
        session.submit_turn("   ")


# --- failure handling ------------------------------------------------------


def test_tick_failure_keeps_the_line(session, monkeypatch):
    """A failed tick costs the *next* turn its setup; this turn's line still stands."""

    def boom(**kwargs):
        raise RuntimeError("narrative backend exploded")

    monkeypatch.setattr(session.engine, "tick", boom)

    session.submit_turn("你好")
    session.join(timeout=30)

    types = _types(session)
    assert EventType.DIALOGUE.value in types
    failure = next(e for e in session._history if e.type is EventType.TURN_FAILED)
    assert failure.data["stage"] == "tick"
    assert failure.data["error_type"] == "RuntimeError"


def test_dialogue_failure_is_reported_as_such(session, monkeypatch):
    """A failed line means the turn is lost, and must be distinguishable from a tick."""

    def boom(*args, **kwargs):
        raise RuntimeError("model refused")

    monkeypatch.setattr(session.harness, "respond", boom)

    session.submit_turn("你好")
    session.join(timeout=30)

    failure = next(e for e in session._history if e.type is EventType.TURN_FAILED)
    assert failure.data["stage"] == "dialogue"
    assert EventType.DIALOGUE.value not in _types(session)


def test_a_failed_turn_does_not_wedge_the_session(session, monkeypatch):
    """The executor survives a raising job; the next turn still runs."""

    def boom(*args, **kwargs):
        raise RuntimeError("transient")

    monkeypatch.setattr(session.harness, "respond", boom)
    session.submit_turn("第一句")
    session.join(timeout=30)

    monkeypatch.undo()
    session.submit_turn("第二句")
    session.join(timeout=30)

    assert EventType.DIALOGUE.value in _types(session)
    assert session.busy is False


# --- pending event handoff -------------------------------------------------


def test_generated_hook_reaches_the_following_turn(session):
    """``pending_event`` is consumed by the *next* turn, which is why tick is off-path.

    Turn 1 generates content and turn 2 is handed it. This is the contract docs/02 §4
    established and the web layer must not alter — only move the wait off the player.
    """
    session.submit_turn("第一句")
    session.join(timeout=30)

    tick = next(e for e in session._history if e.type is EventType.NARRATIVE_TICK)
    if not tick.data["entry"]["hook"]:
        pytest.skip("no beat fired on turn 1; nothing was handed forward")

    session.submit_turn("第二句")
    session.join(timeout=30)

    dialogues = [e for e in session._history if e.type is EventType.DIALOGUE]
    assert dialogues[-1].data["used_pending_hook"] is True


def test_hook_is_consumed_only_once(session):
    """A hook re-offered every turn would read as an NPC stuck on one line."""
    session.submit_turn("第一句")
    session.join(timeout=30)
    session.submit_turn("第二句")
    session.join(timeout=30)

    # Whatever the engine generated on turn 1 was taken by turn 2; turn 2's own
    # content is what is pending now, so the queue never accumulates.
    assert session.engine.pending_event is None or isinstance(session.engine.pending_event, dict)
    session.submit_turn("第三句")
    session.join(timeout=30)
    dialogues = [e for e in session._history if e.type is EventType.DIALOGUE]
    assert len(dialogues) == 3


# --- replay ----------------------------------------------------------------


def test_history_replay_skips_what_the_client_already_has(session):
    """``Last-Event-ID`` replay is what makes EventSource's reconnect lossless."""
    session.submit_turn("你好")
    session.join(timeout=30)

    all_seqs = [e.seq for e in session._history]
    cutoff = all_seqs[len(all_seqs) // 2]

    q = session.subscribe(last_event_id=cutoff)
    replayed = []
    while not q.empty():
        replayed.append(q.get_nowait().seq)

    assert replayed == [s for s in all_seqs if s > cutoff]


# --- save and resume -------------------------------------------------------


def test_a_turn_writes_a_resumable_save(session, tmp_path):
    """The tick is the turn boundary, so a save exists once the turn closes."""
    session.submit_turn("那天晚上你看到了什么？")
    session.join(timeout=30)

    directory = tmp_path / "saves" / "test_session"
    assert (directory / "world.json").is_file()
    assert (directory / "session.json").is_file()
    assert (directory / f"memory_{session.npc_id}.json").is_file()


def test_resuming_continues_the_world_rather_than_restarting_it(tmp_path):
    """The point of the feature: trust, turn count and told clues all carry over.

    A resumed session is a *new* session built on saved state — the live runtime does
    not survive a restart — so what has to match is the world, not the object.
    """
    first = Session(
        session_id="run_one",
        scenario=SCENARIO,
        trace_dir=tmp_path,
        save_dir=tmp_path / "saves",
    )
    try:
        first.submit_turn("你好，我想打听失踪的事")
        first.join(timeout=30)
        turn_before = first.scene().turn
        trust_before = first.manager.get_trust(first.npc_id, first.player_id)
        memories_before = first.memory.episodic_count
        assert turn_before > 0
    finally:
        first.close()

    save = load_save("run_one", root=tmp_path / "saves")
    second = Session(
        session_id="run_two",
        scenario=SCENARIO,
        trace_dir=tmp_path,
        save_dir=tmp_path / "saves",
        resume=save,
    )
    try:
        assert second.resumed_from == "run_one"
        assert second.scene().turn == turn_before
        assert second.manager.get_trust(second.npc_id, second.player_id) == trust_before
        # the NPC still remembers the conversation, which WorldState does not hold
        assert second.memory.episodic_count == memories_before
        # and the player reopens on the transcript they left
        assert len(second.transcript) == 2
    finally:
        second.close()


def test_a_resumed_session_keeps_playing_from_there(tmp_path):
    first = Session(
        session_id="run_one",
        scenario=SCENARIO,
        trace_dir=tmp_path,
        save_dir=tmp_path / "saves",
    )
    try:
        first.submit_turn("你好")
        first.join(timeout=30)
        turn_before = first.scene().turn
    finally:
        first.close()

    second = Session(
        session_id="run_two",
        scenario=SCENARIO,
        trace_dir=tmp_path,
        save_dir=tmp_path / "saves",
        resume=load_save("run_one", root=tmp_path / "saves"),
    )
    try:
        second.submit_turn("我还想问一件事")
        second.join(timeout=30)
        assert second.scene().turn > turn_before
    finally:
        second.close()


def test_a_save_from_another_scenario_is_refused(tmp_path):
    """Restoring a world into a different pack would pace nothing, silently."""
    first = Session(
        session_id="run_one",
        scenario=SCENARIO,
        trace_dir=tmp_path,
        save_dir=tmp_path / "saves",
    )
    try:
        first.submit_turn("你好")
        first.join(timeout=30)
    finally:
        first.close()

    save = load_save("run_one", root=tmp_path / "saves")
    mismatched = replace(save, scenario="some_other_pack")

    with pytest.raises(SessionError, match="scenario"):
        Session(
            session_id="run_two",
            scenario=SCENARIO,
            trace_dir=tmp_path,
            save_dir=tmp_path / "saves",
            resume=mismatched,
        )


def test_a_save_whose_pack_is_gone_does_not_block_a_new_game(monkeypatch, tmp_path):
    """A stale save must not stop the server from starting a fresh session.

    Regression, found by running the thing: ``saves/`` accumulates runs from packs that no
    longer exist — a throwaway fixture pack, a scenario that lived under a temp directory.
    The page offered the newest save it found, resumed it without naming its pack, and
    ``POST /api/session`` answered 400. The game would not start at all.

    The server's half of the fix is that the save list stays honest and the refusal names
    the pack; the front end's half is to filter by ``/api/scenarios`` and fall back to a new
    game. This pins the server half: a new session is always available.
    """
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    saves = tmp_path / "saves"
    monkeypatch.setattr(session_module, "SAVE_DIR", saves)

    # A save that names a pack which is not installed.
    ghost = saves / "ghost_run"
    ghost.mkdir(parents=True)
    (ghost / "session.json").write_text(
        json.dumps(
            {
                "session_id": "ghost_run",
                "scenario": "lighthouse",
                "npc_id": "warden",
                "turn": 3,
                "saved_at": "2099-01-01T00:00:00+00:00",
                "transcript": [],
            }
        ),
        encoding="utf-8",
    )

    with TestClient(create_app(dev_mode=True)) as client:
        # It is listed — the list reports what is on disk, which is what lets a client
        # decide — but it names a pack that ``/api/scenarios`` does not offer.
        listed = client.get("/api/saves").json()["saves"]
        assert listed[0]["save_id"] == "ghost_run"
        assert "lighthouse" not in client.get("/api/scenarios").json()["scenarios"]

        # Resuming it fails, and says why.
        refused = client.post(
            "/api/session", json={"scenario": "lighthouse", "resume_from": "ghost_run"}
        )
        assert refused.status_code == 400

        # And a plain new session still works, which is the fallback the page takes.
        # `turn` is 1, not 0: a fresh session runs its opening tick before this response
        # is built, so M1's line is already staged (docs/12 §4.2).
        fresh = client.post("/api/session", json={"scenario": SCENARIO})
        assert fresh.status_code == 200
        assert fresh.json()["turn"] == 1


def test_a_session_saves_under_the_configured_root_only(tmp_path, monkeypatch):
    """No test may write a playthrough into the repo's own ``saves/``.

    ``conftest`` redirects ``SAVE_DIR`` for the whole suite because the default is what
    gets used when a test forgets, and a leaked save is not merely untidy: the front end
    offers to resume the newest save it finds, so one from a vanished fixture pack broke
    session creation outright.
    """
    from ai_native_rpg.web import session as session_module

    root = tmp_path / "elsewhere"
    monkeypatch.setattr(session_module, "SAVE_DIR", root)

    # Constructed *without* save_dir, i.e. the forgetful case.
    s = Session(session_id="default_root", scenario=SCENARIO, trace_dir=tmp_path / "traces")
    try:
        s.submit_turn("你好")
        s.join(timeout=30)
    finally:
        s.close()

    assert (root / "default_root" / "session.json").is_file()
    assert not (Path("saves") / "default_root").exists()


def test_a_missing_save_is_a_clear_error(tmp_path):
    with pytest.raises(SessionError, match="no save to resume"):
        load_save("nope", root=tmp_path / "saves")


def test_saves_are_listed_newest_first(tmp_path):
    for name in ("run_one", "run_two"):
        s = Session(
            session_id=name,
            scenario=SCENARIO,
            trace_dir=tmp_path,
            save_dir=tmp_path / "saves",
        )
        try:
            s.submit_turn("你好")
            s.join(timeout=30)
        finally:
            s.close()

    saves = list_saves(tmp_path / "saves")
    assert {s["save_id"] for s in saves} == {"run_one", "run_two"}
    assert all(s["scenario"] == SCENARIO for s in saves)
    assert all(s["turn"] > 0 for s in saves)


def test_the_saves_endpoint_lists_nothing_before_a_turn_is_played(monkeypatch, tmp_path):
    """An empty list, not a 404: "no saves yet" is a normal state on first run."""
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        assert client.get("/api/saves").json() == {"saves": []}


def test_resuming_over_http_reports_the_turn_it_continues_from(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        created = client.post("/api/session", json={"scenario": SCENARIO}).json()
        client.post(f"/api/session/{created['session_id']}/turn", json={"text": "你好"})
        registry = client.app.state.registry
        registry.get(created["session_id"]).join(timeout=30)

        saves = client.get("/api/saves").json()["saves"]
        assert len(saves) == 1
        played = saves[0]["turn"]
        assert played > 0

        resumed = client.post(
            "/api/session",
            json={"scenario": SCENARIO, "resume_from": saves[0]["save_id"]},
        ).json()
        assert resumed["resumed_from"] == saves[0]["save_id"]
        assert resumed["turn"] == played


def test_resuming_an_unknown_save_is_a_400(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        res = client.post("/api/session", json={"scenario": SCENARIO, "resume_from": "nope"})
        assert res.status_code == 400
        assert "no save to resume" in res.json()["detail"]

"""Location selection at the Web layer (docs/12 §13.2).

The page used to assume one scene, one NPC, and talking to him forever. Movement breaks
that assumption, and the three properties worth pinning are the ones docs/12 §13.2 names:

* a move goes through the **session executor**, because it writes world state twice (the
  move itself, then the slot charge) and ``move_player`` guarantees the Validator ran, not
  that nothing else is mid-write;
* the Web layer **never re-derives adjacency** — the refusal reason is passed through as
  the Validator wrote it;
* "first visit" lives in ``story_beats.visited_locations`` and is read from there rather
  than tracked a second time.

Offline throughout: ``conftest``'s autouse fixture pins ``USE_MOCK_LLM=1``.
"""

from __future__ import annotations

import threading

import pytest

# Optional extra; see the note in test_web_turn.py.
pytest.importorskip("fastapi", reason="needs the 'web' extra")

from ai_native_rpg.schemas.narrative import SLOTS_PER_DAY, TimeSlot
from ai_native_rpg.web.events import PLAYER_EVENTS, EventType
from ai_native_rpg.web.session import Session, SessionError

SCENARIO = "village_disappearance"

#: Where the pack starts the player, and the one place adjacent to it.
START = "npc_a_house"
NEXT_DOOR = "village_square"
#: Two hops away: reachable from the square, not from her doorstep.
TOO_FAR = "forest_edge"


@pytest.fixture
def session(tmp_path):
    s = Session(
        session_id="move_session",
        scenario=SCENARIO,
        trace_dir=tmp_path,
        save_dir=tmp_path / "saves",
    )
    yield s
    s.close()


def _events(session: Session, type_: EventType) -> list[dict]:
    return [e.data for e in session._history if e.type is type_]


def _beats(session: Session):
    return session.manager.snapshot().story_beats


# --- the happy path --------------------------------------------------------


def test_a_move_relocates_the_player_and_spends_a_slot(session):
    """docs/13 §4.1: the slot is the unit of cost, and going somewhere is what spends it."""
    assert session.manager.snapshot().player_locations[session.player_id] == START

    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)

    world = session.manager.snapshot()
    assert world.player_locations[session.player_id] == NEXT_DOOR
    assert world.story_beats.slots_spent_today == 1
    assert world.story_beats.time_slot is TimeSlot.AFTERNOON


def test_the_move_event_carries_the_slot_it_landed_in(session):
    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)

    (move,) = _events(session, EventType.MOVE)
    assert move["approved"] is True
    assert move["slot_spent"] is True
    assert move["slot"] == TimeSlot.AFTERNOON.value
    assert move["day"] == 1
    # The pack's display name, not the id: the player reads names.
    assert move["name"] == "村庄广场"


def test_a_landed_move_is_a_turn_and_runs_a_tick(session):
    """A move is a turn with no line, so ``advance_turn`` still gets recorded.

    The pacing rules read adjacency off that record, so a move that skipped it would make
    a beat several turns back look like it had just fired.
    """
    before = _beats(session).turn

    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)

    assert _beats(session).turn == before + 1
    assert _events(session, EventType.NARRATIVE_TICK)


def test_the_move_event_precedes_the_tick(session):
    """Same ordering guarantee as dialogue: the player's act is confirmed at once."""
    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)

    types = [e.type.value for e in session._history]
    assert types.index(EventType.MOVE.value) < types.index(EventType.NARRATIVE_TICK.value)


def test_a_move_produces_no_dialogue(session):
    """ "A turn that produces no line" is the whole shape of this event (docs/12 §13.2)."""
    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)

    assert _events(session, EventType.DIALOGUE) == []


# --- refusals --------------------------------------------------------------


def test_an_unreachable_destination_is_refused_with_the_validators_words(session):
    """Passed through verbatim (docs/12 §13.2): the reason is already written for a reader.

    Asserting on the string is deliberate. It is the *interface contract* — this text is
    what the player sees, so the front end needs no rule of its own to explain the refusal.
    """
    session.submit_move(TOO_FAR)
    session.join(timeout=30)

    (move,) = _events(session, EventType.MOVE)
    assert move["approved"] is False
    assert move["reason"] == f"{TOO_FAR!r} is not reachable from {START!r}"


def test_a_refused_move_spends_nothing(session):
    """docs/13 §12: a rejected move costs nothing — nothing happened."""
    session.submit_move(TOO_FAR)
    session.join(timeout=30)

    world = session.manager.snapshot()
    assert world.player_locations[session.player_id] == START
    assert world.story_beats.slots_spent_today == 0
    assert world.story_beats.time_slot is TimeSlot.MORNING


def test_a_refused_move_does_not_advance_the_story_a_turn(session):
    """No tick, because no turn happened.

    Running one anyway would spend a narrative turn on an act the world refused, and
    ``story_beats.turn`` is what the pacing rules measure recency in.
    """
    before = _beats(session).turn

    session.submit_move(TOO_FAR)
    session.join(timeout=30)

    assert _beats(session).turn == before
    assert _events(session, EventType.NARRATIVE_TICK) == []


def test_a_nonexistent_location_is_refused_not_raised(session):
    session.submit_move("atlantis")
    session.join(timeout=30)

    (move,) = _events(session, EventType.MOVE)
    assert move["approved"] is False
    assert "no such location" in move["reason"]


def test_a_refused_move_releases_the_session(session):
    """``busy`` has to clear even though no tick ran, or the session wedges forever."""
    session.submit_move(TOO_FAR)
    session.join(timeout=30)

    assert session.busy is False
    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)
    assert session.manager.snapshot().player_locations[session.player_id] == NEXT_DOOR


def test_a_refused_move_reaches_the_panel_as_a_rejected_proposal(session):
    """The player's write path gets checked like any other, and shows it (docs/07 §2.3)."""
    session.submit_move(TOO_FAR)
    session.join(timeout=30)

    reasons = [r.reason for r in session.panel().rejected_proposals]
    assert any(r and "not reachable" in r for r in reasons)


# --- serialisation ---------------------------------------------------------


def test_a_move_and_a_turn_cannot_overlap(session):
    """One executor for both, so the two writers of world state never interleave."""
    session.submit_move(NEXT_DOOR)

    with pytest.raises(SessionError, match="in flight"):
        session.submit_turn("你好")

    session.join(timeout=30)


def test_a_second_move_is_refused_while_one_is_in_flight(session):
    session.submit_move(NEXT_DOOR)

    with pytest.raises(SessionError, match="in flight"):
        session.submit_move(TOO_FAR)

    session.join(timeout=30)


def test_the_session_stays_busy_until_the_moves_tick_finishes(session, monkeypatch):
    """Same guarantee as a dialogue turn: the tick writes, so it holds the session.

    The move enqueues its tick only after landing, so this also pins that the counting
    never dips to zero in between and lets a request in.
    """
    tick_entered = threading.Event()
    release = threading.Event()
    real_tick = session.engine.tick

    def slow_tick(**kwargs):
        tick_entered.set()
        release.wait(timeout=10)
        return real_tick(**kwargs)

    monkeypatch.setattr(session.engine, "tick", slow_tick)

    session.submit_move(NEXT_DOOR)
    assert tick_entered.wait(timeout=20), "the move never queued a tick"

    assert session.busy is True
    with pytest.raises(SessionError, match="in flight"):
        session.submit_turn("插一句")

    release.set()
    session.join(timeout=30)
    assert session.busy is False


def test_concurrent_moves_serialise(session):
    """Whatever is admitted runs one at a time; slots are never double-spent."""
    accepted = []
    errors = []

    def submit():
        try:
            accepted.append(session.submit_move(NEXT_DOOR))
        except SessionError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    session.join(timeout=60)

    assert len(accepted) >= 1
    assert len(accepted) + len(errors) == 6
    # Every accepted move spent exactly one slot. Only the first can land (afterwards the
    # player is already there), but none may double-charge.
    assert _beats(session).slots_spent_today <= SLOTS_PER_DAY


# --- the scene half --------------------------------------------------------


def test_the_scene_offers_only_adjacent_places(session):
    """Reachability comes from the projection, so the interface never computes it."""
    scene = session.scene()

    assert [d.location_id for d in scene.destinations] == [NEXT_DOOR]


def test_destinations_carry_the_packs_names(session):
    (square,) = session.scene().destinations

    assert square.name == "村庄广场"


def test_the_scene_follows_the_player_after_a_move(session):
    """The multi-location assumption: who is present changes with where you stand."""
    before = session.scene()
    assert [n.npc_id for n in before.npcs] == ["npc_a"]

    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)

    after = session.scene()
    assert after.location is not None
    assert after.location.location_id == NEXT_DOOR
    # Marta stayed at home, and nobody is in the square.
    assert after.npcs == []
    assert sorted(d.location_id for d in after.destinations) == [
        "forest_edge",
        "npc_a_house",
        "tavern",
    ]


def test_dialogue_routes_to_the_npc_at_the_current_location(session):
    """Each move selects a fresh NPC runtime instead of reusing the opening one."""
    session.submit_turn("浣犲ソ")
    session.join(timeout=30)
    dialogue = _events(session, EventType.DIALOGUE)
    assert dialogue[-1]["npc_id"] == "npc_a"

    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)
    session.submit_move(TOO_FAR)
    session.join(timeout=30)
    session.submit_turn("浣犲ソ")
    session.join(timeout=30)
    assert _events(session, EventType.DIALOGUE)[-1]["npc_id"] == "npc_b"


def test_dialogue_fails_cleanly_when_the_current_location_has_no_npc(session):
    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)

    session.submit_turn("鏈変汉鍚楋紵")
    session.join(timeout=30)

    assert _events(session, EventType.DIALOGUE) == []
    failed = _events(session, EventType.TURN_FAILED)
    assert failed[-1]["error_type"] == "NoConversationNPC"


def test_first_visit_is_read_from_the_beats_not_tracked_separately(session):
    """docs/12 §13.2: ``visited_locations`` carries this, and nothing keeps a copy."""
    # The player starts at Marta's door, so that counts as visited from turn one.
    assert START in _beats(session).visited_locations
    assert NEXT_DOOR not in _beats(session).visited_locations
    assert [d.visited for d in session.scene().destinations] == [False]

    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)

    assert NEXT_DOOR in _beats(session).visited_locations
    # The flag now agrees, because it is the same list being read.
    visited = {d.location_id: d.visited for d in session.scene().destinations}
    assert visited[START] is True
    assert visited["tavern"] is False


def test_the_move_event_reports_a_first_arrival(session):
    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)

    (move,) = _events(session, EventType.MOVE)
    assert move["first_visit"] is True


def test_returning_somewhere_is_not_a_first_arrival(session):
    session.submit_move(NEXT_DOOR)
    session.join(timeout=30)
    session.submit_move(START)
    session.join(timeout=30)

    moves = _events(session, EventType.MOVE)
    assert moves[0]["first_visit"] is True
    assert moves[1]["first_visit"] is False


# --- classification --------------------------------------------------------


def test_move_is_a_player_event(session):
    """Everything in it is the player's own act or the clock; nothing is gated."""
    assert EventType.MOVE in PLAYER_EVENTS


# --- the HTTP surface ------------------------------------------------------


def test_the_move_endpoint_accepts_and_answers_202(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]

        res = client.post(f"/api/session/{sid}/move", json={"destination": NEXT_DOOR})
        assert res.status_code == 202
        assert res.json()["turn_id"]

        client.app.state.registry.get(sid).join(timeout=30)
        scene = client.get(f"/api/session/{sid}/scene").json()
        assert scene["location"]["location_id"] == NEXT_DOOR


def test_an_unreachable_destination_is_still_a_202(monkeypatch, tmp_path):
    """It was admitted and it ran; the refusal is an answer, not an HTTP error.

    Only *admission* failures answer synchronously (docs/12 §3.2). Turning this into a 4xx
    would also mean the endpoint knew the adjacency rule.
    """
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]

        assert (
            client.post(f"/api/session/{sid}/move", json={"destination": TOO_FAR}).status_code
            == 202
        )

        client.app.state.registry.get(sid).join(timeout=30)
        # Nothing moved.
        scene = client.get(f"/api/session/{sid}/scene").json()
        assert scene["location"]["location_id"] == START


def test_an_empty_destination_is_a_400(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]

        res = client.post(f"/api/session/{sid}/move", json={"destination": "  "})
        assert res.status_code == 400


def test_moving_in_an_unknown_session_is_a_404(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from ai_native_rpg.web.app import create_app

    with TestClient(create_app(dev_mode=True)) as client:
        res = client.post("/api/session/nope/move", json={"destination": NEXT_DOOR})
        assert res.status_code == 404

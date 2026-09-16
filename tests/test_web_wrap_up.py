"""Daily review cadence, visibility and HTTP contract."""

from __future__ import annotations

import json

import pytest

# Optional extra; see the note in test_web_turn.py.
pytest.importorskip("fastapi", reason="needs the 'web' extra")

from pathlib import Path

from ai_native_rpg.schemas.narrative import SLOTS_PER_DAY, TimeSlot
from ai_native_rpg.schemas.world_state import Visibility
from ai_native_rpg.web.session import Session

SCENARIO = "village_disappearance"
WEB_DIR = Path(__file__).resolve().parents[1] / "src" / "ai_native_rpg" / "web"

NEXT_DOOR = "village_square"


@pytest.fixture
def session(tmp_path):
    s = Session(
        session_id="wrap_session",
        scenario=SCENARIO,
        trace_dir=tmp_path,
        save_dir=tmp_path / "saves",
    )
    yield s
    s.close()


def _reach_the_wrap_up(session: Session) -> None:
    """Spend the day's slots the way play would: by going places.

    Driven through the real action rather than by writing the slot field, so the state the
    screen renders is one the game can actually reach.
    """
    hops = [NEXT_DOOR, "tavern", NEXT_DOOR]
    for destination in hops[:SLOTS_PER_DAY]:
        session.submit_move(destination)
        session.join(timeout=30)


def test_daily_changes_survive_resume_and_reset_next_morning(session, tmp_path):
    from ai_native_rpg.schemas.world_state import ActionProposal
    from ai_native_rpg.web.session import load_save

    baseline = session._day_start["known"].copy()
    result = session.manager.submit(
        ActionProposal(
            proposal_id="daily-trust",
            actor_id="npc_a",
            action_type="adjust_relationship",
            target_id=session.player_id,
            payload={"trust": 5},
        )
    )
    assert result.approved
    _reach_the_wrap_up(session)
    review = session.wrap_up()
    assert {f["id"] for f in review.discovered} == {
        f["id"] for f in review.known if baseline.get(f["id"]) != f["value"]
    }
    assert next(r for r in review.relationship_changes if r["npc_id"] == "npc_a")["trust"] == 5
    saved = load_save(session.session_id, tmp_path / "saves")
    resumed = Session(
        session_id="resumed-review",
        scenario=SCENARIO,
        resume=saved,
        trace_dir=tmp_path / "resumed-traces",
        save_dir=tmp_path / "saves",
    )
    try:
        assert resumed.wrap_up() == review
        resumed.submit_close_out_day()
        resumed.join(timeout=30)
        assert resumed._day_start["day"] == 2
        assert resumed._day_start["relationships"]["npc_a"]["trust"] == 15
    finally:
        resumed.close()


def test_old_save_without_daily_baseline_does_not_invent_new_clues(session):
    session._day_start = None
    _reach_the_wrap_up(session)
    review = session.wrap_up()
    assert not review.baseline_available
    assert review.discovered == review.relationship_changes == []
    assert review.known


def test_all_npcs_have_separate_memories_and_relationships(session):
    from ai_native_rpg.schemas.memory import SemanticMemory

    session._npcs["npc_b"].memory.add_semantic(
        SemanticMemory(
            memory_id="private-belief",
            npc_id="npc_b",
            fact="Only Loren remembers this",
            confidence=0.7,
        )
    )
    session.submit_turn("I want to help", option_id="goodwill")
    session.join(timeout=30)
    rows = {r["npc_id"]: r for r in session.panel().npcs}
    assert set(rows) == set(session.manager.snapshot().npcs)
    assert rows["npc_a"]["relationships"][session.player_id]["trust"] == 13
    assert rows["npc_b"]["relationships"][session.player_id]["trust"] != 13
    assert "private-belief" in json.dumps(rows["npc_b"]["memory"])
    assert "private-belief" not in json.dumps(rows["npc_a"]["memory"])
    assert "embedding" not in json.dumps(rows)
    assert rows["npc_a"]["last_dialogue"]
    assert "last_dialogue" not in rows["npc_b"]
    rows["npc_b"]["memory"]["semantic"].clear()
    assert session._npcs["npc_b"].memory.semantic_count > 0


# --- the cadence -----------------------------------------------------------


def test_no_wrap_up_before_the_day_is_spent(session):
    """docs/13 §4.2: once a day is what makes it a ritual rather than a lookup."""
    assert session.manager.snapshot().story_beats.time_slot is TimeSlot.MORNING

    assert session.wrap_up() is None


def test_the_wrap_up_arrives_when_the_slots_run_out(session):
    _reach_the_wrap_up(session)

    assert session.manager.snapshot().story_beats.time_slot is TimeSlot.WRAP_UP
    assert session.wrap_up() is not None


def test_the_wrap_up_costs_no_slot(session):
    """The named trap in docs/12 §13.1: "已用 3 / 12" is right at this moment."""
    _reach_the_wrap_up(session)

    budget = session.panel().slot_budget
    assert budget is not None
    assert budget.wrapping_up is True
    assert budget.spent_total == SLOTS_PER_DAY
    assert budget.remaining == budget.total - SLOTS_PER_DAY


def test_closing_out_the_day_starts_the_next_morning(session):
    _reach_the_wrap_up(session)

    session.submit_close_out_day()
    session.join(timeout=30)

    world = session.manager.snapshot()
    assert world.time_day == 2
    assert world.story_beats.time_slot is TimeSlot.MORNING
    assert world.story_beats.slots_spent_today == 0
    # And the interlude still cost nothing: a fresh day, three slots spent in total.
    assert session.panel().slot_budget.spent_total == SLOTS_PER_DAY


def test_the_wrap_up_is_gone_once_dismissed(session):
    _reach_the_wrap_up(session)
    session.submit_close_out_day()
    session.join(timeout=30)

    assert session.wrap_up() is None


def test_closing_out_a_day_is_not_a_narrative_turn(session):
    """The interlude spends nothing, so it must not spend a turn either."""
    _reach_the_wrap_up(session)
    before = session.manager.snapshot().story_beats.turn

    session.submit_close_out_day()
    session.join(timeout=30)

    assert session.manager.snapshot().story_beats.turn == before


# --- the review restates, never adds ---------------------------------------


def test_the_review_holds_only_what_the_player_can_see(session):
    _reach_the_wrap_up(session)
    view = session.wrap_up()

    visible = dict(session.manager.player_view(session.player_id).visible_facts)
    assert {entry["id"] for entry in view.known} == set(visible)


def test_the_review_adds_no_clue(session):
    """A wrap-up that handed something out would make the slot cost refundable by waiting."""
    _reach_the_wrap_up(session)
    before = set(session.manager.player_view(session.player_id).visible_facts)

    session.wrap_up()

    assert set(session.manager.player_view(session.player_id).visible_facts) == before


def test_unanswered_questions_are_passed_through_unedited(session):
    """docs/12 §13.3: the renderer must not append "该去哪查".

    Verified by identity with the engine's own list rather than by inspecting wording —
    equality is the mechanism, and any helpful addition breaks it.
    """
    _reach_the_wrap_up(session)
    day = session.engine.wrap_up(player_id=session.player_id)

    assert session.wrap_up().unanswered == list(day.review.unanswered)


# --- no leaks --------------------------------------------------------------


def _hidden_values(session) -> list[str]:
    """Every value the player has not earned, as it would appear serialised."""
    world = session.manager.snapshot()
    visible = session.manager.player_view(session.player_id).visible_facts
    values = [
        str(fact.value)
        for fact_id, fact in world.facts.items()
        if fact.visibility is not Visibility.REVEALED and fact_id not in visible
    ]
    return [v for v in values if v.strip()]


def test_no_hidden_fact_value_reaches_the_wrap_up_payload(session):
    """The HTTP-layer counterpart of the review's type-level guarantee.

    ``loren_that_night``'s value is the bare id ``npc_b``, so this also covers the
    "an id leaked as a value" case.
    """
    _reach_the_wrap_up(session)

    body = json.dumps(session.wrap_up().model_dump(), ensure_ascii=False, default=str)
    for value in _hidden_values(session):
        assert value not in body


def test_the_wrap_up_module_does_not_import_world_state():
    """Same mechanism as ``scene.py`` (docs/12 §7 item 2): the import is the guarantee."""
    source = (WEB_DIR / "wrap_up_view.py").read_text(encoding="utf-8")
    imports = [
        line
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and "WorldState" in line
    ]
    assert imports == []


def test_the_builder_takes_the_projections_not_the_world():
    import inspect

    from ai_native_rpg.narrative.wrap_up import ClueReview
    from ai_native_rpg.web.wrap_up_view import build_wrap_up

    annotations = inspect.get_annotations(build_wrap_up, eval_str=True)
    assert annotations["review"] is ClueReview


# --- the HTTP surface ------------------------------------------------------


def test_the_endpoint_returns_null_outside_the_wrap_up(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]

        res = client.get(f"/api/session/{sid}/wrap_up")
        assert res.status_code == 200
        assert res.json() is None


def test_the_endpoint_serves_the_screen_at_the_wrap_up(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]
        session = client.app.state.registry.get(sid)
        _reach_the_wrap_up(session)

        body = client.get(f"/api/session/{sid}/wrap_up").json()
        assert body["day"] == 1
        assert "discovered" in body
        assert "relationship_changes" in body

        # And no hidden value came along for the ride.
        for value in _hidden_values(session):
            assert value not in json.dumps(body, ensure_ascii=False)


def test_closing_out_the_day_over_http(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]
        session = client.app.state.registry.get(sid)
        _reach_the_wrap_up(session)

        res = client.post(f"/api/session/{sid}/wrap_up/close")
        assert res.status_code == 202
        session.join(timeout=30)

        scene = client.get(f"/api/session/{sid}/scene").json()
        assert scene["time_day"] == 2
        assert scene["time_slot"] == TimeSlot.MORNING.value


def test_closing_out_is_refused_outside_the_wrap_up(monkeypatch, tmp_path):
    """``advance_past_wrap_up`` owns this refusal; the Web layer only reports it.

    It is not an HTTP error — the request was admitted and ran. The refusal arrives on the
    stream with the reason, like any other rejected action.
    """
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]
        session = client.app.state.registry.get(sid)

        assert client.post(f"/api/session/{sid}/wrap_up/close").status_code == 202
        session.join(timeout=30)

        world = session.manager.snapshot()
        assert world.time_day == 1
        assert world.story_beats.time_slot is TimeSlot.MORNING
        refusal = [e.data for e in session._history if e.type.value == "move"][-1]
        assert refusal["approved"] is False
        assert "wrap-up" in refusal["reason"]

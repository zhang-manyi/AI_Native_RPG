"""The wrap-up screen at the Web layer (docs/12 §13.3).

Two properties carry this file, and both are leak checks rather than rendering checks:

* the review **only restates what the player has** — its input is a ``VisibleState``, so a
  leak would require widening a parameter type, not forgetting a branch (docs/13 §5.1);
* the reading **carries no raw counts, and the Web layer does not reconstruct them**.
  ``TarotReading`` withholds them on purpose ("还剩 7 条" is a number the player optimises
  against, docs/15 §2), so inverting the ratio back into a count here would undo the one
  decision that shaped the model.

``tests/test_wrap_up.py`` pins the same properties one layer down. These are the
HTTP-layer counterparts docs/12 §13.3 asks for.
"""

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

    from ai_native_rpg.narrative.wrap_up import ClueReview, TarotReading
    from ai_native_rpg.web.wrap_up_view import build_wrap_up

    annotations = inspect.get_annotations(build_wrap_up, eval_str=True)
    assert annotations["review"] is ClueReview
    assert annotations["reading"] is TarotReading


# --- the reading says shape, not counts ------------------------------------


def test_the_reading_carries_a_ratio_and_a_label(session):
    _reach_the_wrap_up(session)
    reading = session.wrap_up().reading

    assert 0.0 <= reading.darkness <= 1.0
    assert reading.reads_as in {"mostly_dark", "half_lit", "nearly_clear"}
    # The label is the engine's judgement, not a constant this layer knows: asserting a
    # particular one here would break whenever the pack revealed one more opening fact.
    assert reading.reads_as == session.engine.wrap_up(player_id=session.player_id).reading.reads_as


def test_a_darker_case_reads_darker(session):
    """The label tracks the world, so the screen is not showing a fixed mood.

    Re-hiding an opening fact is the smallest way to move ``darkness`` without playing a
    whole case, and it checks the direction rather than a threshold value.
    """
    _reach_the_wrap_up(session)
    lit = session.wrap_up().reading

    world = session.manager.snapshot()
    for fact in world.facts.values():
        fact.visibility = Visibility.HIDDEN
        fact.reveal_condition = None
    session.manager._state = world

    darker = session.wrap_up().reading
    assert darker.darkness > lit.darkness
    assert darker.reads_as == "mostly_dark"


def test_the_reading_exposes_no_count_field(session):
    """docs/12 §13.3: ``TarotReading`` omits raw counts, and so must its view."""
    _reach_the_wrap_up(session)
    payload = session.wrap_up().reading.model_dump()

    assert set(payload) == {"darkness", "tension", "imagery", "reads_as"}


def test_the_payload_never_states_how_many_clues_remain(session):
    """The ratio must not be invertible from what is sent.

    Recovering "7 left" needs the total, and no total appears in the payload — which is
    the structural version of "don't multiply the fraction back out".
    """
    _reach_the_wrap_up(session)
    world = session.manager.snapshot()
    view = session.wrap_up()

    hidden_count = len(world.facts) - len(view.known)
    payload = json.dumps(view.model_dump(), ensure_ascii=False, default=str)

    # Neither the number still hidden nor the total it would be measured against.
    for forbidden in (hidden_count, len(world.facts)):
        assert f'"{forbidden}"' not in payload
    numbers = {v for v in view.reading.model_dump().values() if isinstance(v, int)}
    assert numbers == set()


def test_the_imagery_never_names_a_fact(session):
    _reach_the_wrap_up(session)
    world = session.manager.snapshot()

    for card in session.wrap_up().reading.imagery:
        assert card not in world.facts
        for fact in world.facts.values():
            if isinstance(fact.value, str):
                assert card not in fact.value


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
        assert body["reading"]["reads_as"] in {"mostly_dark", "half_lit", "nearly_clear"}
        assert set(body["reading"]) == {"darkness", "tension", "imagery", "reads_as"}

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

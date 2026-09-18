"""The three input forms at the Web layer (docs/12 §13.7, docs/15 §1).

Scripted line, tagged option, free text. What is worth pinning here is not the rendering
— docs/12 §11 leaves the front end to a manual pass — but the three structural claims the
interface rests on:

* **the pack decides the form, not this layer.** docs/15 §1's criterion is a schema
  validator, so by the time options reach a scene they are already known to diverge;
* **clicking and typing share one submit path.** Same endpoint, same executor, same
  resolution call (docs/15 §1.1). If typing had no defined check while clicking did,
  typing would be strictly worse and everyone would click — which kills the open-input
  path docs/03 §5 exists to protect;
* **an option carries no consequences.** No check, no threshold, no outcome id: a player
  who could see which branch pays better is picking a consequence, and consequences are
  authored (docs/13 §3).

Offline throughout, via ``conftest``'s autouse ``USE_MOCK_LLM=1``.
"""

from __future__ import annotations

import pytest

# Optional extra; see the note in test_web_turn.py.
pytest.importorskip("fastapi", reason="needs the 'web' extra")

from ai_native_rpg.scenario import load_event_script
from ai_native_rpg.web.scene import SceneOption
from ai_native_rpg.web.session import Session, SessionError

SCENARIO = "village_disappearance"

#: The pack's opening event, and the options it authors.
OPENING = "M1_knock"


@pytest.fixture
def session(tmp_path):
    s = Session(
        session_id="forms_session",
        scenario=SCENARIO,
        trace_dir=tmp_path,
        save_dir=tmp_path / "saves",
    )
    yield s
    s.close()


def _open_the_event(session: Session) -> None:
    """Run one tick so the opening event is in progress.

    The engine opens events; nothing in the Web layer may. This is the same order
    ``tests/test_event_chain.py`` uses: tick, then resolve.
    """
    session.engine.tick(player_id=session.player_id)


# --- the script is actually loaded -----------------------------------------


def test_the_session_loads_the_packs_event_script(session):
    """Without this the page has nothing to render: no active event, ever.

    The engine was previously assembled without ``script=``, so ``active_event()`` could
    only ever be ``None`` and both other forms were unreachable.
    """
    assert set(session.engine.script.events) == set(load_event_script(SCENARIO).events)


def test_an_event_opens_and_reaches_the_scene(session):
    _open_the_event(session)

    assert session.engine.active_event() is not None
    assert session.scene().options


# --- no options is a state, not an empty box -------------------------------


def test_no_event_means_no_options_and_no_scripted_lines(session):
    """docs/12 §13.7: an option appearing *is* the signal, so it must not be permanent.

    An empty list is what tells the front end to render no option area at all. A fresh
    session no longer starts in this state on its own — it runs an opening tick so M1's
    line is in the very first scene (docs/12 §4.2) — so this closes that event first to
    reach the state being pinned: no event in progress, at all.
    """
    session.engine.end_conversation()

    scene = session.scene()

    assert scene.options == []
    assert scene.scripted_lines == []


# --- form one: the scripted line -------------------------------------------


def test_scripted_lines_come_from_the_pack(session):
    _open_the_event(session)
    definition = session.engine.script.get(OPENING)

    assert session.scene().scripted_lines == list(definition.scripted_lines)


def test_authored_passages_carry_their_actual_speakers(session):
    """Fixed NPC replies and narration never become player submissions."""
    _open_the_event(session)
    scene = session.scene()

    assert scene.scripted_lines == []
    assert [line.speaker for line in scene.passages] == ["narrator", "player", "npc_a"]


# --- form two: the tagged option -------------------------------------------


def test_options_carry_public_fields_without_resolution_details(session):
    """No check, no threshold, no outcome (docs/13 §3: consequences are authored)."""
    _open_the_event(session)

    assert set(SceneOption.model_fields) == {"option_id", "tag", "text", "final_report"}
    assert all(not option.final_report for option in session.scene().options)


def test_no_option_payload_names_an_outcome(session):
    """The strongest form of the above: the outcome ids simply are not in the payload."""
    _open_the_event(session)
    definition = session.engine.script.get(OPENING)
    outcomes = set(definition.outcomes)

    body = session.scene().model_dump_json()
    for outcome_id in outcomes:
        assert outcome_id not in body


def test_no_option_payload_leaks_a_threshold(session):
    """A visible threshold turns the three-band check into arithmetic to optimise."""
    _open_the_event(session)
    checked = [o for o in session.engine.script.get(OPENING).options if o.check]
    assert checked, "the opening event should have at least one checked option"

    payload = [o.model_dump() for o in session.scene().options]
    for option in payload:
        assert "check" not in option
        assert "threshold" not in option


def test_the_visible_tag_is_the_packs_tag(session):
    _open_the_event(session)
    definition = session.engine.script.get(OPENING)

    expected = {(o.option_id, o.tag.value) for o in definition.options}
    assert {(o.option_id, o.tag) for o in session.scene().options} == expected


def test_clicking_an_option_resolves_the_event(session):
    """The click reaches ``resolve_player_response``, so an outcome lands."""
    _open_the_event(session)

    session.submit_turn("我不是来添麻烦的", option_id="goodwill")
    session.join(timeout=30)

    beats = session.manager.snapshot().story_beats
    # The exchange was recorded, which only the resolution path does.
    assert OPENING in beats.completed_events or beats.active_event is not None
    assert session.manager.get_trust("npc_a", session.player_id) == 13
    assert session.scene().passages[0].speaker == "npc_a"
    assert (
        session.scene().passages[0].text
        == session.engine.script.get(OPENING).outcomes["she_opens"].npc_reply
    )


def test_a_checked_option_can_move_the_relationship(session):
    """End to end: the click landed an authored outcome, not just a line.

    ``press_that_night`` is a certain failure at opening trust (docs/15 §4 M1), and its
    failure branch raises fear — so the numbers moving is evidence the outcome applied.
    """
    _open_the_event(session)
    before = session.manager.get_relationship(session.npc_id, session.player_id).fear

    session.submit_turn("那晚你在外面，对吗", option_id="press_that_night")
    session.join(timeout=30)

    after = session.manager.get_relationship(session.npc_id, session.player_id).fear
    assert after > before


# --- form three: free text, judged the same way ----------------------------


def test_free_text_is_classified_against_the_current_options(session):
    """docs/15 §1.1: the classification rides along on the NPC call, no extra request."""
    _open_the_event(session)
    calls_before = len(session._history)

    session.submit_turn("我不会为难你")
    session.join(timeout=30)

    # The turn ran, and the options were offered to the planner (the mock ignores them,
    # but the wiring is what this asserts).
    assert session.engine.active_event_options()[0]["id"] == "goodwill"
    assert len(session._history) > calls_before


def test_the_options_handed_to_the_model_are_ids_and_text_only(session):
    """A model shown consequences would pick by preferred outcome (docs/13 §3)."""
    _open_the_event(session)

    for offered in session.engine.active_event_options():
        assert set(offered) == {"id", "text"}


def test_typing_and_clicking_use_the_same_endpoint(monkeypatch, tmp_path):
    """One path, so the two forms cannot be judged differently (docs/12 §13.7)."""
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "saves")
    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]
        live = client.app.state.registry.get(sid)
        _open_the_event(live)

        typed = client.post(f"/api/session/{sid}/turn", json={"text": "我不会为难你"})
        assert typed.status_code == 202
        live.join(timeout=30)

        clicked = client.post(
            f"/api/session/{sid}/turn",
            json={"text": "我不是来添麻烦的", "option_id": "goodwill"},
        )
        assert clicked.status_code == 202
        live.join(timeout=30)


def test_an_option_nobody_offered_is_refused_without_side_effects(session):
    """A stale or forged id cannot consume an authored decision."""
    _open_the_event(session)

    before = session.manager.snapshot()
    with pytest.raises(SessionError):
        session.submit_turn("随便说点什么", option_id="charm_her_completely")
    assert session.manager.snapshot() == before


def test_an_option_sent_without_an_event_is_refused(session):
    """With nothing in progress, ``resolve_player_response`` returns None and the turn stands.

    The turn's *own* tick may then open an event — that is the normal sequence — so what
    this pins is that the stray option resolved nothing on the way in, not that the world
    stayed empty afterwards.

    Closes the session's own opening tick first (docs/12 §4.2 now runs one at
    construction, so a fresh session no longer starts with ``active_event is None`` on
    its own) to reach the state being pinned.
    """
    session.engine.end_conversation()
    assert session.manager.snapshot().story_beats.active_event is None

    with pytest.raises(SessionError):
        session.submit_turn("你好", option_id="goodwill")
    assert session.manager.snapshot().story_beats.active_event is None


# --- the waiting rule -------------------------------------------------------


def test_an_option_cannot_be_submitted_while_a_turn_is_in_flight(session):
    """Options are disabled during the wait for the same reason the input box is.

    The front end greys them out; the server stays the authority (docs/12 §5.3).
    """
    from ai_native_rpg.web.session import SessionError

    _open_the_event(session)
    session.submit_turn("第一句", option_id="goodwill")

    with pytest.raises(SessionError, match="in flight"):
        session.submit_turn("第二句", option_id="goodwill")

    session.join(timeout=30)

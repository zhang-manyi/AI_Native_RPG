"""Play using only actions offered by the page. Never patch world state to progress."""

import json

import pytest

from ai_native_rpg.config import Settings
from ai_native_rpg.web.events import EventType
from ai_native_rpg.web.session import Session, SessionError, load_save


@pytest.fixture
def game(tmp_path):
    session = Session(
        session_id="playthrough",
        scenario="village_disappearance",
        settings=Settings(use_mock=True),
        trace_dir=tmp_path / "traces",
        save_dir=tmp_path / "saves",
        dev_mode=False,
    )
    yield session
    session.close()


def settle(game):
    game.join(timeout=10)
    assert not game.busy
    failures = [e.data for e in game._history if e.type == EventType.TURN_FAILED]
    assert failures == []


def choose(game, option_id):
    option = next(o for o in game.scene().options if o.option_id == option_id)
    game.submit_turn(option.text, option_id=option_id)
    settle(game)


def move(game, destination):
    assert destination in {d.location_id for d in game.scene().destinations}
    game.submit_move(destination)
    settle(game)
    assert game.scene().location.location_id == destination
    if game.scene().time_slot == "wrap_up":
        game.submit_close_out_day()
        settle(game)


def investigate(game):
    assert {f["id"] for f in game.scene().visible_facts} == {
        "victim_name",
        "disappearance_night",
        "search_failed",
    }
    assert [p.speaker for p in game.scene().passages] == ["narrator", "player", "npc_a"]
    choose(game, "goodwill")
    assert game.engine.active_event().event_id == "S1_help_marta"
    choose(game, "help_latch")
    choose(game, "goodwill")
    assert game.manager.get_trust("npc_a", game.player_id) == 40
    assert "F1_things_missing" in game.manager.snapshot().story_beats.completed_events
    choose(game, "goodwill")
    assert game.scene().quest_stages["investigation"] == 1
    move(game, "village_square")
    assert game.scene().current_npc_id is None
    assert any("车最近要出村" in p.text for p in game.scene().passages)
    assert all(p.speaker == "narrator" for p in game.scene().passages)
    move(game, "tavern")
    choose(game, "goodwill")
    assert game.scene().quest_stages["investigation"] == 2
    move(game, "village_square")
    move(game, "forest_edge")
    for option in ["observe_traces", "observe_cloth", "observe_blood"]:
        choose(game, option)
    assert game.scene().quest_stages["investigation"] == 3
    assert game.scene().ending is None
    assert "ella_whereabouts" not in {f["id"] for f in game.scene().visible_facts}
    assert game.scene().can_conclude


def test_complete_truth_route_without_typing_or_model_calls(game, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("the mock authored route must not call an LLM")

    monkeypatch.setattr(game.engine._llm, "complete", unexpected)
    investigate(game)
    game.submit_conclude_case()
    settle(game)
    choose(game, "tell_loren_alive")
    assert game.engine.reached_ending().ending_id == "truth_uncovered"
    assert game.scene().ending["title"] == "结局：通往镇上的路"
    assert game._npcs["npc_a"].memory.episodic_count > 0
    assert game._npcs["npc_c"].memory.episodic_count > 0
    with pytest.raises(SessionError):
        game.submit_turn("继续")


def test_early_accusation_reaches_an_ending_without_exposing_the_answer(game):
    move(game, "village_square")
    move(game, "forest_edge")
    for option in ["observe_traces", "observe_cloth", "observe_blood"]:
        choose(game, option)
    game.submit_conclude_case()
    settle(game)
    assert "tell_loren_alive" not in {o.option_id for o in game.scene().options}
    choose(game, "accuse_loren")
    assert game.engine.reached_ending().ending_id == "accused_the_wrong_man"
    assert "错" not in game.scene().ending["title"]


def test_deadline_is_reachable_using_only_travel_and_day_close(game):
    for _ in range(6):
        move(game, "village_square")
        move(game, "npc_a_house")
    assert game.engine.reached_ending().ending_id == "never_found_out"
    assert game.scene().options == []


def raise_loren_pressure(game, *, check_stale=True):
    """Start from the ordinary forest menu; every mutation is a player action."""
    choose(game, "press_loren")  # trust 0: certain failure, closes only M5
    assert game.manager.snapshot().story_beats.tension == 0.2
    for expected in (0.4, 0.6):
        choose(game, "keep_pressing")
        assert game.manager.snapshot().story_beats.tension == expected
    assert game.engine.reached_ending() is None
    assert "keep_pressing" not in {o.option_id for o in game.scene().options}
    if check_stale:
        before = game.manager.snapshot()
        with pytest.raises(SessionError):
            game.submit_turn("继续追问", option_id="keep_pressing")
        assert game.manager.snapshot() == before


def hear_loren_warning(game):
    fear_before = game.manager.get_relationship("npc_a", game.player_id).fear
    move(game, "village_square")
    move(game, "npc_a_house")
    world = game.manager.snapshot()
    assert world.story_beats.completed_events.count("M6_warning") == 1
    assert game.manager.get_relationship("npc_a", game.player_id).fear == fear_before + 12
    assert world.story_beats.tension == 0.6
    assert world.facts["npc_b_aware_of_investigation"].visibility == "revealed"
    assert world.facts["npc_a_threatened"].visibility == "revealed"
    assert any("你不在的时候" in p.text for p in game.scene().passages)
    visible = {f["id"] for f in game.scene().visible_facts}
    assert {"loren_that_night", "ella_whereabouts"}.isdisjoint(visible)
    assert game.engine.reached_ending() is None


def test_loren_moves_first_from_initial_state_and_survives_resume(game, tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("the authored failure route must not call an LLM")

    monkeypatch.setattr(game.engine._llm, "complete", unexpected)
    move(game, "village_square")
    move(game, "forest_edge")
    raise_loren_pressure(game)
    hear_loren_warning(game)
    move(game, "village_square")
    move(game, "forest_edge")
    choose(game, "keep_pressing")
    world = game.manager.snapshot()
    assert world.story_beats.tension == 0.8
    assert world.story_beats.ended_at == "loren_moves_first"
    assert world.time_day <= world.story_beats.day_limit
    assert game.scene().ending["title"] == "结局：调查失控"
    assert game.scene().options == []
    assert not game.scene().can_conclude

    # The authored choice and all Validator decisions remain in the persisted trace.
    records = [
        json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path / "traces").rglob("*.json")
    ]
    resolutions = [
        step["output_summary"]
        for record in records
        for step in record.get("steps", [])
        if step["step_name"] == "event_resolution"
    ]
    assert any(r["event_id"] == "M6_pressure" for r in resolutions)
    assert all(p["approved"] for r in resolutions for p in r["proposals"])
    warning_ticks = [r for r in records if "M6_warning" in r.get("authored_events", [])]
    assert len(warning_ticks) == 1
    assert all(p["approved"] for p in warning_ticks[0]["proposals"])
    saved = load_save(game.session_id, tmp_path / "saves")
    resumed = Session(
        session_id="resumed-ending",
        scenario=game.scenario,
        settings=Settings(use_mock=True),
        trace_dir=tmp_path / "resume-traces",
        save_dir=tmp_path / "saves",
        resume=saved,
    )
    try:
        assert resumed.manager.snapshot() == world
        assert resumed.scene().ending == game.scene().ending
        with pytest.raises(SessionError):
            resumed.submit_turn("继续追问")
        resumed.submit_move("village_square")
        settle(resumed)
        resumed.submit_conclude_case()
        settle(resumed)
        refused = [e for e in resumed._history if e.data.get("approved") is False]
        assert len(refused) == 2
        assert resumed.manager.snapshot() == world
    finally:
        resumed.close()


def test_warning_is_once_only_and_backing_off_preserves_the_truth_route(game):
    for option in ("goodwill", "help_latch", "goodwill", "goodwill"):
        choose(game, option)
    move(game, "village_square")
    move(game, "tavern")
    choose(game, "goodwill")
    move(game, "village_square")
    move(game, "forest_edge")
    choose(game, "observe_traces")  # secure evidence before closing M5
    raise_loren_pressure(game)
    hear_loren_warning(game)
    fear = game.manager.get_relationship("npc_a", game.player_id).fear
    game.engine.refresh_authored(game.player_id)
    assert game.manager.get_relationship("npc_a", game.player_id).fear == fear
    assert game.manager.snapshot().story_beats.completed_events.count("M6_warning") == 1
    move(game, "village_square")
    move(game, "forest_edge")
    choose(game, "stop_pressing")
    assert game.manager.snapshot().story_beats.is_closed("M6_pressure")
    game.submit_conclude_case()
    settle(game)
    choose(game, "tell_loren_alive")
    assert game.engine.reached_ending().ending_id == "truth_uncovered"
    assert game.manager.snapshot().story_beats.tension == 0.6


def test_stale_option_is_refused_without_changing_the_world(game):
    before = game.manager.snapshot()
    with pytest.raises(SessionError):
        game.submit_turn("她还活着", option_id="tell_loren_alive")
    assert game.manager.snapshot() == before


def test_resume_keeps_the_decision_and_does_not_reapply_automatic_clues(game, tmp_path):
    choose(game, "goodwill")
    choose(game, "help_latch")
    choose(game, "goodwill")
    saved = load_save(game.session_id, tmp_path / "saves")
    second = Session(
        session_id="resumed",
        scenario=game.scenario,
        settings=Settings(use_mock=True),
        trace_dir=tmp_path / "resume-traces",
        save_dir=tmp_path / "saves",
        resume=saved,
    )
    try:
        assert second.manager.snapshot() == game.manager.snapshot()
        assert second.scene().options == game.scene().options
        choose(second, "goodwill")
        assert (
            second.manager.snapshot().story_beats.completed_events.count("F1_things_missing") == 1
        )
    finally:
        second.close()


def use_expression_client(game, responses):
    from pydantic import SecretStr

    from ai_native_rpg.llm import MockLLMClient

    # Exercise the real-backend branch with an injected offline client. Never use a key.
    game._settings = Settings(use_mock=False, api_key=SecretStr("offline-test"))
    client = MockLLMClient(responses)
    game._npcs["npc_a"].harness._llm = client
    return client


def test_clicked_choice_resolves_before_one_expression_call(game, monkeypatch):
    client = use_expression_client(game, [{"dialogue": "进来说吧，小声些。"}])
    complete = client.complete
    trust_at_generation = []

    def inspect(*args, **kwargs):
        trust_at_generation.append(game.manager.get_trust("npc_a", game.player_id))
        return complete(*args, **kwargs)

    monkeypatch.setattr(client, "complete", inspect)
    choose(game, "goodwill")
    assert trust_at_generation == [13]
    assert game.panel().last_turn_cost.llm_calls == 1
    prompt = "\n".join(m.content for m in client.calls[0].messages)
    assert "不得改变成败" in prompt
    assert "她开门" in prompt


def test_free_text_classifies_then_resolves_then_speaks(game, monkeypatch):
    client = use_expression_client(
        game,
        [
            {"matched_option_id": "goodwill"},
            {"dialogue": "那你进来吧。"},
        ],
    )
    complete = client.complete
    trust_at_call = []

    def inspect(*args, **kwargs):
        trust_at_call.append(game.manager.get_trust("npc_a", game.player_id))
        return complete(*args, **kwargs)

    monkeypatch.setattr(client, "complete", inspect)
    game.submit_turn("放心，我只是想帮你，不会给你惹事。")
    settle(game)
    assert trust_at_call == [10, 13]
    assert game.engine.active_event().event_id == "S1_help_marta"
    assert game.panel().last_turn_cost.llm_calls == 2
    classifier_prompt = "\n".join(m.content for m in client.calls[0].messages)
    assert "she_opens" not in classifier_prompt


def test_expression_failure_keeps_the_result_and_uses_authored_reply(game, monkeypatch):
    client = use_expression_client(game, [])

    def unavailable(*args, **kwargs):
        raise TimeoutError("offline failure")

    monkeypatch.setattr(client, "complete", unavailable)
    choose(game, "goodwill")
    assert game.manager.get_trust("npc_a", game.player_id) == 13
    assert game.scene().passages[0].speaker == "npc_a"
    assert (
        game.scene().passages[0].text
        == game.engine.script.get("M1_knock").outcomes["she_opens"].npc_reply
    )
    assert game.engine.active_event().event_id == "S1_help_marta"


def test_leaving_closes_the_previous_event_and_cannot_repeat_an_observation(game):
    choose(game, "goodwill")
    choose(game, "help_latch")
    choose(game, "observe_hands")
    before = game.manager.snapshot()
    with pytest.raises(SessionError):
        game.submit_turn("再看看", option_id="observe_hands")
    assert game.manager.snapshot() == before
    move(game, "village_square")
    assert game.engine.active_event() is None
    assert all(p.speaker == "narrator" for p in game.scene().passages)


def test_losing_the_witness_still_allows_independent_investigation(game):
    choose(game, "goodwill")
    choose(game, "help_latch")
    choose(game, "goodwill")
    choose(game, "press_who")  # certain failure: trust 40 against threshold 50
    assert game.manager.snapshot().story_beats.is_closed("M3_witness")
    move(game, "village_square")
    move(game, "tavern")
    choose(game, "goodwill")
    move(game, "village_square")
    move(game, "forest_edge")
    for option in ["observe_traces", "observe_cloth", "observe_blood"]:
        choose(game, option)
    assert game.scene().quest_stages["investigation"] == 2
    game.submit_conclude_case()
    settle(game)
    choose(game, "tell_loren_alive")
    assert game.engine.reached_ending().ending_id == "truth_uncovered"


@pytest.mark.parametrize("route", ["truth", "loren"])
def test_complete_playthrough_through_player_http_routes(tmp_path, monkeypatch, route):
    from fastapi.testclient import TestClient

    from ai_native_rpg.web import session as session_module
    from ai_native_rpg.web.app import create_app
    from ai_native_rpg.web.scene import SceneView

    monkeypatch.setattr(session_module, "SAVE_DIR", tmp_path / "http-saves")
    with TestClient(create_app(dev_mode=False)) as client:
        response = client.post("/api/session", json={"scenario": "village_disappearance"})
        assert response.status_code == 200
        sid = response.json()["session_id"]
        live = client.app.state.registry.get(sid)

        class HTTPGame:
            def __getattr__(self, name):
                return getattr(live, name)

            def scene(self):
                response = client.get(f"/api/session/{sid}/scene")
                assert response.status_code == 200
                return SceneView.model_validate(response.json())

            def post(self, endpoint, body=None):
                response = client.post(f"/api/session/{sid}/{endpoint}", json=body)
                assert response.status_code == 202, response.text

            def submit_turn(self, text, *, option_id=None):
                self.post("turn", {"text": text, "option_id": option_id})

            def submit_move(self, destination):
                self.post("move", {"destination": destination})

            def submit_close_out_day(self):
                self.post("wrap_up/close")

        game = HTTPGame()
        if route == "truth":
            investigate(game)
            game.post("conclude")
            settle(game)
            choose(game, "tell_loren_alive")
            assert live.engine.reached_ending().ending_id == "truth_uncovered"
        else:
            move(game, "village_square")
            move(game, "forest_edge")
            raise_loren_pressure(game, check_stale=False)
            hear_loren_warning(game)
            move(game, "village_square")
            move(game, "forest_edge")
            choose(game, "keep_pressing")
            assert live.engine.reached_ending().ending_id == "loren_moves_first"
        assert game.scene().ending is not None
        frames = "".join(event.frame() for event in live._history)
        assert "event: dialogue" in frames
        assert "event: scene" in frames
        assert "event: panel" not in frames
        assert "event: narrative_tick" not in frames
        assert live._history[-1].data["ready"] is True

"""Input isolation and actual verdicts, including paths that previously bypassed disclosure."""

import json

import pytest
from pydantic import SecretStr

from ai_native_rpg.agent.expression import ResolvedPublicOutcome
from ai_native_rpg.agent.harness import Harness, PlanningOutput
from ai_native_rpg.agent.memory_store import MemoryStore
from ai_native_rpg.agent.tools import build_npc_tools
from ai_native_rpg.config import Settings
from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.scenario import load_personas, load_scenario
from ai_native_rpg.schemas.memory import EpisodicMemory
from ai_native_rpg.world.manager import WorldStateManager


def setup(responses):
    client = MockLLMClient(responses)
    manager = WorldStateManager(load_scenario("village_disappearance"))
    memory = MemoryStore("npc_a")
    memory.add_episodic(
        EpisodicMemory(
            memory_id="private",
            npc_id="npc_a",
            event_description="PRIVATE_SOURCE_CANARY",
            importance=1,
            occurred_at_day=1,
        )
    )
    harness = Harness(
        npc_state=load_personas("village_disappearance")["npc_a"],
        manager=manager,
        memory=memory,
        llm=client,
        public_expression=True,
        tools=build_npc_tools(npc_id="npc_a", manager=manager, memory=memory, player_id="player_1"),
    )
    return harness, manager, memory, client


@pytest.mark.parametrize(
    "action,status,kind",
    [
        (None, "none", None),
        (
            {"action_type": "adjust_relationship", "payload": {"trust": 5}},
            "applied",
            "adjust_relationship",
        ),
        (
            {"action_type": "reveal_fact", "target_id": "npc_a_threatened"},
            "rejected",
            "reveal_fact",
        ),
        ({"action_type": "reveal_fact", "target_id": "victim_name"}, "applied", "reveal_fact"),
        ({"action_type": "move", "target_id": "forest_edge"}, "rejected", "move"),
        ({"action_type": "SECRET_INVALID_ACTION"}, "rejected", "unsupported"),
    ],
)
def test_public_generation_never_receives_private_plan_or_denied_target(action, status, kind):
    harness, manager, _, client = setup(
        [
            PlanningOutput(
                reasoning="SECRET_PLAN",
                strategy="SECRET_STYLE",
                dialogue="SECRET_DRAFT",
                action=action,
            ),
            {"dialogue": "公开回应"},
        ]
    )
    harness.remember_exchange("我的笔记本叫松风", "SECRET_NPC_REPLY", player_id="player_1")
    harness.remember_exchange("OTHER_PLAYER_PRIVATE", "another reply", player_id="other")
    before = manager.snapshot()
    response, trace = harness.respond(
        "我的笔记本叫什么？", player_id="player_1", narrative_event={"content": "SECRET_HOOK"}
    )
    assert response.dialogue == "公开回应"
    assert len(client.calls) == 2
    final = "\n".join(m.content for m in client.calls[-1].messages)
    assert "SECRET" not in final
    assert "PRIVATE_SOURCE_CANARY" not in final
    assert "OTHER_PLAYER_PRIVATE" not in final
    assert harness._npc.persona.background not in final
    assert harness._npc.goal.primary not in final
    assert "npc_a_threatened" not in final
    evidence = json.loads(client.calls[-1].messages[-1].content)["allowed_evidence"]
    assert any(p["text"] == "我的笔记本叫松风" for p in evidence["player_statements"])
    assert evidence["action_result"]["status"] == status
    assert evidence["action_result"].get("kind") == kind
    assert "reason" not in evidence["action_result"]
    if kind == "adjust_relationship":
        assert manager.get_trust("npc_a", "player_1") == 15
        assert evidence["action_result"]["relationship_now"]["trust"] == 15
    if status == "applied" and kind == "reveal_fact":
        assert evidence["action_result"]["revealed_fact"] == "磨坊主的女儿艾拉，十四岁"
    if status != "applied":
        assert manager.snapshot() == before
    assert client.calls[-1].tools is None
    assert any(s.step_name == "expression_evidence" for s in trace.steps)


def test_only_explicit_public_outcome_reaches_resolved_expression():
    harness, _, _, client = setup([{"dialogue": "门已经修好了，谢谢。"}])
    response, _ = harness.respond(
        "修门",
        player_id="player_1",
        resolved_outcome="PRIVATE_AUTHOR_CONSTRAINT",
        resolved_public_outcome=ResolvedPublicOutcome(
            summary="门闩已修好", reply="谢谢，门能关上了"
        ),
    )
    assert response.dialogue == "门已经修好了，谢谢。"
    assert len(client.calls) == 1
    final = "\n".join(m.content for m in client.calls[0].messages)
    assert "PRIVATE" not in final
    assert "门闩已修好" in final
    with pytest.raises(ValueError, match="explicit resolved public outcome"):
        harness.respond(
            "修门", player_id="player_1", resolved_outcome="not classified for disclosure"
        )
    assert (
        len(client.calls) == 1
    )  # Fail before any generation, never fall back to a private prompt.


def test_expression_error_does_not_publish_private_draft_or_repeat_action():
    harness, manager, memory, client = setup(
        [
            PlanningOutput(
                reasoning="SECRET_PLAN",
                strategy="s",
                dialogue="SECRET_DRAFT",
                action={"action_type": "adjust_relationship", "payload": {"trust": 5}},
            ),
            {},  # Invalid DialogueOutput: the actual expression call fails.
            PlanningOutput(reasoning="r", strategy="s", dialogue="SECRET_DRAFT"),
            {"dialogue": "可以继续说。"},
        ]
    )
    with pytest.raises(RuntimeError):
        harness.respond("你好", player_id="player_1")
    assert manager.get_trust("npc_a", "player_1") == 15
    assert memory.episodic_count == 1  # No reflection of the private draft.
    response, _ = harness.respond("继续", player_id="player_1")
    assert response.dialogue == "可以继续说。"
    assert manager.get_trust("npc_a", "player_1") == 15
    assert len(client.calls) == 4


def test_web_real_assembly_applies_boundary_and_authored_failure_can_continue(
    tmp_path, monkeypatch
):
    from ai_native_rpg.web import session as module

    client = MockLLMClient([{}, {"dialogue": "门修好了，谢谢。"}])
    monkeypatch.setattr(module, "build_llm_client", lambda *_args, **_kwargs: client)
    settings = Settings(use_mock=False, api_key=SecretStr("offline-test"))
    assert settings.public_expression
    game = module.Session(
        session_id="public-boundary",
        scenario="village_disappearance",
        settings=settings,
        save_dir=tmp_path / "saves",
        trace_dir=tmp_path / "traces",
    )
    try:
        assert all(n.harness._public_expression for n in game._npcs.values())
        game.engine.active_event().constraints.append("PRIVATE_AUTHOR_CONSTRAINT")
        for option_id in ("goodwill", "help_latch"):
            option = next(o for o in game.scene().options if o.option_id == option_id)
            game.submit_turn(option.text, option_id=option_id)
            game.join(timeout=10)
            assert not game.busy
        assert game.manager.get_trust("npc_a", game.player_id) == 28
        assert all(
            "PRIVATE_AUTHOR_CONSTRAINT" not in m.content for c in client.calls for m in c.messages
        )
        assert game.scene().passages[0].text == "门修好了，谢谢。"
        traces = [
            json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path / "traces").glob("*.json")
        ]
        assert sum(s["step_name"] == "expression_fallback" for t in traces for s in t["steps"]) == 1
        saved = module.load_save(game.session_id, tmp_path / "saves")
        resumed = module.Session(
            session_id="public-resumed",
            scenario=game.scenario,
            settings=settings,
            save_dir=tmp_path / "saves",
            trace_dir=tmp_path / "resume-traces",
            resume=saved,
        )
        try:
            assert all(n.harness._public_expression for n in resumed._npcs.values())
            assert resumed.manager.snapshot() == game.manager.snapshot()
        finally:
            resumed.close()
    finally:
        game.close()

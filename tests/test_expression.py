"""Disclosure boundaries are deterministic contracts, not string-based permissions."""

import json

import pytest

from ai_native_rpg.agent.expression import assemble_expression_evidence
from ai_native_rpg.agent.harness import Harness, PlanningOutput
from ai_native_rpg.agent.memory_store import MemoryStore
from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.memory_evaluation import EvidenceClient
from ai_native_rpg.scenario import load_personas, load_scenario
from ai_native_rpg.schemas.agent_trace import TraceStep
from ai_native_rpg.schemas.common import Condition
from ai_native_rpg.schemas.memory import EpisodicMemory, MemoryRetrievalResult
from ai_native_rpg.schemas.world_state import Fact
from ai_native_rpg.world.manager import WorldStateManager


def setup(llm=None, filtered=True):
    manager = WorldStateManager(load_scenario("village_disappearance"))
    store = MemoryStore("npc_a")
    harness = Harness(
        npc_state=load_personas("village_disappearance")["npc_a"],
        manager=manager,
        memory=store,
        llm=llm,
        filtered_dialogue_evidence=filtered,
    )
    return harness, manager, store


def project(manager, store, retrieval=None, steps=None, player="player_1"):
    return assemble_expression_evidence(
        manager=manager,
        memory=store,
        retrieval=retrieval or MemoryRetrievalResult(),
        steps=steps or [],
        player_id=player,
    )


def test_player_provenance_survives_save_without_laundering_npc_reply(tmp_path):
    harness, manager, store = setup()
    harness.remember_exchange("我的本子叫松风", "SECRET NPC REPLY", player_id="player_1")
    path = tmp_path / "memory.json"
    store.save(path)
    restored = MemoryStore("npc_a")
    restored.load(path)
    result = project(manager, restored, restored.retrieve("松风"))
    assert result["player_statements"][0]["text"] == "我的本子叫松风"
    assert "SECRET" not in json.dumps(result)
    assert not project(manager, restored, restored.retrieve("松风"), player="other")[
        "player_statements"
    ]


def test_legacy_and_authored_memories_remain_private(tmp_path):
    _, manager, store = setup()
    old = {
        "memory_id": "old",
        "npc_id": "npc_a",
        "event_description": "玩家说：公开秘密；我回应：SECRET",
        "importance": 1,
        "occurred_at_day": 1,
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"npc_id": "npc_a", "episodic": [old]}), encoding="utf-8")
    store.load(path)
    assert store.episodic_count == 1
    assert project(manager, store, store.retrieve("秘密"))["player_statements"] == []


def test_only_executed_tool_ids_are_resolved_from_current_owner_store():
    harness, manager, store = setup()
    harness.remember_exchange("可回述", "秘密", player_id="player_1")
    mid = store.snapshot()["episodic"][0]["memory_id"]
    step = TraceStep(
        step_name="tool_call",
        input_summary={"tool": "query_memory"},
        output_summary={
            "ok": True,
            "result": {
                "episodic_ids": [mid, "foreign"],
                "episodic": ["FORGED SECRET"],
                "disclosure": "public",
            },
        },
    )
    result = project(manager, store, steps=[step])
    assert [m["text"] for m in result["player_statements"]] == ["可回述"]
    assert "FORGED" not in json.dumps(result)
    step.output_summary["ok"] = False
    assert not project(manager, store, steps=[step])["player_statements"]
    store.forget(mid)
    step.output_summary["ok"] = True
    assert not project(manager, store, steps=[step])["player_statements"]


def test_visibility_is_live_projection_and_partial_never_sends_full_value():
    _, manager, store = setup()
    state = manager.snapshot()
    state.facts["partial"] = Fact(
        fact_id="partial", value="SECRET", visibility="partial", partial_value="一位村民"
    )
    state.facts["unlock"] = Fact(
        fact_id="unlock",
        value="earned",
        visibility="hidden",
        reveal_condition=Condition(clauses=[{"path": "time_day", "op": "gte", "value": 0}]),
    )
    manager = WorldStateManager(state)
    facts = project(manager, store)["public_facts"]
    assert facts["partial"] == "一位村民"
    assert facts["unlock"] == "earned"
    assert "loren_that_night" not in facts


@pytest.mark.parametrize(
    "action,status",
    [
        (None, "none"),
        ({"action_type": "reveal_fact", "target_id": "loren_that_night"}, "rejected"),
        ({"action_type": "adjust_relationship", "payload": {"trust": 1}}, "applied"),
    ],
)
def test_every_candidate_branch_uses_filtered_expression(action, status):
    client = EvidenceClient(
        MockLLMClient(
            [
                PlanningOutput(
                    reasoning="SECRET REASON",
                    strategy="SECRET STRATEGY",
                    dialogue="SECRET DRAFT",
                    action=action,
                ),
                {"dialogue": "安全台词"},
            ]
        )
    )
    harness, manager, store = setup(client)
    store.add_episodic(
        EpisodicMemory(
            memory_id="secret",
            npc_id="npc_a",
            event_description="SECRET MEMORY",
            importance=1,
            occurred_at_day=1,
        )
    )
    before = manager.snapshot()
    response, trace = harness.respond("你好", player_id="player_1")
    assert response.dialogue == "安全台词"
    final = json.dumps(client.calls[-1]["messages"], ensure_ascii=False)
    assert "SECRET" not in final
    assert harness._npc.persona.background not in final
    assert harness._npc.goal.primary not in final
    evidence = next(s for s in trace.steps if s.step_name == "expression_evidence")
    assert evidence.output_summary["action_result"]["status"] == status
    if status != "applied":
        assert manager.snapshot() == before
    assert store.snapshot()["episodic"][-1]["source_player_id"] == "player_1"


def test_resolved_outcome_does_not_receive_private_planning_context():
    client = EvidenceClient(MockLLMClient([{"dialogue": "门修好了"}]))
    harness, _, _ = setup(client)
    harness.respond("修门", player_id="player_1", resolved_outcome="门已修好")
    assert len(client.calls) == 1
    context = json.dumps(client.calls[0]["messages"], ensure_ascii=False)
    assert "门已修好" in context
    assert harness._npc.persona.background not in context


def test_baseline_still_uses_no_action_fast_path():
    client = EvidenceClient(
        MockLLMClient([PlanningOutput(reasoning="r", strategy="s", dialogue="原台词")])
    )
    harness, _, _ = setup(client, filtered=False)
    response, _ = harness.respond("你好", player_id="player_1")
    assert response.dialogue == "原台词"
    assert len(client.calls) == 1

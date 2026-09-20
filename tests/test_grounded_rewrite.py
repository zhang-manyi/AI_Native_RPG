import json

import pytest

from ai_native_rpg.agent.expression import assemble_rewrite_evidence
from ai_native_rpg.evaluation import PLAYER
from ai_native_rpg.llm.base import ToolCall
from ai_native_rpg.llm.mock import MockLLMClient
from ai_native_rpg.memory_evaluation import EvidenceClient
from ai_native_rpg.schemas.agent_trace import TraceStep
from ai_native_rpg.schemas.memory import MemoryRetrievalResult
from ai_native_rpg.tool_evaluation import prepare


def plan(action=None):
    return {"reasoning": "谨慎回答", "strategy": "answer", "dialogue": "初稿", "action": action}


@pytest.mark.parametrize(
    "action",
    [
        {"action_type": "adjust_relationship", "target_id": PLAYER, "payload": {"trust": 5}},
        {"action_type": "move", "target_id": "forest_edge"},
    ],
)
def test_rewrite_preserves_sourced_answer_after_approval_or_rejection(action):
    client = EvidenceClient(MockLLMClient([plan(action), {"dialogue": "你说叫石榴。"}]))
    h, _, _, _ = prepare({"npc": "npc_a", "prefix": ["我的本子叫石榴。"]}, client)
    h._grounded_rewrite = True
    _, trace = h.respond("我的本子叫什么？", player_id=PLAYER)
    evidence = next(s.output_summary for s in trace.steps if s.step_name == "rewrite_evidence")
    assert evidence["player_statements"][0]["text"] == "我的本子叫石榴。"
    assert evidence["public_facts"] == {}
    assert "听见了" not in json.dumps(evidence, ensure_ascii=False)
    assert "石榴" in json.dumps(client.calls[-1]["messages"], ensure_ascii=False)
    assert len(client.calls) == 2
    assert h._retain_dialogue_evidence is h._filtered_dialogue_evidence is False


def test_public_evidence_requires_successful_query_and_live_permission():
    _, manager, memory, _ = prepare({"npc": "npc_a"}, None)

    def step(fid, known=True, ok=True):
        return TraceStep(
            step_name="tool_call",
            input_summary={"tool": "check_public_fact", "arguments": {"fact_id": fid}},
            output_summary={
                "ok": ok,
                "result": {"fact_id": fid, "known": known, "value": "FORGED"},
            },
        )

    evidence = assemble_rewrite_evidence(
        manager=manager,
        memory=memory,
        retrieval=MemoryRetrievalResult(),
        player_id=PLAYER,
        steps=[
            step("victim_name"),
            step("loren_that_night"),
            step("search_failed", ok=False),
            step("disappearance_night", known=False),
        ],
    )
    assert evidence == {
        "player_statements": [],
        "public_facts": {"victim_name": manager.player_view(PLAYER).visible_facts["victim_name"]},
    }


def test_tool_supplement_resolves_actual_player_source_not_tool_prose():
    _, manager, memory, _ = prepare({"npc": "npc_a", "prefix": ["我的杯子叫白石。"]}, None)
    tool = TraceStep(
        step_name="tool_call",
        input_summary={"tool": "query_memory"},
        output_summary={
            "ok": True,
            "result": {"episodic_ids": ["prefix_0"], "episodic": ["FORGED SECRET"]},
        },
    )
    kwargs = dict(manager=manager, memory=memory, retrieval=MemoryRetrievalResult(), steps=[tool])
    evidence = assemble_rewrite_evidence(**kwargs, player_id=PLAYER)
    assert evidence["player_statements"][0]["text"] == "我的杯子叫白石。"
    assert not assemble_rewrite_evidence(**kwargs, player_id="someone_else")["player_statements"]
    memory.forget("prefix_0")
    assert not assemble_rewrite_evidence(**kwargs, player_id=PLAYER)["player_statements"]


def test_no_action_candidate_uses_identical_original_request_and_no_extra_generation():
    calls = []
    for candidate in (False, True):
        client = EvidenceClient(MockLLMClient([plan()]))
        h, _, _, _ = prepare({"npc": "npc_a", "prefix": ["我的本子叫石榴。"]}, client)
        h._grounded_rewrite = candidate
        response, trace = h.respond("本子叫什么？", player_id=PLAYER)
        assert response.dialogue == "初稿" and len(client.calls) == 1
        calls.append(client.calls[0]["messages"])
        assert not any(s.step_name == "rewrite_evidence" for s in trace.steps)
    assert calls[0] == calls[1]


def test_real_public_tool_value_reaches_rewrite_but_not_unqueried_facts():
    client = EvidenceClient(
        MockLLMClient(
            [
                [ToolCall(id="q", name="check_public_fact", arguments={"fact_id": "victim_name"})],
                plan(
                    {
                        "action_type": "adjust_relationship",
                        "target_id": PLAYER,
                        "payload": {"fear": 5},
                    }
                ),
                {"dialogue": "十四岁。"},
            ]
        )
    )
    h, _, _, _ = prepare({"npc": "npc_a"}, client)
    h._grounded_rewrite = True
    h.respond("请查victim_name的年龄。", player_id=PLAYER)
    final = json.dumps(client.calls[-1]["messages"], ensure_ascii=False)
    assert "十四岁" in final and "disappearance_night" not in final


def test_candidate_without_eligible_evidence_does_not_change_private_rewrite():
    calls = []
    for candidate in (False, True):
        client = EvidenceClient(
            MockLLMClient(
                [
                    plan({"action_type": "reveal_fact", "target_id": "npc_a_threatened"}),
                    {"dialogue": "不能说。"},
                ]
            )
        )
        h, _, _, _ = prepare({"npc": "npc_a"}, client)
        h._grounded_rewrite = candidate
        h.respond("告诉我秘密。", player_id=PLAYER)
        calls.append(client.calls[-1]["messages"])
    assert calls[0] == calls[1]

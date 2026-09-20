import copy
import json

import httpx
import pytest

from ai_native_rpg.evaluation import PLAYER, BudgetExceeded
from ai_native_rpg.llm.base import ToolCall
from ai_native_rpg.llm.mock import MockLLMClient
from ai_native_rpg.memory_evaluation import EvidenceClient
from ai_native_rpg.tool_evaluation import (
    DATASET,
    TaskTransport,
    execute_turn,
    prepare,
    state_consistent,
    structural_scores,
)


def case(name):
    return next(
        c for c in json.loads(DATASET.read_text(encoding="utf-8"))["cases"] if c["id"] == name
    )


def plan(action=None, dialogue="好。"):
    return {
        "reasoning": "按当前请求行动",
        "strategy": "respond",
        "dialogue": dialogue,
        "action": action,
    }


def execute(name, responses):
    c = case(name)
    client = EvidenceClient(MockLLMClient(responses=responses))
    h, manager, memory, tools = prepare(c["input"], client)
    row = execute_turn(
        h, manager, memory, tools, client, c["input"]["turns"][0], local=c["mode"] == "local"
    )
    return c, row


def test_labels_do_not_enter_model_and_unknown_fact_is_not_good_parameters():
    c, row = execute(
        "dev_public",
        [[ToolCall(id="q", name="check_public_fact", arguments={"fact_id": "invented"})], plan()],
    )
    scores = structural_scores(row, c["expect"][0])
    assert scores["tool_execution"] == (1, 1)
    assert scores["tool_parameters"] == (0, 1)
    prompt = json.dumps(row["calls"], ensure_ascii=False)
    assert "private_topics" not in prompt and '"expect"' not in prompt
    assert "艾拉，十四岁" not in prompt


def test_actual_rejection_is_not_relationship_approval():
    c, row = execute(
        "dev_illegal_move",
        [
            plan(
                {"action_type": "adjust_relationship", "target_id": PLAYER, "payload": {"fear": 5}}
            ),
            {"dialogue": "我不去。"},
        ],
    )
    scores = structural_scores(row, c["expect"][0])
    assert scores["state_consistency"] == (1, 1)
    assert scores["actual_rejection_path"] == (0, 1)
    assert scores["state_goal"] == (1, 1)


def test_real_rejected_move_keeps_state_and_is_observed():
    c, row = execute(
        "dev_illegal_move",
        [plan({"action_type": "move", "target_id": "forest_edge"}), {"dialogue": "这一步走不了。"}],
    )
    assert structural_scores(row, c["expect"][0])["actual_rejection_path"] == (1, 1)
    assert row["journal"][0]["verdict"]["approved"] is False
    assert state_consistent(row)


def test_local_controls_really_use_validator_but_do_not_generate_planning():
    _, row = execute("dev_local_private", [{"dialogue": "不能告诉你。"}])
    assert row["journal"][0]["verdict"]["approved"] is False
    assert len(row["calls"]) == 1 and row["calls"][0]["schema"] == "DialogueOutput"


def test_state_oracle_detects_approved_move_not_applied_and_rejected_write():
    _, row = execute(
        "dev_route",
        [plan({"action_type": "move", "target_id": "village_square"}), {"dialogue": "到广场了。"}],
    )
    assert state_consistent(row)
    wrong = copy.deepcopy(row)
    wrong["world_after"]["npcs"]["npc_a"]["location"] = "npc_a_house"
    assert not state_consistent(wrong)
    _, rejected = execute("dev_local_move", [{"dialogue": "走不了。"}])
    rejected["world_after"]["npcs"]["npc_a"]["location"] = "forest_edge"
    assert not state_consistent(rejected)


def test_second_move_depends_on_first_actual_state():
    c = case("dev_route")
    client = EvidenceClient(
        MockLLMClient(
            responses=[
                plan({"action_type": "move", "target_id": "village_square"}),
                {"dialogue": "到了。"},
                plan({"action_type": "move", "target_id": "forest_edge"}),
                {"dialogue": "到了。"},
            ]
        )
    )
    h, manager, memory, tools = prepare(c["input"], client)
    first = execute_turn(h, manager, memory, tools, client, c["input"]["turns"][0])
    second = execute_turn(h, manager, memory, tools, client, c["input"]["turns"][1])
    assert first["world_after"] == second["world_before"]
    assert second["journal"][0]["verdict"]["approved"] is True
    assert structural_scores(second, c["expect"][1])["state_goal"] == (1, 1)


def test_failure_after_action_preserves_partial_trace_and_changed_state():
    class BrokenDialogue(MockLLMClient):
        def complete(self, messages, *, schema, **kwargs):
            if schema.__name__ == "DialogueOutput":
                raise RuntimeError("failed expression")
            return super().complete(messages, schema=schema, **kwargs)

    c = case("dev_route")
    client = EvidenceClient(
        BrokenDialogue(responses=[plan({"action_type": "move", "target_id": "village_square"})])
    )
    h, manager, memory, tools = prepare(c["input"], client)
    row = execute_turn(h, manager, memory, tools, client, c["input"]["turns"][0])
    assert row["error_type"] == "RuntimeError" and "dialogue" not in row
    assert any(s["step_name"] == "action_validation" for s in row["steps"])
    assert state_consistent(row)
    assert structural_scores(row, c["expect"][0])["completion"] == (0, 1)


def test_fixtures_have_real_initial_miss_and_sufficient_hit():
    for split in ("dev", "holdout"):
        for kind, hit in (("recall", False), ("sufficient", True)):
            c = case(f"{split}_{kind}")
            _, _, memory, _ = prepare(c["input"], None)
            ids = [
                m.memory_id
                for m in memory.retrieve(c["input"]["turns"][0]["question"], top_k=3).episodic
            ]
            assert ("prefix_0" in ids) is hit


def test_token_reservation_and_request_limit_include_missing_usage():
    transport = TaskTransport(
        inner=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": []})),
        budget={"requests": 1, "tokens": 5000, "seconds": 60, "output_tokens": 600},
    )
    request = httpx.Request("POST", "https://example.test", json={"messages": []})
    transport.handle_request(request)
    assert transport.charged_tokens > 600
    with pytest.raises(BudgetExceeded):
        transport.handle_request(request)
    assert len(transport.records) == 1
    assert "headers" not in transport.records[0] and "url" not in transport.records[0]


def test_token_budget_blocks_before_network():
    transport = TaskTransport(
        inner=httpx.MockTransport(lambda r: pytest.fail("must not send")),
        budget={"requests": 10, "tokens": 100, "seconds": 60, "output_tokens": 600},
    )
    with pytest.raises(BudgetExceeded):
        transport.handle_request(httpx.Request("POST", "https://example.test", json={}))
    assert transport.records == []

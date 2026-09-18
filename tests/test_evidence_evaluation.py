"""Check that replay changes evidence only and cannot silently change its baseline."""

import copy

import pytest

from ai_native_rpg.agent.memory_store import MemoryStore
from ai_native_rpg.evidence_evaluation import replay_cases, run_replay
from ai_native_rpg.memory_evaluation import harness_for, write_json
from ai_native_rpg.scenario import load_scenario
from ai_native_rpg.schemas.npc_agent import AgentPlan, NPCAgentResponse
from ai_native_rpg.world.manager import WorldStateManager


def source_probe():
    harness = harness_for(
        "npc_a",
        WorldStateManager(load_scenario("village_disappearance")),
        MemoryStore("npc_a"),
        None,
    )
    plan = AgentPlan(reasoning="Answer the name", strategy="recall")
    response = NPCAgentResponse(npc_id="npc_a", plan=plan, dialogue="我记得")
    messages = harness._dialogue_messages(plan, "approved", "笔记叫什么？")
    return {
        "id": "sample",
        "repeat": 1,
        "lag": 0,
        "question": "笔记叫什么？",
        "expected_answer": "白桦随记",
        "literal_answer_hit": False,
        "response": response.model_dump(mode="json"),
        "calls": [
            {"schema": "DialogueOutput", "messages": [m.model_dump(mode="json") for m in messages]}
        ],
        "trace": {
            "steps": [
                {
                    "step_name": "memory_retrieval",
                    "output_summary": {
                        "episodic": [{"text": "玩家说他的笔记叫白桦随记"}],
                        "semantic": [],
                    },
                },
                {"step_name": "action_validation", "output_summary": {"reason": "approved"}},
            ]
        },
    }


def test_replay_preserves_original_messages_and_selects_independently_of_score():
    failed = source_probe()
    passed = copy.deepcopy(failed)
    passed.update(id="passed", literal_answer_hit=True)
    excluded = copy.deepcopy(failed)
    excluded.update(id="later", repeat=2)
    cases = replay_cases({"probes": [failed, passed, excluded]})
    answers = [c for c in cases if c["kind"] == "answer"]
    assert [c["id"] for c in answers] == ["sample", "passed"]
    for case in answers:
        assert case["baseline"] == failed["calls"][-1]["messages"]
        assert case["candidate"][0] == case["baseline"][0]
        assert case["candidate"][1]["content"].startswith(case["baseline"][1]["content"])
        assert "白桦随记" not in case["baseline"][1]["content"]
        assert "白桦随记" in case["candidate"][1]["content"]
    assert sum(c["kind"] == "privacy" for c in cases) == 2


def test_changed_baseline_is_rejected():
    probe = source_probe()
    probe["calls"][-1]["messages"][1]["content"] += "tampered"
    with pytest.raises(ValueError, match="differs"):
        replay_cases({"probes": [probe]})


def test_replay_does_not_call_real_model_when_disabled(tmp_path):
    source = tmp_path / "source.json"
    write_json(source, {"probes": [source_probe()]})
    report = run_replay(source=source, output=tmp_path / "replay")
    assert report["status"] == "unavailable"
    assert report["error_type"] == "RealBackendUnavailable"
    assert report["rows"] == []

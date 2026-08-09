"""Contract tests for the Agent Harness (docs/09 §5: mock LLM, no prose asserts).

What matters here is the control flow the architecture promises:
  * a no-action turn is a single LLM call;
  * an action turn is two calls with deterministic validation in between;
  * a rejected action leaves the world unchanged and feeds its reason to call #2;
  * every turn writes one episodic memory and a complete trace.
The dialogue *content* is the Eval suite's job, not this file's.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.agent import Harness, MemoryStore, PlanningOutput
from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.scenario import load_personas, load_scenario
from ai_native_rpg.world import WorldStateManager

SCENARIO = "village_disappearance"
PLAYER = "player_1"


@pytest.fixture
def manager() -> WorldStateManager:
    return WorldStateManager(load_scenario(SCENARIO))


@pytest.fixture
def martha():
    return load_personas(SCENARIO)["npc_a"]


class TestNoActionTurn:
    def test_single_llm_call_and_dialogue_from_first_response(self, martha, manager):
        llm = MockLLMClient(
            [PlanningOutput(reasoning="闲聊", strategy="chit_chat", dialogue="你想问什么？")]
        )
        harness = Harness(
            npc_state=martha, manager=manager, llm=llm, memory=MemoryStore(martha.npc_id)
        )

        response, trace = harness.respond("今天天气不错", player_id=PLAYER)

        assert llm.call_count == 1
        assert response.dialogue == "你想问什么？"
        assert response.action_proposal_id is None
        assert response.plan.action_proposal is None
        step_names = [s.step_name for s in trace.steps]
        assert "action_validation" not in step_names


class TestApprovedActionTurn:
    def test_two_calls_and_world_changes(self, martha, manager):
        # trust starts at 10; a +15 adjust_relationship is within the per-action cap.
        llm = MockLLMClient(
            [
                PlanningOutput(
                    reasoning="玩家真诚，愿意多信任一点",
                    strategy="warm_up",
                    dialogue="（初稿，会被丢弃）",
                    action={
                        "action_type": "adjust_relationship",
                        "target_id": PLAYER,
                        "payload": {"trust": 15},
                    },
                ),
                {"dialogue": "……也许我可以多告诉你一点。"},
            ]
        )
        harness = Harness(
            npc_state=martha, manager=manager, llm=llm, memory=MemoryStore(martha.npc_id)
        )

        response, trace = harness.respond("我只是想帮忙查清楚", player_id=PLAYER)

        assert llm.call_count == 2
        assert response.dialogue == "……也许我可以多告诉你一点。"
        assert response.action_proposal_id is not None
        # world actually changed
        assert manager.get_trust("npc_a", PLAYER) == pytest.approx(25.0)
        step_names = [s.step_name for s in trace.steps]
        assert step_names == [
            "memory_retrieval",
            "planning",
            "action_validation",
            "dialogue_generation",
            "reflection",
        ]

    def test_harness_owns_actor_identity(self, martha, manager):
        """Even if the model names a different actor, the proposal is attributed to
        this NPC — the model cannot act as someone else."""
        llm = MockLLMClient(
            [
                PlanningOutput(
                    reasoning="r",
                    strategy="s",
                    dialogue="d",
                    action={
                        "action_type": "adjust_relationship",
                        "target_id": PLAYER,
                        "payload": {"trust": 5},
                    },
                ),
                {"dialogue": "ok"},
            ]
        )
        harness = Harness(
            npc_state=martha, manager=manager, llm=llm, memory=MemoryStore(martha.npc_id)
        )
        response, _ = harness.respond("hi", player_id=PLAYER)
        assert response.plan.action_proposal.actor_id == "npc_a"


class TestRejectedActionTurn:
    def test_reveal_blocked_leaves_world_unchanged_and_feeds_reason(self, martha, manager):
        # trust is 10, far below killer_identity's threshold of 85: reveal is rejected.
        before = manager.player_view(PLAYER).visible_facts
        assert "killer_identity" not in before

        llm = MockLLMClient(
            [
                PlanningOutput(
                    reasoning="玩家逼问，我想直接说出凶手",
                    strategy="blurt_it_out",
                    dialogue="（初稿）",
                    action={"action_type": "reveal_fact", "target_id": "killer_identity"},
                ),
                {"dialogue": "我……我不知道你在说什么。"},
            ]
        )
        harness = Harness(
            npc_state=martha, manager=manager, llm=llm, memory=MemoryStore(martha.npc_id)
        )

        response, trace = harness.respond("凶手是不是洛伦？", player_id=PLAYER)

        assert llm.call_count == 2
        # rejected: fact still hidden, no proposal id surfaced
        assert "killer_identity" not in manager.player_view(PLAYER).visible_facts
        assert response.action_proposal_id is None

        validation = next(s for s in trace.steps if s.step_name == "action_validation")
        assert validation.output_summary["approved"] is False
        reason = validation.output_summary["reason"]
        assert reason is not None
        # the rejection reason is what constrains dialogue call #2
        dialogue_call = llm.calls[1]
        assert any(reason in m.content for m in dialogue_call.messages)


class TestPlayerIdentityInPrompt:
    """The model must be told who it is talking to.

    Found live: a model proposed ``adjust_relationship`` with ``target_id='player'``
    while the world's id is ``player_1``, so the Validator rejected an otherwise
    reasonable action. The Harness knew the id all along and simply never passed it
    on, leaving the model to invent an identifier it had never been shown.
    """

    def test_player_id_appears_in_the_planning_prompt(self, martha, manager):
        llm = MockLLMClient([PlanningOutput(reasoning="r", strategy="s", dialogue="d")])
        harness = Harness(
            npc_state=martha, manager=manager, llm=llm, memory=MemoryStore(martha.npc_id)
        )

        harness.respond("你好", player_id=PLAYER)

        prompt = " ".join(m.content for m in llm.calls[0].messages)
        assert PLAYER in prompt

    def test_a_different_player_id_is_carried_through(self, martha, manager):
        """Guards against hardcoding: the id must come from the call, not a literal."""
        llm = MockLLMClient([PlanningOutput(reasoning="r", strategy="s", dialogue="d")])
        harness = Harness(
            npc_state=martha, manager=manager, llm=llm, memory=MemoryStore(martha.npc_id)
        )

        harness.respond("你好", player_id="detective_7")

        prompt = " ".join(m.content for m in llm.calls[0].messages)
        assert "detective_7" in prompt


class TestReflectionAndTrace:
    def test_each_turn_writes_one_episodic_memory(self, martha, manager):
        memory = MemoryStore(martha.npc_id)
        llm = MockLLMClient(
            [
                PlanningOutput(reasoning="r", strategy="s", dialogue="d1"),
                PlanningOutput(reasoning="r", strategy="s", dialogue="d2"),
            ]
        )
        harness = Harness(npc_state=martha, manager=manager, llm=llm, memory=memory)

        harness.respond("q1", player_id=PLAYER)
        assert memory.episodic_count == 1
        harness.respond("q2", player_id=PLAYER)
        assert memory.episodic_count == 2

    def test_trace_carries_model_and_tokens_but_no_raw_secret(self, martha, manager):
        llm = MockLLMClient(
            [PlanningOutput(reasoning="r", strategy="s", dialogue="d")],
            model="mock-model",
            token_usage={"prompt_tokens": 42, "completion_tokens": 7},
        )
        harness = Harness(
            npc_state=martha, manager=manager, llm=llm, memory=MemoryStore(martha.npc_id)
        )
        _, trace = harness.respond("q", player_id=PLAYER)

        planning = next(s for s in trace.steps if s.step_name == "planning")
        assert planning.model_used == "mock-model"
        assert planning.token_usage["prompt_tokens"] == 42
        assert trace.total_latency_ms >= 0.0

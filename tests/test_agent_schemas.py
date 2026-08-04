"""Contract tests for the agent-side schemas ported into src.

These assert structure and defaults, not behaviour: the point is that the models
round-trip and that the fields the Harness depends on (notably
``AgentPlan.action_proposal``) exist with the right defaults.
"""

from __future__ import annotations

from ai_native_rpg.schemas.agent_trace import AgentTrace, TraceStep
from ai_native_rpg.schemas.memory import (
    EpisodicMemory,
    MemoryRetrievalResult,
    RelationshipMemory,
    SemanticMemory,
)
from ai_native_rpg.schemas.npc_agent import (
    AgentPlan,
    NPCAgentResponse,
    NPCGoal,
    NPCPersona,
    NPCState,
)
from ai_native_rpg.schemas.world_state import ActionProposal


class TestNPCState:
    def test_round_trips(self):
        state = NPCState(
            npc_id="npc_a",
            persona=NPCPersona(traits={"fearful": 0.8}, background="护家的母亲"),
            goal=NPCGoal(primary="隐藏秘密", secondary=["保护儿子"]),
            emotion="afraid",
        )
        restored = NPCState.model_validate(state.model_dump())
        assert restored == state
        assert restored.persona.traits["fearful"] == 0.8


class TestAgentPlan:
    def test_action_proposal_defaults_to_none(self):
        """The Harness branches on this: None => fast path, set => validate + call #2."""
        plan = AgentPlan(reasoning="闲聊，无需改变世界", strategy="chit_chat")
        assert plan.action_proposal is None

    def test_carries_an_action_proposal(self):
        proposal = ActionProposal(
            proposal_id="p1", actor_id="npc_a", action_type="reveal_fact", target_id="clue_1"
        )
        plan = AgentPlan(
            reasoning="信任够了，可以透露", strategy="reveal", action_proposal=proposal
        )
        assert plan.action_proposal is not None
        assert plan.action_proposal.target_id == "clue_1"


class TestMemorySchemas:
    def test_episodic_importance_bounds(self):
        mem = EpisodicMemory(
            memory_id="m1",
            npc_id="npc_a",
            event_description="玩家帮她儿子找回了走失的羊",
            importance=0.9,
            occurred_at_day=1,
        )
        assert 0.0 <= mem.importance <= 1.0

    def test_retrieval_result_empty_by_default(self):
        result = MemoryRetrievalResult()
        assert result.episodic == []
        assert result.semantic == []
        assert result.relationship is None

    def test_relationship_memory_is_a_projection_shape(self):
        rel = RelationshipMemory(npc_id="npc_a", target_id="player_1", trust=40.0)
        assert rel.trust == 40.0
        semantic = SemanticMemory(
            memory_id="s1", npc_id="npc_a", fact="玩家讨厌被欺骗", confidence=0.7
        )
        assert semantic.confidence == 0.7


class TestAgentTrace:
    def test_round_trips_with_steps(self):
        trace = AgentTrace(
            trace_id="t1",
            npc_id="npc_a",
            player_id="player_1",
            session_id="s1",
            steps=[TraceStep(step_name="planning", model_used="mock", latency_ms=12.0)],
            final_dialogue="我……我那晚睡得很沉。",
        )
        restored = AgentTrace.model_validate(trace.model_dump())
        assert restored == trace
        assert restored.steps[0].step_name == "planning"


class TestNPCAgentResponse:
    def test_action_proposal_id_optional(self):
        resp = NPCAgentResponse(
            npc_id="npc_a",
            plan=AgentPlan(reasoning="r", strategy="s"),
            dialogue="你问这个做什么？",
        )
        assert resp.action_proposal_id is None

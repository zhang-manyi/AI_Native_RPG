"""Agent Harness: the runtime loop that orchestrates one NPC interaction.

Sequence (docs/02_Sequence_Diagram.md, docs/06 §4): assemble context (PlayerView
+ relationship + retrieved memory + persona/goal) -> LLM call #1 (Planning +
Dialogue merged) -> branch on whether the plan proposes an action:

  * no action  -> fast path: use the dialogue from call #1 (1 LLM call total).
  * has action -> submit the proposal to the World State Manager for validation,
    then LLM call #2 to regenerate dialogue constrained by the verdict (2 calls).

A rejected proposal is not an error branch: the NPC deflects in character, and the
rejection reason becomes the constraint fed into dialogue generation
(docs/02 §4.1). Every step is recorded into an ``AgentTrace``.

The Harness reaches models only through the ``LLMClient`` Protocol and reads its
prompts from files (never inlined) so prompt selection stays in the Prompt Lab
(docs/06 §5, docs/01 §7). Tool Use (Function Calling) is slice 2 and absent here.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..llm.base import LLMClient, Message
from ..schemas.agent_trace import AgentTrace, TraceStep
from ..schemas.memory import EpisodicMemory, MemoryRetrievalResult
from ..schemas.npc_agent import AgentPlan, NPCAgentResponse, NPCState
from ..schemas.world_state import ActionProposal
from ..world.manager import WorldStateManager
from .memory_store import MemoryStore

_PROMPTS_ROOT = Path(__file__).resolve().parents[3] / "prompts"

# Reflection writes a single low-importance episodic memory per turn (docs/06
# §implementation: "async, low-frequency, one episodic memory per turn").
_REFLECTION_IMPORTANCE = 0.4


class ProposedAction(BaseModel):
    """The action intent the model may emit. The Harness — not the model — sets
    ``actor_id`` and ``proposal_id`` when wrapping this into an ``ActionProposal``,
    so the LLM cannot act as another NPC or forge a proposal identity."""

    action_type: str
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class PlanningOutput(BaseModel):
    """Structured output of LLM call #1 (Planning + Dialogue merged)."""

    reasoning: str
    strategy: str
    dialogue: str = Field(description="first-pass line, used directly on the no-action fast path")
    action: ProposedAction | None = None


class DialogueOutput(BaseModel):
    """Structured output of LLM call #2 (dialogue regenerated under the verdict)."""

    dialogue: str


class PromptLibrary:
    """Loads prompt templates from files. The Harness never inlines prompt text."""

    def __init__(self, root: str | Path = _PROMPTS_ROOT) -> None:
        self._root = Path(root)

    def load(self, name: str) -> str:
        return (self._root / name).read_text(encoding="utf-8")


class Harness:
    """Runs one NPC's decision loop. Dependencies are injected so tests can supply
    a MockLLMClient and an in-memory world."""

    def __init__(
        self,
        *,
        npc_state: NPCState,
        manager: WorldStateManager,
        llm: LLMClient,
        memory: MemoryStore,
        prompts: PromptLibrary | None = None,
        top_k: int = 3,
    ) -> None:
        self._npc = npc_state
        self._manager = manager
        self._llm = llm
        self._memory = memory
        self._prompts = prompts or PromptLibrary()
        self._top_k = top_k

    def respond(
        self, observation: str, *, player_id: str, session_id: str | None = None
    ) -> tuple[NPCAgentResponse, AgentTrace]:
        """Handle one player utterance. Returns the response and its trace.

        Persisting the trace is the caller's job (fire-and-forget, off the player's
        critical path per docs/07 §2.1); the Harness only builds it.
        """
        npc_id = self._npc.npc_id
        session_id = session_id or uuid.uuid4().hex
        steps: list[TraceStep] = []
        turn_start = time.perf_counter()

        retrieval = self._retrieve(observation, npc_id, player_id, steps)

        planning = self._plan(observation, retrieval, steps)

        if planning.action is None:
            plan = AgentPlan(reasoning=planning.reasoning, strategy=planning.strategy)
            dialogue = planning.dialogue
            action_proposal_id = None
        else:
            plan, dialogue, action_proposal_id = self._act_and_regenerate(planning, npc_id, steps)

        self._reflect(observation, dialogue, npc_id, steps)

        total_ms = (time.perf_counter() - turn_start) * 1000.0
        response = NPCAgentResponse(
            npc_id=npc_id, plan=plan, dialogue=dialogue, action_proposal_id=action_proposal_id
        )
        trace = AgentTrace(
            trace_id=uuid.uuid4().hex,
            npc_id=npc_id,
            player_id=player_id,
            session_id=session_id,
            steps=steps,
            total_latency_ms=total_ms,
            final_dialogue=dialogue,
        )
        return response, trace

    # --- steps -------------------------------------------------------------

    def _retrieve(
        self, observation: str, npc_id: str, player_id: str, steps: list[TraceStep]
    ) -> MemoryRetrievalResult:
        start = time.perf_counter()
        relationship = self._manager.get_relationship(npc_id, player_id)
        retrieval = self._memory.retrieve(
            observation, top_k=self._top_k, relationship=relationship, target_id=player_id
        )
        steps.append(
            TraceStep(
                step_name="memory_retrieval",
                input_summary={"observation": observation, "top_k": self._top_k},
                output_summary={
                    "episodic_hits": len(retrieval.episodic),
                    "semantic_hits": len(retrieval.semantic),
                    "trust": relationship.trust,
                },
                latency_ms=(time.perf_counter() - start) * 1000.0,
            )
        )
        return retrieval

    def _plan(
        self, observation: str, retrieval: MemoryRetrievalResult, steps: list[TraceStep]
    ) -> PlanningOutput:
        start = time.perf_counter()
        messages = self._planning_messages(observation, retrieval)
        resp = self._llm.complete(messages, schema=PlanningOutput)
        planning: PlanningOutput = resp.parsed  # type: ignore[assignment]
        steps.append(
            TraceStep(
                step_name="planning",
                input_summary={"observation": observation},
                output_summary={
                    "strategy": planning.strategy,
                    "proposes_action": planning.action is not None,
                },
                latency_ms=(time.perf_counter() - start) * 1000.0,
                model_used=resp.model,
                token_usage=resp.token_usage,
            )
        )
        return planning

    def _act_and_regenerate(
        self, planning: PlanningOutput, npc_id: str, steps: list[TraceStep]
    ) -> tuple[AgentPlan, str, str | None]:
        assert planning.action is not None
        proposal = ActionProposal(
            proposal_id=uuid.uuid4().hex,
            actor_id=npc_id,  # the Harness owns identity; the model cannot spoof it
            action_type=planning.action.action_type,
            target_id=planning.action.target_id,
            payload=dict(planning.action.payload),
        )
        plan = AgentPlan(
            reasoning=planning.reasoning, strategy=planning.strategy, action_proposal=proposal
        )

        start = time.perf_counter()
        result = self._manager.submit(proposal)
        steps.append(
            TraceStep(
                step_name="action_validation",
                input_summary={
                    "action_type": proposal.action_type,
                    "target_id": proposal.target_id,
                },
                output_summary={
                    "approved": result.approved,
                    "reason": result.reason,
                    "rule_name": result.rule_name,
                },
                latency_ms=(time.perf_counter() - start) * 1000.0,
            )
        )

        # Whether approved or rejected, dialogue is regenerated under the verdict:
        # an approved reveal must now voice the fact, a rejected one must deflect
        # without leaking it. docs/02 §4.1.
        start = time.perf_counter()
        messages = self._dialogue_messages(plan, result.reason)
        resp = self._llm.complete(messages, schema=DialogueOutput)
        dialogue_out: DialogueOutput = resp.parsed  # type: ignore[assignment]
        steps.append(
            TraceStep(
                step_name="dialogue_generation",
                input_summary={"validation_approved": result.approved},
                output_summary={"regenerated": True},
                latency_ms=(time.perf_counter() - start) * 1000.0,
                model_used=resp.model,
                token_usage=resp.token_usage,
            )
        )
        # The proposal id is surfaced only if it actually changed the world, so a
        # rejected action does not masquerade as an applied one downstream.
        proposal_id = proposal.proposal_id if result.approved else None
        return plan, dialogue_out.dialogue, proposal_id

    def _reflect(
        self, observation: str, dialogue: str, npc_id: str, steps: list[TraceStep]
    ) -> None:
        start = time.perf_counter()
        day = self._manager.snapshot().time_day
        self._memory.add_episodic(
            EpisodicMemory(
                memory_id=uuid.uuid4().hex,
                npc_id=npc_id,
                event_description=f"玩家说：{observation}；我回应：{dialogue}",
                importance=_REFLECTION_IMPORTANCE,
                emotion=self._npc.emotion,
                occurred_at_day=day,
            )
        )
        steps.append(
            TraceStep(
                step_name="reflection",
                output_summary={"episodic_written": 1},
                latency_ms=(time.perf_counter() - start) * 1000.0,
            )
        )

    # --- prompt assembly ---------------------------------------------------

    def _planning_messages(
        self, observation: str, retrieval: MemoryRetrievalResult
    ) -> list[Message]:
        system = self._prompts.load("npc_planning.txt")
        context = self._context_block(retrieval)
        return [
            Message(role="system", content=f"{system}\n\n{self._persona_block()}"),
            Message(role="user", content=f"{context}\n\n玩家说：{observation}"),
        ]

    def _dialogue_messages(self, plan: AgentPlan, validation_reason: str | None) -> list[Message]:
        system = self._prompts.load("npc_dialogue.txt")
        verdict = validation_reason or "（无被拒约束）"
        return [
            Message(role="system", content=f"{system}\n\n{self._persona_block()}"),
            Message(
                role="user",
                content=(
                    f"你的计划：{plan.reasoning}（策略：{plan.strategy}）\n校验结果：{verdict}"
                ),
            ),
        ]

    def _persona_block(self) -> str:
        persona = self._npc.persona
        traits = ", ".join(f"{k}={v}" for k, v in persona.traits.items())
        return (
            f"角色：{self._npc.npc_id}\n"
            f"背景：{persona.background}\n"
            f"特质：{traits}\n"
            f"当前情绪：{self._npc.emotion}\n"
            f"目标：{self._npc.goal.primary}"
        )

    def _context_block(self, retrieval: MemoryRetrievalResult) -> str:
        lines = ["可见信息与记忆："]
        if retrieval.relationship is not None:
            rel = retrieval.relationship
            lines.append(f"- 对玩家：trust={rel.trust}, fear={rel.fear}, respect={rel.respect}")
        for mem in retrieval.episodic:
            lines.append(f"- 回忆：{mem.event_description}")
        for mem in retrieval.semantic:
            lines.append(f"- 信念：{mem.fact}")
        if len(lines) == 1:
            lines.append("- （暂无相关记忆）")
        return "\n".join(lines)

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
(docs/06 §5, docs/01 §7).

LLM call #1 is wrapped in a bounded tool-use loop: the model may request tools
(relationship values, its own memories, public facts) before committing to a plan,
and each result is fed back for the next call. The bound matters because a player
is waiting — on the final iteration tools are withheld, forcing an answer rather
than another request.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..llm.base import LLMClient, Message, ToolCall
from ..schemas.agent_trace import AgentTrace, TraceStep
from ..schemas.memory import EpisodicMemory, MemoryRetrievalResult
from ..schemas.npc_agent import AgentPlan, NPCAgentResponse, NPCState
from ..schemas.world_state import ActionProposal
from ..world.manager import WorldStateManager
from .memory_store import MemoryStore
from .tools import ToolError, ToolRegistry

_PROMPTS_ROOT = Path(__file__).resolve().parents[3] / "prompts"

# Reflection writes a single low-importance episodic memory per turn (docs/06
# §implementation: "async, low-frequency, one episodic memory per turn").
_REFLECTION_IMPORTANCE = 0.4

#: Character cap on each half of a reflection memory. Player input is the only
#: unbounded text in the loop, and an embedder truncates at its context window
#: without saying so — a pasted wall of text would become a memory whose vector
#: describes just its opening while the prompt shows the whole thing, so it would be
#: retrieved for the wrong queries. Capping keeps the stored text and its vector
#: describing the same content. Both halves are capped, since what the NPC said is
#: what it must stay consistent with next turn.
_REFLECTION_HALF_LIMIT = 160


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
    """Loads prompt templates from files. The Harness never inlines prompt text.

    An optional ``overlay`` directory (a scenario pack's own ``prompts/``) is
    consulted first, so a story can restyle its NPCs' planning/dialogue voice
    without touching the shared templates. A template missing from the overlay
    falls back to ``root``; this way a pack overrides only what it cares to,
    rather than having to copy every template to change one.
    """

    def __init__(
        self, root: str | Path = _PROMPTS_ROOT, overlay: str | Path | None = None
    ) -> None:
        self._root = Path(root)
        self._overlay = Path(overlay) if overlay is not None else None

    def load(self, name: str) -> str:
        if self._overlay is not None:
            override = self._overlay / name
            if override.is_file():
                return override.read_text(encoding="utf-8")
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
        tools: ToolRegistry | None = None,
        max_tool_iterations: int = 3,
    ) -> None:
        self._npc = npc_state
        self._manager = manager
        self._llm = llm
        self._memory = memory
        self._prompts = prompts or PromptLibrary()
        self._top_k = top_k
        self._tools = tools
        self._max_tool_iterations = max_tool_iterations

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

        planning = self._plan(observation, retrieval, steps, player_id)

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
                    # Full recall, in rank order, so a trace shows *which* memories
                    # were retrieved and how they sorted -- not just how many.
                    "episodic": [
                        {
                            "id": m.memory_id,
                            "importance": m.importance,
                            "text": m.event_description,
                        }
                        for m in retrieval.episodic
                    ],
                    "semantic": [
                        {"id": m.memory_id, "confidence": m.confidence, "text": m.fact}
                        for m in retrieval.semantic
                    ],
                    "trust": relationship.trust,
                },
                latency_ms=(time.perf_counter() - start) * 1000.0,
            )
        )
        return retrieval

    def _plan(
        self,
        observation: str,
        retrieval: MemoryRetrievalResult,
        steps: list[TraceStep],
        player_id: str,
    ) -> PlanningOutput:
        """LLM call #1, wrapped in the tool-use loop.

        The model may ask for tools before committing to a plan; each result is fed
        back and the call repeats. The loop is bounded because a player is waiting:
        on the last iteration tools are withheld, which forces an answer instead of
        another request (docs/06 §4).
        """
        messages = self._planning_messages(observation, retrieval, player_id)
        tool_specs = self._tools.specs() if self._tools is not None else None

        for iteration in range(self._max_tool_iterations + 1):
            # Withhold tools on the final iteration so the model must answer.
            offer_tools = tool_specs if iteration < self._max_tool_iterations else None
            start = time.perf_counter()
            resp = self._llm.complete(messages, schema=PlanningOutput, tools=offer_tools)
            latency_ms = (time.perf_counter() - start) * 1000.0

            if resp.wants_tools and offer_tools is not None:
                # Echo the assistant's request before the results: providers reject
                # a tool message that is not paired with the call that asked for it.
                messages.append(
                    Message(role="assistant", content="", tool_calls=list(resp.tool_calls))
                )
                for call in resp.tool_calls:
                    messages.append(self._run_tool(call, steps))
                continue

            planning: PlanningOutput = resp.parsed  # type: ignore[assignment]
            steps.append(
                TraceStep(
                    step_name="planning",
                    input_summary={"observation": observation, "tool_iterations": iteration},
                    output_summary={
                        "strategy": planning.strategy,
                        "proposes_action": planning.action is not None,
                    },
                    latency_ms=latency_ms,
                    model_used=resp.model,
                    token_usage=resp.token_usage,
                )
            )
            return planning

        # Unreachable: the final iteration withholds tools, so it must parse.
        raise RuntimeError("planning loop ended without a plan")

    def _run_tool(self, call: ToolCall, steps: list[TraceStep]) -> Message:
        """Execute one tool call and render the result as a ``tool`` message.

        A failed call is *reported*, not raised: an unknown name or bad arguments
        is the model drifting, and handing the error back costs one iteration
        instead of failing the player's turn. The trace records success either way,
        which is what docs/08's Tool Use Success Rate counts.
        """
        assert self._tools is not None
        start = time.perf_counter()
        try:
            result = self._tools.call(call.name, call.arguments)
        except ToolError as exc:
            content, summary = str(exc), {"ok": False, "error": str(exc)}
        else:
            content = json.dumps(result, ensure_ascii=False)
            summary = {"ok": True, "result_keys": sorted(result)}

        steps.append(
            TraceStep(
                step_name="tool_call",
                input_summary={"tool": call.name, "arguments": call.arguments},
                output_summary=summary,
                latency_ms=(time.perf_counter() - start) * 1000.0,
            )
        )
        return Message(role="tool", content=content, tool_call_id=call.id)

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
                event_description=self._reflection_text(observation, dialogue),
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

    @staticmethod
    def _reflection_text(observation: str, dialogue: str) -> str:
        """Build the reflection memory, capping each half independently.

        Capping the halves separately rather than the joined string means a long
        player turn cannot push the NPC's own line out of its memory — that line is
        what the NPC has to stay consistent with next turn.
        """

        def clip(text: str) -> str:
            text = text.strip()
            if len(text) <= _REFLECTION_HALF_LIMIT:
                return text
            return text[:_REFLECTION_HALF_LIMIT] + "…"

        return f"玩家说：{clip(observation)}；我回应：{clip(dialogue)}"

    # --- prompt assembly ---------------------------------------------------

    def _planning_messages(
        self, observation: str, retrieval: MemoryRetrievalResult, player_id: str
    ) -> list[Message]:
        system = self._prompts.load("npc_planning.txt")
        context = self._context_block(retrieval, player_id)
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

    def _context_block(self, retrieval: MemoryRetrievalResult, player_id: str) -> str:
        # Name the interlocutor explicitly. Actions reference characters by id, and
        # a model that was never shown the id will invent one ("player" instead of
        # "player_1"), which the Validator then rejects for a reason that has
        # nothing to do with the NPC's intent.
        lines = [f"你正在和 {player_id} 说话（行动里要用这个 id 指代他）。", "", "可见信息与记忆："]
        if retrieval.relationship is not None:
            rel = retrieval.relationship
            lines.append(
                f"- 对 {player_id}：trust={rel.trust}, fear={rel.fear}, respect={rel.respect}"
            )
        for mem in retrieval.episodic:
            lines.append(f"- 回忆：{mem.event_description}")
        for mem in retrieval.semantic:
            lines.append(f"- 信念：{mem.fact}")
        if len(lines) == 3:
            lines.append("- （暂无相关记忆）")
        return "\n".join(lines)

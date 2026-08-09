"""NPC Agent state and I/O schema. See docs/06_NPC_Agent_Spec.md for rationale.

Ported from the design-time draft in docs/schemas/npc_agent.py with three
changes: ``datetime.utcnow`` -> ``common.utc_now`` (deprecated in 3.12);
``AgentPlan`` gains an ``action_proposal`` field (the signal the Harness branches
on to decide 1-vs-2 LLM calls, see docs/02_Sequence_Diagram.md#42); and the
``reveal_timing`` mention in ``DialogueGenerationInput`` is dropped — the
Narrative Engine now emits a structured ``payoff_condition`` instead of a free
string (docs/05_Narrative_Engine.md#23).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .common import utc_now
from .world_state import ActionProposal


class NPCPersona(BaseModel):
    """The relatively fixed character personality, not changed by a single turn."""

    traits: dict[str, float] = Field(
        default_factory=dict, description="e.g. {'honest': 0.8, 'ambitious': 0.6}"
    )
    background: str = ""


class NPCGoal(BaseModel):
    primary: str
    secondary: list[str] = Field(default_factory=list)


class NPCState(BaseModel):
    """The NPC's *internal* agent state — "what this NPC thinks".

    Distinct from ``world_state.NPCWorldState`` ("where this NPC is / whether it
    is alive"). Persona, goal, emotion and beliefs live here and are loaded from
    the scenario pack's ``npc_personas`` section, not from ``WorldState``.
    """

    npc_id: str
    persona: NPCPersona
    goal: NPCGoal
    emotion: str = "neutral"
    beliefs: dict[str, Any] = Field(
        default_factory=dict, description="e.g. {'player_is_suspicious': True}"
    )


class ToolCall(BaseModel):
    """A Function Calling request recorded on a plan, with its result.

    This is the *record* kept for inspection; the live wire shape the Harness
    executes against is ``llm.base.ToolCall``. Traces are the primary place tool
    activity is read from (``TraceStep`` with ``step_name="tool_call"``).
    """

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None


class AgentPlan(BaseModel):
    """Planning-stage output. Usually merged with dialogue into one structured
    LLM response (docs/02_Sequence_Diagram.md#42)."""

    reasoning: str = Field(description="internal reasoning, e.g. 'a direct answer breaks trust'")
    strategy: str = Field(description="e.g. 'avoid_direct_answer'")
    tool_calls: list[ToolCall] = Field(default_factory=list)
    action_proposal: ActionProposal | None = Field(
        default=None,
        description="set when this turn wants to change the world. When present, the Harness "
        "validates it and re-generates dialogue against the verdict (LLM call #2); when None, "
        "the merged dialogue is used directly. See docs/02_Sequence_Diagram.md#42.",
    )


class DialogueGenerationInput(BaseModel):
    """Input to Dialogue Generation: turn structured state/events into in-character
    lines. See docs/06_NPC_Agent_Spec.md#3-dialogue-generation."""

    persona: NPCPersona
    emotion: str
    plan: AgentPlan
    narrative_event: dict[str, Any] | None = Field(
        default=None,
        description="structured content from the Narrative Engine if an event fired this turn, "
        "e.g. {'who_betrays': 'npc_b', 'how': '...', 'payoff_condition': {...}}. "
        "Corresponds to narrative_event.NarrativeEvent.generated_content.",
    )
    validation_reason: str | None = Field(
        default=None,
        description="the Validator's verdict on plan.action_proposal, fed back so the NPC "
        "deflects in character when an action was rejected instead of leaking the fact.",
    )


class NPCAgentResponse(BaseModel):
    """The full output of one interaction, written to AgentTrace and returned to
    the client."""

    npc_id: str
    plan: AgentPlan
    dialogue: str = Field(description="the final output of the Dialogue Generation stage")
    action_proposal_id: str | None = Field(
        default=None,
        description="the ActionProposal id if this response changed world state, else None",
    )
    timestamp: datetime = Field(default_factory=utc_now)

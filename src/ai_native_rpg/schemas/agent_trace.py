"""Agent Trace schema for the Developer Platform. See docs/07_Observability.md.

Ported from docs/schemas/agent_trace.py; ``datetime.utcnow`` -> ``common.utc_now``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .common import utc_now


class TraceStep(BaseModel):
    """One step on the decision chain."""

    step_name: str = Field(description="e.g. 'memory_retrieval', 'planning', 'tool_call'")
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    model_used: str | None = Field(default=None, description="None for deterministic steps")
    token_usage: dict[str, int] | None = None


class AgentTrace(BaseModel):
    """A complete record of one NPC Agent interaction.

    Never contains API keys or raw prompt text carrying secrets — only model name
    and token counts (docs/06_NPC_Agent_Spec.md#5).
    """

    trace_id: str
    npc_id: str
    player_id: str
    session_id: str

    steps: list[TraceStep] = Field(default_factory=list)

    total_latency_ms: float = 0.0
    final_dialogue: str = ""

    eval_flags: dict[str, Any] = Field(
        default_factory=dict,
        description="e.g. {'persona_consistency_score': 0.6, 'flagged_low_quality': True}",
    )

    created_at: datetime = Field(default_factory=utc_now)

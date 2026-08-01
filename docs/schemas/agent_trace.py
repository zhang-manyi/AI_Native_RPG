"""Agent Trace schema for the Developer Platform. See ../07_Observability.md."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TraceStep(BaseModel):
    """决策链上的一步。"""

    step_name: str = Field(description="e.g. 'memory_retrieval', 'planning', 'tool_call'")
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    model_used: str | None = Field(default=None, description="None for deterministic steps")
    token_usage: dict[str, int] | None = None


class AgentTrace(BaseModel):
    """一次完整的 NPC Agent 交互记录。"""

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

    created_at: datetime = Field(default_factory=datetime.utcnow)

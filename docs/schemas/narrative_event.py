"""Narrative Event schema. See ../05_Narrative_Engine.md for design rationale."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EventCandidate(BaseModel):
    """Narrative Engine 规则触发产生的候选事件，尚未生成具体内容。"""

    event_type: str
    intensity: float = Field(ge=0.0, le=1.0)
    preference_tag: str = Field(
        description="对应 PlayerProfile.play_style 里的 key，供 Experience Controller 打分"
    )
    trigger_reason: str = Field(description="e.g. 'faction.royal_family.stability < 0.3'")


class NarrativeEvent(BaseModel):
    """LLM 生成内容之后的完整事件，写回 World State 前的中间产物。"""

    event_id: str
    event_type: str
    participants: list[str] = Field(default_factory=list, description="NPC/faction ids")
    generated_content: dict[str, Any] = Field(
        default_factory=dict,
        description="LLM 输出，如 {'who_betrays': 'NPC_B', 'how': '...', 'dialogue_hook': '...'}",
    )
    reveal_timing: str = Field(default="immediate")
    resolution_options: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

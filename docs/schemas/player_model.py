"""Player Model schema. Schema-as-code: this file is the source of truth,
not the markdown doc. See ../03_Player_Model.md for design rationale."""

from datetime import datetime

from pydantic import BaseModel, Field


class PlayerRawStats(BaseModel):
    """实时统计层。每个玩家行为事件更新，纯代码计算，不涉及 LLM。"""

    player_id: str

    total_play_time_seconds: float = 0.0
    time_spent_exploring_seconds: float = 0.0

    combat_encounters_initiated: int = 0
    combat_encounters_avoided: int = 0
    total_encounters: int = 0

    risky_choices_taken: int = 0
    risky_choices_offered: int = 0

    dialogue_choices_made: int = 0
    social_npc_interactions: int = 0

    last_updated: datetime = Field(default_factory=datetime.utcnow)

    @property
    def exploration_score(self) -> float:
        if self.total_play_time_seconds == 0:
            return 0.0
        return self.time_spent_exploring_seconds / self.total_play_time_seconds

    @property
    def combat_score(self) -> float:
        if self.total_encounters == 0:
            return 0.0
        return self.combat_encounters_initiated / self.total_encounters

    @property
    def risk_preference(self) -> float:
        if self.risky_choices_offered == 0:
            return 0.0
        return self.risky_choices_taken / self.risky_choices_offered


class PlayerProfile(BaseModel):
    """周期性摘要层。会话结束/章节结束时批量更新，narrative_preference
    由 Profile Summarizer(LLM 或规则模板)生成，供 Narrative Engine 和
    NPC Agent 的 prompt 消费。"""

    player_id: str

    play_style: dict[str, float] = Field(
        default_factory=dict,
        description="e.g. {'exploration': 0.8, 'combat': 0.2, 'social': 0.9}",
    )

    narrative_preference: str = Field(
        default="",
        description="自然语言摘要，如 '偏好探索、回避冲突、重视人际关系的玩家'",
    )

    risk_tolerance: float = 0.0

    last_summarized_at: datetime | None = None
    summarized_from_session: str | None = None

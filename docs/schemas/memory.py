"""NPC Memory schema. Three-tier memory, not chat history.
See ../06_NPC_Agent_Spec.md for design rationale."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class WorkingMemoryItem(BaseModel):
    """当前对话上下文，短期，不持久化到长期存储。"""

    role: Literal["player", "npc"]
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class EpisodicMemory(BaseModel):
    """具体发生过的事件。importance 用于检索排序和遗忘衰减。"""

    memory_id: str
    npc_id: str
    event_description: str
    importance: float = Field(ge=0.0, le=1.0)
    emotion: str | None = Field(default=None, description="e.g. 'gratitude', 'fear'")
    embedding: list[float] | None = Field(default=None, description="用于相似度检索")
    occurred_at_day: int
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SemanticMemory(BaseModel):
    """抽象事实/信念，非具体事件。"""

    memory_id: str
    npc_id: str
    fact: str = Field(description="e.g. 'player dislikes betrayal'")
    confidence: float = Field(ge=0.0, le=1.0)
    embedding: list[float] | None = None


class RelationshipMemory(BaseModel):
    """对某个特定角色（通常是玩家）的量化关系。"""

    npc_id: str
    target_id: str
    trust: float = Field(default=0.0, ge=-100, le=100)
    fear: float = Field(default=0.0, ge=-100, le=100)
    respect: float = Field(default=0.0, ge=-100, le=100)
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class MemoryRetrievalResult(BaseModel):
    """一次检索返回的记忆集合，供 Planning 阶段的 prompt 组装。"""

    episodic: list[EpisodicMemory] = Field(default_factory=list)
    semantic: list[SemanticMemory] = Field(default_factory=list)
    relationship: RelationshipMemory | None = None

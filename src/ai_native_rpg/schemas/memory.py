"""NPC Memory schema. Three-tier memory, not chat history.

See docs/06_NPC_Agent_Spec.md#Memory for design rationale. Ported from the
design-time draft in docs/schemas/memory.py; ``datetime.utcnow`` (deprecated in
3.12) is replaced by ``common.utc_now`` so every timestamp in the system is
timezone-aware and comparable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .common import utc_now


class WorkingMemoryItem(BaseModel):
    """Current conversation context. Short-term, not persisted to long-term store."""

    role: Literal["player", "npc"]
    content: str
    timestamp: datetime = Field(default_factory=utc_now)


class EpisodicMemory(BaseModel):
    """A concrete event that happened. ``importance`` drives retrieval ranking
    and forgetting decay. Agent-private storage."""

    memory_id: str
    npc_id: str
    event_description: str
    importance: float = Field(ge=0.0, le=1.0)
    emotion: str | None = Field(default=None, description="e.g. 'gratitude', 'fear'")
    embedding: list[float] | None = Field(default=None, description="for similarity retrieval")
    occurred_at_day: int
    created_at: datetime = Field(default_factory=utc_now)


class SemanticMemory(BaseModel):
    """An abstract fact or belief, not a concrete event. Agent-private storage."""

    memory_id: str
    npc_id: str
    fact: str = Field(description="e.g. 'player dislikes betrayal'")
    confidence: float = Field(ge=0.0, le=1.0)
    embedding: list[float] | None = None


class RelationshipMemory(BaseModel):
    """Quantified relationship toward a specific character (usually the player).

    A *read-only projection* of ``WorldState.relationships``, not private storage:
    the source of truth is the world, so the Validator and the Agent can never
    disagree about it. Writes must go through an Action Proposal. See
    docs/06_NPC_Agent_Spec.md#Memory and docs/04_World_State_Manager.md#21.
    """

    npc_id: str
    target_id: str
    trust: float = Field(default=0.0, ge=-100, le=100)
    fear: float = Field(default=0.0, ge=-100, le=100)
    respect: float = Field(default=0.0, ge=-100, le=100)
    last_updated: datetime = Field(default_factory=utc_now)


class MemoryRetrievalResult(BaseModel):
    """One retrieval's results, assembled into the Planning/Dialogue prompt.

    ``relationship`` is filled from the World State Manager at retrieval time, not
    stored alongside episodic/semantic memory (see ``RelationshipMemory``).
    """

    episodic: list[EpisodicMemory] = Field(default_factory=list)
    semantic: list[SemanticMemory] = Field(default_factory=list)
    relationship: RelationshipMemory | None = None

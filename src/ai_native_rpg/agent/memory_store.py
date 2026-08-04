"""MemoryStore: per-NPC episodic + semantic memory with RAG-style retrieval.

Implements the retrieval described in docs/06_NPC_Agent_Spec.md#Memory: encode the
observation, cosine-scan this NPC's memories, take Top-K and re-rank by
``importance``, then hand the result to prompt assembly. No vector database — an
in-memory linear scan is right at the scale of "one NPC's memories" (docs/06),
and the ``Embedder`` seam is where slice 2 swaps in a real model.

``RelationshipMemory`` is deliberately absent from storage: it is a read-only
projection of ``WorldState.relationships`` fetched from the World State Manager at
retrieval time, so the world stays the single source of truth for relationship
values (docs/06 §Memory, docs/04 §2.1). The store never holds relationship data.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from ..schemas.memory import (
    EpisodicMemory,
    MemoryRetrievalResult,
    RelationshipMemory,
    SemanticMemory,
)
from ..schemas.world_state import RelationshipState
from .embedding import Embedder, HashingEmbedder, cosine_similarity

# Retrieval score = similarity * (1 + IMPORTANCE_WEIGHT * importance). importance
# breaks ties and lifts a slightly-less-similar but highly-salient memory above a
# marginally-more-similar trivial one, without letting importance alone dominate a
# genuinely irrelevant match. See docs/06 §Memory ("weighted by importance").
IMPORTANCE_WEIGHT = 0.5

_M = TypeVar("_M", EpisodicMemory, SemanticMemory)


class MemoryStore:
    """Owns one NPC's private memory. Relationship values are not stored here."""

    def __init__(self, npc_id: str, embedder: Embedder | None = None) -> None:
        self.npc_id = npc_id
        self._embedder = embedder or HashingEmbedder()
        self._episodic: list[EpisodicMemory] = []
        self._semantic: list[SemanticMemory] = []

    # --- writes ------------------------------------------------------------

    def add_episodic(self, memory: EpisodicMemory) -> None:
        self._require_own(memory.npc_id)
        if memory.embedding is None:
            memory = memory.model_copy(
                update={"embedding": self._embedder.encode(memory.event_description)}
            )
        self._episodic.append(memory)

    def add_semantic(self, memory: SemanticMemory) -> None:
        self._require_own(memory.npc_id)
        if memory.embedding is None:
            memory = memory.model_copy(update={"embedding": self._embedder.encode(memory.fact)})
        self._semantic.append(memory)

    def _require_own(self, npc_id: str) -> None:
        if npc_id != self.npc_id:
            raise ValueError(
                f"MemoryStore for {self.npc_id!r} refuses a memory belonging to {npc_id!r}: "
                "each NPC's memory is private"
            )

    # --- reads -------------------------------------------------------------

    @property
    def episodic_count(self) -> int:
        return len(self._episodic)

    @property
    def semantic_count(self) -> int:
        return len(self._semantic)

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 3,
        relationship: RelationshipState | None = None,
        target_id: str | None = None,
    ) -> MemoryRetrievalResult:
        """Top-K episodic + semantic memories for ``query``, importance-reranked.

        ``relationship`` is the value read from the World State Manager for the
        (npc, target) pair; if given, it is projected into a ``RelationshipMemory``
        for the prompt. The store neither owns nor persists it.
        """
        query_vec = self._embedder.encode(query)

        episodic = self._top_k(self._episodic, query_vec, top_k, importance=lambda m: m.importance)
        semantic = self._top_k(self._semantic, query_vec, top_k, importance=lambda m: m.confidence)

        rel_memory = None
        if relationship is not None and target_id is not None:
            rel_memory = RelationshipMemory(
                npc_id=self.npc_id,
                target_id=target_id,
                trust=relationship.trust,
                fear=relationship.fear,
                respect=relationship.respect,
                last_updated=relationship.last_updated,
            )

        return MemoryRetrievalResult(episodic=episodic, semantic=semantic, relationship=rel_memory)

    def _top_k(
        self,
        memories: Sequence[_M],
        query_vec: list[float],
        top_k: int,
        *,
        importance: Callable[[_M], float],
    ) -> list[_M]:
        if top_k <= 0 or not memories:
            return []
        scored = [
            (self._score(m.embedding, query_vec, importance(m)), idx, m)
            for idx, m in enumerate(memories)
        ]
        # Sort by score desc; original index asc as a stable, deterministic
        # tiebreaker so identical-score memories keep insertion order.
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [m for _, _, m in scored[:top_k]]

    @staticmethod
    def _score(embedding: list[float] | None, query_vec: list[float], importance: float) -> float:
        similarity = cosine_similarity(embedding, query_vec) if embedding is not None else 0.0
        return similarity * (1.0 + IMPORTANCE_WEIGHT * importance)

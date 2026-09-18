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

import json
from collections.abc import Callable, Sequence
from pathlib import Path
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

    def rebind_embedder(self, embedder: Embedder) -> int:
        """Swap the embedder, re-encoding any memory whose vector no longer fits.

        Changing embedder (or MRL width) invalidates stored vectors: comparing a
        64-dim saved vector against a 256-dim query raises a length mismatch deep
        inside ``cosine_similarity``. Re-encoding is what keeps an existing save
        file usable across the swap, which is the whole point of having this seam.

        Returns the number of memories re-encoded.
        """
        self._embedder = embedder
        expected = embedder.dim
        recoded = 0

        for index, memory in enumerate(self._episodic):
            if memory.embedding is None or len(memory.embedding) != expected:
                self._episodic[index] = memory.model_copy(
                    update={"embedding": embedder.encode(memory.event_description)}
                )
                recoded += 1

        for index, memory in enumerate(self._semantic):
            if memory.embedding is None or len(memory.embedding) != expected:
                self._semantic[index] = memory.model_copy(
                    update={"embedding": embedder.encode(memory.fact)}
                )
                recoded += 1

        return recoded

    # --- persistence -------------------------------------------------------

    def snapshot(self) -> dict[str, list[dict]]:
        """All stored memories for inspection, without embedding vectors or mutable references."""
        return {
            "episodic": [m.model_dump(mode="json", exclude={"embedding"}) for m in self._episodic],
            "semantic": [m.model_dump(mode="json", exclude={"embedding"}) for m in self._semantic],
        }

    def save(self, path: str | Path) -> None:
        """Write this NPC's memories to JSON, atomically.

        Same temp-file-then-rename as ``WorldStateManager.save`` and for the same
        reason: a crash mid-write must not leave a half-written store that fails to
        parse on the next load.

        Embeddings are written along with the text. They are the expensive part
        (a real embedder is a model call per memory), and ``rebind_embedder`` already
        handles the case where a saved vector does not fit the live embedder — so
        storing them is a cache that is safe to be wrong about, not a correctness risk.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "npc_id": self.npc_id,
            "episodic": [m.model_dump(mode="json") for m in self._episodic],
            "semantic": [m.model_dump(mode="json") for m in self._semantic],
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def load(self, path: str | Path) -> None:
        """Replace the contents of this store from a save file.

        Loads *into* an existing store rather than constructing one, because the
        embedder is injected and shared (one instance serves every session), and a
        classmethod would have to take it as an argument anyway.

        Refuses a file belonging to another NPC: ``_require_own`` guards the same
        boundary on every single-memory write, and a whole-file load is the one path
        that could otherwise install another character's private memories wholesale.

        Vectors are then reconciled against the live embedder, so a file saved under
        a different embedder (or MRL width) loads without a dimension mismatch later.
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        stored = raw.get("npc_id")
        if stored != self.npc_id:
            raise ValueError(
                f"MemoryStore for {self.npc_id!r} refuses a save file belonging to "
                f"{stored!r}: each NPC's memory is private"
            )

        self._episodic = [EpisodicMemory.model_validate(m) for m in raw.get("episodic") or []]
        self._semantic = [SemanticMemory.model_validate(m) for m in raw.get("semantic") or []]
        self.rebind_embedder(self._embedder)

    # --- mutation ----------------------------------------------------------

    def update_semantic(
        self,
        memory_id: str,
        *,
        fact: str | None = None,
        confidence: float | None = None,
    ) -> SemanticMemory:
        """Revise a belief, re-encoding it when the text changes.

        Beliefs are the NPC's own model of the world and may be wrong; without a
        way to correct one, an NPC could never realise it misunderstood something.
        Changing the text must recompute the embedding, or the stale vector would
        keep answering retrieval for content that is no longer there.
        """
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {confidence}")

        for index, memory in enumerate(self._semantic):
            if memory.memory_id != memory_id:
                continue
            updates: dict[str, object] = {}
            if fact is not None and fact != memory.fact:
                updates["fact"] = fact
                updates["embedding"] = self._embedder.encode(fact)
            if confidence is not None:
                updates["confidence"] = confidence
            updated = memory.model_copy(update=updates) if updates else memory
            self._semantic[index] = updated
            return updated

        raise KeyError(f"no semantic memory {memory_id!r} for {self.npc_id!r}")

    def forget(self, memory_id: str) -> bool:
        """Delete one memory by id. Returns whether anything was removed."""
        for bucket in (self._episodic, self._semantic):
            for index, memory in enumerate(bucket):
                if memory.memory_id == memory_id:
                    del bucket[index]
                    return True
        return False

    def decay(self, rate: float) -> None:
        """Reduce every episodic memory's ``importance`` by ``rate``, floored at 0.

        Subtractive, not multiplicative: only subtraction lets a trivial memory
        actually reach zero and fall under ``prune``'s floor. Exponential decay
        preserves ratios forever, so nothing would ever cross a fixed threshold and
        the pair below would never forget anything.

        Paired with ``prune``, this is the forgetting mechanism: trivia sinks below
        the floor and is dropped, while salient events stay retrievable. Semantic
        beliefs do not decay — a belief is either revised or it stands.
        """
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"decay rate must be within [0, 1], got {rate}")
        self._episodic = [
            m.model_copy(update={"importance": max(0.0, m.importance - rate)})
            for m in self._episodic
        ]

    def prune(self, *, max_items: int | None = None, min_importance: float | None = None) -> int:
        """Drop episodic memories, keeping the most important and most recent.

        Only episodic memory is pruned: it grows once per turn and would otherwise
        carry every trivial exchange into the prompt at equal weight. Semantic
        beliefs are a small, deliberately curated set.

        Returns the number of memories removed.
        """
        before = len(self._episodic)
        kept = self._episodic

        if min_importance is not None:
            kept = [m for m in kept if m.importance >= min_importance]

        if max_items is not None:
            # Importance first, then recency as the tiebreaker: among equally
            # salient memories the later one is the more useful to keep.
            kept = sorted(kept, key=lambda m: (-m.importance, -m.occurred_at_day))[
                : max(0, max_items)
            ]
            # Restore insertion order so retrieval's stable tiebreaker is unchanged.
            keep_ids = {m.memory_id for m in kept}
            kept = [m for m in self._episodic if m.memory_id in keep_ids]

        self._episodic = kept
        return before - len(self._episodic)

    # --- reads -------------------------------------------------------------

    def episodic_by_ids(self, memory_ids: list[str]) -> list[EpisodicMemory]:
        """Resolve executed-tool IDs locally; caller text never grants disclosure."""
        wanted = set(memory_ids)
        return [m.model_copy(deep=True) for m in self._episodic if m.memory_id in wanted]

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
        # Query side, not document side: instruction-tuned embedders encode the two
        # asymmetrically (see ``Embedder``).
        query_vec = self._embedder.encode_query(query)

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

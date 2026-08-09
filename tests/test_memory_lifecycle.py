"""Tests for memory mutation and forgetting. Deterministic, strict TDD.

Slice 1's store was append-and-read only. An NPC whose knowledge can never be
corrected cannot be wrong on purpose, and one whose memory only grows will
eventually carry every trivial exchange into the prompt at equal weight. So:

  * ``update_semantic`` — a belief can be revised (the NPC realises it
    misunderstood something), and revising the text must re-encode it or the old
    vector would keep answering for the new content;
  * ``forget`` / ``prune`` / ``decay`` — bounded memory, with importance and
    recency deciding what survives.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.agent.memory_store import MemoryStore
from ai_native_rpg.schemas.memory import EpisodicMemory, SemanticMemory

NPC = "npc_a"


def _episodic(memory_id: str, text: str, *, importance: float = 0.5, day: int = 1):
    return EpisodicMemory(
        memory_id=memory_id,
        npc_id=NPC,
        event_description=text,
        importance=importance,
        occurred_at_day=day,
    )


def _semantic(memory_id: str, fact: str, *, confidence: float = 0.7):
    return SemanticMemory(memory_id=memory_id, npc_id=NPC, fact=fact, confidence=confidence)


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore(NPC)


class TestUpdateSemantic:
    def test_revises_the_fact_text(self, store):
        store.add_semantic(_semantic("s1", "凶手是外来人"))
        store.update_semantic("s1", fact="凶手是村里人")

        result = store.retrieve("凶手是谁", top_k=5)
        assert [m.fact for m in result.semantic] == ["凶手是村里人"]

    def test_revised_text_is_retrievable_by_its_new_content(self, store):
        """The embedding must be recomputed; a stale vector would keep matching the
        old wording and hide the correction from retrieval."""
        store.add_semantic(_semantic("s1", "猎人洛伦那晚在家"))
        store.update_semantic("s1", fact="猎人洛伦那晚在森林里")

        hits = store.retrieve("森林", top_k=3).semantic
        assert any("森林" in m.fact for m in hits)

    def test_updates_confidence_alone_without_touching_text(self, store):
        store.add_semantic(_semantic("s1", "玩家值得信任", confidence=0.3))
        before = store.retrieve("信任", top_k=1).semantic[0].embedding

        store.update_semantic("s1", confidence=0.9)
        after = store.retrieve("信任", top_k=1).semantic[0]

        assert after.confidence == pytest.approx(0.9)
        assert after.fact == "玩家值得信任"
        assert after.embedding == before

    def test_unknown_id_raises(self, store):
        with pytest.raises(KeyError, match="nope"):
            store.update_semantic("nope", fact="x")

    def test_rejects_out_of_range_confidence(self, store):
        store.add_semantic(_semantic("s1", "x"))
        with pytest.raises(ValueError, match="confidence"):
            store.update_semantic("s1", confidence=1.5)


class TestForget:
    def test_removes_an_episodic_memory(self, store):
        store.add_episodic(_episodic("m1", "那晚我看见有人回村"))
        assert store.forget("m1") is True

        assert store.episodic_count == 0
        assert store.retrieve("那晚", top_k=5).episodic == []

    def test_removes_a_semantic_memory(self, store):
        store.add_semantic(_semantic("s1", "说出去很危险"))
        assert store.forget("s1") is True
        assert store.semantic_count == 0

    def test_unknown_id_returns_false(self, store):
        assert store.forget("nope") is False


class TestPrune:
    def test_keeps_the_most_important(self, store):
        store.add_episodic(_episodic("low", "闲聊天气", importance=0.1))
        store.add_episodic(_episodic("high", "目击了关键的事", importance=0.9))

        store.prune(max_items=1)

        remaining = store.retrieve("任何", top_k=5).episodic
        assert [m.memory_id for m in remaining] == ["high"]

    def test_drops_below_an_importance_floor(self, store):
        store.add_episodic(_episodic("a", "小事", importance=0.2))
        store.add_episodic(_episodic("b", "大事", importance=0.8))

        removed = store.prune(min_importance=0.5)

        assert removed == 1
        assert store.episodic_count == 1

    def test_recency_breaks_ties(self, store):
        """Same importance: the later day is the more useful memory to keep."""
        store.add_episodic(_episodic("old", "同样重要的旧事", importance=0.5, day=1))
        store.add_episodic(_episodic("new", "同样重要的新事", importance=0.5, day=9))

        store.prune(max_items=1)

        assert [m.memory_id for m in store.retrieve("事", top_k=5).episodic] == ["new"]

    def test_prune_leaves_semantic_memory_alone(self, store):
        """Beliefs are not events; they do not accumulate per turn and are not what
        pruning is for."""
        store.add_episodic(_episodic("m1", "x", importance=0.1))
        store.add_semantic(_semantic("s1", "一个信念"))

        store.prune(max_items=0)

        assert store.episodic_count == 0
        assert store.semantic_count == 1

    def test_no_op_when_already_within_limits(self, store):
        store.add_episodic(_episodic("m1", "x", importance=0.5))
        assert store.prune(max_items=5) == 0
        assert store.episodic_count == 1


class TestDecay:
    def test_lowers_importance(self, store):
        store.add_episodic(_episodic("m1", "x", importance=0.8))
        store.decay(0.25)
        assert store.retrieve("x", top_k=1).episodic[0].importance == pytest.approx(0.55)

    def test_never_goes_negative(self, store):
        store.add_episodic(_episodic("m1", "x", importance=0.1))
        store.decay(0.9)
        assert store.retrieve("x", top_k=1).episodic[0].importance == pytest.approx(0.0)

    def test_is_monotonic_over_repeated_application(self, store):
        store.add_episodic(_episodic("m1", "x", importance=1.0))
        seen = []
        for _ in range(3):
            store.decay(0.2)
            seen.append(store.retrieve("x", top_k=1).episodic[0].importance)
        assert seen == sorted(seen, reverse=True)

    def test_rejects_invalid_rate(self, store):
        with pytest.raises(ValueError, match="rate"):
            store.decay(1.5)

    def test_decay_then_prune_forgets_trivia(self, store):
        """The pair is the forgetting mechanism: unimportant memories decay under
        the floor and get pruned, while salient ones survive."""
        store.add_episodic(_episodic("trivia", "打了个招呼", importance=0.2))
        store.add_episodic(_episodic("key", "他威胁了我", importance=1.0))

        store.decay(0.15)
        store.prune(min_importance=0.1)

        assert [m.memory_id for m in store.retrieve("任何", top_k=5).episodic] == ["key"]

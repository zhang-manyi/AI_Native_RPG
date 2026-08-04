"""Tests for the memory store and its retrieval ranking.

Deterministic module (the embedder is a fixed hash), so these are strict
input/output assertions per docs/09 §5. Two invariants matter most: retrieval
ranks by similarity-then-importance, and relationship values come from the World
State Manager, never from the store.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.agent import HashingEmbedder, MemoryStore, cosine_similarity
from ai_native_rpg.schemas.memory import EpisodicMemory, SemanticMemory
from ai_native_rpg.schemas.world_state import RelationshipState


def _episodic(mem_id: str, desc: str, importance: float, npc_id: str = "npc_a") -> EpisodicMemory:
    return EpisodicMemory(
        memory_id=mem_id,
        npc_id=npc_id,
        event_description=desc,
        importance=importance,
        occurred_at_day=1,
    )


class TestEmbedder:
    def test_deterministic(self):
        emb = HashingEmbedder()
        assert emb.encode("玩家询问凶手") == emb.encode("玩家询问凶手")

    def test_overlapping_text_scores_higher_than_unrelated(self):
        emb = HashingEmbedder()
        q = emb.encode("玩家 询问 森林 的 脚印")
        related = emb.encode("森林 的 脚印 被 雨 冲过")
        unrelated = emb.encode("酒馆 老板 擦 杯子")
        assert cosine_similarity(q, related) > cosine_similarity(q, unrelated)

    def test_empty_text_is_zero_similarity(self):
        emb = HashingEmbedder()
        assert cosine_similarity(emb.encode(""), emb.encode("anything")) == 0.0


class TestRetrieval:
    def test_empty_store_returns_empty(self):
        store = MemoryStore("npc_a")
        result = store.retrieve("凶手是谁")
        assert result.episodic == []
        assert result.semantic == []
        assert result.relationship is None

    def test_top_k_truncates(self):
        store = MemoryStore("npc_a")
        for i in range(5):
            store.add_episodic(_episodic(f"m{i}", f"森林 脚印 线索 {i}", importance=0.5))
        result = store.retrieve("森林 脚印", top_k=2)
        assert len(result.episodic) == 2

    def test_relevance_beats_irrelevance(self):
        store = MemoryStore("npc_a")
        store.add_episodic(_episodic("relevant", "森林 入口 有 脚印", importance=0.3))
        store.add_episodic(_episodic("irrelevant", "酒馆 老板 擦 杯子", importance=0.3))
        result = store.retrieve("森林 脚印", top_k=1)
        assert result.episodic[0].memory_id == "relevant"

    def test_importance_reranks_within_similar_matches(self):
        """Two equally-relevant memories: the more important one ranks first."""
        store = MemoryStore("npc_a")
        store.add_episodic(_episodic("low", "森林 脚印 线索", importance=0.1))
        store.add_episodic(_episodic("high", "森林 脚印 线索", importance=0.9))
        result = store.retrieve("森林 脚印 线索", top_k=2)
        assert result.episodic[0].memory_id == "high"

    def test_semantic_and_episodic_are_separate(self):
        store = MemoryStore("npc_a")
        store.add_episodic(_episodic("e1", "玩家 帮过 忙", importance=0.5))
        store.add_semantic(
            SemanticMemory(memory_id="s1", npc_id="npc_a", fact="玩家 讨厌 被 欺骗", confidence=0.8)
        )
        result = store.retrieve("玩家", top_k=3)
        assert store.episodic_count == 1
        assert store.semantic_count == 1
        assert {m.memory_id for m in result.episodic} == {"e1"}
        assert {m.memory_id for m in result.semantic} == {"s1"}


class TestPrivacy:
    def test_store_refuses_another_npcs_memory(self):
        store = MemoryStore("npc_a")
        with pytest.raises(ValueError, match="private"):
            store.add_episodic(_episodic("m1", "something", importance=0.5, npc_id="npc_b"))


class TestRelationshipProjection:
    def test_relationship_comes_from_the_passed_in_value_not_storage(self):
        """The store holds no relationship data; it only projects what it is given
        (which the Harness reads from the World State Manager)."""
        store = MemoryStore("npc_a")
        rel = RelationshipState(trust=40.0, fear=15.0, respect=5.0)
        result = store.retrieve("信任", relationship=rel, target_id="player_1")
        assert result.relationship is not None
        assert result.relationship.trust == 40.0
        assert result.relationship.target_id == "player_1"

    def test_no_relationship_without_target(self):
        store = MemoryStore("npc_a")
        rel = RelationshipState(trust=40.0)
        assert store.retrieve("x", relationship=rel).relationship is None

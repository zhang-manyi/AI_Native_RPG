"""Tests for the real embedding seam.

Split deliberately by what needs the model:

  * the mechanics — MRL truncation and renormalisation, caching, asymmetric
    query/document encoding, dimension guards — are pure and strictly tested
    against a stub encoder, so they run in CI with no model and no torch;
  * the semantic claim that actually motivates the swap (paraphrases with no
    shared characters should still retrieve) needs real weights, and is skipped
    when the model is absent.

The asymmetry matters: Qwen3-Embedding is trained with an instruction prefix on
the *query* side only. Encoding both sides identically leaves ~1-5% retrieval
quality on the table, and the Protocol had no way to express the difference.
"""

from __future__ import annotations

import math

import pytest

from ai_native_rpg.agent.embedding import HashingEmbedder, cosine_similarity
from ai_native_rpg.agent.embedding_qwen import (
    MODEL_PATH_VAR,
    QUERY_INSTRUCTION,
    Qwen3Embedder,
    format_query,
    truncate_and_renormalise,
)
from ai_native_rpg.agent.memory_store import MemoryStore
from ai_native_rpg.schemas.memory import EpisodicMemory


class _StubEncoder:
    """Stands in for SentenceTransformer: records prompts, returns fixed vectors."""

    def __init__(self, dim: int = 8) -> None:
        self.seen: list[tuple[str, str | None]] = []
        self._dim = dim

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, text, prompt_name=None, **kwargs):
        single = isinstance(text, str)
        texts = [text] if single else list(text)
        for item in texts:
            self.seen.append((item, prompt_name))
        # Deterministic and content-dependent. prompt_name participates so that a
        # query and a document encoding of the same text differ, as they do for the
        # real model, whose query side carries an instruction prefix.
        marker = "q" if prompt_name == "query" else "d"
        vectors = [
            [float((ord(c) % 7) + 1) for c in (marker + item)[: self._dim].ljust(self._dim, "a")]
            for item in texts
        ]
        return vectors[0] if single else vectors


def _stub_embedder(dim: int = 8, **kwargs) -> tuple[Qwen3Embedder, _StubEncoder]:
    encoder = _StubEncoder(dim=dim)
    return Qwen3Embedder(encoder=encoder, dim=dim, **kwargs), encoder


class TestTruncateAndRenormalise:
    def test_truncates_to_requested_dim(self):
        assert len(truncate_and_renormalise([1.0] * 10, 4)) == 4

    def test_result_is_unit_length(self):
        """Truncation drops magnitude unevenly; without renormalising, cosine
        against a full-length vector is distorted rather than merely coarser."""
        out = truncate_and_renormalise([3.0, 4.0, 10.0, 10.0], 2)
        assert math.sqrt(sum(x * x for x in out)) == pytest.approx(1.0)

    def test_preserves_direction_within_the_kept_prefix(self):
        out = truncate_and_renormalise([3.0, 4.0, 99.0], 2)
        assert out[0] / out[1] == pytest.approx(3.0 / 4.0)

    def test_longer_dim_than_vector_is_left_alone(self):
        out = truncate_and_renormalise([3.0, 4.0], 8)
        assert len(out) == 2
        assert math.sqrt(sum(x * x for x in out)) == pytest.approx(1.0)

    def test_zero_vector_survives_without_dividing_by_zero(self):
        assert truncate_and_renormalise([0.0, 0.0, 0.0], 2) == [0.0, 0.0]

    def test_rejects_non_positive_dim(self):
        with pytest.raises(ValueError, match="dim"):
            truncate_and_renormalise([1.0, 2.0], 0)


class TestAsymmetricEncoding:
    def test_documents_are_encoded_without_an_instruction(self):
        embedder, encoder = _stub_embedder()
        embedder.encode("玛尔塔那晚看见有人回村")

        text, prompt_name = encoder.seen[-1]
        assert prompt_name is None
        assert QUERY_INSTRUCTION not in text

    def test_queries_carry_the_instruction(self):
        embedder, encoder = _stub_embedder()
        embedder.encode_query("那晚发生了什么")

        _, prompt_name = encoder.seen[-1]
        assert prompt_name == "query"

    def test_query_and_document_encodings_differ(self):
        embedder, _ = _stub_embedder()
        assert embedder.encode("同一句话") != embedder.encode_query("同一句话")

    def test_declared_dim_matches_output(self):
        embedder, _ = _stub_embedder(dim=6)
        assert embedder.dim == 6
        assert len(embedder.encode("x")) == 6


class TestCaching:
    def test_repeated_text_is_encoded_once(self):
        """Retrieval encodes the same observation every turn; re-running a 0.6B
        model for a cache hit is pure waste."""
        embedder, encoder = _stub_embedder()
        embedder.encode("重复的句子")
        embedder.encode("重复的句子")
        assert len(encoder.seen) == 1

    def test_cache_distinguishes_query_from_document(self):
        embedder, encoder = _stub_embedder()
        embedder.encode("同一句话")
        embedder.encode_query("同一句话")
        assert len(encoder.seen) == 2

    def test_cache_can_be_disabled(self):
        embedder, encoder = _stub_embedder(cache_size=0)
        embedder.encode("x")
        embedder.encode("x")
        assert len(encoder.seen) == 2

    def test_cached_result_is_not_mutable_by_the_caller(self):
        """Handing out the cached list itself would let one caller corrupt every
        later retrieval."""
        embedder, _ = _stub_embedder()
        first = embedder.encode("x")
        first[0] = 999.0
        assert embedder.encode("x")[0] != 999.0


class TestProtocolCompatibility:
    def test_hashing_embedder_still_satisfies_the_protocol(self):
        """HashingEmbedder stays symmetric: it has no notion of a query side, so
        encode_query must simply delegate rather than pretend otherwise."""
        embedder = HashingEmbedder(dim=16)
        assert embedder.encode_query("那晚") == embedder.encode("那晚")

    def test_memory_store_uses_encode_query_for_retrieval(self):
        embedder, encoder = _stub_embedder()
        store = MemoryStore("npc_a", embedder=embedder)
        store.add_episodic(
            EpisodicMemory(
                memory_id="m1",
                npc_id="npc_a",
                event_description="文档侧",
                importance=0.5,
                occurred_at_day=1,
            )
        )
        encoder.seen.clear()

        store.retrieve("查询侧", top_k=1)

        assert encoder.seen[-1][1] == "query"


class TestDimensionMismatch:
    def test_stored_vectors_of_a_stale_dimension_are_re_encoded(self):
        """Swapping embedders invalidates stored vectors. Comparing them would raise
        a length mismatch deep in cosine_similarity; re-encoding is the only answer
        that keeps an existing save file usable."""
        store = MemoryStore("npc_a", embedder=HashingEmbedder(dim=64))
        store.add_episodic(
            EpisodicMemory(
                memory_id="m1",
                npc_id="npc_a",
                event_description="用旧维度编码的记忆",
                importance=0.5,
                occurred_at_day=1,
            )
        )

        store.rebind_embedder(HashingEmbedder(dim=16))

        result = store.retrieve("记忆", top_k=1)
        assert len(result.episodic) == 1
        assert len(result.episodic[0].embedding) == 16

    def test_retrieval_after_rebind_does_not_raise(self):
        store = MemoryStore("npc_a", embedder=HashingEmbedder(dim=32))
        for i in range(3):
            store.add_episodic(
                EpisodicMemory(
                    memory_id=f"m{i}",
                    npc_id="npc_a",
                    event_description=f"第{i}条关于森林的记忆",
                    importance=0.5,
                    occurred_at_day=1,
                )
            )
        store.rebind_embedder(HashingEmbedder(dim=8))
        assert len(store.retrieve("森林", top_k=3).episodic) == 3


class TestQueryFormatting:
    def test_wraps_query_in_the_trained_instruction_format(self):
        out = format_query("那晚你看到了什么")
        assert out.startswith("Instruct: ")
        assert QUERY_INSTRUCTION in out
        assert out.endswith("那晚你看到了什么")

    def test_document_text_is_never_wrapped(self):
        """Only the query side carries an instruction; wrapping documents too would
        push both sides off the format the model was trained on."""
        embedder, encoder = _stub_embedder()
        embedder.encode("玛尔塔那晚看见有人回村")
        assert "Instruct:" not in encoder.seen[-1][0]


class TestBackendSelection:
    def test_gguf_path_requires_llama_cpp(self, monkeypatch, tmp_path):
        """A .gguf file cannot be read by sentence-transformers, so the error must
        name the right missing dependency rather than the wrong one."""
        weights = tmp_path / "model-Q8_0.gguf"
        weights.write_bytes(b"not a real model")
        monkeypatch.setenv(MODEL_PATH_VAR, str(weights))

        try:
            import llama_cpp  # noqa: F401
        except ImportError:
            with pytest.raises(RuntimeError, match="llama-cpp-python"):
                Qwen3Embedder()
        else:  # pragma: no cover - only when the runtime is installed
            # With the runtime present, the bytes above are not a loadable model;
            # what matters is that it fails on the weights, not on the backend pick.
            with pytest.raises(Exception, match=r".+"):
                Qwen3Embedder()

    def test_missing_gguf_file_is_reported_clearly(self, monkeypatch, tmp_path):
        monkeypatch.setenv(MODEL_PATH_VAR, str(tmp_path / "absent.gguf"))
        # Whichever dependency is missing first, the message must not be a bare
        # KeyError or a torch import failure.
        with pytest.raises((RuntimeError, FileNotFoundError)):
            Qwen3Embedder()

    def test_is_available_is_false_for_a_nonexistent_gguf(self, monkeypatch, tmp_path):
        monkeypatch.setenv(MODEL_PATH_VAR, str(tmp_path / "absent.gguf"))
        assert Qwen3Embedder.is_available() is False

    def test_injected_encoder_bypasses_backend_selection(self, monkeypatch):
        """The stub path must not consult the environment at all, or these tests
        would depend on the developer's local model layout."""
        monkeypatch.setenv(MODEL_PATH_VAR, "/nonexistent/model.gguf")
        embedder, _ = _stub_embedder()
        assert len(embedder.encode("x")) == 8


# --- tests that need the real model ---------------------------------------

model_required = pytest.mark.skipif(
    not Qwen3Embedder.is_available(),
    reason="no embedding backend available; see README for GGUF or the optional extra",
)


@model_required
class TestRealModel:
    @pytest.fixture(scope="class")
    def embedder(self):
        return Qwen3Embedder()

    def test_paraphrase_beats_unrelated_text(self, embedder):
        """The whole point of the swap: HashingEmbedder scores these near zero
        because they share almost no characters."""
        query = embedder.encode_query("那天晚上你看到了什么？")
        paraphrase = embedder.encode("失踪当夜我目击了有人返回村庄")
        unrelated = embedder.encode("酒馆老板在擦杯子")

        assert cosine_similarity(query, paraphrase) > cosine_similarity(query, unrelated)

    def test_encoding_is_deterministic(self, embedder):
        assert embedder.encode("确定性检查") == embedder.encode("确定性检查")

    def test_truncated_dim_is_honoured(self):
        assert len(Qwen3Embedder(dim=256).encode("x")) == 256

"""Qwen3-Embedding-0.6B behind the ``Embedder`` Protocol.

Why a local model rather than an API: DeepSeek's public surface is chat only — it
has no embeddings endpoint (confirmed in its API docs and in DeepSeek-V3 issue
#806) — so the real embedder is unrelated to ``DeepSeekClient``. Running locally
also keeps retrieval off the player's critical path in latency terms: docs/02 §5
budgets ~30-60ms for the whole deterministic stretch, which a network round trip
would blow.

Why replace ``HashingEmbedder`` at all: it matches characters, not meaning. "那晚
你看到什么" and "失踪当夜我目击了有人返回村庄" share almost nothing lexically, so
the memory that the entire clue chain depends on fails to retrieve. That is the
one thing this module buys.

Two model-specific details drive the design:

  * **Asymmetric encoding.** The model is trained with a task instruction on the
    query side only; documents get none. ``sentence-transformers`` exposes this as
    ``prompt_name="query"``.
  * **MRL.** Output dimensionality is configurable from 32 to 1024. The default
    here is 256: at the scale of one NPC's memories, 1024 floats per memory buys
    nothing measurable and costs four times the storage in every save file.

Requires the optional ``embedding`` extra (``sentence-transformers``, which pulls
torch). Everything above the Protocol is unaffected when it is absent.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Any, Protocol

#: Default MRL output width. See the module docstring for why not 1024.
DEFAULT_DIM = 256

#: ModelScope/HuggingFace id. ModelScope mirrors it for faster access from China.
DEFAULT_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"

#: Query-side task instruction. English on purpose: the model's training
#: instructions were overwhelmingly English, so an English instruction retrieves
#: better even over Chinese content.
QUERY_INSTRUCTION = (
    "Given a character's question or utterance, retrieve the memories most relevant to answering it"
)

DEFAULT_CACHE_SIZE = 512


def truncate_and_renormalise(vector: list[float], dim: int) -> list[float]:
    """Apply MRL truncation, then restore unit length.

    Renormalising is not cosmetic: truncation removes magnitude unevenly across
    vectors, so skipping it distorts cosine comparisons rather than merely making
    them coarser.
    """
    if dim <= 0:
        raise ValueError(f"dim must be positive, got {dim}")
    head = list(vector[:dim])
    norm = math.sqrt(sum(x * x for x in head))
    if norm == 0.0:
        # No signal to preserve; 0.0 similarity downstream is the honest answer.
        return head
    return [x / norm for x in head]


class _SentenceEncoder(Protocol):
    """The slice of ``SentenceTransformer`` this module uses."""

    def encode(self, text: Any, prompt_name: str | None = ..., **kwargs: Any) -> Any: ...

    def get_sentence_embedding_dimension(self) -> int: ...


class Qwen3Embedder:
    """Instruction-tuned embedder with MRL truncation and an LRU text cache.

    ``encoder`` is injectable so the mechanics (truncation, caching, asymmetry) can
    be tested without loading a 0.6B model.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        dim: int = DEFAULT_DIM,
        encoder: _SentenceEncoder | None = None,
        cache_size: int = DEFAULT_CACHE_SIZE,
        model_path: str | None = None,
    ) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self._dim = dim
        self._cache_size = max(0, cache_size)
        self._cache: OrderedDict[tuple[bool, str], list[float]] = OrderedDict()
        self._encoder = encoder if encoder is not None else self._load(model_id, model_path)

    # --- loading -----------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """Whether the optional dependency is importable.

        Used to skip the model-dependent tests rather than fail CI, which
        deliberately installs neither torch nor the weights.
        """
        try:
            import sentence_transformers  # noqa: F401
        except Exception:
            return False
        return True

    @staticmethod
    def _load(model_id: str, model_path: str | None) -> _SentenceEncoder:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                "Qwen3Embedder needs the optional 'embedding' extra: "
                "uv pip install -e '.[embedding]'"
            ) from exc

        source = model_path or Qwen3Embedder._resolve_from_modelscope(model_id)
        return SentenceTransformer(source)

    @staticmethod
    def _resolve_from_modelscope(model_id: str) -> str:
        """Download via ModelScope when available, else fall back to the HF id.

        ModelScope is materially faster from China, but it is not required: if it
        is not installed, ``SentenceTransformer`` resolves the id itself.
        """
        try:
            from modelscope import snapshot_download
        except ImportError:  # pragma: no cover - depends on the environment
            return model_id
        return snapshot_download(model_id)

    # --- Embedder Protocol -------------------------------------------------

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, text: str) -> list[float]:
        """Encode stored text (a memory). No instruction prefix."""
        return self._encode(text, is_query=False)

    def encode_query(self, text: str) -> list[float]:
        """Encode search text, with the query-side task instruction."""
        return self._encode(text, is_query=True)

    # --- internals ---------------------------------------------------------

    def _encode(self, text: str, *, is_query: bool) -> list[float]:
        key = (is_query, text)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            # Copy: handing out the cached list would let one caller corrupt every
            # later retrieval.
            return list(cached)

        raw = self._encoder.encode(
            text,
            prompt_name="query" if is_query else None,
        )
        vector = truncate_and_renormalise([float(x) for x in raw], self._dim)

        if self._cache_size:
            self._cache[key] = list(vector)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

        return vector

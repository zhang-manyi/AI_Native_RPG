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
    ``prompt_name="query"``; llama.cpp has no prompt registry, so there the same
    wrapping is applied by hand (``format_query``).
  * **MRL.** Output dimensionality is configurable from 32 to 1024. The default
    here is 256: at the scale of one NPC's memories, 1024 floats per memory buys
    nothing measurable and costs four times the storage in every save file.

Two backends, chosen by what the weights actually are rather than by a config
flag — the file extension is the one signal that cannot disagree with reality:

  * ``.gguf`` -> llama.cpp via ``llama-cpp-python``. Quantised, CPU-only, no torch.
  * a directory or HF id -> ``sentence-transformers`` (the ``embedding`` extra).

Point ``EMBEDDING_MODEL_PATH`` at local weights to use them. Everything above the
Protocol is unaffected when no backend is installed: ``HashingEmbedder`` stays the
default.
"""

from __future__ import annotations

import math
import os
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

#: Default MRL output width. See the module docstring for why not 1024.
DEFAULT_DIM = 256

#: ModelScope/HuggingFace id. ModelScope mirrors it for faster access from China.
DEFAULT_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"

#: Points at local weights: either a .gguf file (llama.cpp) or a model directory.
MODEL_PATH_VAR = "EMBEDDING_MODEL_PATH"

#: Query-side task instruction. English on purpose: the model's training
#: instructions were overwhelmingly English, so an English instruction retrieves
#: better even over Chinese content.
QUERY_INSTRUCTION = (
    "Given a character's question or utterance, retrieve the memories most relevant to answering it"
)

DEFAULT_CACHE_SIZE = 512

#: llama.cpp context window. Deliberately small: the model *supports* 32K, but a
#: memory entry is a sentence, and an oversized n_ctx makes llama.cpp allocate and
#: walk a KV cache far larger than any input needs. Measured on CPU, dropping this
#: from 8192 to 512 took one encode from ~250ms to ~80ms — a 3x latency win for
#: capacity nothing here uses. Raise it only if memories become paragraphs.
DEFAULT_N_CTX = 512


def _configured_model_path() -> str | None:
    raw = os.environ.get(MODEL_PATH_VAR, "").strip()
    return raw or None


def _is_gguf(path: str) -> bool:
    return path.lower().endswith(".gguf")


def _has_llama_cpp() -> bool:
    try:
        import llama_cpp  # noqa: F401
    except Exception:
        return False
    return True


def format_query(text: str) -> str:
    """Wrap a query in the instruction format Qwen3-Embedding was trained on.

    ``sentence-transformers`` applies this itself via ``prompt_name="query"``.
    llama.cpp has no prompt registry, so it must be applied by hand there, or the
    two backends would encode queries differently and their vectors would not be
    comparable.
    """
    return f"Instruct: {QUERY_INSTRUCTION}\nQuery:{text}"


def fit_to_token_budget(text: str, budget: int, tokenize: Callable[[str], Sequence[Any]]) -> str:
    """Cut ``text`` down to ``budget`` tokens, keeping the start.

    llama.cpp does not raise when input exceeds ``n_ctx`` — it drops the excess
    silently, and two inputs sharing a long prefix then encode to cosine 1.000000
    however much their tails differ (measured). A memory whose vector covers only
    its first half retrieves for the wrong queries while the prompt still shows the
    whole text: a wrong answer wearing the costume of a working one. Truncating here
    reaches the same outcome deliberately — bounded, documented and testable.

    The start is kept because a memory opens with its subject. Cutting is a binary
    search over characters rather than arithmetic, since CJK token boundaries have
    no fixed ratio to characters.
    """
    if budget <= 0:
        raise ValueError(f"budget must be positive, got {budget}")
    if len(tokenize(text)) <= budget:
        return text

    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if len(tokenize(text[:mid])) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[:low]


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


class _GGUFEncoder:
    """llama.cpp backend, presenting the same surface as ``SentenceTransformer``.

    Adapting to the sentence-transformers shape rather than the reverse keeps the
    branch confined to construction: ``Qwen3Embedder`` never learns which backend
    it holds. ``prompt_name="query"`` is honoured by applying the instruction
    wrapper by hand, since llama.cpp has no prompt registry.
    """

    def __init__(self, model_path: str, *, dim: int, n_ctx: int = DEFAULT_N_CTX) -> None:
        try:
            from llama_cpp import Llama
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                f"{model_path} is a GGUF file, which needs llama-cpp-python: "
                "pip install llama-cpp-python"
            ) from exc

        if not Path(model_path).exists():
            raise FileNotFoundError(f"embedding model not found: {model_path}")

        self._model = Llama(
            model_path=model_path,
            embedding=True,
            n_ctx=n_ctx,
            verbose=False,
        )
        self._dim = dim
        self._n_ctx = n_ctx

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    @property
    def n_ctx(self) -> int:
        return self._n_ctx

    def encode(self, text: Any, prompt_name: str | None = None, **kwargs: Any) -> Any:
        single = isinstance(text, str)
        texts = [text] if single else list(text)
        prepared = [format_query(item) if prompt_name == "query" else item for item in texts]

        vectors = [self._embed_one(item) for item in prepared]
        return vectors[0] if single else vectors

    def _embed_one(self, text: str) -> list[float]:
        raw = self._model.create_embedding(self._fit_to_window(text))
        embedding = raw["data"][0]["embedding"]
        # llama.cpp returns token-level vectors for some models and a single
        # pooled vector for others; mean-pool the former so both shapes reduce to
        # one vector per text.
        if embedding and isinstance(embedding[0], list):
            columns = zip(*embedding, strict=True)
            return [sum(col) / len(embedding) for col in columns]
        return [float(x) for x in embedding]

    def _fit_to_window(self, text: str) -> str:
        """Apply the token budget, tolerating builds whose tokenizer differs."""
        try:
            return fit_to_token_budget(
                text, self._n_ctx, lambda s: self._model.tokenize(s.encode("utf-8"))
            )
        except Exception:  # pragma: no cover - tokenizer shape varies by build
            return text


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
        self._encoder = encoder if encoder is not None else self._load(model_id, model_path, dim)

    # --- loading -----------------------------------------------------------

    @staticmethod
    def is_available(model_path: str | None = None) -> bool:
        """Whether some backend can actually encode right now.

        Checked rather than assumed, because two independent things can be missing:
        the runtime library and the weights. Tests skip on this instead of failing,
        since CI installs neither.
        """
        path = model_path or _configured_model_path()
        if path is not None and _is_gguf(path):
            return _has_llama_cpp() and Path(path).exists()
        try:
            import sentence_transformers  # noqa: F401
        except Exception:
            return False
        return True

    @staticmethod
    def _load(model_id: str, model_path: str | None, dim: int) -> _SentenceEncoder:
        """Pick a backend from what the weights actually are.

        GGUF is a llama.cpp format: ``sentence-transformers`` cannot read it, and
        conversely a llama.cpp build has no use for a safetensors directory. So the
        file extension, not a config flag, decides — that is the one signal that
        cannot be set inconsistently with reality.
        """
        source = model_path or _configured_model_path()

        if source is not None and _is_gguf(source):
            return _GGUFEncoder(source, dim=dim)

        if source is None:
            source = Qwen3Embedder._resolve_from_modelscope(model_id)

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                "Qwen3Embedder needs a backend. For a GGUF file set "
                f"{MODEL_PATH_VAR}=<path to .gguf> and install llama-cpp-python; "
                "for HuggingFace weights install the optional extra: "
                "uv pip install -e '.[embedding]'"
            ) from exc

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

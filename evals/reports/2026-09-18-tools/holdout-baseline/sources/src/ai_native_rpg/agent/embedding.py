"""Embedding: the text -> vector seam for memory retrieval.

Two implementations sit behind ``Embedder``: the dependency-free
``HashingEmbedder`` here, and ``embedding_qwen.Qwen3Embedder`` (a real model,
optional extra). The hashing one keeps the whole retrieval chain (encode ->
cosine -> importance rerank) exercised and unit-testable with no model, no torch
and no network; the real one is what makes retrieval actually semantic.

The Protocol is *asymmetric*: ``encode`` for stored text, ``encode_query`` for the
search text. Instruction-tuned embedding models — Qwen3-Embedding included — are
trained with a task instruction on the query side only, and encoding both sides
identically costs retrieval quality. ``HashingEmbedder`` has no notion of a query
side, so it simply delegates.

The mock is a hashing bag-of-words projection: deterministic (same text -> same
vector, required for reproducible tests) and giving overlapping-word texts a
higher cosine than unrelated ones — enough signal to verify ranking, not a claim
of semantic quality. Its real limitation is that it matches *characters*, not
meaning: two paraphrases sharing no characters score near zero.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


class Embedder(Protocol):
    """Encodes text into a fixed-length vector.

    ``encode`` handles stored documents (memories); ``encode_query`` handles the
    text being searched for. They differ for instruction-tuned models.
    """

    @property
    def dim(self) -> int: ...

    def encode(self, text: str) -> list[float]: ...

    def encode_query(self, text: str) -> list[float]: ...


_WORD = re.compile(r"\w+", re.UNICODE)
# CJK ranges are split per-character: `\w+` would match a whole run of Chinese as
# one token, so two different Chinese sentences would share nothing. Splitting per
# character restores the word-overlap signal the cosine ranking relies on, while
# latin/digit runs stay whole.
_CJK = re.compile(r"[一-鿿぀-ヿ]")


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for run in _WORD.findall(text.lower()):
        buf = ""
        for ch in run:
            if _CJK.match(ch):
                if buf:
                    tokens.append(buf)
                    buf = ""
                tokens.append(ch)  # one token per CJK character
            else:
                buf += ch
        if buf:
            tokens.append(buf)
    return tokens


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity, defined as 0.0 when either vector has zero norm.

    A zero norm means "no signal" (e.g. empty text), and 0.0 similarity is the
    honest answer there — not an error and not a false match.
    """
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class HashingEmbedder(Embedder):
    """Deterministic bag-of-words hashing embedder. No dependencies, no network."""

    def __init__(self, dim: int = 64) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def _bucket(self, token: str) -> int:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % self._dim

    def encode(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _tokenize(text):
            vec[self._bucket(token)] += 1.0
        return vec

    def encode_query(self, text: str) -> list[float]:
        """Symmetric: a hashing projection has no query-side instruction to add,
        and pretending otherwise would only desynchronise the two sides."""
        return self.encode(text)

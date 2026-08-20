"""Session registry: creation, lookup, eviction (docs/12 §5.4).

In memory, like ``WorldStateManager`` itself — docs/04 §5 keeps the world an
in-process model with JSON snapshots, and a local debug tool has no reason to outlive
its process. Traces still land on disk; that half was always persistent.

The embedder is shared across every session. It is read-only and can cost hundreds of
MB to load, so refreshing the page must not build a second one — which is also why
there is a session cap.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

from ..agent import HashingEmbedder
from ..agent.embedding import Embedder
from ..config import Settings
from .session import Session, SessionError

#: Concurrent sessions allowed. A refresh-happy afternoon should not accumulate
#: dozens of runtimes; this is a local tool with one user.
MAX_SESSIONS = 8

#: Seconds of inactivity after which a session may be evicted to make room.
IDLE_TIMEOUT = 3600.0


def build_shared_embedder() -> tuple[Embedder, str]:
    """Prefer real semantic retrieval, fall back to hashing.

    Same choice ``chat_demo`` makes, and reported the same way: which embedder is
    live changes retrieval quality substantially, so the page header names it rather
    than letting a silent fallback look like a model problem.
    """
    try:
        from ..agent.embedding_qwen import Qwen3Embedder

        if Qwen3Embedder.is_available():
            embedder = Qwen3Embedder()
            return embedder, f"Qwen3-Embedding ({embedder.dim}d, 语义检索)"
    except Exception:
        # A debug server must not fail to start because optional model setup broke.
        pass
    return HashingEmbedder(), "HashingEmbedder (字面匹配，非语义)"


class SessionRegistry:
    """Owns every live session and the one embedder they share."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        dev_mode: bool = True,
        max_sessions: int = MAX_SESSIONS,
    ) -> None:
        self._settings = settings or Settings.from_env()
        self._dev_mode = dev_mode
        self._max = max_sessions
        self._sessions: dict[str, Session] = {}
        self._touched: dict[str, float] = {}
        self._lock = threading.Lock()
        self._embedder: Embedder | None = None
        self._embedder_label = ""

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def dev_mode(self) -> bool:
        return self._dev_mode

    @property
    def embedder_label(self) -> str:
        # Built lazily so importing the app costs nothing; the first session pays.
        if self._embedder is None:
            self._ensure_embedder()
        return self._embedder_label

    def _ensure_embedder(self) -> Embedder:
        with self._lock:
            if self._embedder is None:
                self._embedder, self._embedder_label = build_shared_embedder()
            return self._embedder

    def create(
        self,
        *,
        scenario: str,
        npc_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> Session:
        embedder = self._ensure_embedder()
        self._evict_if_needed()

        session_id = uuid.uuid4().hex
        session = Session(
            session_id=session_id,
            scenario=scenario,
            npc_id=npc_id,
            settings=self._settings,
            embedder=embedder,
            dev_mode=self._dev_mode,
            loop=loop,
        )
        with self._lock:
            self._sessions[session_id] = session
            self._touched[session_id] = time.monotonic()
        return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            self._touched[session_id] = time.monotonic()
            return session

    def close(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            self._touched.pop(session_id, None)
        if session is not None:
            session.close()

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._touched.clear()
        for session in sessions:
            session.close()

    def _evict_if_needed(self) -> None:
        """Drop the least recently used idle session when at capacity."""
        with self._lock:
            if len(self._sessions) < self._max:
                return
            now = time.monotonic()
            candidates = [
                (touched, sid)
                for sid, touched in self._touched.items()
                if not self._sessions[sid].busy and now - touched > 0
            ]
            if not candidates:
                raise SessionError(f"at capacity ({self._max} sessions) and all of them are busy")
            candidates.sort()
            _, victim = candidates[0]
            session = self._sessions.pop(victim)
            self._touched.pop(victim, None)
        session.close()

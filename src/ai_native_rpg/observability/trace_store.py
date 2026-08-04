"""TraceStore: persist AgentTrace records for the Developer Platform.

Slice 1 writes one JSON file per trace under a directory — docs/07 §5 rules out a
dedicated tracing backend at this scale ("SQLite/JSON is enough"). The write is
atomic (temp file then rename) so a crash mid-write cannot leave a half-written
trace that later fails to parse, mirroring WorldStateManager.save.

Writing is fire-and-forget from the Harness's point of view (docs/07 §2.1: trace
logging must not block the player response); keeping it a plain method here leaves
that scheduling decision to the caller.
"""

from __future__ import annotations

from pathlib import Path

from ..schemas.agent_trace import AgentTrace


class TraceStore:
    """Reads and writes ``AgentTrace`` records as JSON files, one per trace."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, trace_id: str) -> Path:
        return self._root / f"{trace_id}.json"

    def save(self, trace: AgentTrace) -> Path:
        """Persist a trace atomically and return the path it was written to."""
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(trace.trace_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(trace.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, trace_id: str) -> AgentTrace:
        path = self._path(trace_id)
        return AgentTrace.model_validate_json(path.read_text(encoding="utf-8"))

    def list_trace_ids(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.stem for p in self._root.glob("*.json"))

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
from typing import TYPE_CHECKING, Generic, TypeVar

from pydantic import BaseModel

from ..schemas.agent_trace import AgentTrace

if TYPE_CHECKING:
    from ..narrative.engine import NarrativeTick

#: Older TypeVar syntax rather than PEP 695 (``class Store[T: BaseModel]``):
#: pyproject declares ``requires-python = ">=3.11"`` and type parameter lists are
#: 3.12+.
RecordT = TypeVar("RecordT", bound=BaseModel)


class _JsonRecordStore(Generic[RecordT]):
    """One JSON file per record, written atomically.

    Shared by the agent-trace and narrative-tick stores: both want the same
    durability property (a crash mid-write must not leave a half-written file that
    later fails to parse) and neither wants a database at this scale.
    """

    def __init__(self, root: str | Path, model: type[RecordT], id_field: str) -> None:
        self._root = Path(root)
        self._model = model
        self._id_field = id_field

    def _path(self, record_id: str) -> Path:
        return self._root / f"{record_id}.json"

    def save(self, record: RecordT) -> Path:
        """Persist a record atomically and return the path it was written to."""
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(getattr(record, self._id_field))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, record_id: str) -> RecordT:
        return self._model.model_validate_json(self._path(record_id).read_text(encoding="utf-8"))

    def _ids(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.stem for p in self._root.glob("*.json"))


class TraceStore(_JsonRecordStore[AgentTrace]):
    """Reads and writes ``AgentTrace`` records as JSON files, one per trace."""

    def __init__(self, root: str | Path) -> None:
        super().__init__(root, AgentTrace, "trace_id")

    def list_trace_ids(self) -> list[str]:
        return self._ids()


class NarrativeTickStore(_JsonRecordStore["NarrativeTick"]):
    """Reads and writes ``NarrativeTick`` records (docs/07 §2.3).

    Separate from ``TraceStore`` rather than a subtype of one record kind: a tick is
    not an agent turn, and the narrative panel reads a different question ("what
    shape is the story in") than the Trace Viewer does ("how did this NPC decide").
    Keeping them in separate directories means either can be cleared alone.
    """

    def __init__(self, root: str | Path) -> None:
        # Imported here, not at module scope: ``narrative.engine`` imports the
        # Harness, which would make this a cycle at import time.
        from ..narrative.engine import NarrativeTick

        super().__init__(root, NarrativeTick, "tick_id")

    def load(self, record_id: str) -> NarrativeTick:
        return super().load(record_id)

    def list_tick_ids(self) -> list[str]:
        return self._ids()

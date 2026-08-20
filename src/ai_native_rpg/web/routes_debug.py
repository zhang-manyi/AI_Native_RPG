"""Developer routes (docs/12 §4.3). Reads ``WorldState`` directly, on purpose.

These endpoints exist to show what the player cannot see, so they deliberately bypass
``PlayerView`` (docs/07 §2.4). That makes them the exact leak channel the visibility
mechanism defends against, hence the two hard rules:

* the whole router is **not registered** when dev mode is off — not 403'd. A 403
  announces that the endpoint exists and merely refused this caller;
* nothing here shares a handler or a serialiser with the player side.

There is no auth, and there should not be: this binds to loopback and is a
single-player local tool. Do not expose it (docs/12 §9).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..observability.panels import PanelView, build_unlock_board
from .registry import SessionRegistry

router = APIRouter(prefix="/debug", tags=["debug"])


def _registry(request: Request) -> SessionRegistry:
    return request.app.state.registry


def _session(request: Request, session_id: str):
    try:
        return _registry(request).get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no session {session_id!r}") from None


@router.get("/session/{session_id}/panel", response_model=PanelView)
def get_panel(session_id: str, request: Request) -> PanelView:
    """Every panel block in one snapshot — same payload as the ``panel`` event."""
    return _session(request, session_id).panel()


@router.get("/session/{session_id}/world")
def get_world(session_id: str, request: Request) -> dict:
    """The full ``WorldState`` (World Viewer, docs/07 §2.2)."""
    return _session(request, session_id).manager.snapshot().model_dump(mode="json")


@router.get("/session/{session_id}/memory")
def get_memory(session_id: str, request: Request, top_k: int = 50) -> dict:
    """One NPC's private memory (Memory Explorer, docs/07 §2.2).

    Memory is per-NPC and private — each NPC has its own store, and this session
    holds exactly one. The owner is named in the response so the page cannot present
    it as a global store.
    """
    session = _session(request, session_id)
    result = session.memory.retrieve("", top_k=top_k)
    return {
        "npc_id": session.npc_id,
        "name": session.npc_name,
        "episodic_count": session.memory.episodic_count,
        "semantic_count": session.memory.semantic_count,
        "episodic": [
            {"id": m.memory_id, "text": m.event_description, "importance": m.importance}
            for m in result.episodic
        ],
        "semantic": [
            {"id": m.memory_id, "text": m.fact, "confidence": m.confidence} for m in result.semantic
        ],
    }


@router.get("/session/{session_id}/unlock/{fact_id}")
def get_unlock_detail(session_id: str, fact_id: str, request: Request) -> dict:
    """ "Why can't the player see X yet" — every clause, with its current value.

    Just the evaluator's intermediate result exposed, which docs/07 §2.3 notes costs
    almost nothing and turns threshold tuning from guesswork into evidence.
    """
    session = _session(request, session_id)
    world = session.manager.snapshot()
    row = next((r for r in build_unlock_board(world) if r.fact_id == fact_id), None)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"{fact_id!r} is not a gated fact in this world "
            "(either unknown, already revealed, or it carries no reveal_condition)",
        )
    return {
        **row.model_dump(),
        "unlockable": row.unlockable,
        "progress": row.progress,
    }


@router.get("/trace/{trace_id}")
def get_trace(trace_id: str, request: Request) -> dict:
    """One ``AgentTrace``: the whole decision chain for a turn (docs/07 §2.1)."""
    registry = _registry(request)
    for session in list(getattr(registry, "_sessions", {}).values()):
        try:
            return session.traces.load(trace_id).model_dump(mode="json")
        except (FileNotFoundError, OSError):
            continue
    raise HTTPException(status_code=404, detail=f"no trace {trace_id!r}")


@router.get("/tick/{tick_id}")
def get_tick(tick_id: str, request: Request) -> dict:
    """One ``NarrativeTick``: candidates, what pacing blocked, what was generated."""
    registry = _registry(request)
    for session in list(getattr(registry, "_sessions", {}).values()):
        try:
            return session.narrative_traces.load(tick_id).model_dump(mode="json")
        except (FileNotFoundError, OSError):
            continue
    raise HTTPException(status_code=404, detail=f"no tick {tick_id!r}")

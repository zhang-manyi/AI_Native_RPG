"""Small, deterministic projection from private planning to player-facing evidence.

This does not classify free text as safe. Memory provenance is written by the
runtime; world disclosure remains PlayerView's job. Unmapped memories stay private.
"""

from __future__ import annotations

from typing import Any

from ..schemas.agent_trace import TraceStep
from ..schemas.memory import MemoryRetrievalResult
from ..world.manager import WorldStateManager
from .memory_store import MemoryStore


def assemble_expression_evidence(
    *,
    manager: WorldStateManager,
    memory: MemoryStore,
    retrieval: MemoryRetrievalResult,
    steps: list[TraceStep],
    player_id: str,
    action_status: str = "none",
) -> dict[str, Any]:
    """Use only actual recall; a tool cannot pass arbitrary prose as evidence.

    Re-read public facts after adjudication so a rejected proposal grants nothing
    and a successful reveal uses the authoritative value, not private recollection.
    No-action and rejected-action statuses are distinct. No free-text rejection
    reason, proposal payload, private plan or semantic belief crosses this boundary.
    """
    recalled = list(retrieval.episodic)
    for step in steps:
        if (
            step.step_name == "tool_call"
            and step.input_summary.get("tool") == "query_memory"
            and step.output_summary.get("ok") is True
        ):
            ids = step.output_summary.get("result", {}).get("episodic_ids", [])
            if isinstance(ids, list) and all(isinstance(mid, str) for mid in ids):
                recalled.extend(memory.episodic_by_ids(ids))
    statements = {}
    for item in recalled:
        if (
            item.npc_id == memory.npc_id
            and item.source_player_id == player_id
            and item.player_statement
        ):
            statements[item.memory_id] = {
                "memory_id": item.memory_id,
                "source": "player_statement",
                "text": item.player_statement,
            }
    return {
        "player_statements": list(statements.values()),
        "public_facts": manager.player_view(player_id).visible_facts,
        "action_result": {"status": action_status},
    }

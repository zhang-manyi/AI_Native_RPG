"""NPC Agent Runtime: the one true LLM Agent in the system (docs/06).

Memory retrieval, planning, tool use, dialogue generation, action proposal and
trace, orchestrated by the Harness.

``Qwen3Embedder`` is deliberately not re-exported here: importing it pulls
sentence-transformers/torch, and this package must stay importable without the
optional ``embedding`` extra. Import it from ``agent.embedding_qwen`` directly.
"""

from .embedding import Embedder, HashingEmbedder, cosine_similarity
from .harness import (
    DialogueOutput,
    Harness,
    PlanningOutput,
    PromptLibrary,
    ProposedAction,
)
from .memory_store import MemoryStore
from .tools import ToolError, ToolRegistry, build_npc_tools

__all__ = [
    "DialogueOutput",
    "Embedder",
    "Harness",
    "HashingEmbedder",
    "MemoryStore",
    "PlanningOutput",
    "PromptLibrary",
    "ProposedAction",
    "ToolError",
    "ToolRegistry",
    "build_npc_tools",
    "cosine_similarity",
]

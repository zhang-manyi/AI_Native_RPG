"""NPC Agent Runtime: the one true LLM Agent in the system (docs/06).

Memory retrieval, planning, dialogue generation, action proposal and trace,
orchestrated by the Harness. Tool Use (Function Calling) arrives in slice 2.
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

__all__ = [
    "DialogueOutput",
    "Embedder",
    "Harness",
    "HashingEmbedder",
    "MemoryStore",
    "PlanningOutput",
    "PromptLibrary",
    "ProposedAction",
    "cosine_similarity",
]

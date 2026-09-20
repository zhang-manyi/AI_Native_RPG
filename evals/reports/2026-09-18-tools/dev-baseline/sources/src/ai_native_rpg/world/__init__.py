"""World State Manager: the deterministic core. No LLM calls in this package.

See docs/04_World_State_Manager.md.
"""

from .conditions import UnknownPathError, evaluate, resolve_path
from .manager import WorldStateManager
from .player_view import player_view

__all__ = [
    "UnknownPathError",
    "WorldStateManager",
    "evaluate",
    "player_view",
    "resolve_path",
]

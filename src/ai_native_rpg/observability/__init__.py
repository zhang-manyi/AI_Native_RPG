"""Observability: Trace persistence for the Developer Platform (docs/07).

Consumes data the runtime produces; never on the player's critical path.
"""

from .trace_store import TraceStore

__all__ = ["TraceStore"]

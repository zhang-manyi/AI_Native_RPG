"""Web interface: scene page + developer panel (docs/12_Web_Interface.md).

Replaces ``scripts/chat_demo.py`` as the main entry point and delivers the debug-panel
half of slice 4. This package is an assembler, an executor and an event stream — no
domain logic: panel judgements live in ``observability/panels.py``, the runtime in
``agent/`` and ``narrative/``.

FastAPI is an optional dependency (the ``web`` extra), so importing this package is
only possible where it is installed. Nothing in the core library imports it.

``create_app`` is imported lazily by name to keep ``import ai_native_rpg.web`` cheap
and to avoid requiring FastAPI for modules that only want ``SceneView``.
"""

from __future__ import annotations

__all__ = ["create_app", "resolve_dev_mode"]


def __getattr__(name: str):
    if name in __all__:
        from . import app as _app

        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

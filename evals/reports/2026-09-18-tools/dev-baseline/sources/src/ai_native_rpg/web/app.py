"""ASGI app assembly (docs/12 §9, §10).

``DEV_MODE`` decides whether ``/debug`` is mounted at all. Off means the routes do not
exist, rather than existing and refusing — a 403 tells the caller there is something
there (docs/07 §2.4).

The app carries no game logic. It wires a registry, a router pair and the static
files; every judgement lives in ``observability/panels.py`` and every runtime step in
``agent/`` and ``narrative/``. If a rule about foreshadowing or pacing ever appears
under ``web/``, it is in the wrong place (docs/12 §6.1).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ..config import Settings
from .registry import SessionRegistry

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Spellings accepted as true, matching ``config._TRUTHY`` so the two agree.
_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Sent with every static file.
#:
#: ``StaticFiles`` ships ``etag``/``last-modified`` but no ``Cache-Control``, which lets a
#: browser reuse a cached copy without asking. For a developer tool whose js/css change
#: every few minutes that is the wrong default: it produced a real confusion where
#: ``localhost:8000`` showed new sprites while ``127.0.0.1:8000`` still showed old ones —
#: same server, byte-identical responses, but two separate cache origins as far as the
#: browser is concerned.
NO_CACHE = "no-cache, no-store, must-revalidate"


class _NoCacheStatic(StaticFiles):
    """``StaticFiles`` that always revalidates. See ``NO_CACHE``."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = NO_CACHE
        return response


def resolve_dev_mode(*, host: str = "127.0.0.1", override: bool | None = None) -> bool:
    """Whether the developer panel and ``/debug`` are available.

    Defaults to on for a loopback bind and off otherwise: the panel exposes the full
    world, so the safe default is tied to "only this machine can reach it" rather than
    to a flag someone has to remember. ``DEV_MODE`` in the environment wins, and an
    explicit argument wins over that.
    """
    if override is not None:
        return override

    raw = os.environ.get("DEV_MODE")
    if raw is not None:
        return raw.strip().lower() in _TRUTHY

    return host in {"127.0.0.1", "localhost", "::1"}


def create_app(
    *,
    settings: Settings | None = None,
    dev_mode: bool = True,
    max_sessions: int | None = None,
) -> FastAPI:
    registry_kwargs = {"settings": settings, "dev_mode": dev_mode}
    if max_sessions is not None:
        registry_kwargs["max_sessions"] = max_sessions

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Captured here because sync handlers run in a threadpool, where
        # ``get_running_loop()`` fails. Session worker threads need this handle to
        # marshal events back into the loop (``Session._deliver``).
        import asyncio

        app.state.loop = asyncio.get_running_loop()
        # Open SSE streams watch this so Ctrl+C can actually stop the server. uvicorn
        # waits for in-flight connections to finish, and an event stream never finishes
        # on its own — without a signal to end, the two wait for each other forever.
        app.state.shutdown_event = asyncio.Event()
        # The tasks running those streams. Setting the flag alone is not enough:
        # Starlette races a streaming body against ``listen_for_disconnect``, which
        # waits on the client and never wakes, so the generator is never resumed to
        # notice the flag. Cancelling the task is what actually frees the connection.
        app.state.live_streams = set()
        try:
            yield
        finally:
            app.state.shutdown_event.set()
            for task in list(app.state.live_streams):
                task.cancel()
            app.state.registry.close_all()

    app = FastAPI(
        title="AI Native RPG — Developer Interface",
        description=(
            "Scene page plus Narrative State Panel (docs/12). Local tool: no auth, "
            "binds to loopback, /debug exposes the full world."
        ),
        version="0.1.0",
        lifespan=lifespan,
        # The interactive docs are themselves a developer surface.
        docs_url="/docs" if dev_mode else None,
        redoc_url=None,
    )
    app.state.registry = SessionRegistry(**registry_kwargs)
    app.state.dev_mode = dev_mode

    from . import routes_player

    app.include_router(routes_player.router)

    if dev_mode:
        # Registered only in dev mode. Off means absent, not forbidden.
        from . import routes_debug

        app.include_router(routes_debug.router)

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {"ok": True, "dev_mode": dev_mode}

    if STATIC_DIR.is_dir():
        app.mount("/static", _NoCacheStatic(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": NO_CACHE})

    return app

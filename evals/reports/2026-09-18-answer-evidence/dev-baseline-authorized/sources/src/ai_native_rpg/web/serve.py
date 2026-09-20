"""Serving the app so that Ctrl+C actually stops it.

An SSE stream never ends on its own, and ``uvicorn.Server.shutdown`` waits for
in-flight tasks *before* sending the lifespan shutdown event:

    for connection in list(self.server_state.connections): connection.shutdown()
    await asyncio.wait_for(self._wait_tasks_to_complete(), timeout=...)
    # ... only now is the lifespan shutdown event sent

So cleaning up in ``lifespan`` cannot help — by the time it runs, the wait is already
over (measured: exit took the full graceful timeout, with the flag still unset). Two
earlier attempts failed for adjacent reasons and are worth recording:

1. Watching a shutdown flag inside the generator. Necessary but not sufficient:
   Starlette races a streaming body against ``listen_for_disconnect``, which waits on
   the client and never wakes, so the generator is never resumed to see the flag.
2. Cancelling the stream tasks from ``lifespan``. Right action, wrong moment — see
   above.

The fix is to end the streams *before* uvicorn starts waiting, which means overriding
``shutdown``. ``timeout_graceful_shutdown`` stays as a backstop, but a passing test must
not depend on it: the check uses a generous timeout so only genuinely self-ending
streams look fast.
"""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI

#: Backstop for a connection that still refuses to end. Small, because reaching it at
#: all means something is wrong and a developer is waiting at a terminal.
DEFAULT_GRACEFUL_TIMEOUT = 3


class StreamAwareServer(uvicorn.Server):
    """A server that closes open event streams before waiting on them.

    Holds the ``FastAPI`` instance directly rather than reading
    ``config.loaded_app``, which by then may be wrapped in middleware and no longer
    expose ``.state``.
    """

    def __init__(self, config: uvicorn.Config, *, app: FastAPI) -> None:
        super().__init__(config)
        self._app = app

    async def shutdown(self, sockets=None) -> None:
        state = getattr(self._app, "state", None)

        # Tell the generators to stop looping...
        event = getattr(state, "shutdown_event", None)
        if event is not None:
            event.set()

        # ...and cancel the tasks running them, which is what frees the connection:
        # a generator blocked in listen_for_disconnect will not resume by itself.
        for task in list(getattr(state, "live_streams", None) or ()):
            task.cancel()

        # Yield once so the cancellations propagate before uvicorn counts tasks.
        await asyncio.sleep(0)

        await super().shutdown(sockets)


def build_server(
    app: FastAPI,
    *,
    host: str,
    port: int,
    log_level: str = "info",
    graceful_timeout: int = DEFAULT_GRACEFUL_TIMEOUT,
) -> StreamAwareServer:
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level,
        timeout_graceful_shutdown=graceful_timeout,
    )
    return StreamAwareServer(config, app=app)

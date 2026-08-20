"""Ctrl+C must stop the server even with event streams open (docs/12 §3.1).

An SSE stream never ends on its own, and ``uvicorn.Server.shutdown`` waits for
in-flight tasks *before* dispatching the lifespan shutdown event. So a server with an
open stream hangs at "Waiting for application shutdown." until something kills it —
which is what happened in practice, needing ``Stop-Process``.

The test that matters uses a **generous** graceful timeout. With a short one, a passing
result proves only that the backstop fired; the point is that the streams end
themselves, so the timeout must be long enough that relying on it would fail the test.
"""

from __future__ import annotations

import threading
import time

import httpx
import pytest

# Optional extra; see the note in test_web_turn.py. uvicorn is named explicitly because
# these tests drive a real server, not just the ASGI app.
pytest.importorskip("fastapi", reason="needs the 'web' extra")
pytest.importorskip("uvicorn", reason="needs the 'web' extra")

from ai_native_rpg.web.app import create_app
from ai_native_rpg.web.events import KEEPALIVE_SECONDS
from ai_native_rpg.web.serve import StreamAwareServer, build_server

SCENARIO = "village_disappearance"

#: Long enough that a shutdown which merely waits it out cannot look fast.
GENEROUS_TIMEOUT = 30

#: What we consider "ended promptly". Two orders of magnitude below the backstop.
PROMPT_SECONDS = 8


@pytest.fixture
def running_server():
    """A real server on a real socket, torn down at the end of the test."""
    app = create_app(dev_mode=True)
    server = build_server(
        app,
        host="127.0.0.1",
        port=8799,
        log_level="critical",
        graceful_timeout=GENEROUS_TIMEOUT,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = "http://127.0.0.1:8799"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        pytest.fail("server never came up")

    yield app, server, thread, base

    server.should_exit = True
    thread.join(timeout=20)


def _hold_stream(base: str, session_id: str) -> threading.Thread:
    """Hold an SSE stream open the way a browser tab does."""

    def read():
        try:
            with httpx.stream("GET", f"{base}/api/session/{session_id}/events", timeout=60) as s:
                for _ in s.iter_lines():
                    pass
        except httpx.HTTPError:
            pass

    t = threading.Thread(target=read, daemon=True)
    t.start()
    return t


def test_shutdown_is_prompt_with_streams_open(running_server):
    """The regression that made Ctrl+C useless on Windows."""
    app, server, thread, base = running_server

    session_id = httpx.post(f"{base}/api/session", json={"scenario": SCENARIO}).json()["session_id"]
    for _ in range(3):
        _hold_stream(base, session_id)

    # Wait until the streams have actually registered.
    for _ in range(100):
        if len(app.state.live_streams) >= 3:
            break
        time.sleep(0.05)
    assert len(app.state.live_streams) >= 3, "streams did not register"

    started = time.time()
    server.should_exit = True  # exactly what a Ctrl+C sets
    thread.join(timeout=GENEROUS_TIMEOUT + 10)
    elapsed = time.time() - started

    assert not thread.is_alive(), "server never exited"
    assert elapsed < PROMPT_SECONDS, (
        f"took {elapsed:.1f}s with a {GENEROUS_TIMEOUT}s backstop — the streams are not "
        "ending themselves, the timeout is doing the work"
    )


def test_streams_deregister_when_a_client_leaves(running_server):
    """The registry must not grow forever; a closed tab has to drop out of it.

    Cleanup lands one keepalive interval after the client goes, not instantly: the
    generator is parked in its ``KEEPALIVE_SECONDS`` wait and only discovers the
    departure when it wakes and tries to write. That is the intended cost of not
    polling, so the test allows for it rather than demanding immediacy.
    """
    app, _server, _thread, base = running_server

    session_id = httpx.post(f"{base}/api/session", json={"scenario": SCENARIO}).json()["session_id"]

    # A stream opened and abandoned inside its own client context.
    def brief():
        try:
            with httpx.stream("GET", f"{base}/api/session/{session_id}/events", timeout=5) as s:
                next(s.iter_lines())  # take the hello frame, then leave
        except (httpx.HTTPError, StopIteration):
            pass

    t = threading.Thread(target=brief, daemon=True)
    t.start()
    t.join(timeout=10)

    deadline = time.time() + KEEPALIVE_SECONDS + 10
    while app.state.live_streams and time.time() < deadline:
        time.sleep(0.1)

    assert not app.state.live_streams, (
        f"a departed client's task was still registered after {KEEPALIVE_SECONDS + 10:.0f}s"
    )


def test_static_files_are_never_cached(running_server):
    """Editable assets must revalidate, or the browser shows yesterday's sprite.

    ``StaticFiles`` sends ``etag``/``last-modified`` but no ``Cache-Control``, which lets
    a browser reuse a copy without asking. That produced a genuinely confusing report:
    ``localhost:8000`` rendered the new sprites while ``127.0.0.1:8000`` still rendered
    the old ones — the same server serving byte-identical files, but two separate cache
    origins as far as the browser is concerned.
    """
    _app, _server, _thread, base = running_server

    for path in ("/", "/static/app.js", "/static/styles.css", "/static/sprites.js"):
        response = httpx.get(f"{base}{path}", timeout=5)
        assert response.status_code == 200, path
        directive = response.headers.get("cache-control", "")
        assert "no-cache" in directive or "no-store" in directive, (
            f"{path} may be cached: Cache-Control={directive!r}"
        )


def test_server_type_is_the_stream_aware_one():
    """A plain ``uvicorn.run`` would reintroduce the hang, so pin the type.

    The override exists because lifespan cleanup provably cannot work here: uvicorn
    sends the lifespan shutdown event only after the task wait it is meant to shorten.
    """
    app = create_app(dev_mode=False)
    server = build_server(app, host="127.0.0.1", port=8798)

    assert isinstance(server, StreamAwareServer)
    # A backstop must exist, but a small one — reaching it means something is wrong.
    assert 0 < server.config.timeout_graceful_shutdown <= 10

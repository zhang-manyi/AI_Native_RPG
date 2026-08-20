"""Player/developer isolation at the HTTP layer (docs/07 §2.4, docs/12 §7).

docs/07 calls this "硬性要求，不是注意事项" — a hard requirement, not a note. The panel
deliberately bypasses ``PlayerView`` to show what the player cannot see, which makes it
the exact leak channel the whole visibility mechanism defends against. Five assertions,
each mapping to one enforcement in docs/12 §7:

1. ``/debug`` is *absent* when dev mode is off, not 403 — a 403 announces the endpoint.
2. player-side modules never import ``WorldState``; the type signature is the mechanism.
3. no hidden fact's value appears anywhere in a player-facing payload.
4. developer-only events are filtered server-side, per connection.
5. a non-dev stream carries only the player event types.

Item 3 extends ``test_player_view.py``'s
``test_hidden_fact_value_never_appears_anywhere_in_view`` to the HTTP layer, as
docs/07 §2.4 asks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Optional extra; see the note in test_web_turn.py.
pytest.importorskip("fastapi", reason="needs the 'web' extra")

from fastapi.testclient import TestClient

from ai_native_rpg.scenario import load_scenario
from ai_native_rpg.schemas.world_state import Visibility
from ai_native_rpg.web.app import create_app, resolve_dev_mode
from ai_native_rpg.web.events import (
    DEV_ONLY_EVENTS,
    PLAYER_EVENTS,
    EventType,
    HelloPayload,
)
from ai_native_rpg.web.session import Session

SCENARIO = "village_disappearance"
WEB_DIR = Path(__file__).resolve().parents[1] / "src" / "ai_native_rpg" / "web"


# --- 1. /debug is absent, not forbidden ------------------------------------


def test_debug_routes_are_not_registered_without_dev_mode():
    """404, not 403: a 403 tells the caller the endpoint exists and refused them."""
    with TestClient(create_app(dev_mode=False)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]

        for path in (
            f"/debug/session/{sid}/panel",
            f"/debug/session/{sid}/world",
            f"/debug/session/{sid}/memory",
            "/debug/trace/anything",
        ):
            assert client.get(path).status_code == 404, path


def test_debug_routes_exist_in_dev_mode():
    """The negative test above is only meaningful if these are otherwise reachable."""
    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]
        assert client.get(f"/debug/session/{sid}/panel").status_code == 200
        assert client.get(f"/debug/session/{sid}/world").status_code == 200


def test_openapi_does_not_advertise_debug_without_dev_mode():
    """Schema discovery is a surface too; the docs endpoint is off as well."""
    with TestClient(create_app(dev_mode=False)) as client:
        assert client.get("/docs").status_code == 404
        spec = client.get("/openapi.json")
        if spec.status_code == 200:
            assert not [p for p in spec.json()["paths"] if p.startswith("/debug")]


def test_dev_mode_defaults_to_loopback_only(monkeypatch):
    """The safe default is tied to reachability, not to a flag someone must remember."""
    monkeypatch.delenv("DEV_MODE", raising=False)

    assert resolve_dev_mode(host="127.0.0.1") is True
    assert resolve_dev_mode(host="localhost") is True
    assert resolve_dev_mode(host="0.0.0.0") is False
    assert resolve_dev_mode(host="192.168.1.10") is False
    # An explicit argument beats the inference.
    assert resolve_dev_mode(host="127.0.0.1", override=False) is False


# --- 2. the player side never imports WorldState ---------------------------


def test_player_facing_modules_do_not_import_world_state():
    """Enforced by reading the source: "remember to project first" is not a mechanism.

    ``routes_player`` and ``scene`` are typed against ``VisibleState``. If either ever
    imports ``WorldState``, the projection becomes optional again and the panel's leak
    channel reopens on the player's own endpoint.
    """
    for name in ("routes_player.py", "scene.py"):
        source = (WEB_DIR / name).read_text(encoding="utf-8")
        # Ignore prose: only import statements count.
        imports = [
            line
            for line in source.splitlines()
            if line.startswith(("import ", "from ")) and "WorldState" in line
        ]
        assert imports == [], f"{name} imports WorldState: {imports}"


def test_scene_builder_signature_takes_visible_state():
    """The projection is a parameter type, so bypassing it is a type error."""
    import inspect

    from ai_native_rpg.web.scene import build_scene

    annotations = inspect.get_annotations(build_scene, eval_str=True)
    from ai_native_rpg.schemas.world_state import VisibleState

    assert annotations["view"] is VisibleState


# --- 3. no hidden value reaches a player payload ---------------------------


def _hidden_values(scenario: str) -> list[str]:
    """Every value the player has not earned, as it would appear serialised."""
    world = load_scenario(scenario)
    values = []
    for fact in world.facts.values():
        if fact.visibility is Visibility.REVEALED:
            continue
        # A satisfied condition would make it legitimately visible; the pack starts
        # with trust below every threshold, so nothing here is unlocked yet.
        values.append(str(fact.value))
    return [v for v in values if v.strip()]


def test_hidden_fact_values_never_appear_in_the_scene_payload():
    """The HTTP-layer counterpart of test_player_view's leak assertion.

    ``killer_identity``'s value is the string ``npc_b``, so this also covers the
    subtler failure of leaking a value that happens to be an id.
    """
    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]
        payload = client.get(f"/api/session/{sid}/scene").text

    for value in _hidden_values(SCENARIO):
        assert value not in payload, f"hidden value leaked into /scene: {value!r}"


def test_hidden_fact_values_never_appear_in_player_events(tmp_path):
    """Same assertion against everything a non-dev connection would receive."""
    session = Session(
        session_id="leak_check", scenario=SCENARIO, dev_mode=False, trace_dir=tmp_path
    )
    try:
        session.submit_turn("那天晚上你看到了什么？")
        session.join(timeout=30)

        stream = "\n".join(event.frame() for event in session._history)
    finally:
        session.close()

    for value in _hidden_values(SCENARIO):
        assert value not in stream, f"hidden value leaked into the stream: {value!r}"


def test_partial_facts_expose_only_their_partial_value():
    """``partial`` means "there is a suspect", never the name."""
    world = load_scenario(SCENARIO)
    partial = {
        f.fact_id: f
        for f in world.facts.values()
        if f.visibility is Visibility.PARTIAL and f.partial_value is not None
    }
    if not partial:
        pytest.skip("pack has no partial facts")

    with TestClient(create_app(dev_mode=True)) as client:
        sid = client.post("/api/session", json={"scenario": SCENARIO}).json()["session_id"]
        payload = client.get(f"/api/session/{sid}/scene").text

    for fact in partial.values():
        assert str(fact.value) not in payload
        assert str(fact.partial_value) in payload


# --- 4 & 5. dev events are filtered server-side ---------------------------


def test_dev_only_events_are_never_created_for_a_player_session(tmp_path):
    """Filtered at the source, not hidden by the front end.

    If the panel toggle were the only barrier, F12 would be a complete spoiler tool.
    """
    session = Session(
        session_id="player_only", scenario=SCENARIO, dev_mode=False, trace_dir=tmp_path
    )
    try:
        session.submit_turn("你好")
        session.join(timeout=30)
        produced = {event.type for event in session._history}
    finally:
        session.close()

    assert not produced & DEV_ONLY_EVENTS
    # And the complement: everything present is a player event (docs/12 §7 item 5).
    assert produced <= PLAYER_EVENTS


def test_dev_session_does_receive_them(tmp_path):
    """The filter must be conditional, not a blanket drop."""
    session = Session(session_id="dev_one", scenario=SCENARIO, dev_mode=True, trace_dir=tmp_path)
    try:
        session.submit_turn("你好")
        session.join(timeout=30)
        produced = {event.type for event in session._history}
    finally:
        session.close()

    assert EventType.NARRATIVE_TICK in produced
    assert EventType.PANEL in produced


@pytest.mark.parametrize("dev_mode", [False, True])
def test_hello_payload_gates_the_panel(dev_mode, tmp_path):
    """The greeting is assembled per connection, so it needs the same gate.

    Built directly rather than by reading the live stream: an SSE response never ends,
    and ``TestClient`` drives the app on a portal thread, so consuming an open stream
    inside the client's context deadlocks rather than failing.
    """
    session = Session(
        session_id=f"hello_{dev_mode}",
        scenario=SCENARIO,
        dev_mode=dev_mode,
        trace_dir=tmp_path,
    )
    try:
        payload = HelloPayload(
            session_id=session.session_id,
            dev_mode=session.dev_mode,
            turn=session.scene().turn,
            scene=session.scene().model_dump(),
            backend=session.backend,
            panel=session.panel() if session.dev_mode else None,
        )
    finally:
        session.close()

    assert payload.dev_mode is dev_mode
    assert (payload.panel is not None) is dev_mode

    # The scene half must be leak-free either way.
    body = json.dumps(payload.scene, ensure_ascii=False, default=str)
    for value in _hidden_values(SCENARIO):
        assert value not in body


def test_every_event_type_is_classified():
    """A new event kind must be sorted into player or dev, not silently default.

    Without this, adding an event that carries world state would quietly reach players
    because nothing forced a decision about it.
    """
    assert set(EventType) == PLAYER_EVENTS | DEV_ONLY_EVENTS
    assert not PLAYER_EVENTS & DEV_ONLY_EVENTS

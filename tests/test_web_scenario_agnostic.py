"""The interface carries no story content (docs/12 §6.3).

A pack with one NPC, no ``narrative:`` block and no ``intro:`` block must render: empty
ledger and empty unlock board are *states*, not errors. This is the check that the page
reads its labels from the pack rather than from the village scenario — the same split
``rules.py`` had to make when its Chinese fact ids were pulled out into directives.
"""

from __future__ import annotations

import textwrap

import pytest

# Optional extra; see the note in test_web_turn.py.
pytest.importorskip("fastapi", reason="needs the 'web' extra")

from fastapi.testclient import TestClient

from ai_native_rpg.web.app import create_app
from ai_native_rpg.web.scene import SPRITE_KEYS, build_pack_display
from ai_native_rpg.web.session import Session

MINIMAL_PACK = """\
world_id: lighthouse
time_day: 1

locations:
  cliff:
    name: The Cliff Path
    description: Wind, gorse, and a long way down.

players:
  keeper:
    location: cliff

npcs:
  warden:
    name: Warden Hale
    location: cliff

npc_personas:
  warden:
    persona:
      background: Warden Hale, who has watched this coast for thirty years.
      traits:
        taciturn: 0.8
    goal:
      primary: Say as little as possible about the light
"""


@pytest.fixture
def minimal_pack(tmp_path, monkeypatch):
    """A pack with no narrative directives, no intro, one NPC, one location."""
    root = tmp_path / "scenarios"
    pack = root / "lighthouse"
    pack.mkdir(parents=True)
    (pack / "world.yaml").write_text(textwrap.dedent(MINIMAL_PACK), encoding="utf-8")

    monkeypatch.setattr("ai_native_rpg.scenario.SCENARIOS_ROOT", root)
    return "lighthouse"


def test_session_runs_a_turn_on_a_bare_pack(minimal_pack, tmp_path):
    """No directives means no paced clues — the engine still runs its other operators."""
    session = Session(session_id="bare", scenario=minimal_pack, trace_dir=tmp_path / "traces")
    try:
        # The pack, not the code, names the player and the NPC.
        assert session.player_id == "keeper"
        assert session.npc_id == "warden"
        assert session.npc_name == "Warden Hale"

        before = session.panel().beats.turn

        session.submit_turn("What happened to the light?")
        session.join(timeout=30)

        panel = session.panel()
        # Empty is a state, not a failure.
        assert panel.ledger == []
        assert panel.unlock_board == []
        # +1, not the literal value: the session already ran an opening tick before this
        # (docs/12 §4.2), so `turn` starts above 0 even on a bare pack.
        assert panel.beats.turn == before + 1
    finally:
        session.close()


def test_scene_uses_the_packs_own_labels(minimal_pack, tmp_path):
    session = Session(session_id="bare2", scenario=minimal_pack, trace_dir=tmp_path / "traces")
    try:
        scene = session.scene()
    finally:
        session.close()

    assert scene.location is not None
    assert scene.location.name == "The Cliff Path"
    assert [n.name for n in scene.npcs] == ["Warden Hale"]
    # No intro block: the renderer supplies nothing rather than detective phrasing.
    assert scene.intro == {}


def test_sprite_keys_come_from_position_not_from_the_pack():
    """Art is a front-end asset; a pack ships none and must not need to."""

    class _Npc:
        def __init__(self, name):
            self.name = name
            self.display_name = name

    display = build_pack_display(
        locations={},
        npcs={"a": _Npc("First"), "b": _Npc("Second"), "c": _Npc("Third")},
    )

    assert [display.npcs[k].sprite_key for k in ("a", "b", "c")] == list(SPRITE_KEYS[:3])


def test_http_surface_works_for_a_bare_pack(minimal_pack):
    with TestClient(create_app(dev_mode=True)) as client:
        listed = client.get("/api/scenarios").json()["scenarios"]
        assert minimal_pack in listed

        sid = client.post("/api/session", json={"scenario": minimal_pack}).json()["session_id"]

        scene = client.get(f"/api/session/{sid}/scene").json()
        assert scene["location"]["name"] == "The Cliff Path"

        panel = client.get(f"/debug/session/{sid}/panel").json()
        assert panel["ledger"] == []
        assert panel["unlock_board"] == []


def test_unknown_npc_is_rejected_with_the_available_names(minimal_pack):
    with TestClient(create_app(dev_mode=True)) as client:
        res = client.post("/api/session", json={"scenario": minimal_pack, "npc_id": "nobody"})
        assert res.status_code == 400
        assert "warden" in res.json()["detail"]

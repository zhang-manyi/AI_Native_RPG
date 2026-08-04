"""Tests for load_personas: agent-internal NPCState loaded from the scenario pack.

Personas are deliberately not part of WorldState. This loader is the seam that
keeps "what the NPC thinks" in the scenario pack while the world stays purely
objective — so the tests focus on that separation and on the cross-reference
checks that turn silent authoring mistakes into load-time errors.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.scenario import ScenarioError, load_personas, load_scenario

SCENARIO = "village_disappearance"


class TestReferencePersonas:
    def test_loads_a_persona_for_every_npc(self):
        world = load_scenario(SCENARIO)
        personas = load_personas(SCENARIO)
        assert set(personas) == set(world.npcs)

    def test_martha_persona_fields(self):
        personas = load_personas(SCENARIO)
        martha = personas["npc_a"]
        assert martha.npc_id == "npc_a"
        assert martha.persona.traits["fearful"] == pytest.approx(0.9)
        assert martha.goal.primary
        assert martha.emotion == "afraid"
        assert martha.beliefs["saw_something_that_night"] is True

    def test_loren_is_the_dishonest_one(self):
        personas = load_personas(SCENARIO)
        assert personas["npc_b"].persona.traits["honest"] < 0.5


class TestValidation:
    def _write(self, tmp_path, body: str):
        pack = tmp_path / "bad_pack"
        pack.mkdir()
        (pack / "world.yaml").write_text(body, encoding="utf-8")
        return pack

    def test_persona_for_unknown_npc_fails(self, tmp_path):
        pack = self._write(
            tmp_path,
            """
world_id: bad
locations:
  square:
    name: Square
npcs:
  npc_a:
    location: square
npc_personas:
  ghost:
    persona:
      background: nobody
    goal:
      primary: haunt
""",
        )
        with pytest.raises(ScenarioError, match="ghost"):
            load_personas(pack)

    def test_npc_without_persona_fails(self, tmp_path):
        pack = self._write(
            tmp_path,
            """
world_id: bad
locations:
  square:
    name: Square
npcs:
  npc_a:
    location: square
  npc_b:
    location: square
npc_personas:
  npc_a:
    persona:
      background: present
    goal:
      primary: talk
""",
        )
        with pytest.raises(ScenarioError, match="npc_b"):
            load_personas(pack)

    def test_trait_out_of_range_fails(self, tmp_path):
        pack = self._write(
            tmp_path,
            """
world_id: bad
locations:
  square:
    name: Square
npcs:
  npc_a:
    location: square
npc_personas:
  npc_a:
    persona:
      traits:
        honest: not_a_number
    goal:
      primary: talk
""",
        )
        with pytest.raises(ScenarioError, match="does not match the schema"):
            load_personas(pack)

    def test_empty_personas_with_npcs_fails(self, tmp_path):
        pack = self._write(
            tmp_path,
            """
world_id: bad
locations:
  square:
    name: Square
npcs:
  npc_a:
    location: square
""",
        )
        with pytest.raises(ScenarioError, match="no persona"):
            load_personas(pack)

"""Scenario pack loading.

The load step is where scenario-authoring mistakes must surface. A condition path
typo that survives loading becomes a clue that can never be revealed, with nothing
in the logs to explain it — so the loader validates every path against the world
it just built.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.scenario import ScenarioError, load_scenario
from ai_native_rpg.schemas.world_state import Visibility

SCENARIO = "village_disappearance"


class TestReferenceScenario:
    def test_loads_the_shipped_scenario(self):
        world = load_scenario(SCENARIO)
        assert world.world_id == SCENARIO

    def test_locations_are_connected_both_ways_where_declared(self):
        world = load_scenario(SCENARIO)
        assert "tavern" in world.locations["village_square"].connected_to
        assert "village_square" in world.locations["tavern"].connected_to

    def test_relationships_are_loaded(self):
        world = load_scenario(SCENARIO)
        assert world.relationships["npc_a"]["player_1"].trust == 10.0
        assert world.relationships["npc_a"]["player_1"].fear == 30.0

    def test_hidden_facts_stay_hidden_at_load(self):
        world = load_scenario(SCENARIO)
        assert world.facts["killer_identity"].visibility is Visibility.HIDDEN

    def test_conditions_are_parsed_into_models(self):
        world = load_scenario(SCENARIO)
        cond = world.facts["clue_1_witness"].reveal_condition
        assert cond is not None
        assert cond.clauses[0].path == "relationships.npc_a.player_1.trust"

    def test_player_start_location_is_loaded(self):
        world = load_scenario(SCENARIO)
        # The player starts at NPC_A's house, co-located with the witness, so
        # PlayerView surfaces her on turn one (see the pack's players: comment).
        assert world.player_locations["player_1"] == "npc_a_house"


class TestValidation:
    def _write(self, tmp_path, body: str):
        pack = tmp_path / "bad_pack"
        pack.mkdir()
        (pack / "world.yaml").write_text(body, encoding="utf-8")
        return pack

    def test_unknown_condition_path_fails_at_load(self, tmp_path):
        pack = self._write(
            tmp_path,
            """
world_id: bad
facts:
  f1:
    value: x
    visibility: hidden
    reveal_condition:
      mode: any
      clauses:
        - path: relationships.npc_typo.player_1.trust
          op: gte
          value: 40
""",
        )
        with pytest.raises(ScenarioError, match="reveal_condition"):
            load_scenario(pack)

    def test_npc_in_unknown_location_fails(self, tmp_path):
        pack = self._write(
            tmp_path,
            """
world_id: bad
locations:
  square:
    name: Square
npcs:
  npc_a:
    location: nowhere
""",
        )
        with pytest.raises(ScenarioError, match="nowhere"):
            load_scenario(pack)

    def test_dangling_location_connection_fails(self, tmp_path):
        pack = self._write(
            tmp_path,
            """
world_id: bad
locations:
  square:
    name: Square
    connected_to: [atlantis]
""",
        )
        with pytest.raises(ScenarioError, match="atlantis"):
            load_scenario(pack)

    def test_player_in_unknown_location_fails(self, tmp_path):
        pack = self._write(
            tmp_path,
            """
world_id: bad
locations:
  square:
    name: Square
players:
  player_1:
    location: void
""",
        )
        with pytest.raises(ScenarioError, match="void"):
            load_scenario(pack)

    def test_partial_fact_without_partial_value_fails(self, tmp_path):
        """A partial fact with nothing safe to show would silently render as
        invisible, which is almost certainly an authoring mistake."""
        pack = self._write(
            tmp_path,
            """
world_id: bad
facts:
  f1:
    value: secret
    visibility: partial
""",
        )
        with pytest.raises(ScenarioError, match="partial_value"):
            load_scenario(pack)

    def test_missing_pack_fails_clearly(self, tmp_path):
        with pytest.raises(ScenarioError, match="not found"):
            load_scenario(tmp_path / "does_not_exist")

    def test_hidden_fact_without_condition_is_allowed(self, tmp_path):
        """Permanently hidden facts are legitimate: they exist for the Narrative
        Engine to force-reveal, not for conditions to unlock."""
        pack = self._write(
            tmp_path,
            """
world_id: ok
facts:
  f1:
    value: secret
    visibility: hidden
""",
        )
        world = load_scenario(pack)
        assert world.facts["f1"].reveal_condition is None


class TestManagerIntegration:
    def test_loaded_world_drives_the_manager(self):
        from ai_native_rpg.world import WorldStateManager

        mgr = WorldStateManager(load_scenario(SCENARIO))
        assert mgr.get_trust("npc_a", "player_1") == 10.0
        view = mgr.player_view("player_1")
        assert "victim_name" in view.visible_facts
        assert "killer_identity" not in view.visible_facts

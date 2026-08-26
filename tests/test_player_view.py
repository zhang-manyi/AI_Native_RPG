"""PlayerView projection: the information-asymmetry mechanism.

This is the test suite that matters most for narrative correctness — a leak here
means the player is told something the story has not earned yet, and no amount of
prompt tuning downstream can undo it.
"""

from __future__ import annotations

from ai_native_rpg.schemas.world_state import Visibility
from ai_native_rpg.world.player_view import player_view

PLAYER = "player_1"
NPC_A = "npc_a"
NPC_B = "npc_b"


class TestVisibilityFiltering:
    def test_revealed_facts_are_included(self, world):
        view = player_view(world, PLAYER)
        assert view.visible_facts["victim_name"] == "磨坊主的女儿"

    def test_hidden_facts_are_excluded(self, world):
        view = player_view(world, PLAYER)
        assert "clue_1" not in view.visible_facts
        assert "loren_that_night" not in view.visible_facts

    def test_hidden_fact_value_never_appears_anywhere_in_view(self, world):
        """Stronger than key absence: the secret must not leak through any field."""
        view = player_view(world, PLAYER)
        assert NPC_B not in view.model_dump_json()

    def test_partial_visibility_shows_partial_value_only(self, world):
        world.facts["loren_that_night"].visibility = Visibility.PARTIAL
        view = player_view(world, PLAYER)
        assert view.visible_facts["loren_that_night"] == "村里有人那晚在外面"
        assert NPC_B not in view.model_dump_json()

    def test_partial_without_partial_value_is_omitted(self, world):
        """Fail closed: a partial fact with nothing safe to show reveals nothing."""
        world.facts["clue_1"].visibility = Visibility.PARTIAL
        world.facts["clue_1"].partial_value = None
        view = player_view(world, PLAYER)
        assert "clue_1" not in view.visible_facts


class TestRevealConditions:
    def test_condition_met_reveals_fact(self, world):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        view = player_view(world, PLAYER)
        assert "clue_1" in view.visible_facts

    def test_condition_unmet_keeps_fact_hidden(self, world):
        world.relationships[NPC_A][PLAYER].trust = 39.0
        view = player_view(world, PLAYER)
        assert "clue_1" not in view.visible_facts

    def test_boundary_is_inclusive_for_gte(self, world):
        world.relationships[NPC_A][PLAYER].trust = 40.0
        assert "clue_1" in player_view(world, PLAYER).visible_facts

    def test_any_mode_second_clause_can_unlock(self, world):
        """Trust stays low but the quest advanced — the OR branch must fire."""
        world.quests["investigation"].stage = 3
        view = player_view(world, PLAYER)
        assert view.visible_facts["loren_that_night"] == NPC_B

    def test_projection_does_not_mutate_world_state(self, world):
        """A reveal condition being satisfied must not persist visibility changes;
        PlayerView is a pure projection, only the Validator writes state."""
        world.quests["investigation"].stage = 3
        player_view(world, PLAYER)
        assert world.facts["loren_that_night"].visibility is Visibility.HIDDEN


class TestNPCLocations:
    def test_npc_in_same_location_is_visible(self, world):
        view = player_view(world, PLAYER)
        assert view.known_npc_locations == {NPC_A: "tavern"}

    def test_npc_elsewhere_is_not_visible(self, world):
        view = player_view(world, PLAYER)
        assert NPC_B not in view.known_npc_locations

    def test_dead_npc_is_not_reported_as_present(self, world):
        world.npcs[NPC_A].alive = False
        view = player_view(world, PLAYER)
        assert NPC_A not in view.known_npc_locations


class TestViewBasics:
    def test_carries_time_and_location(self, world):
        view = player_view(world, PLAYER)
        assert view.time_day == 1
        assert view.current_location == "tavern"

    def test_quest_stages_are_exposed(self, world):
        view = player_view(world, PLAYER)
        assert view.quest_stages == {"investigation": 0}

    def test_unknown_player_gets_empty_but_valid_view(self, world):
        view = player_view(world, "ghost_player")
        assert view.current_location is None
        assert view.known_npc_locations == {}

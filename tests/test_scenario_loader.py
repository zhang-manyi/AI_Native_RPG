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

    def test_public_notes_are_loaded(self):
        world = load_scenario(SCENARIO)
        assert world.npcs["npc_a"].public_note
        assert world.npcs["npc_b"].public_note

    def test_public_note_reveals_nothing_the_player_must_earn(self):
        """The note is what a stranger learns by asking around, nothing more.

        It exists as its own authored field precisely so it cannot be an excerpt of
        ``persona.background`` — that text runs on into what Marta saw that night, so
        slicing it would leak the mystery by construction.
        """
        world = load_scenario(SCENARIO)
        notes = " ".join(npc.public_note for npc in world.npcs.values())

        for hidden in world.facts.values():
            if hidden.visibility is Visibility.REVEALED:
                continue
            assert str(hidden.value) not in notes

    def test_backdrops_are_loaded_as_kinds(self):
        """The pack names a *kind* of place; the renderer owns what it looks like."""
        world = load_scenario(SCENARIO)
        assert world.locations["npc_a_house"].backdrop == "interior"
        assert world.locations["forest_edge"].backdrop == "forest"

    def test_backdrop_and_note_are_optional(self, tmp_path, monkeypatch):
        """A pack predating these fields must still load."""
        root = tmp_path / "scenarios" / "bare"
        root.mkdir(parents=True)
        (root / "world.yaml").write_text(
            "world_id: bare\n"
            "locations:\n  room:\n    name: Room\n"
            "players:\n  p1:\n    location: room\n"
            "npcs:\n  someone:\n    name: Someone\n    location: room\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("ai_native_rpg.scenario.SCENARIOS_ROOT", tmp_path / "scenarios")

        world = load_scenario("bare")
        assert world.locations["room"].backdrop == ""
        assert world.npcs["someone"].public_note == ""


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


class TestThePackOwnsTheSlotBudget:
    """``day_limit`` is the case's length, so the pack sets it (docs/15 §6.2).

    It had a field, a default, and no reader: a pack could declare 7 days and silently get
    4. That is the shape of bug docs/13 §11 is about, and ``StoryBeats(day_limit=1)`` in
    ``test_time_slots.py`` did not catch it because it tested the field rather than the
    authoring path. Same reasoning as ``progress_quest`` living in the pack rather than in
    ``rules.py``: swapping the story must not require touching code.
    """

    def _write(self, tmp_path, body: str):
        pack = tmp_path / "pack"
        pack.mkdir()
        (pack / "world.yaml").write_text(body, encoding="utf-8")
        return pack

    def test_a_pack_can_set_the_day_limit(self, tmp_path):
        """Deliberately not 4: the default is 4, so 4 would prove nothing."""
        pack = self._write(
            tmp_path,
            """
world_id: long_case
story_beats:
  day_limit: 7
""",
        )

        assert load_scenario(pack).story_beats.day_limit == 7

    def test_a_pack_that_says_nothing_gets_the_default(self, tmp_path):
        from ai_native_rpg.schemas.narrative import DEFAULT_DAY_LIMIT

        pack = self._write(tmp_path, "world_id: quiet\n")

        assert load_scenario(pack).story_beats.day_limit == DEFAULT_DAY_LIMIT

    def test_the_shipped_pack_declares_its_own_budget(self):
        """Written down rather than inherited, so the number lives with the story."""
        raw = (
            __import__("pathlib")
            .Path("scenarios/village_disappearance/world.yaml")
            .read_text(encoding="utf-8")
        )

        assert "day_limit: 4" in raw
        assert load_scenario(SCENARIO).story_beats.day_limit == 4

    def test_runtime_progress_cannot_be_authored(self, tmp_path):
        """A pack able to set ``turn`` or ``completed_events`` would ship a half-played game.

        The failure would look like a mystery whose opening beat never fires, because M1 is
        already recorded as complete — so this is refused rather than merged.
        """
        pack = self._write(
            tmp_path,
            """
world_id: cheating
story_beats:
  turn: 5
  completed_events: [M1_knock]
""",
        )

        with pytest.raises(ScenarioError, match="runtime progress"):
            load_scenario(pack)

    def test_the_error_names_what_is_authorable(self, tmp_path):
        pack = self._write(
            tmp_path,
            """
world_id: cheating
story_beats:
  tension: 0.9
""",
        )

        with pytest.raises(ScenarioError, match="day_limit"):
            load_scenario(pack)

    def test_story_beats_must_be_a_mapping(self, tmp_path):
        pack = self._write(tmp_path, "world_id: bad\nstory_beats: 4\n")

        with pytest.raises(ScenarioError, match="mapping"):
            load_scenario(pack)

    def test_the_starting_location_is_still_seeded_as_visited(self, tmp_path):
        """The other thing the loader puts on the beats must survive the new block."""
        pack = self._write(
            tmp_path,
            """
world_id: with_player
locations:
  room:
    name: 房间
players:
  player_1:
    location: room
story_beats:
  day_limit: 2
""",
        )

        beats = load_scenario(pack).story_beats
        assert beats.visited_locations == ["room"]
        assert beats.day_limit == 2


class TestManagerIntegration:
    def test_loaded_world_drives_the_manager(self):
        from ai_native_rpg.world import WorldStateManager

        mgr = WorldStateManager(load_scenario(SCENARIO))
        assert mgr.get_trust("npc_a", "player_1") == 10.0
        view = mgr.player_view("player_1")
        assert "victim_name" in view.visible_facts
        assert "killer_identity" not in view.visible_facts

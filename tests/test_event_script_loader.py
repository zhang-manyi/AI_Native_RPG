"""Loading a pack's ``events:`` block (docs/13 §11, docs/15 §7).

The loader validates hard for the reason the rest of ``scenario.py`` does: scenario
files are hand-written and their failure modes are silent. docs/13 §11 names the two
that already happened — a stage channel with no writer, and an ending reachable only
through a route the player could not take — and calls them the same failure: an author
wrote a path and nobody verified it went anywhere.

So an event naming a fact that does not exist, an NPC who is not in the cast, or an
outcome nothing points at is a startup error rather than a scene that mysteriously
never fires.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.scenario import ScenarioError, load_event_script

PACK = "village_disappearance"


class TestTheShippedPack:
    def test_the_village_pack_loads(self):
        script = load_event_script(PACK)

        assert script.events

    def test_the_first_batch_events_are_present(self):
        """docs/15 §8 batch one: M1 + M2 + F1.

        F1 is in the first batch deliberately — hanging foreshadowing on the scripted
        clue chain is the core motive for the whole refactor (docs/13 §9), so leaving
        it for later would mean testing the riskiest part last.
        """
        script = load_event_script(PACK)

        assert {"M1_knock", "M2_window", "F1_things_missing"} <= set(script.events)

    def test_every_fact_an_event_names_exists_in_the_pack(self):
        from ai_native_rpg.scenario import load_scenario

        script = load_event_script(PACK)
        world = load_scenario(PACK)

        assert script.fact_ids() <= set(world.facts)

    def test_every_npc_an_event_names_is_in_the_cast(self):
        from ai_native_rpg.scenario import load_scenario

        script = load_event_script(PACK)
        world = load_scenario(PACK)

        assert script.npc_ids() <= set(world.npcs)

    def test_m1_opens_on_turn_zero_so_the_game_has_a_first_beat(self):
        """An opening event that cannot trigger would leave the first turn empty.

        With no operator-level fallback (docs/13 §2.1) that silence is now permanent
        rather than filled by a stray ``foreshadow``, which makes "does anything
        trigger at turn 0" a question worth a test.
        """
        from ai_native_rpg.narrative.rules import triggerable_events
        from ai_native_rpg.scenario import load_scenario

        script = load_event_script(PACK)
        world = load_scenario(PACK)

        assert "M1_knock" in {e.event_id for e in triggerable_events(world, script)}

    def test_f1_and_m2_both_wait_on_trust_twenty(self):
        """docs/15 §6.1 has F1 arriving alongside M2, both at ``trust >= 20``."""
        script = load_event_script(PACK)

        for event_id in ("M2_window", "F1_things_missing"):
            paths = {c.path: c.value for c in script.events[event_id].trigger.clauses}
            assert any("trust" in path and value == 20 for path, value in paths.items())

    def test_f1_names_the_fact_whose_disclosure_settles_it(self):
        """docs/13 §9: what a plant waits on is authored, not model-written.

        This is the field that reconnects the ledger to the clue chain. Without it the
        generator wrote its own payoff condition, so it decided both what to bury and
        when it counted as recovered — and three runs buried a rope, a button and a
        stub of wax, none of which led anywhere.
        """
        script = load_event_script(PACK)

        assert script.events["F1_things_missing"].payoff_target


class TestCrossReferences:
    def _write(self, tmp_path, events_block: str) -> str:
        pack = tmp_path / "tiny"
        pack.mkdir()
        (pack / "world.yaml").write_text(
            "world_id: tiny\n"
            "locations:\n"
            "  home:\n"
            "    name: Home\n"
            "players:\n"
            "  player_1:\n"
            "    location: home\n"
            "npcs:\n"
            "  npc_a:\n"
            "    name: Marta\n"
            "    location: home\n"
            "facts:\n"
            "  known_fact:\n"
            "    value: something\n"
            "    visibility: revealed\n"
            f"{events_block}",
            encoding="utf-8",
        )
        return str(pack)

    def test_an_event_naming_an_unknown_fact_is_rejected(self, tmp_path):
        pack = self._write(
            tmp_path,
            "events:\n"
            "  E1:\n"
            "    operator: reveal\n"
            "    npc_id: npc_a\n"
            "    max_exchanges: 3\n"
            "    default_outcome: nothing\n"
            "    trigger:\n"
            "      clauses:\n"
            "        - path: story_beats.turn\n"
            "          op: gte\n"
            "          value: 0\n"
            "    outcomes:\n"
            "      nothing:\n"
            "        reveals_facts: [ghost_fact]\n",
        )

        with pytest.raises(ScenarioError, match="ghost_fact"):
            load_event_script(pack)

    def test_an_event_naming_an_unknown_npc_is_rejected(self, tmp_path):
        pack = self._write(
            tmp_path,
            "events:\n"
            "  E1:\n"
            "    operator: reveal\n"
            "    npc_id: nobody\n"
            "    max_exchanges: 3\n"
            "    default_outcome: nothing\n"
            "    trigger:\n"
            "      clauses:\n"
            "        - path: story_beats.turn\n"
            "          op: gte\n"
            "          value: 0\n"
            "    outcomes:\n"
            "      nothing: {}\n",
        )

        with pytest.raises(ScenarioError, match="nobody"):
            load_event_script(pack)

    def test_an_unresolvable_trigger_path_is_rejected(self, tmp_path):
        """The same check ``reveal_condition`` paths already get.

        A trigger nobody can evaluate is an event that never happens, with nothing
        anywhere to say why — precisely the silent content bug this loader exists for.
        """
        pack = self._write(
            tmp_path,
            "events:\n"
            "  E1:\n"
            "    operator: reveal\n"
            "    npc_id: npc_a\n"
            "    max_exchanges: 3\n"
            "    default_outcome: nothing\n"
            "    trigger:\n"
            "      clauses:\n"
            "        - path: story_beats.no_such_field\n"
            "          op: gte\n"
            "          value: 1\n"
            "    outcomes:\n"
            "      nothing: {}\n",
        )

        with pytest.raises(ScenarioError, match="no_such_field"):
            load_event_script(pack)

    def test_an_outcome_over_the_relationship_step_limit_is_rejected(self, tmp_path):
        """docs/15 §3.1. The Validator would refuse it at runtime; refuse it at load.

        Otherwise a rare branch ships broken and only fails the first time a player
        reaches it, which for a story event could be never.
        """
        pack = self._write(
            tmp_path,
            "events:\n"
            "  E1:\n"
            "    operator: reveal\n"
            "    npc_id: npc_a\n"
            "    max_exchanges: 3\n"
            "    default_outcome: nothing\n"
            "    trigger:\n"
            "      clauses:\n"
            "        - path: story_beats.turn\n"
            "          op: gte\n"
            "          value: 0\n"
            "    outcomes:\n"
            "      nothing:\n"
            "        relationship_changes:\n"
            "          npc_a:\n"
            "            trust: 40\n",
        )

        with pytest.raises(ScenarioError, match=r"MAX_RELATIONSHIP_STEP|exceeds"):
            load_event_script(pack)

    def test_a_pack_with_no_events_block_loads_as_an_empty_script(self, tmp_path):
        """Absence is valid: a pack ships no events and simply gets quiet turns."""
        pack = self._write(tmp_path, "")

        assert load_event_script(pack).events == {}

    def test_the_event_id_is_injected_from_the_key(self, tmp_path):
        """Authors write ids once, as the mapping key — same shape as facts and npcs."""
        pack = self._write(
            tmp_path,
            "events:\n"
            "  E1:\n"
            "    operator: reveal\n"
            "    npc_id: npc_a\n"
            "    max_exchanges: 3\n"
            "    default_outcome: nothing\n"
            "    trigger:\n"
            "      clauses:\n"
            "        - path: story_beats.turn\n"
            "          op: gte\n"
            "          value: 0\n"
            "    outcomes:\n"
            "      nothing: {}\n",
        )

        assert load_event_script(pack).events["E1"].event_id == "E1"

    def test_an_outcome_id_is_injected_from_its_key_too(self, tmp_path):
        pack = self._write(
            tmp_path,
            "events:\n"
            "  E1:\n"
            "    operator: reveal\n"
            "    npc_id: npc_a\n"
            "    max_exchanges: 3\n"
            "    default_outcome: nothing\n"
            "    trigger:\n"
            "      clauses:\n"
            "        - path: story_beats.turn\n"
            "          op: gte\n"
            "          value: 0\n"
            "    outcomes:\n"
            "      nothing: {}\n",
        )

        assert load_event_script(pack).events["E1"].outcomes["nothing"].outcome_id == "nothing"

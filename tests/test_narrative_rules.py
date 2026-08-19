"""Trigger rules: which operators the world currently permits (docs/05 §2.1).

Pure functions over ``WorldState``, so this is strict-TDD territory. The rules are
hard-coded per docs/05 §6 ("write a few rules; do not build a configuration
system yet").

The distinction these tests pin hardest is *unlockable* vs *told*. PlayerView
already auto-reveals a fact the moment its condition holds, with no action needed,
so a `reveal` operator cannot be what unlocks information. What it schedules is the
NPC voicing it: a fact whose condition is satisfied while its stored visibility is
still hidden is exactly "the player may know this, but nobody has said it out
loud". That gap is the operator's job, and it is deterministic.
"""

from __future__ import annotations

from ai_native_rpg.narrative.rules import MAX_OPEN_FORESHADOWINGS, check_triggers
from ai_native_rpg.schemas.narrative import NarrativeOperator, StoryBeats
from ai_native_rpg.schemas.world_state import Visibility, WorldState

PLAYER = "player_1"
NPC_A = "npc_a"


def _operators(world: WorldState, directives) -> list[NarrativeOperator]:
    return [c.operator for c in check_triggers(world, player_id=PLAYER, directives=directives)]


class TestRevealTriggers:
    def test_nothing_to_reveal_while_trust_is_low(self, world: WorldState, directives):
        # fixture trust is 20; clue_1 needs 40
        assert NarrativeOperator.REVEAL not in _operators(world, directives)

    def test_an_unlockable_but_untold_clue_triggers_reveal(self, world: WorldState, directives):
        world.relationships[NPC_A][PLAYER].trust = 45.0

        candidates = [
            c
            for c in check_triggers(world, player_id=PLAYER, directives=directives)
            if c.operator is NarrativeOperator.REVEAL
        ]

        assert len(candidates) == 1
        assert candidates[0].event_type == "clue_1"
        assert "trust" in candidates[0].trigger_reason

    def test_an_already_told_clue_does_not_re_trigger(self, world: WorldState, directives):
        """Once voiced, a clue stops being a candidate.

        ``reveal_fact`` sets stored visibility to ``revealed``, which is the
        deterministic record of "this was said". Without this check the same clue
        would be re-scheduled every turn forever.
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.facts["clue_1"].visibility = Visibility.REVEALED

        assert NarrativeOperator.REVEAL not in _operators(world, directives)

    def test_a_locked_clue_is_never_a_reveal_candidate(self, world: WorldState, directives):
        """The Engine cannot schedule what the world has not unlocked.

        docs/04 §3.3: no actor bypasses reveal_condition. killer_identity needs
        trust 70 or stage 3; at trust 45 it must not appear even though the
        player is clearly making progress.
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0

        told = {
            c.event_type for c in check_triggers(world, player_id=PLAYER, directives=directives)
        }

        assert "killer_identity" not in told

    def test_a_settled_ledger_entry_is_offered_as_a_payoff(self, world: WorldState, directives):
        from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
        from ai_native_rpg.schemas.narrative import Foreshadowing

        world.story_beats.open_foreshadowings["clue_1"] = Foreshadowing(
            fact_id="clue_1",
            planted_at_turn=1,
            payoff_condition=Condition(
                clauses=[
                    ConditionClause(
                        path=f"relationships.{NPC_A}.{PLAYER}.trust",
                        op=ConditionOp.GTE,
                        value=40,
                    )
                ]
            ),
        )
        world.relationships[NPC_A][PLAYER].trust = 45.0

        payoffs = [
            c for c in check_triggers(world, player_id=PLAYER, directives=directives) if c.pays_off
        ]

        assert len(payoffs) == 1
        # a payoff is a reveal that settles a debt, not a fifth operator
        assert payoffs[0].operator is NarrativeOperator.REVEAL
        assert payoffs[0].pays_off == "clue_1"

    def test_an_unsettled_ledger_entry_is_not_offered(self, world: WorldState, directives):
        from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
        from ai_native_rpg.schemas.narrative import Foreshadowing

        world.story_beats.open_foreshadowings["clue_1"] = Foreshadowing(
            fact_id="clue_1",
            planted_at_turn=1,
            payoff_condition=Condition(
                clauses=[
                    ConditionClause(
                        path=f"relationships.{NPC_A}.{PLAYER}.trust",
                        op=ConditionOp.GTE,
                        value=90,
                    )
                ]
            ),
        )

        assert not [
            c for c in check_triggers(world, player_id=PLAYER, directives=directives) if c.pays_off
        ]


def _open_loop(world: WorldState, fact_id: str, *, at_turn: int = 1) -> None:
    """Put an unsettleable loop in the ledger (trust 99 is out of reach here)."""
    from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
    from ai_native_rpg.schemas.narrative import Foreshadowing

    world.story_beats.open_foreshadowings[fact_id] = Foreshadowing(
        fact_id=fact_id,
        planted_at_turn=at_turn,
        payoff_condition=Condition(
            clauses=[
                ConditionClause(
                    path=f"relationships.{NPC_A}.{PLAYER}.trust", op=ConditionOp.GTE, value=99
                )
            ]
        ),
    )


class TestForeshadowTriggers:
    def test_an_empty_ledger_invites_a_foreshadow(self, world: WorldState, directives):
        world.story_beats = StoryBeats(turn=1)

        assert NarrativeOperator.FORESHADOW in _operators(world, directives)

    def test_several_loops_may_be_open_at_once(self, world: WorldState, directives):
        """Mystery structure wants several hints per conclusion, not one.

        The Three Clue Rule argues it from failure rates: one hint carrying one
        reveal is a chokepoint. Here the last step is an LLM choosing to use the
        hook, so the argument applies harder than it does at a table.
        """
        world.story_beats = StoryBeats(turn=4)
        _open_loop(world, "hint_one")

        assert MAX_OPEN_FORESHADOWINGS > 1
        assert NarrativeOperator.FORESHADOW in _operators(world, directives)

    def test_no_plant_once_the_ledger_is_full(self, world: WorldState, directives):
        """The cap is runaway protection, not narrative structure.

        A long ledger is what ``is_overdue`` exists to surface (docs/10 §4); the cap
        only stops debt growing without bound.
        """
        world.story_beats = StoryBeats(turn=4)
        for index in range(MAX_OPEN_FORESHADOWINGS):
            _open_loop(world, f"hint_{index}")

        assert NarrativeOperator.FORESHADOW not in _operators(world, directives)

    def test_no_two_plants_back_to_back(self, world: WorldState, directives):
        """Spacing, which also covers what the old lifetime quota did by accident.

        Without it a payoff that empties the ledger re-offers a plant on the very
        next turn, and a generator reusing the just-spent fact_id burns an LLM call
        to be rejected by ``planted_fact_id_must_be_new``.
        """
        world.story_beats = StoryBeats(turn=4)
        world.story_beats.record_operator(NarrativeOperator.FORESHADOW)

        assert NarrativeOperator.FORESHADOW not in _operators(world, directives)

    def test_a_plant_is_offered_again_once_spacing_has_passed(self, world: WorldState, directives):
        world.story_beats = StoryBeats(turn=5)
        world.story_beats.record_operator(NarrativeOperator.FORESHADOW)
        world.story_beats.record_operator(NarrativeOperator.RELIEVE)

        assert NarrativeOperator.FORESHADOW in _operators(world, directives)

    def test_planting_is_not_confined_to_the_opening_turns(self, world: WorldState, directives):
        """A later hint about the same thing is the normal case, not an edge case.

        It has to land after the player has met the earlier one, so an early-game
        window would rule out every hint but the first.
        """
        world.story_beats = StoryBeats(turn=40)

        assert NarrativeOperator.FORESHADOW in _operators(world, directives)


class TestEscalateTriggers:
    def test_progress_with_low_tension_invites_escalation(self, world: WorldState, directives):
        world.quests["investigation"].stage = 2
        world.story_beats = StoryBeats(turn=4, tension=0.1)

        assert NarrativeOperator.ESCALATE in _operators(world, directives)

    def test_no_escalation_when_tension_is_already_high(self, world: WorldState, directives):
        world.quests["investigation"].stage = 2
        world.story_beats = StoryBeats(turn=4, tension=0.9)

        assert NarrativeOperator.ESCALATE not in _operators(world, directives)

    def test_no_escalation_before_the_player_has_made_progress(self, world: WorldState, directives):
        world.story_beats = StoryBeats(turn=4, tension=0.0)

        assert NarrativeOperator.ESCALATE not in _operators(world, directives)

    def test_the_ceiling_rises_with_investigation_stage(self, world: WorldState, directives):
        """Early progress buys a little tension; late progress buys a lot.

        A flat ceiling was the first design and it stranded content: tension
        plateaued just above the constant, so any fact gated higher could never
        unlock. The stage-indexed ceiling is what keeps those thresholds reachable.
        """
        world.story_beats = StoryBeats(turn=4, tension=0.5)

        world.quests["investigation"].stage = 1
        assert NarrativeOperator.ESCALATE not in _operators(world, directives)

        world.quests["investigation"].stage = 2
        assert NarrativeOperator.ESCALATE in _operators(world, directives)

    def test_the_top_stage_can_reach_full_tension(self, world: WorldState, directives):
        # Without this the scenario's tension-gated fact would be dead content.
        world.quests["investigation"].stage = 3
        world.story_beats = StoryBeats(turn=4, tension=0.8)

        assert NarrativeOperator.ESCALATE in _operators(world, directives)

    def test_a_stage_beyond_the_table_does_not_crash(self, world: WorldState, directives):
        world.quests["investigation"].stage = 99
        world.story_beats = StoryBeats(turn=4, tension=0.5)

        assert NarrativeOperator.ESCALATE in _operators(world, directives)


class TestReverseTriggers:
    def _threat_visible(self, world: WorldState) -> None:
        """Make the reversal fact knowable outright, skipping its trust/fear gate."""
        world.facts["npc_a_threatened"] = world.facts["npc_a_threatened"].model_copy(
            update={"visibility": Visibility.REVEALED}
        )

    def test_the_threat_becoming_known_invites_a_reverse(self, world: WorldState, directives):
        self._threat_visible(world)

        assert NarrativeOperator.REVERSE in _operators(world, directives)

    def test_reverse_does_not_fire_before_the_threat_is_known(self, world: WorldState, directives):
        assert NarrativeOperator.REVERSE not in _operators(world, directives)

    def test_reverse_fires_only_once(self, world: WorldState, directives):
        """A reverse re-reads known information, so nothing stops it recurring.

        Unlike `reveal` (which flips a fact and thereby retires its own trigger) a
        reverse changes no visibility. Without the one-shot marker it would be a
        candidate on every remaining turn of the game.
        """
        self._threat_visible(world)
        world.story_beats.spend_one_shot("reverse:npc_a_threatened")

        assert NarrativeOperator.REVERSE not in _operators(world, directives)


class TestCandidateShape:
    def test_every_candidate_carries_a_tag_reason_and_constraints(
        self, world: WorldState, directives
    ):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.quests["investigation"].stage = 2
        world.story_beats = StoryBeats(turn=3, tension=0.1)

        candidates = check_triggers(world, player_id=PLAYER, directives=directives)

        assert candidates
        for candidate in candidates:
            assert candidate.preference_tag
            assert candidate.trigger_reason
            # docs/10 §2.3: the generator is told explicitly what it may not say.
            assert candidate.constraints
            assert 0.0 <= candidate.intensity <= 1.0

    def test_constraints_never_carry_a_hidden_facts_value(self, world: WorldState, directives):
        """Prohibitions name the secret; they must not quote it.

        Same stance as tools.py: text that never enters the prompt cannot be
        talked out of the model. A constraint reading "do not reveal that the
        killer is npc_b" would hand over the answer while forbidding it.
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.quests["investigation"].stage = 2
        world.story_beats = StoryBeats(turn=3, tension=0.1)
        secret = str(world.facts["killer_identity"].value)

        for candidate in check_triggers(world, player_id=PLAYER, directives=directives):
            for constraint in candidate.constraints:
                assert secret not in constraint

    def test_rules_do_not_mutate_the_world(self, world: WorldState, directives):
        before = world.model_dump_json()

        check_triggers(world, player_id=PLAYER, directives=directives)

        assert world.model_dump_json() == before

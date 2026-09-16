"""Resolving a player response into an outcome, and applying it (docs/15 §3, docs/13 §3).

The chain this pins: an option (or a free-text reply classified as one) resolves
through the three-band check into exactly one authored outcome, and that outcome's
consequences reach the world as ordinary Action Proposals.

Deterministic except for the middle band, so strict TDD applies (docs/09 §5). The
random band is tested by seeding the RNG rather than by asserting a distribution: what
matters for a trace is that the same seed replays the same result (docs/07).

Nothing here lets a model choose consequences. Classification picks *which authored
outcome* a reply matches; the outcome's effects were written by the author, because
outcomes move the values that gate ``reveal_condition`` and docs/04 §3.3 lets no actor
route around that.
"""

from __future__ import annotations

import random

from ai_native_rpg.narrative.resolution import (
    apply_outcome,
    resolve_option,
    resolve_out_of_patience,
)
from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
from ai_native_rpg.schemas.events import (
    FLAG_PROBED_ONCE,
    CheckBand,
    EventDefinition,
    EventOption,
    EventOutcome,
    OptionCheck,
    OptionTag,
)
from ai_native_rpg.schemas.narrative import NarrativeOperator
from ai_native_rpg.schemas.world_state import Visibility, WorldState
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"
NPC_A = "npc_a"


def _event(**overrides) -> EventDefinition:
    spec: dict = {
        "event_id": "M2_window",
        "operator": NarrativeOperator.REVEAL,
        "trigger": Condition(
            clauses=[
                ConditionClause(
                    path=f"relationships.{NPC_A}.{PLAYER}.trust", op=ConditionOp.GTE, value=20
                )
            ]
        ),
        "npc_id": NPC_A,
        "outcomes": {
            "softened": EventOutcome(
                outcome_id="softened",
                summary="她松一点",
                relationship_changes={NPC_A: {"trust": 8}},
            ),
            "half_told": EventOutcome(
                outcome_id="half_told",
                summary="她说了一半",
                relationship_changes={NPC_A: {"trust": 5, "fear": 5}},
            ),
            "shut_down": EventOutcome(
                outcome_id="shut_down",
                summary="她退回门后",
                relationship_changes={NPC_A: {"trust": -8, "fear": 8}},
                sets_flags=[FLAG_PROBED_ONCE],
            ),
            "deflected": EventOutcome(outcome_id="deflected", summary="她转了话题"),
        },
        "default_outcome": "deflected",
        "max_exchanges": 4,
        "options": [
            EventOption(
                option_id="goodwill",
                tag=OptionTag.GOODWILL,
                text="你不用一个人扛着",
                on_success="softened",
            ),
            EventOption(
                option_id="press",
                tag=OptionTag.PRESS,
                text="那晚你为什么一直没睡",
                check=OptionCheck(npc_id=NPC_A, dimension="trust", threshold=30),
                on_success="half_told",
                on_failure="deflected",
            ),
            EventOption(
                option_id="probe",
                tag=OptionTag.PROBE,
                text="我听说那晚有人看见你了",
                check=OptionCheck(npc_id=NPC_A, dimension="trust", threshold=35),
                on_success="half_told",
                on_failure="shut_down",
            ),
        ],
    }
    spec.update(overrides)
    return EventDefinition.model_validate(spec)


class TestUncheckedOptions:
    def test_goodwill_always_lands_its_outcome(self, world: WorldState):
        """docs/15 §2: [示好] differs in amplitude, never in success."""
        event = _event()

        resolution = resolve_option(
            event, "goodwill", world, player_id=PLAYER, rng=random.Random(0)
        )

        assert resolution.outcome_id == "softened"
        assert resolution.band is CheckBand.CERTAIN_SUCCESS
        assert resolution.checked is False

    def test_goodwill_succeeds_even_at_hostile_trust(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = -50.0
        event = _event()

        assert resolve_option(
            event, "goodwill", world, player_id=PLAYER, rng=random.Random(0)
        ).outcome_id == ("softened")


class TestCheckedOptions:
    def test_far_below_the_threshold_takes_the_failure_branch(self, world: WorldState):
        # fixture trust 20 against a probe threshold of 35: a 15-point gap
        event = _event()

        resolution = resolve_option(event, "probe", world, player_id=PLAYER, rng=random.Random(0))

        assert resolution.band is CheckBand.CERTAIN_FAILURE
        assert resolution.outcome_id == "shut_down"

    def test_far_above_the_threshold_takes_the_success_branch(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 60.0
        event = _event()

        resolution = resolve_option(event, "probe", world, player_id=PLAYER, rng=random.Random(0))

        assert resolution.band is CheckBand.CERTAIN_SUCCESS
        assert resolution.outcome_id == "half_told"

    def test_inside_the_band_the_same_seed_replays_the_same_result(self, world: WorldState):
        """docs/07 requires a replayable trace, so the roll has to be seedable."""
        world.relationships[NPC_A][PLAYER].trust = 35.0
        event = _event()

        first = resolve_option(event, "probe", world, player_id=PLAYER, rng=random.Random(1234))
        again = resolve_option(event, "probe", world, player_id=PLAYER, rng=random.Random(1234))

        assert first.band is CheckBand.RANDOM
        assert first.outcome_id == again.outcome_id

    def test_both_branches_are_reachable_inside_the_band(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 35.0
        event = _event()

        seen = {
            resolve_option(
                event, "probe", world, player_id=PLAYER, rng=random.Random(seed)
            ).outcome_id
            for seed in range(40)
        }

        assert seen == {"half_told", "shut_down"}

    def test_a_failed_probe_raises_the_threshold_for_the_next_one(self, world: WorldState):
        """docs/15 §4 M2 → M3: the penalty changes the shape of the road ahead.

        The probe's threshold is 35 with a ±12 band, so trust 48 clears it outright.
        Once ``probed_once`` is set the threshold is 45 and 48 falls back inside the
        band. The player did not get weaker — this one road got narrower.
        """
        world.relationships[NPC_A][PLAYER].trust = 48.0
        event = _event()

        assert (
            resolve_option(event, "probe", world, player_id=PLAYER, rng=random.Random(0)).band
            is CheckBand.CERTAIN_SUCCESS
        )

        world.story_beats.raise_flag(FLAG_PROBED_ONCE)

        assert (
            resolve_option(event, "probe", world, player_id=PLAYER, rng=random.Random(0)).band
            is CheckBand.RANDOM
        )

    def test_the_penalty_leaves_other_tags_alone(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 44.0
        world.story_beats.raise_flag(FLAG_PROBED_ONCE)
        event = _event()

        assert resolve_option(
            event, "press", world, player_id=PLAYER, rng=random.Random(0)
        ).band is (CheckBand.CERTAIN_SUCCESS)

    def test_the_band_comes_from_the_tag(self, world: WorldState):
        """[试探] ±12 vs [追问] ±8 (docs/15 §3), so the same gap resolves differently.

        Trust 20 sits 10 below the press threshold of 30 — outside its ±8 band, so a
        certain failure — while the probe's ±12 would have made a 10-point gap random.
        """
        event = _event()

        assert resolve_option(
            event, "press", world, player_id=PLAYER, rng=random.Random(0)
        ).band is (CheckBand.CERTAIN_FAILURE)


class TestUnknownAndDefaultResolution:
    def test_an_unrecognised_option_falls_to_the_default_outcome(self, world: WorldState):
        """The id may come from a model classifying free text (docs/13 §3).

        A label nobody wrote means "nothing recognisable happened", which is precisely
        the default outcome — not a crash, and not a guess at what the player meant.
        """
        event = _event()

        resolution = resolve_option(
            event, "improvised_nonsense", world, player_id=PLAYER, rng=random.Random(0)
        )

        assert resolution.outcome_id == "deflected"
        assert resolution.checked is False
        assert resolution.option_id is None

    def test_running_out_of_patience_lands_the_default_outcome(self, world: WorldState):
        """docs/13 §3.1: the limit ends the event; it never makes the NPC volunteer."""
        event = _event()

        assert resolve_out_of_patience(event).outcome_id == "deflected"


class TestApplyingOutcomes:
    def test_relationship_changes_reach_the_world(self, world: WorldState):
        manager = WorldStateManager(world)
        event = _event()

        results = apply_outcome(
            manager, event=event, outcome=event.outcomes["half_told"], player_id=PLAYER
        )

        assert all(r.approved for r in results), [r.reason for r in results]
        rel = manager.get_relationship(NPC_A, PLAYER)
        assert (rel.trust, rel.fear) == (25.0, 15.0)

    def test_a_negative_change_is_applied_as_written(self, world: WorldState):
        manager = WorldStateManager(world)
        event = _event()

        apply_outcome(manager, event=event, outcome=event.outcomes["shut_down"], player_id=PLAYER)

        rel = manager.get_relationship(NPC_A, PLAYER)
        assert (rel.trust, rel.fear) == (12.0, 18.0)

    def test_flags_are_raised_through_the_write_path(self, world: WorldState):
        manager = WorldStateManager(world)
        event = _event()

        apply_outcome(manager, event=event, outcome=event.outcomes["shut_down"], player_id=PLAYER)

        assert manager.snapshot().story_beats.has_flag(FLAG_PROBED_ONCE)

    def test_an_outcome_that_changes_nothing_still_reports_cleanly(self, world: WorldState):
        manager = WorldStateManager(world)
        event = _event()

        results = apply_outcome(
            manager, event=event, outcome=event.outcomes["deflected"], player_id=PLAYER
        )

        assert all(r.approved for r in results)
        assert manager.get_trust(NPC_A, PLAYER) == 20.0

    def test_revealing_a_fact_still_obeys_its_reveal_condition(self, world: WorldState):
        """docs/04 §3.3: an outcome names what to say, it does not grant permission.

        The whole information-asymmetry mechanism rests on no actor bypassing
        ``reveal_condition``, and an authored outcome is an actor like any other.
        """
        manager = WorldStateManager(world)
        event = _event(
            outcomes={
                "told": EventOutcome(outcome_id="told", reveals_facts=["clue_1"]),
                "quiet": EventOutcome(outcome_id="quiet"),
            },
            default_outcome="quiet",
            options=[],
        )

        results = apply_outcome(
            manager, event=event, outcome=event.outcomes["told"], player_id=PLAYER
        )

        # trust is 20; clue_1 needs 40
        assert not any(r.approved for r in results)
        assert manager.snapshot().facts["clue_1"].visibility is Visibility.HIDDEN

    def test_a_reveal_lands_once_the_condition_holds(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        manager = WorldStateManager(world)
        event = _event(
            outcomes={
                "told": EventOutcome(outcome_id="told", reveals_facts=["clue_1"]),
                "quiet": EventOutcome(outcome_id="quiet"),
            },
            default_outcome="quiet",
            options=[],
        )

        results = apply_outcome(
            manager, event=event, outcome=event.outcomes["told"], player_id=PLAYER
        )

        assert all(r.approved for r in results), [r.reason for r in results]
        assert manager.snapshot().facts["clue_1"].visibility is Visibility.REVEALED

    def test_goodwill_amplitude_follows_the_current_trust_curve(self, world: WorldState):
        """docs/15 §3.2, and §6.1 is computed with this curve rather than with §4's
        per-event numbers — that recomputation is what made the slot budget work."""
        manager = WorldStateManager(world)
        event = _event(
            outcomes={
                "opened": EventOutcome(outcome_id="opened", scales_with_current_trust=True),
                "quiet": EventOutcome(outcome_id="quiet"),
            },
            default_outcome="quiet",
            options=[],
        )

        apply_outcome(manager, event=event, outcome=event.outcomes["opened"], player_id=PLAYER)

        # trust 20 is in the "< 25" band, so +8 (docs/15 §6.1's first row)
        assert manager.get_trust(NPC_A, PLAYER) == 28.0

    def test_the_curve_accelerates_on_a_later_application(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 30.0
        manager = WorldStateManager(world)
        event = _event(
            outcomes={
                "opened": EventOutcome(outcome_id="opened", scales_with_current_trust=True),
                "quiet": EventOutcome(outcome_id="quiet"),
            },
            default_outcome="quiet",
            options=[],
        )

        apply_outcome(manager, event=event, outcome=event.outcomes["opened"], player_id=PLAYER)

        assert manager.get_trust(NPC_A, PLAYER) == 42.0

    def test_tension_changes_reach_story_beats(self, world: WorldState):
        manager = WorldStateManager(world)
        event = _event(
            outcomes={
                "noticed": EventOutcome(outcome_id="noticed", tension_change=0.2),
                "quiet": EventOutcome(outcome_id="quiet"),
            },
            default_outcome="quiet",
            options=[],
        )

        results = apply_outcome(
            manager, event=event, outcome=event.outcomes["noticed"], player_id=PLAYER
        )

        assert all(r.approved for r in results), [r.reason for r in results]
        assert manager.snapshot().story_beats.tension == 0.2

    def test_every_effect_travels_as_a_validated_proposal(self, world: WorldState):
        """docs/04: the Validator is the only write path, so nothing may skip it.

        Checked by handing the Manager a rule set that rejects everything: if any
        effect reached the world by another route, the values would still move.
        """
        from ai_native_rpg.world.validator import Rule, RuleOutcome

        reject_all = (
            Rule("reject_all", lambda p, s: RuleOutcome.failed("no writes in this test")),
        )
        manager = WorldStateManager(world, rules=reject_all)
        event = _event()

        results = apply_outcome(
            manager, event=event, outcome=event.outcomes["shut_down"], player_id=PLAYER
        )

        assert results and not any(r.approved for r in results)
        rel = manager.get_relationship(NPC_A, PLAYER)
        assert (rel.trust, rel.fear) == (20.0, 10.0)
        assert not manager.snapshot().story_beats.has_flag(FLAG_PROBED_ONCE)

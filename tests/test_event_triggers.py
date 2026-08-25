"""Event candidates: what the script permits right now (docs/13 §2, docs/15 §6.2).

This replaces the operator candidates the trigger rules used to produce. docs/13 §2.1
is explicit that operator-level candidates are all absorbed into events and that no
operator-sized beat may be emitted when no event qualifies — a filler beat is filler
whatever its size, and "nothing happens" is the correct outcome the architecture
already had a place for (docs/05 §3).

Pure functions over authored data plus ``WorldState``, so strict TDD (docs/09 §5).

The property pinned hardest is the one docs/15 §6.2 was written to prevent: a trigger
is a **hard** ``Condition``. At trust 38 a ``trust >= 40`` event does not fire, is not
"nearly fired", and gets no random band. The band belongs to option checks only.
"""

from __future__ import annotations

from ai_native_rpg.narrative.rules import check_triggers, triggerable_events
from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
from ai_native_rpg.schemas.events import (
    EventDefinition,
    EventOption,
    EventOutcome,
    EventScript,
    OptionCheck,
    OptionTag,
)
from ai_native_rpg.schemas.narrative import NarrativeOperator
from ai_native_rpg.schemas.world_state import WorldState

PLAYER = "player_1"
NPC_A = "npc_a"


def _trust_at_least(value: int) -> Condition:
    return Condition(
        clauses=[
            ConditionClause(
                path=f"relationships.{NPC_A}.{PLAYER}.trust", op=ConditionOp.GTE, value=value
            )
        ]
    )


def _completed(event_id: str) -> ConditionClause:
    return ConditionClause(
        path="story_beats.completed_events", op=ConditionOp.CONTAINS, value=event_id
    )


def _event(event_id: str, *, trigger: Condition, **overrides) -> EventDefinition:
    spec: dict = {
        "event_id": event_id,
        "operator": NarrativeOperator.REVEAL,
        "trigger": trigger,
        "npc_id": NPC_A,
        "outcomes": {
            "told": EventOutcome(outcome_id="told", relationship_changes={NPC_A: {"trust": 8}}),
            "refused": EventOutcome(outcome_id="refused"),
        },
        "default_outcome": "refused",
        "max_exchanges": 4,
    }
    spec.update(overrides)
    return EventDefinition.model_validate(spec)


def _script(*events: EventDefinition) -> EventScript:
    return EventScript(events={e.event_id: e for e in events})


class TestHardTriggers:
    def test_an_event_whose_condition_holds_is_offered(self, world: WorldState):
        # fixture trust is 20
        script = _script(_event("M1", trigger=_trust_at_least(20)))

        assert [e.event_id for e in triggerable_events(world, script)] == ["M1"]

    def test_an_event_whose_condition_fails_is_not_offered(self, world: WorldState):
        script = _script(_event("M3", trigger=_trust_at_least(40)))

        assert triggerable_events(world, script) == []

    def test_a_trigger_two_points_short_simply_does_not_fire(self, world: WorldState):
        """docs/15 §6.2's worked example, and the reason that section exists.

        The slot-budget check hinges on M3 being unreachable at trust 38: no band, no
        roll, no "close enough". Conflating this with the option check's ±8 would make
        every authored threshold behave differently from the script.
        """
        world.relationships[NPC_A][PLAYER].trust = 38.0
        script = _script(_event("M3", trigger=_trust_at_least(40)))

        assert triggerable_events(world, script) == []

        world.relationships[NPC_A][PLAYER].trust = 40.0
        assert [e.event_id for e in triggerable_events(world, script)] == ["M3"]

    def test_a_malformed_trigger_does_not_fire_and_does_not_raise(self, world: WorldState):
        """A broken path must not take the turn down.

        The loud failure belongs to the loader, which rejects unresolvable paths at
        startup — the same division ``_is_unlockable`` already uses.
        """
        script = _script(
            _event(
                "M_broken",
                trigger=Condition(
                    clauses=[ConditionClause(path="nope.not.a.path", op=ConditionOp.GTE, value=1)]
                ),
            )
        )

        assert triggerable_events(world, script) == []

    def test_dependencies_chain_through_completed_events(self, world: WorldState):
        """M2 needs trust *and* M1 done (docs/15 §4), which is an ``all`` condition."""
        script = _script(
            _event(
                "M2",
                trigger=Condition(
                    mode="all",
                    clauses=[
                        ConditionClause(
                            path=f"relationships.{NPC_A}.{PLAYER}.trust",
                            op=ConditionOp.GTE,
                            value=20,
                        ),
                        _completed("M1"),
                    ],
                ),
            )
        )

        assert triggerable_events(world, script) == []

        world.story_beats.open_event("M1", max_exchanges=4)
        world.story_beats.finish_event()

        assert [e.event_id for e in triggerable_events(world, script)] == ["M2"]


class TestReTriggering:
    def test_a_closed_event_never_triggers_again(self, world: WorldState):
        """docs/15 §3.3: closure is the permanent kind of ending."""
        script = _script(_event("M3", trigger=_trust_at_least(20)))
        world.story_beats.open_event("M3", max_exchanges=4)
        world.story_beats.finish_event(close=True)

        assert triggerable_events(world, script) == []

    def test_a_completed_event_is_not_re_offered_by_default(self, world: WorldState):
        """Otherwise a satisfied trigger would re-select the same event every turn.

        docs/15 §4 wants M1 retryable *next slot*, and the slot clock lands in the
        next batch; until it exists "repeatable" would mean "every single turn", which
        is the filler loop this layer replaced. See ``EventDefinition.repeatable``.
        """
        script = _script(_event("M1", trigger=_trust_at_least(20)))
        world.story_beats.open_event("M1", max_exchanges=4)
        world.story_beats.finish_event()

        assert triggerable_events(world, script) == []

    def test_an_event_marked_repeatable_is_re_offered(self, world: WorldState):
        script = _script(_event("S_gossip", trigger=_trust_at_least(20), repeatable=True))
        world.story_beats.open_event("S_gossip", max_exchanges=4)
        world.story_beats.finish_event()

        assert [e.event_id for e in triggerable_events(world, script)] == ["S_gossip"]

    def test_the_active_event_is_not_offered_as_a_new_candidate(self, world: WorldState):
        """One conversation at a time (docs/13 §5.2); continuing is not re-triggering."""
        script = _script(_event("M1", trigger=_trust_at_least(20), repeatable=True))
        world.story_beats.open_event("M1", max_exchanges=4)

        assert triggerable_events(world, script) == []


class TestCandidateShape:
    def test_a_candidate_carries_the_events_operator_and_ranking_inputs(self, world: WorldState):
        script = _script(
            _event(
                "F1",
                trigger=_trust_at_least(20),
                operator=NarrativeOperator.FORESHADOW,
                intensity=0.4,
                preference_tag="intrigue",
                payoff_target="ella_whereabouts",
            )
        )

        candidates = check_triggers(world, player_id=PLAYER, script=script)

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.event_id == "F1"
        # The operator survives as *how it is voiced* (docs/13 §2), which is what the
        # pacing cooldowns in the Controller still act on.
        assert candidate.operator is NarrativeOperator.FORESHADOW
        assert candidate.intensity == 0.4
        assert candidate.preference_tag == "intrigue"
        assert candidate.trigger_reason

    def test_candidates_carry_the_packs_universal_constraints(self, world: WorldState, directives):
        script = _script(_event("M1", trigger=_trust_at_least(20), constraints=["不要提洛伦"]))

        candidate = check_triggers(world, player_id=PLAYER, script=script, directives=directives)[0]

        assert "不要提洛伦" in candidate.constraints
        for universal in directives.universal_constraints:
            assert universal in candidate.constraints

    def test_no_script_means_no_candidates(self, world: WorldState):
        """docs/13 §2.1: with no event qualifying, the turn is quiet.

        No operator-level fallback beat. That degradation is what made ``foreshadow``
        the filler for empty turns, occupying the slot ``relieve`` was designed for.
        """
        assert check_triggers(world, player_id=PLAYER, script=None) == []
        assert check_triggers(world, player_id=PLAYER, script=EventScript()) == []

    def test_an_options_preference_tags_do_not_override_the_events(self, world: WorldState):
        """The event is what gets ranked; options are what the player does inside it."""
        script = _script(
            _event(
                "M2",
                trigger=_trust_at_least(20),
                preference_tag="social",
                options=[
                    EventOption(
                        option_id="probe",
                        tag=OptionTag.PROBE,
                        text="我听说那晚有人看见你了",
                        check=OptionCheck(npc_id=NPC_A, dimension="trust", threshold=35),
                        on_success="told",
                        on_failure="refused",
                    )
                ],
            )
        )

        assert check_triggers(world, player_id=PLAYER, script=script)[0].preference_tag == "social"

    def test_rules_do_not_mutate_the_world(self, world: WorldState):
        script = _script(_event("M1", trigger=_trust_at_least(20)))
        before = world.model_dump_json()

        check_triggers(world, player_id=PLAYER, script=script)

        assert world.model_dump_json() == before

    def test_candidates_keep_script_order_so_selection_is_reproducible(self, world: WorldState):
        """docs/07: a replayed trace must select the same beat.

        The Controller breaks ties with a stable ``max``, which only helps if the
        candidate list arrives in a fixed order.
        """
        script = _script(
            _event("M1", trigger=_trust_at_least(10)),
            _event("M2", trigger=_trust_at_least(15)),
            _event("F1", trigger=_trust_at_least(20)),
        )

        ids = [c.event_id for c in check_triggers(world, player_id=PLAYER, script=script)]

        assert ids == ["M1", "M2", "F1"]

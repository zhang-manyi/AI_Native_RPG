"""StoryBeats and the foreshadowing ledger (docs/04 §4.1, docs/10 §4).

Deterministic layer, so these were written before the schema existed. Three
properties carry weight beyond "the fields exist":

* ``recent_operators`` is written *every* turn, ``relieve`` included, so the
  pacing rules can tell "two reveals in a row" from "a reveal on turn 3 and
  another on turn 9". A list of only-the-firings cannot express that.
* ``story_beats`` has a default, so a world snapshot written by slice 1/2 still
  loads. The migration cost of adding narrative state must be zero.
* ``tension`` is bounded, because it is a condition path scenario authors compare
  against and an out-of-range value would make thresholds meaningless.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
from ai_native_rpg.schemas.narrative import (
    RECENT_OPERATOR_WINDOW,
    EventCandidate,
    Foreshadowing,
    NarrativeOperator,
    PlayerProfile,
    StoryBeats,
)
from ai_native_rpg.schemas.world_state import WorldState


def _condition(value: int = 40) -> Condition:
    return Condition(
        clauses=[
            ConditionClause(
                path="relationships.npc_a.player_1.trust", op=ConditionOp.GTE, value=value
            )
        ]
    )


class TestStoryBeats:
    def test_starts_at_chapter_one_turn_zero_no_tension(self):
        beats = StoryBeats()
        assert (beats.chapter, beats.turn, beats.tension) == (1, 0, 0.0)
        assert beats.open_foreshadowings == {}
        assert beats.recent_operators == []

    @pytest.mark.parametrize("bad", [-0.1, 1.1])
    def test_tension_is_bounded_to_the_unit_interval(self, bad):
        # Authors write `story_beats.tension >= 0.6` thresholds against this; a
        # value outside [0,1] would silently make every such threshold unreachable
        # or always-true.
        with pytest.raises(ValidationError):
            StoryBeats(tension=bad)

    def test_operator_history_keeps_the_last_window_entries(self):
        beats = StoryBeats()
        for i in range(RECENT_OPERATOR_WINDOW + 3):
            beats.record_operator(NarrativeOperator.RELIEVE if i % 2 else NarrativeOperator.REVEAL)
        assert len(beats.recent_operators) == RECENT_OPERATOR_WINDOW
        # the window holds the *most recent* entries, in chronological order
        assert beats.recent_operators[-1] == NarrativeOperator.RELIEVE.value

    def test_quiet_turns_are_recorded_as_relieve(self):
        """A turn where nothing fired is a `relieve` beat, not a gap.

        docs/10 §2.1 defines `relieve` as "no operator this turn". Recording it
        keeps positional distance meaningful: without it, a reveal on turn 3 and
        one on turn 9 would look adjacent to the pacing rules.
        """
        beats = StoryBeats()
        beats.record_operator(NarrativeOperator.REVEAL)
        beats.record_operator(NarrativeOperator.RELIEVE)
        beats.record_operator(NarrativeOperator.RELIEVE)
        assert beats.recent_operators == ["reveal", "relieve", "relieve"]
        assert beats.last_operator == NarrativeOperator.RELIEVE.value

    def test_last_operator_is_none_before_any_turn(self):
        assert StoryBeats().last_operator is None

    def test_turns_since_reports_distance_from_the_end(self):
        beats = StoryBeats()
        for op in (NarrativeOperator.REVEAL, NarrativeOperator.RELIEVE, NarrativeOperator.RELIEVE):
            beats.record_operator(op)
        # the most recent turn is distance 1, so "not two reveals in a row" reads
        # as turns_since(REVEAL) > 1
        assert beats.turns_since(NarrativeOperator.REVEAL) == 3
        assert beats.turns_since(NarrativeOperator.RELIEVE) == 1

    def test_turns_since_an_operator_that_never_fired_is_none(self):
        beats = StoryBeats()
        beats.record_operator(NarrativeOperator.RELIEVE)
        assert beats.turns_since(NarrativeOperator.ESCALATE) is None


class TestForeshadowingLedger:
    def test_entry_records_what_the_panel_and_eval_need(self):
        entry = Foreshadowing(
            fact_id="marta_was_awake",
            planted_at_turn=3,
            payoff_condition=_condition(),
            overdue_after_turns=8,
            note="玛尔塔那晚没睡",
        )
        assert entry.fact_id == "marta_was_awake"
        assert entry.planted_at_turn == 3

    def test_debt_and_overdue_are_computed_from_the_current_turn(self):
        entry = Foreshadowing(
            fact_id="f", planted_at_turn=3, payoff_condition=_condition(), overdue_after_turns=8
        )
        assert entry.turns_owed(current_turn=12) == 9
        assert entry.is_overdue(current_turn=12) is True
        assert entry.is_overdue(current_turn=10) is False

    def test_payoff_condition_survives_a_round_trip_through_json(self):
        # The ledger lives inside WorldState, which is persisted as JSON. A
        # condition that does not survive the round trip cannot be re-evaluated
        # after a reload, which is the whole point of it being structured.
        beats = StoryBeats(
            open_foreshadowings={
                "f": Foreshadowing(fact_id="f", planted_at_turn=1, payoff_condition=_condition(55))
            }
        )
        restored = StoryBeats.model_validate_json(beats.model_dump_json())
        clause = restored.open_foreshadowings["f"].payoff_condition.clauses[0]
        assert (clause.path, clause.op, clause.value) == (
            "relationships.npc_a.player_1.trust",
            ConditionOp.GTE,
            55,
        )


class TestWorldStateCarriesBeats:
    def test_world_state_has_beats_by_default(self):
        assert WorldState(world_id="w").story_beats == StoryBeats()

    def test_a_snapshot_without_beats_still_loads(self):
        """Slice 1/2 wrote worlds with no ``story_beats`` key.

        Adding narrative state must not invalidate saved worlds, so the field
        defaults rather than being required.
        """
        legacy = '{"world_id": "w", "time_day": 2}'
        world = WorldState.model_validate_json(legacy)
        assert world.story_beats.chapter == 1
        assert world.story_beats.turn == 0

    def test_beats_are_addressable_as_condition_paths(self):
        # docs/04 §3.3: advancing beats is the Engine's only channel, which
        # requires these to be resolvable paths for scenario authors.
        from ai_native_rpg.world.conditions import resolve_path

        world = WorldState(world_id="w", story_beats=StoryBeats(chapter=2, tension=0.7))
        assert resolve_path(world, "story_beats.chapter") == 2
        assert resolve_path(world, "story_beats.tension") == 0.7


class TestEventCandidate:
    def test_candidate_names_its_operator(self):
        # docs/05 §4: candidates carry an operator, not just an event_type.
        candidate = EventCandidate(
            operator=NarrativeOperator.REVEAL,
            event_type="clue_disclosure",
            intensity=0.5,
            preference_tag="social",
            trigger_reason="trust >= 40",
        )
        assert candidate.operator is NarrativeOperator.REVEAL
        assert candidate.pays_off is None
        assert candidate.constraints == []

    def test_intensity_is_bounded(self):
        with pytest.raises(ValidationError):
            EventCandidate(
                operator=NarrativeOperator.ESCALATE,
                event_type="pressure",
                intensity=1.5,
                preference_tag="intrigue",
                trigger_reason="r",
            )


class TestPlayerProfile:
    def test_weight_falls_back_to_neutral_for_an_untracked_tag(self):
        # The Controller scores by looking a tag up here; an absent tag must read
        # as "no opinion" (0.5) rather than 0, which would veto the candidate.
        profile = PlayerProfile(player_id="player_1", play_style={"social": 0.9})
        assert profile.weight_for("social") == 0.9
        assert profile.weight_for("intrigue") == 0.5

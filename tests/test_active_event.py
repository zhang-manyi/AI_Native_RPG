"""``active_event`` and the completed/closed distinction (docs/13 §3.2, §7).

Before this the world had no notion of an event in progress: every turn was
independent and ``pending_event`` carried one hook that then vanished. An event
layer cannot exist without somewhere to record "this event, this many exchanges in".

All deterministic, so strict TDD (docs/09 §5). Everything here has to be readable by
a ``Condition``, which is why it sits under ``story_beats`` rather than in a new
top-level field — docs/13 §7 requires each new state to be a legal condition path.
"""

from __future__ import annotations

from ai_native_rpg.schemas.narrative import ActiveEvent, StoryBeats
from ai_native_rpg.schemas.world_state import WorldState
from ai_native_rpg.world.conditions import resolve_path


class TestActiveEvent:
    def test_a_fresh_world_has_no_active_event(self):
        assert StoryBeats().active_event is None

    def test_opening_an_event_starts_at_zero_exchanges(self):
        beats = StoryBeats()

        beats.open_event("M1_knock", max_exchanges=4)

        assert beats.active_event is not None
        assert beats.active_event.event_id == "M1_knock"
        assert beats.active_event.exchanges == 0
        assert beats.active_event.max_exchanges == 4

    def test_recording_an_exchange_counts_up(self):
        beats = StoryBeats()
        beats.open_event("M1_knock", max_exchanges=2)

        beats.record_exchange()

        assert beats.active_event is not None
        assert beats.active_event.exchanges == 1
        assert beats.active_event.is_out_of_patience is False

    def test_patience_runs_out_at_the_authored_limit(self):
        """docs/13 §3.1: the limit is deterministic, so the NPC never decides it."""
        beats = StoryBeats()
        beats.open_event("M1_knock", max_exchanges=2)

        beats.record_exchange()
        beats.record_exchange()

        assert beats.active_event is not None
        assert beats.active_event.is_out_of_patience is True

    def test_recording_an_exchange_with_no_active_event_is_a_no_op(self):
        """A player line outside any event must not crash the turn."""
        beats = StoryBeats()

        beats.record_exchange()

        assert beats.active_event is None

    def test_finishing_clears_the_active_event_and_marks_it_completed(self):
        beats = StoryBeats()
        beats.open_event("M1_knock", max_exchanges=4)

        beats.finish_event()

        assert beats.active_event is None
        assert beats.has_completed("M1_knock")
        # Completed is not closed: M1 may run again next slot (docs/15 §4).
        assert not beats.is_closed("M1_knock")

    def test_finishing_with_closure_marks_it_closed_as_well(self):
        """docs/15 §7 asks for completed and closed to be distinguishable.

        M3 and M5 close permanently on failure, and "closed" has to mean "never
        triggers again" — whereas a completed M1 is expected to be re-runnable.
        Collapsing the two would either make every event once-only or make a
        permanent consequence temporary.
        """
        beats = StoryBeats()
        beats.open_event("M3_witness", max_exchanges=4)

        beats.finish_event(close=True)

        assert beats.has_completed("M3_witness")
        assert beats.is_closed("M3_witness")

    def test_finishing_twice_does_not_duplicate_the_record(self):
        beats = StoryBeats()
        beats.open_event("M1_knock", max_exchanges=4)
        beats.finish_event()
        beats.finish_event()

        assert beats.completed_events.count("M1_knock") == 1

    def test_opening_a_second_event_replaces_the_first(self):
        """docs/13 §5.2: one conversation at a time, so one active event at a time."""
        beats = StoryBeats()
        beats.open_event("M1_knock", max_exchanges=4)

        beats.open_event("M2_window", max_exchanges=4)

        assert beats.active_event is not None
        assert beats.active_event.event_id == "M2_window"


class TestFlags:
    def test_flags_start_empty(self):
        assert StoryBeats().flags == []

    def test_raising_a_flag_is_idempotent(self):
        """``probed_once`` asks "has it happened", and the penalty does not stack."""
        beats = StoryBeats()

        beats.raise_flag("probed_once")
        beats.raise_flag("probed_once")

        assert beats.flags == ["probed_once"]
        assert beats.has_flag("probed_once")


class TestConditionReadability:
    """docs/13 §7: every new state must be addressable by a ``Condition``.

    Not a formality. The last two content bugs were authored thresholds against
    values nothing could read or write, so a new field that conditions cannot see
    would be the same failure again.
    """

    def test_the_active_event_id_resolves_as_a_condition_path(self, world: WorldState):
        world.story_beats.open_event("M1_knock", max_exchanges=4)

        assert resolve_path(world, "story_beats.active_event.event_id") == "M1_knock"

    def test_completed_and_closed_lists_resolve(self, world: WorldState):
        world.story_beats.open_event("M3_witness", max_exchanges=4)
        world.story_beats.finish_event(close=True)

        assert resolve_path(world, "story_beats.completed_events") == ["M3_witness"]
        assert resolve_path(world, "story_beats.closed_events") == ["M3_witness"]

    def test_flags_resolve_so_an_event_can_trigger_on_one(self, world: WorldState):
        world.story_beats.raise_flag("promised_silence")

        assert resolve_path(world, "story_beats.flags") == ["promised_silence"]

    def test_a_completed_event_can_gate_a_trigger(self, world: WorldState):
        """M2 triggers on "M1 finished", which is the shape that has to work.

        Needs ``contains``, not ``in``: the existing ``in`` asks whether the value at
        the path belongs to an author-supplied collection, and here it is the *path*
        that holds the collection. See ``ConditionOp.CONTAINS``.
        """
        from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
        from ai_native_rpg.world.conditions import evaluate

        condition = Condition(
            clauses=[
                ConditionClause(
                    path="story_beats.completed_events", op=ConditionOp.CONTAINS, value="M1_knock"
                )
            ]
        )

        assert evaluate(condition, world) is False
        world.story_beats.open_event("M1_knock", max_exchanges=4)
        world.story_beats.finish_event()
        assert evaluate(condition, world) is True


class TestActiveEventShape:
    def test_exchanges_cannot_go_negative(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ActiveEvent(event_id="M1", exchanges=-1, max_exchanges=4)

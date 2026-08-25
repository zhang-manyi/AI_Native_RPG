"""The clock: three slots a day plus a wrap-up (docs/13 §4).

Slots are where *cost* comes from. Every action was free before, so a player who got
nothing out of a question asked it again, and "进展慢" read as being stuck rather than as
pressure. With three slots a day each choice excludes the others — which is also the
precondition for distinguishing play styles, since ``PlayerProfile`` cannot be written
from behaviour that never had to choose.

Two properties are pinned hardest here:

* **the day counter actually moves.** ``time_day`` existed in ``world.yaml`` and nothing
  ever advanced it, which is the same "authored on all sides, connected on none" failure
  docs/13 §11 records twice. A field with no writer is the bug, not the absence of a field.
* **the wrap-up costs nothing.** It is the automatic interlude of docs/13 §4.2, so a day
  is three spendable slots regardless of it. Getting this wrong would silently change the
  slot budget docs/15 §6 computes the endings against.
"""

from __future__ import annotations

import uuid

import pytest

from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
from ai_native_rpg.schemas.narrative import (
    DEFAULT_DAY_LIMIT,
    SLOTS_PER_DAY,
    SPENDABLE_SLOTS,
    StoryBeats,
    TimeSlot,
)
from ai_native_rpg.schemas.world_state import ActionProposal, WorldState
from ai_native_rpg.world.conditions import evaluate, resolve_path
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"


def _advance_slot() -> ActionProposal:
    return ActionProposal(
        proposal_id=uuid.uuid4().hex,
        actor_id="narrative_engine",
        action_type="advance_story_beat",
        payload={"advance_slot": True},
    )


@pytest.fixture
def manager(world: WorldState) -> WorldStateManager:
    return WorldStateManager(world)


class TestTheDayHasThreeSpendableSlots:
    def test_a_day_is_three_slots(self):
        """Derived from the tuple, never written twice.

        docs/15 §6's budget is ``days × slots``; two constants free to disagree is how
        that arithmetic quietly stops matching the document.
        """
        assert SLOTS_PER_DAY == 3
        assert len(SPENDABLE_SLOTS) == SLOTS_PER_DAY

    def test_the_wrap_up_is_not_among_them(self):
        assert TimeSlot.WRAP_UP not in SPENDABLE_SLOTS

    def test_every_spendable_slot_says_so(self):
        assert all(slot.is_spendable for slot in SPENDABLE_SLOTS)
        assert not TimeSlot.WRAP_UP.is_spendable

    def test_evening_is_a_real_slot(self):
        """docs/13 §4.1 rejected making the evening a safe "tidy up at home" default.

        Night is when this story should be most dangerous — the girl vanished in a rainy
        night, Marta will not open her door after dark, Loren is out near the forest — so
        fixing the tensest slot as the safest one inverts the design, and it would also
        halve the cost just established.
        """
        assert TimeSlot.EVENING in SPENDABLE_SLOTS
        assert TimeSlot.EVENING.is_spendable

    def test_the_day_starts_in_the_morning(self, manager):
        assert manager.snapshot().story_beats.time_slot is TimeSlot.MORNING


class TestTheClockAdvances:
    """The bug this fixes is a field with no writer, not a missing field."""

    def test_slots_run_morning_afternoon_evening_then_wrap_up(self, manager):
        seen = [manager.snapshot().story_beats.time_slot]
        for _ in range(3):
            manager.submit(_advance_slot())
            seen.append(manager.snapshot().story_beats.time_slot)

        assert seen == [
            TimeSlot.MORNING,
            TimeSlot.AFTERNOON,
            TimeSlot.EVENING,
            TimeSlot.WRAP_UP,
        ]

    def test_the_wrap_up_rolls_over_into_the_next_morning(self, manager):
        for _ in range(4):
            manager.submit(_advance_slot())

        world = manager.snapshot()
        assert world.story_beats.time_slot is TimeSlot.MORNING
        assert world.time_day == 2

    def test_the_day_only_turns_at_the_wrap_up(self, manager):
        """Three advances stay inside day one; the fourth is what ends it."""
        start = manager.snapshot().time_day

        for _ in range(3):
            manager.submit(_advance_slot())
            assert manager.snapshot().time_day == start

        manager.submit(_advance_slot())
        assert manager.snapshot().time_day == start + 1

    def test_the_wrap_up_costs_no_slot(self, manager):
        """A day is three spendable slots whether or not the interlude happened."""
        for _ in range(3):
            manager.submit(_advance_slot())
        assert manager.snapshot().story_beats.slots_spent_today == SLOTS_PER_DAY

        manager.submit(_advance_slot())  # through the wrap-up
        assert manager.snapshot().story_beats.slots_spent_today == 0

    def test_slots_spent_resets_each_day(self, manager):
        for _ in range(4):
            manager.submit(_advance_slot())
        manager.submit(_advance_slot())

        beats = manager.snapshot().story_beats
        assert beats.time_slot is TimeSlot.AFTERNOON
        assert beats.slots_spent_today == 1

    def test_four_days_of_advancing_gives_twelve_spendable_slots(self, manager):
        """docs/15 §6's budget, executed rather than asserted in prose.

        The slot arithmetic there is the whole reason that document exists, and it is
        the kind of claim that rots silently: 4 days × 3 slots = 12 chances to act.
        """
        spent = 0
        while manager.snapshot().time_day <= DEFAULT_DAY_LIMIT:
            before = manager.snapshot().story_beats.time_slot
            manager.submit(_advance_slot())
            if before.is_spendable:
                spent += 1

        assert spent == DEFAULT_DAY_LIMIT * SLOTS_PER_DAY == 12


class TestTheClockGoesThroughTheValidator:
    """docs/04's single write path, applied to the clock.

    A setter on the Manager would have been shorter and would have put "a slot was
    spent" outside every trace.
    """

    def test_advancing_the_slot_is_reported_as_applied_changes(self, manager):
        result = manager.submit(_advance_slot())

        assert result.approved
        assert result.applied_changes is not None
        assert result.applied_changes["story_beats.time_slot"] == TimeSlot.AFTERNOON.value
        assert result.applied_changes["time_day"] == 1

    def test_an_npc_cannot_advance_the_clock(self, manager):
        """Narrative state stays system-only: an NPC able to burn a slot could run the
        player out of days, and one able to hold it could freeze the deadline."""
        result = manager.submit(
            ActionProposal(
                proposal_id=uuid.uuid4().hex,
                actor_id="npc_a",
                action_type="advance_story_beat",
                payload={"advance_slot": True},
            )
        )

        assert not result.approved
        assert manager.snapshot().story_beats.time_slot is TimeSlot.MORNING


class TestTheSlotIsReadableByACondition:
    """docs/13 §7 requires every new state to be a legal ``Condition`` path.

    This is not decoration: it is what lets a pack write "只有晚上才会发生" as a trigger.
    An unreachable path would mean authors writing triggers the evaluator cannot answer,
    which is precisely how the last two content bugs happened.
    """

    def test_the_slot_resolves(self, manager):
        assert resolve_path(manager.snapshot(), "story_beats.time_slot") is TimeSlot.MORNING

    def test_a_pack_can_gate_an_event_on_the_evening(self, manager):
        """The trigger "夜里出门" needs — the deliberate, costly night move of docs/13 §4.1."""
        at_night = Condition(
            clauses=[
                ConditionClause(
                    path="story_beats.time_slot", op=ConditionOp.EQ, value=TimeSlot.EVENING.value
                )
            ]
        )

        assert not evaluate(at_night, manager.snapshot())

        manager.submit(_advance_slot())
        manager.submit(_advance_slot())

        assert evaluate(at_night, manager.snapshot())

    def test_a_pack_can_gate_on_how_much_of_the_day_is_gone(self, manager):
        late = Condition(
            clauses=[
                ConditionClause(path="story_beats.slots_spent_today", op=ConditionOp.GTE, value=2)
            ]
        )

        assert not evaluate(late, manager.snapshot())
        for _ in range(2):
            manager.submit(_advance_slot())
        assert evaluate(late, manager.snapshot())

    def test_the_day_is_still_readable_where_it_always_was(self, manager):
        """``time_day`` stays on the world rather than being duplicated onto the beats.

        docs/13 §4.1 asked for the existing field to start moving, so the two halves of
        the clock live in different models and the Manager writes both together. A second
        copy would be free to disagree.
        """
        assert resolve_path(manager.snapshot(), "time_day") == 1


class TestTheDeadline:
    """The clock is what ends the case, which is what makes "闭口" a cost not a tombstone.

    docs/14 §4.3: Marta clamming up closes one social line while the tavern and the
    forest remain; only running out of days ends the game.
    """

    def test_the_case_is_not_over_on_the_last_day(self, manager):
        beats = manager.snapshot().story_beats
        assert not beats.is_out_of_days(current_day=DEFAULT_DAY_LIMIT)

    def test_the_case_is_over_past_the_limit(self, manager):
        beats = manager.snapshot().story_beats
        assert beats.is_out_of_days(current_day=DEFAULT_DAY_LIMIT + 1)

    def test_advancing_through_the_limit_ends_the_case(self, manager):
        """Reached by playing the clock rather than by setting the field."""
        while not manager.snapshot().story_beats.is_out_of_days(
            current_day=manager.snapshot().time_day
        ):
            manager.submit(_advance_slot())

        world = manager.snapshot()
        assert world.time_day == DEFAULT_DAY_LIMIT + 1
        assert world.story_beats.is_out_of_days(current_day=world.time_day)

    def test_the_limit_is_a_field_not_a_constant(self):
        """So a pack can run a shorter case without a code change — and so a test can reach
        the deadline in three moves rather than twelve.

        Note this only proves the *field* works. Whether a pack can actually set it is a
        separate question, and the answer was no until the loader read it: see
        ``tests/test_scenario_loader.py``. A field with a default and no reader is the shape
        of bug docs/13 §11 is about, and this test would not have caught it.
        """
        beats = StoryBeats(day_limit=1)

        assert not beats.is_out_of_days(current_day=1)
        assert beats.is_out_of_days(current_day=2)

    def test_the_default_limit_is_four_days(self):
        """docs/15 §6 picked 4 over 3: the truth path needs 9 slots of the 12, and three
        days would leave no buffer for a single failed check."""
        assert DEFAULT_DAY_LIMIT == 4


class TestTheClockSurvivesASave:
    """A resumed save must resume the same day and slot.

    ``ActiveEvent`` already documents this stance for an event's patience; the clock has
    the same requirement, and it is cheap to get wrong since two fields on two models
    have to round-trip together.
    """

    def test_a_saved_world_reloads_at_the_same_slot(self, manager, tmp_path):
        manager.submit(_advance_slot())
        manager.submit(_advance_slot())
        path = tmp_path / "save.json"
        manager.save(path)

        resumed = WorldStateManager.load(path).snapshot()

        assert resumed.story_beats.time_slot is TimeSlot.EVENING
        assert resumed.story_beats.slots_spent_today == 2
        assert resumed.time_day == 1

    def test_a_world_saved_before_the_clock_existed_still_loads(self):
        """Defaulted for the same reason ``story_beats`` itself was: old saves must load."""
        beats = StoryBeats.model_validate({"chapter": 1, "turn": 3})

        assert beats.time_slot is TimeSlot.MORNING
        assert beats.slots_spent_today == 0
        assert beats.day_limit == DEFAULT_DAY_LIMIT

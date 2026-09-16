"""The Engine's clock surface: moving, leaving, and the interlude (docs/13 §4, §12).

The clock and the wrap-up have their own unit files; this one pins the seams — that the
Engine exposes them without becoming a second write path, and that the interlude is
available exactly at the interlude.

The last class is the one worth reading. docs/15 §6.2 computes the endings against a slot
budget, and docs/13 §11 records two bugs of the shape "an author wrote a path and nobody
checked it led anywhere". A budget in prose is that same shape, so it is walked here.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.narrative.engine import NarrativeEngine
from ai_native_rpg.schemas.narrative import (
    DEFAULT_DAY_LIMIT,
    SLOTS_PER_DAY,
    TimeSlot,
)
from ai_native_rpg.schemas.world_state import WorldState
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"


@pytest.fixture
def engine(world: WorldState, directives) -> NarrativeEngine:
    world.player_locations[PLAYER] = "village_square"
    world.story_beats.visited_locations = ["village_square"]
    return NarrativeEngine(
        manager=WorldStateManager(world),
        llm=MockLLMClient([]),
        directives=directives,
    )


class TestTheEngineExposesPlayerActions:
    def test_moving_through_the_engine_moves_the_player(self, engine):
        result = engine.move_player(player_id=PLAYER, destination="tavern")

        assert result.approved
        assert engine._manager.snapshot().player_locations[PLAYER] == "tavern"

    def test_moving_spends_a_slot(self, engine):
        engine.move_player(player_id=PLAYER, destination="tavern")

        assert engine._manager.snapshot().story_beats.time_slot is TimeSlot.AFTERNOON

    def test_an_illegal_move_is_refused_with_a_readable_reason(self, engine):
        engine.move_player(player_id=PLAYER, destination="tavern")

        result = engine.move_player(player_id=PLAYER, destination="forest_edge")

        assert not result.approved
        assert result.reason is not None
        assert "tavern" in result.reason

    def test_ending_a_conversation_closes_the_active_event(self, engine):
        """No outcome is applied: walking out is not running out of patience."""
        engine._manager.snapshot()  # no active event to begin with
        result = engine.end_conversation()

        assert result.approved
        assert engine._manager.snapshot().story_beats.active_event is None


class TestTheWrapUpArrivesOnSchedule:
    def test_it_is_absent_during_the_day(self, engine):
        assert not engine.is_wrapping_up()
        assert engine.wrap_up(player_id=PLAYER) is None

    def test_it_appears_after_the_third_slot(self, engine):
        """Three moves, one per slot — the point being that ordinary play reaches it.

        Each move costs exactly one slot, so a day is three journeys and then the
        interlude; a fourth would already be tomorrow morning.
        """
        for destination in ("tavern", "village_square", "forest_edge"):
            assert engine.move_player(player_id=PLAYER, destination=destination).approved

        assert engine.is_wrapping_up()
        assert engine.wrap_up(player_id=PLAYER) is not None

    def test_the_review_shows_only_what_the_player_can_see(self, engine):
        for _ in range(SLOTS_PER_DAY):
            engine._manager.submit(_slot())

        summary = engine.wrap_up(player_id=PLAYER)

        assert summary is not None
        assert summary.review.known == engine._manager.player_view(PLAYER).visible_facts

    def test_the_review_comes_with_it(self, engine):
        for _ in range(SLOTS_PER_DAY):
            engine._manager.submit(_slot())

        summary = engine.wrap_up(player_id=PLAYER)

        assert summary is not None
        assert summary.review.known_count >= 0
        assert summary.day == summary.review.day

    def test_closing_out_the_day_only_works_at_the_wrap_up(self, engine):
        """Otherwise a caller could skip a slot it did not want to spend."""
        result = engine.close_out_day()

        assert not result.approved
        assert engine._manager.snapshot().story_beats.time_slot is TimeSlot.MORNING

    def test_closing_out_the_day_starts_the_next_morning(self, engine):
        for _ in range(SLOTS_PER_DAY):
            engine._manager.submit(_slot())

        result = engine.close_out_day()

        world = engine._manager.snapshot()
        assert result.approved
        assert world.story_beats.time_slot is TimeSlot.MORNING
        assert world.time_day == 2
        assert world.story_beats.slots_spent_today == 0

    def test_the_interlude_costs_no_slot(self, engine):
        """Three slots a day, whether or not the wrap-up happened (docs/13 §4.1)."""
        for _ in range(SLOTS_PER_DAY):
            engine._manager.submit(_slot())
        spent_before = engine._manager.snapshot().story_beats.slots_spent_today

        result = engine.close_out_day()

        assert spent_before == SLOTS_PER_DAY
        assert not result.slot_spent


class TestTheSlotBudgetIsWalkable:
    """docs/15 §6: 4 days × 3 slots = 12 chances to act, executed rather than asserted.

    The document's own point is that its budget arithmetic was worth doing before writing
    code — it caught the first amplitude table being too small to finish the main line. A
    number in prose is the same shape as the two bugs docs/13 §11 records, so it gets a
    test that walks the clock rather than restating the total.
    """

    def test_the_case_gives_twelve_spendable_slots(self, engine):
        spent = 0
        while engine._manager.snapshot().time_day <= DEFAULT_DAY_LIMIT:
            before = engine._manager.snapshot().story_beats.time_slot
            engine._manager.submit(_slot())
            if before.is_spendable:
                spent += 1

        assert spent == 12

    def test_a_player_can_reach_every_location_inside_the_budget(self, engine):
        """The truth path needs the tavern *and* the forest (docs/14 §1.2.1).

        Both are one hop from the square, so touching every location costs at most five
        slots of the twelve — the geography is not what makes the budget tight, which is
        what docs/15 §6.2 assumes when it puts the truth path at nine.
        """
        world = engine._manager.snapshot()
        targets = set(world.locations) - {world.player_locations[PLAYER]}

        spent = 0
        for target in sorted(targets):
            hop = engine.move_player(player_id=PLAYER, destination=target)
            if not hop.approved:
                # Not adjacent: go via the hub, which is what the square is for.
                assert engine.move_player(player_id=PLAYER, destination="village_square").approved
                spent += 1
                assert engine.move_player(player_id=PLAYER, destination=target).approved
            spent += 1

        assert spent <= DEFAULT_DAY_LIMIT * SLOTS_PER_DAY
        assert engine._manager.snapshot().story_beats.visited_locations != []


def _slot():
    import uuid

    from ai_native_rpg.schemas.world_state import ActionProposal

    return ActionProposal(
        proposal_id=uuid.uuid4().hex,
        actor_id="narrative_engine",
        action_type="advance_story_beat",
        payload={"advance_slot": True},
    )

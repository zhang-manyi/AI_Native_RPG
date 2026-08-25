"""The day's wrap-up (docs/13 §4.2): review the clues, read the cards.

Two properties carry the whole design:

* **the review adds no information.** A wrap-up that handed out a clue would be a free
  progress channel, and the slot cost docs/13 §4.1 just established would be refundable
  by waiting for the evening. So the test is not "does it summarise nicely" but "can it
  produce anything the player did not already have".
* **it costs no slot.** A day is three spendable slots whether or not the interlude ran.

The visibility guard is structural, and this file pins the structure rather than the
behaviour: ``review_clues`` takes a ``VisibleState``, and the module never imports
``player_view``'s source of truth for its clue list. That is the same mechanism
``build_scene`` uses (docs/12 §7) — the type signature is the guarantee, so a leak would
take changing a parameter rather than forgetting a check. docs/13 §5.1 insists on this
being mechanical: an NPC misspeaking is plot, the objective voice misspeaking is a bug.
"""

from __future__ import annotations

import uuid

import pytest

from ai_native_rpg.narrative.wrap_up import ClueReview, review_clues, tarot_reading
from ai_native_rpg.schemas.narrative import SLOTS_PER_DAY, TimeSlot
from ai_native_rpg.schemas.world_state import ActionProposal, Visibility, WorldState
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"
NPC_A = "npc_a"


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


class TestTheReviewOnlyRestatesWhatIsKnown:
    """docs/13 §4.2: 整理线索只重述已知，不产生新信息."""

    def test_it_reports_exactly_the_visible_facts(self, manager):
        view = manager.player_view(PLAYER)

        review = review_clues(view)

        assert review.known == view.visible_facts

    def test_it_cannot_report_a_hidden_fact(self, manager):
        """The strongest form of the claim: unearned values are absent, by construction.

        "Hidden" here means hidden *right now* — the fixture gates its clues on trust
        rather than leaving them unconditional, so the set worth checking is the one whose
        conditions are still unmet, not the one with no condition at all.
        """
        world = manager.snapshot()
        visible = set(manager.player_view(PLAYER).visible_facts)
        unearned = set(world.facts) - visible
        assert unearned, "fixture must have something unearned for this to mean anything"

        review = review_clues(manager.player_view(PLAYER))

        assert not (unearned & set(review.known))

    def test_a_revealed_clue_shows_up_and_an_unrevealed_one_does_not(self, manager):
        """The same clue, before and after it is earned."""
        before = review_clues(manager.player_view(PLAYER))
        assert "clue_1" not in before.known

        manager.submit(
            ActionProposal(
                proposal_id=uuid.uuid4().hex,
                actor_id=NPC_A,
                action_type="adjust_relationship",
                target_id=PLAYER,
                payload={"trust": 15.0},
            )
        )
        manager.submit(
            ActionProposal(
                proposal_id=uuid.uuid4().hex,
                actor_id=NPC_A,
                action_type="adjust_relationship",
                target_id=PLAYER,
                payload={"trust": 15.0},
            )
        )

        after = review_clues(manager.player_view(PLAYER))
        assert "clue_1" in after.known

    def test_reviewing_twice_changes_nothing_in_the_world(self, manager):
        """Pure: it reorganises, it does not advance."""
        before = manager.snapshot().model_dump_json()

        review_clues(manager.player_view(PLAYER))
        review_clues(manager.player_view(PLAYER))

        assert manager.snapshot().model_dump_json() == before

    def test_it_takes_the_projection_not_the_world(self):
        """Structural, not behavioural: the signature is the guarantee (docs/13 §5.1).

        Passing a ``WorldState`` must not quietly work, because "the review reads only
        visible state" then rests on nobody having passed the wrong thing.
        """
        import inspect

        annotation = inspect.signature(review_clues).parameters["view"].annotation
        assert "VisibleState" in str(annotation)

    def test_the_module_never_imports_world_state_for_its_clue_list(self):
        """The import-absence check, same stance as ``build_scene``'s (docs/07 §2.4)."""
        import ai_native_rpg.narrative.wrap_up as module

        source = inspect_source(module)
        # WorldState appears only for the tarot, which reads counts. What must not appear
        # is any use of the fact table's values.
        assert "fact.value" not in source
        assert ".facts[" not in source


def inspect_source(module) -> str:
    import inspect

    return inspect.getsource(module)


class TestOpenQuestionsDoNotDescribeTheAnswer:
    """The review may say what is still open; it may not say what the answer is.

    Naming an absent clue describes it, which is the leak in disguise. So the questions
    are phrased as ones the player could have asked himself.
    """

    def test_it_raises_open_questions_while_the_case_is_dark(self, manager):
        review = review_clues(manager.player_view(PLAYER))

        assert review.unanswered

    def test_the_questions_never_contain_a_hidden_value(self, manager):
        world = manager.snapshot()
        review = review_clues(manager.player_view(PLAYER))
        joined = " ".join(review.unanswered)

        for fact in world.facts.values():
            if fact.visibility is Visibility.HIDDEN and isinstance(fact.value, str):
                assert fact.value not in joined

    def test_answering_a_question_removes_it(self, manager):
        """Reveal the identity clue and the "who" question should stop being open."""
        before = review_clues(manager.player_view(PLAYER))
        assert any("谁" in q for q in before.unanswered)

        world = manager.snapshot()
        world.facts["killer_identity"].visibility = Visibility.REVEALED
        reopened = WorldStateManager(world)

        after = review_clues(reopened.player_view(PLAYER))
        assert not any("谁" in q for q in after.unanswered)


class TestTheTarotReadsShapeNotContent:
    """docs/13 §4.2: 塔罗占卜给暗示性但不确定的提示.

    Divination is allowed to be vague, which is exactly why it can read world-level
    structure without breaking the visibility rule: the counts say how *much* is left,
    never what it is.
    """

    def test_a_fresh_case_reads_as_mostly_dark(self, manager):
        reading = tarot_reading(manager.snapshot(), manager.player_view(PLAYER))

        assert reading.reads_as == "mostly_dark"

    def test_revealing_everything_makes_it_read_as_nearly_clear(self, manager):
        world = manager.snapshot()
        for fact in world.facts.values():
            fact.visibility = Visibility.REVEALED
        lit = WorldStateManager(world)

        reading = tarot_reading(lit.snapshot(), lit.player_view(PLAYER))

        assert reading.reads_as == "nearly_clear"

    def test_the_imagery_never_names_a_fact(self, manager):
        """A card that varied with a specific hidden fact would be a leak as atmosphere."""
        world = manager.snapshot()
        reading = tarot_reading(world, manager.player_view(PLAYER))

        for card in reading.imagery:
            assert card not in world.facts
            for fact in world.facts.values():
                if isinstance(fact.value, str):
                    assert card not in fact.value

    def test_high_tension_shows_up_in_the_reading(self, manager):
        manager.submit(
            ActionProposal(
                proposal_id=uuid.uuid4().hex,
                actor_id="narrative_engine",
                action_type="advance_story_beat",
                payload={"tension": 0.8},
            )
        )

        reading = tarot_reading(manager.snapshot(), manager.player_view(PLAYER))

        assert reading.tension == pytest.approx(0.8)
        assert "the_tower" in reading.imagery

    def test_the_reading_reports_no_raw_counts(self):
        """docs/15 §2's refusal to print ``[示好 65%]``, applied here.

        A precise "7 clues left" is a number the player optimises against; a fraction
        plus a coarse label is atmosphere with real information behind it.
        """
        fields = set(TarotReading_fields())
        assert "hidden_count" not in fields
        assert "darkness" in fields


def TarotReading_fields() -> list[str]:
    import dataclasses

    from ai_native_rpg.narrative.wrap_up import TarotReading

    return [f.name for f in dataclasses.fields(TarotReading)]


class TestTheWrapUpCostsNoSlot:
    """docs/13 §4.1: 它不占用时段，是自动到来的过场."""

    def test_the_wrap_up_arrives_after_the_third_slot(self, manager):
        for _ in range(SLOTS_PER_DAY):
            manager.submit(_advance_slot())

        assert manager.snapshot().story_beats.time_slot is TimeSlot.WRAP_UP

    def test_the_day_still_had_three_spendable_slots(self, manager):
        for _ in range(SLOTS_PER_DAY):
            manager.submit(_advance_slot())

        assert manager.snapshot().story_beats.slots_spent_today == SLOTS_PER_DAY

    def test_the_review_itself_does_not_move_the_clock(self, manager):
        for _ in range(SLOTS_PER_DAY):
            manager.submit(_advance_slot())
        before = manager.snapshot().story_beats.time_slot

        review_clues(manager.player_view(PLAYER))
        tarot_reading(manager.snapshot(), manager.player_view(PLAYER))

        assert manager.snapshot().story_beats.time_slot is before


class TestTheReviewCarriesTheDay:
    def test_it_defaults_to_the_day_the_view_reports(self, manager):
        review = review_clues(manager.player_view(PLAYER))

        assert review.day == manager.snapshot().time_day

    def test_the_day_can_be_given_explicitly(self, manager):
        review = review_clues(manager.player_view(PLAYER), day=3)

        assert review.day == 3

    def test_known_count_matches(self, manager):
        view = manager.player_view(PLAYER)

        review = review_clues(view)

        assert review.known_count == len(view.visible_facts)
        assert isinstance(review, ClueReview)

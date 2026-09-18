"""M7 reports: private, location-independent judgments with evidence-based endings.

Every hypothesis is offered before and after collecting evidence. Submitting a guess
cannot produce a confession or hidden facts; returning to investigation gives no hint.
"""

from __future__ import annotations

import random

import pytest

from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.narrative.engine import NarrativeEngine
from ai_native_rpg.scenario import (
    load_endings,
    load_event_script,
    load_narrative_directives,
    load_scenario,
)
from ai_native_rpg.schemas.world_state import Visibility
from ai_native_rpg.world.manager import WorldStateManager

PACK = "village_disappearance"
PLAYER = "player_1"
NPC_A = "npc_a"

_TRUTH_FLAGS = [
    "ella_things_missing_told",
    "ella_asked_the_road_told",
    "timeline_mismatch_told",
]
_TRUTH_FACTS = ["ella_things_missing", "ella_asked_the_road", "timeline_mismatch"]


def _content(**overrides) -> dict:
    payload = {"summary": "s", "dialogue_hook": "h", "participants": []}
    payload.update(overrides)
    return payload


def _engine(world, *, responses: int = 6) -> tuple[NarrativeEngine, WorldStateManager]:
    manager = WorldStateManager(world)
    engine = NarrativeEngine(
        manager=manager,
        llm=MockLLMClient([_content() for _ in range(responses)]),
        directives=load_narrative_directives(PACK),
        script=load_event_script(PACK),
        endings=load_endings(PACK),
    )
    return engine, manager


def _world_at_stage(stage: int):
    world = load_scenario(PACK)
    world.player_locations[PLAYER] = "forest_edge"
    world.quests["investigation"].stage = stage
    return world


def _reveal_truth_side(world) -> None:
    """Put the world where all three truth-side clues have actually been told."""
    for fact_id in _TRUTH_FACTS:
        world.facts[fact_id].visibility = Visibility.REVEALED
    world.story_beats.flags.extend(_TRUTH_FLAGS)
    world.facts["forest_traces"].visibility = Visibility.REVEALED
    world.story_beats.flags.append("forest_traces_seen")


class TestConcludeCase:
    @pytest.mark.parametrize("location", ["npc_a_house", "village_square", "tavern", "forest_edge"])
    def test_report_opens_at_any_location_without_a_speaker(self, location):
        world = load_scenario(PACK)
        world.player_locations[PLAYER] = location
        engine, manager = _engine(world)
        assert engine.conclude_reason(PLAYER) == ""
        assert engine.conclude_case().approved
        assert engine.is_reporting()
        assert engine.active_event().npc_id is None
        assert manager.snapshot().player_locations[PLAYER] == location
        assert all(line.speaker == "narrator" for line in engine.active_event().presentation)

    def test_opens_the_conclusion_event_regardless_of_stage(self):
        """docs/14 §4.2: 指控错人 must be reachable before the case is otherwise solved."""
        engine, manager = _engine(_world_at_stage(0))

        result = engine.conclude_case()

        assert result.approved
        assert manager.snapshot().story_beats.active_event.event_id == "M7_conclusion"

    def test_refuses_while_another_event_is_running(self):
        """docs/13 §5.2: one conversation partner at a time."""
        engine, manager = _engine(load_scenario(PACK))
        engine.tick(player_id=PLAYER)  # opens M1
        assert manager.snapshot().story_beats.active_event is not None

        result = engine.conclude_case()

        assert not result.approved
        assert result.reason
        # And nothing was disturbed: the event M1 opened is still the active one.
        assert manager.snapshot().story_beats.active_event.event_id == "M1_knock"

    def test_refuses_when_the_pack_declares_no_conclusion_event(self):
        from ai_native_rpg.scenario import NarrativeDirectives

        manager = WorldStateManager(_world_at_stage(0))
        engine = NarrativeEngine(
            manager=manager,
            llm=MockLLMClient([_content()]),
            directives=NarrativeDirectives(),  # no conclusion_event
            script=load_event_script(PACK),
        )

        result = engine.conclude_case()

        assert not result.approved
        assert "conclusion event" in result.reason

    def test_costs_no_slot(self):
        """Opening the conclusion is a decision, not a journey."""
        engine, manager = _engine(_world_at_stage(0))
        before = manager.snapshot().story_beats.slots_spent_today

        engine.conclude_case()

        assert manager.snapshot().story_beats.slots_spent_today == before


class TestReportHypotheses:
    def test_all_hypotheses_are_available_before_clues(self):
        engine, _ = _engine(_world_at_stage(3))
        engine.conclude_case()

        ids = {o.option_id for o in engine.visible_event_options()}

        assert ids == {
            "accuse_loren",
            "accuse_innkeeper",
            "accuse_marta",
            "tell_loren_alive",
            "report_unresolved",
            "not_yet",
        }

    def test_departure_hypothesis_remains_available_with_all_evidence(self):
        world = _world_at_stage(3)
        _reveal_truth_side(world)
        engine, _ = _engine(world)
        engine.conclude_case()

        ids = {o.option_id for o in engine.visible_event_options()}

        assert "tell_loren_alive" in ids

    def test_the_same_filter_governs_the_classifier_list(self):
        """The list ``active_event_options`` hands the NPC call must match what the
        scene shows — a gated option cannot be invisible to buttons and available to
        free text (docs/15 §7 M7)."""
        engine, _ = _engine(_world_at_stage(3))
        engine.conclude_case()

        classifier_ids = {o["id"] for o in engine.active_event_options()}
        scene_ids = {o.option_id for o in engine.visible_event_options()}

        assert classifier_ids == scene_ids

    def test_innkeeper_hypothesis_does_not_depend_on_a_hidden_clue(self):
        """No hypothesis disappears merely because supporting evidence is absent."""
        world = _world_at_stage(3)
        world.facts["innkeeper_hides_something"].visibility = Visibility.HIDDEN
        engine, _ = _engine(world)
        engine.conclude_case()

        ids = {o.option_id for o in engine.visible_event_options()}

        assert "accuse_innkeeper" in ids

    def test_early_correct_guess_is_unverified_and_does_not_reveal_hidden_truth(self):
        """A guess is a valid submission, but it cannot create evidence or a confession."""
        engine, manager = _engine(_world_at_stage(3))
        engine.conclude_case()

        record = engine.resolve_player_response(
            player_id=PLAYER, option_id="tell_loren_alive", rng=random.Random(0)
        )

        assert record is not None
        assert record.outcome_id == "told_loren_truth"
        assert record.finished
        assert manager.snapshot().story_beats.ended_at == "report_unverified"
        assert manager.snapshot().facts["ella_whereabouts"].visibility is Visibility.HIDDEN
        assert manager.snapshot().facts["loren_that_night"].visibility is Visibility.HIDDEN


class TestM7Outcomes:
    def test_accusing_loren_sets_the_flag_the_ending_reads(self):
        engine, manager = _engine(_world_at_stage(2))
        engine.conclude_case()

        record = engine.resolve_player_response(
            player_id=PLAYER, option_id="accuse_loren", rng=random.Random(0)
        )

        assert record is not None and record.outcome_id == "accused_loren"
        assert "accused_someone" in manager.snapshot().story_beats.flags
        assert manager.snapshot().facts["ella_whereabouts"].visibility is Visibility.HIDDEN

    def test_supported_report_does_not_invent_a_confession(self):
        world = _world_at_stage(3)
        _reveal_truth_side(world)
        engine, manager = _engine(world)
        engine.conclude_case()

        record = engine.resolve_player_response(
            player_id=PLAYER, option_id="tell_loren_alive", rng=random.Random(0)
        )

        assert record is not None and record.outcome_id == "told_loren_truth"
        assert manager.snapshot().story_beats.ended_at == "truth_uncovered"
        assert manager.snapshot().facts["ella_whereabouts"].visibility is Visibility.HIDDEN
        assert manager.snapshot().facts["loren_that_night"].visibility is Visibility.HIDDEN
        assert "report_departure" in manager.snapshot().story_beats.flags
        assert "told_loren_truth" not in manager.snapshot().story_beats.flags

    def test_not_yet_does_not_close_the_case_and_may_be_reopened(self):
        """docs/15 §4 M7: 我还不能确定 是"不结束，回到调查"，不是一个终局.

        The conversation itself does finish (M7 has no [观察]-style non-advancing
        outcome), but it does not *close* — ``repeatable: true`` is what lets
        ``conclude_case`` open it again later, unlike M3/M5's permanent closes.
        """
        engine, manager = _engine(_world_at_stage(2))
        engine.conclude_case()

        record = engine.resolve_player_response(
            player_id=PLAYER, option_id="not_yet", rng=random.Random(0)
        )

        assert record is not None and not record.closed
        assert manager.snapshot().story_beats.ended_at is None
        assert manager.snapshot().story_beats.active_event is None

        reopened = engine.conclude_case()
        assert reopened.approved


class TestEndingsFireAtRuntime:
    """Regression coverage for the validator gap: ``end_case`` was missing from
    ``_BEAT_ADVANCE_KEYS``, so every proposal meant to write ``story_beats.ended_at``
    was rejected before the apply step ran, however correct ``check_terminal_ending``'s
    own answer was."""

    def test_accusing_loren_before_full_evidence_ends_on_accused_the_wrong_man(self):
        engine, manager = _engine(_world_at_stage(2))
        engine.conclude_case()

        engine.resolve_player_response(
            player_id=PLAYER, option_id="accuse_loren", rng=random.Random(0)
        )

        assert manager.snapshot().story_beats.ended_at == "accused_the_wrong_man"

    def test_telling_loren_the_truth_ends_on_truth_uncovered(self):
        world = _world_at_stage(3)
        world.relationships[NPC_A][PLAYER].trust = 40.0
        _reveal_truth_side(world)
        engine, manager = _engine(world)
        engine.conclude_case()

        engine.resolve_player_response(
            player_id=PLAYER, option_id="tell_loren_alive", rng=random.Random(0)
        )

        assert manager.snapshot().story_beats.ended_at == "truth_uncovered"

    def test_a_terminal_ending_is_sticky(self):
        """docs/14 §4.3: the first terminal ending reached is the one recorded."""
        engine, manager = _engine(_world_at_stage(2))
        engine.conclude_case()
        engine.resolve_player_response(
            player_id=PLAYER, option_id="accuse_loren", rng=random.Random(0)
        )
        assert manager.snapshot().story_beats.ended_at == "accused_the_wrong_man"

        # A second conclusion (M7 is repeatable) must not overwrite it, even though
        # `accused_someone` is already set and the condition still reads true.
        engine.conclude_case()
        engine.resolve_player_response(
            player_id=PLAYER, option_id="accuse_innkeeper", rng=random.Random(0)
        )

        assert manager.snapshot().story_beats.ended_at == "accused_the_wrong_man"

"""The whole chain: event selected → voiced by an operator → player answers → outcome → numbers.

This is the acceptance test for the first vertical slice of docs/15 §8. Each layer has
its own unit tests; what this file checks is that they are actually connected, which is
the failure mode docs/13 §11 keeps recording — an author writes a path, every piece
along it works, and nobody verified the path itself goes anywhere.

Runs against the shipped pack rather than a fixture, because "the authored script
works" is the claim being made.

The LLM half stays contract-level (docs/09 §5): call counts and prompt assembly, never
assertions about prose.
"""

from __future__ import annotations

import random

from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.narrative.engine import NarrativeEngine
from ai_native_rpg.scenario import (
    load_event_script,
    load_narrative_directives,
    load_scenario,
)
from ai_native_rpg.schemas.narrative import NarrativeOperator
from ai_native_rpg.schemas.world_state import Visibility
from ai_native_rpg.world.manager import WorldStateManager

PACK = "village_disappearance"
PLAYER = "player_1"
NPC_A = "npc_a"


def _content(**overrides) -> dict:
    payload = {
        "summary": "玛尔塔在门后停了很久",
        "dialogue_hook": "……你先说你是谁。",
        "participants": ["玛尔塔"],
    }
    payload.update(overrides)
    return payload


def _engine(*, responses: int = 4) -> tuple[NarrativeEngine, WorldStateManager]:
    world = load_scenario(PACK)
    manager = WorldStateManager(world)
    engine = NarrativeEngine(
        manager=manager,
        llm=MockLLMClient([_content() for _ in range(responses)]),
        directives=load_narrative_directives(PACK),
        script=load_event_script(PACK),
    )
    return engine, manager


class TestTheOpeningEvent:
    def test_the_first_tick_selects_m1_and_opens_it(self):
        engine, manager = _engine()

        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is not None
        assert tick.selected.event_id == "M1_knock"
        active = manager.snapshot().story_beats.active_event
        assert active is not None
        assert (active.event_id, active.exchanges) == ("M1_knock", 0)

    def test_the_operator_reaches_the_generator_as_how_to_voice_it(self):
        """docs/13 §2: the operator survives, demoted to presentation.

        Worth pinning because the demotion is easy to mistake for removal — the
        generator still needs to be told whether it is planting or disclosing.
        """
        engine, _ = _engine()

        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is not None
        assert tick.selected.operator is NarrativeOperator.REVEAL
        assert tick.event is not None

    def test_authored_opening_needs_no_generation(self):
        world = load_scenario(PACK)
        manager = WorldStateManager(world)
        llm = MockLLMClient([_content(), _content()])
        engine = NarrativeEngine(
            manager=manager,
            llm=llm,
            directives=load_narrative_directives(PACK),
            script=load_event_script(PACK),
        )

        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is not None
        assert llm.calls == []
        assert engine.presented_lines == engine.script.get("M1_knock").presentation


class TestGoodwillPath:
    def test_the_full_chain_moves_trust(self):
        """选中 → 表达 → 回应 → 映射到结果 → 数值变化, once through."""
        engine, manager = _engine()
        before = manager.get_trust(NPC_A, PLAYER)

        engine.tick(player_id=PLAYER)
        record = engine.resolve_player_response(
            player_id=PLAYER, option_id="goodwill", rng=random.Random(0)
        )

        assert record is not None
        assert record.outcome_id == "she_opens"
        assert record.finished
        assert manager.get_trust(NPC_A, PLAYER) == before + 3.0

    def test_the_event_is_recorded_as_completed_but_not_closed(self):
        """docs/15 §4 M1: a slot spent without success is a cost, not a tombstone."""
        engine, manager = _engine()
        engine.tick(player_id=PLAYER)

        engine.resolve_player_response(player_id=PLAYER, option_id="goodwill", rng=random.Random(0))

        beats = manager.snapshot().story_beats
        assert beats.has_completed("M1_knock")
        assert not beats.is_closed("M1_knock")
        assert beats.active_event is None


class TestTheOpeningPressFailsAsDesigned:
    def test_pressing_at_opening_trust_is_a_certain_failure(self):
        """docs/15 §4 M1: trust 10 against threshold 25 is a 15-point gap.

        The lesson lands only if it is certain. A player who fails here and succeeds
        next time by luck learns nothing; one who fails and is later told "she still
        does not trust you" has learned the actual rule.
        """
        engine, manager = _engine()
        engine.tick(player_id=PLAYER)

        record = engine.resolve_player_response(
            player_id=PLAYER, option_id="press_that_night", rng=random.Random(0)
        )

        assert record is not None
        assert record.band == "certain_failure"
        assert record.outcome_id == "she_answers_through_the_door"
        # fear +10, and trust untouched
        rel = manager.get_relationship(NPC_A, PLAYER)
        assert (rel.trust, rel.fear) == (10.0, 40.0)

    def test_the_record_explains_the_failure_numerically(self):
        """docs/07: the panel must be able to say why, not just that."""
        engine, _ = _engine()
        engine.tick(player_id=PLAYER)

        record = engine.resolve_player_response(
            player_id=PLAYER, option_id="press_that_night", rng=random.Random(0)
        )

        assert record is not None
        assert record.threshold == 25.0
        assert record.current_value == 10.0
        # "15 short of the threshold" is the sentence the panel needs to be able to say
        assert record.margin == -15.0

    def test_an_unchecked_option_has_no_margin_to_report(self):
        engine, _ = _engine()
        engine.tick(player_id=PLAYER)

        record = engine.resolve_player_response(
            player_id=PLAYER, option_id="goodwill", rng=random.Random(0)
        )

        assert record is not None
        assert record.margin is None


class TestObserveDoesNotAdvance:
    def test_observing_reveals_a_fact_without_moving_relationship_values(self):
        """docs/15 §2: [观察] is the one option that risks nothing."""
        engine, manager = _engine()
        engine.tick(player_id=PLAYER)
        engine.resolve_player_response(player_id=PLAYER, option_id="goodwill")
        engine.tick(player_id=PLAYER)
        engine.resolve_player_response(player_id=PLAYER, option_id="help_latch")
        engine.tick(player_id=PLAYER)
        before = manager.get_relationship(NPC_A, PLAYER)

        record = engine.resolve_player_response(
            player_id=PLAYER, option_id="observe_hands", rng=random.Random(0)
        )

        assert record is not None
        assert record.outcome_id == "noticed_her_hands"
        after = manager.get_relationship(NPC_A, PLAYER)
        assert (after.trust, after.fear) == (before.trust, before.fear)
        assert manager.snapshot().facts["marta_is_terrified"].visibility is Visibility.REVEALED

    def test_observing_leaves_the_event_open(self):
        """It gives the player something without moving the scene on (docs/15 §4)."""
        engine, manager = _engine()
        engine.tick(player_id=PLAYER)
        engine.resolve_player_response(player_id=PLAYER, option_id="goodwill")
        engine.tick(player_id=PLAYER)
        engine.resolve_player_response(player_id=PLAYER, option_id="help_latch")
        engine.tick(player_id=PLAYER)

        record = engine.resolve_player_response(
            player_id=PLAYER, option_id="observe_hands", rng=random.Random(0)
        )

        assert record is not None and not record.finished
        assert manager.snapshot().story_beats.active_event is not None


class TestPatience:
    def test_unmatched_chat_does_not_spend_the_authored_decision(self):
        """Optional conversation cannot silently choose or exhaust a decision."""
        engine, manager = _engine(responses=6)
        engine.tick(player_id=PLAYER)

        records = [
            engine.resolve_player_response(player_id=PLAYER, option_id=None) for _ in range(4)
        ]

        assert records[-1] is not None
        assert records[-1].outcome_id is None
        assert not records[-1].finished
        # "没谈成" leaves trust untouched (docs/15 §4 M1)
        assert manager.get_trust(NPC_A, PLAYER) == 10.0

    def test_an_unrecognised_reply_does_not_end_the_scene_early(self):
        """Free text that matches no option is conversation, not a resolution.

        Falling to the default on the first ordinary remark would make every event one
        line long — the opposite failure to an event that never ends, and just as wrong.
        """
        engine, manager = _engine(responses=6)
        engine.tick(player_id=PLAYER)

        record = engine.resolve_player_response(player_id=PLAYER, option_id=None)

        assert record is not None and not record.finished
        assert record.outcome_id is None
        assert manager.snapshot().story_beats.active_event is not None

    def test_no_active_event_means_nothing_to_resolve(self):
        engine, _ = _engine()

        assert engine.resolve_player_response(player_id=PLAYER, option_id="goodwill") is None


class TestTheChainContinuesIntoM2AndF1:
    def _reach_m2(self, engine):
        engine.tick(player_id=PLAYER)
        engine.resolve_player_response(player_id=PLAYER, option_id="goodwill")
        engine.tick(player_id=PLAYER)
        engine.resolve_player_response(player_id=PLAYER, option_id="help_latch")
        engine.tick(player_id=PLAYER)

    def test_helping_opens_m2_without_artificial_trust_changes(self):
        engine, manager = _engine()
        self._reach_m2(engine)
        assert manager.get_trust(NPC_A, PLAYER) == 28
        assert engine.active_event().event_id == "M2_window"

    def test_f1_automatically_lands_and_registers_its_authored_payoff(self):
        engine, manager = _engine()
        self._reach_m2(engine)
        engine.resolve_player_response(player_id=PLAYER, option_id="goodwill")
        engine.tick(player_id=PLAYER)
        world = manager.snapshot()
        assert "F1_things_missing" in world.story_beats.completed_events
        assert world.facts["ella_things_missing"].visibility is Visibility.REVEALED
        entry = world.story_beats.open_foreshadowings["hint:F1_things_missing"]
        assert entry.payoff_condition == world.facts["ella_whereabouts"].reveal_condition
        assert engine.active_event().event_id == "M3_witness"

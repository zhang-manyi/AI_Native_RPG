"""Fear has to actually rise (docs/14 §4.3).

"玛尔塔彻底闭口" is gated on ``fear >= 70`` and in play ``fear`` barely moved: NPCs
proposing ``adjust_relationship`` almost only touched ``trust``, and the planning
prompt's examples were all about trust, so the model had no reason to do otherwise. An
ending whose gate nothing moves is the third instance of the failure docs/13 §11 records
twice — an author wrote a road and nobody checked it went anywhere.

Two halves, and the split is the point:

* **the prompt** now names all three dimensions and says outright that trust and fear are
  not two ends of one axis. Necessary, but a prompt cannot be *relied* on: the model does
  not know the ending exists.
* **the floor** is deterministic. A failed [追问]/[试探] raises fear whether or not the
  outcome prices it, so the channel does not depend on a model volunteering the right
  dimension. That is what makes the last class here — the reachability walk — meaningful.
"""

from __future__ import annotations

import random
import uuid
from pathlib import Path

import pytest

from ai_native_rpg.narrative.resolution import apply_outcome, resolve_option
from ai_native_rpg.schemas.common import MAX_RELATIONSHIP_STEP
from ai_native_rpg.schemas.events import (
    FEAR_ON_FAILED_PRESS,
    FEAR_ON_FAILED_PROBE,
    EventDefinition,
    EventOption,
    EventOutcome,
    OptionCheck,
    OptionTag,
    fear_floor_for_failure,
)
from ai_native_rpg.schemas.narrative import NarrativeOperator
from ai_native_rpg.schemas.world_state import ActionProposal, WorldState
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"
NPC_A = "npc_a"

#: The gate docs/14 §4.3 puts the clam-up ending behind.
CLAMS_UP_AT = 70.0


def _event(**overrides) -> EventDefinition:
    """An event with one option per checked tag, and outcomes that price nothing.

    Deliberately silent on ``fear``: the floor exists for exactly these outcomes, and
    "这次没谈成" with no deltas is the common shape in docs/15 §4.
    """
    spec: dict = {
        "event_id": "M_test",
        "operator": NarrativeOperator.REVEAL,
        "npc_id": NPC_A,
        "trigger": {"clauses": []},
        "max_exchanges": 4,
        "outcomes": {
            "she_told": EventOutcome(outcome_id="she_told"),
            "she_refused": EventOutcome(outcome_id="she_refused"),
        },
        "default_outcome": "she_refused",
        "options": [
            EventOption(
                option_id="press",
                tag=OptionTag.PRESS,
                text="你到底在怕谁",
                check=OptionCheck(npc_id=NPC_A, dimension="trust", threshold=90),
                on_success="she_told",
                on_failure="she_refused",
            ),
            EventOption(
                option_id="probe",
                tag=OptionTag.PROBE,
                text="有人说那晚看见你在外面",
                check=OptionCheck(npc_id=NPC_A, dimension="trust", threshold=90),
                on_success="she_told",
                on_failure="she_refused",
            ),
            EventOption(
                option_id="kind",
                tag=OptionTag.GOODWILL,
                text="我不是来添麻烦的",
                on_success="she_told",
            ),
            EventOption(
                option_id="look",
                tag=OptionTag.OBSERVE,
                text="（她的手按着门框）",
                on_success="she_refused",
            ),
        ],
    }
    spec.update(overrides)
    return EventDefinition.model_validate(spec)


@pytest.fixture
def manager(world: WorldState) -> WorldStateManager:
    return WorldStateManager(world)


def _resolve_and_apply(manager, event, option_id) -> None:
    world = manager.snapshot()
    resolution = resolve_option(event, option_id, world, player_id=PLAYER, rng=random.Random(0))
    outcome = event.outcome_for(resolution.outcome_id)
    apply_outcome(
        manager,
        event=event,
        outcome=outcome,
        player_id=PLAYER,
        failed_tag=resolution.failed_tag,
    )


class TestTheFloorAppliesOnFailure:
    """The deterministic half: pressing and missing costs her something."""

    def test_a_failed_press_raises_fear(self, manager):
        before = manager.get_relationship(NPC_A, PLAYER).fear

        _resolve_and_apply(manager, _event(), "press")

        after = manager.get_relationship(NPC_A, PLAYER).fear
        assert after == pytest.approx(before + FEAR_ON_FAILED_PRESS)

    def test_a_failed_probe_raises_fear_more(self, manager):
        """Wider band, heavier consequence: a probe that misses is a caught manoeuvre."""
        before = manager.get_relationship(NPC_A, PLAYER).fear

        _resolve_and_apply(manager, _event(), "probe")

        after = manager.get_relationship(NPC_A, PLAYER).fear
        assert after == pytest.approx(before + FEAR_ON_FAILED_PROBE)
        assert FEAR_ON_FAILED_PROBE > FEAR_ON_FAILED_PRESS

    def test_a_successful_press_does_not(self, manager):
        """Pressing her and getting somewhere is not what frightens her."""
        world = manager.snapshot()
        world.relationships[NPC_A][PLAYER].trust = 100.0
        high_trust = WorldStateManager(world)
        before = high_trust.get_relationship(NPC_A, PLAYER).fear

        _resolve_and_apply(high_trust, _event(), "press")

        assert high_trust.get_relationship(NPC_A, PLAYER).fear == pytest.approx(before)

    @pytest.mark.parametrize("option_id", ["kind", "look"])
    def test_the_unchecked_tags_never_raise_fear(self, manager, option_id):
        """docs/15 §2's asymmetry: [示好] and [观察] cannot fail, so there is nothing to
        charge for — and [观察] is specifically the option a frightened player reaches for."""
        before = manager.get_relationship(NPC_A, PLAYER).fear

        _resolve_and_apply(manager, _event(), option_id)

        assert manager.get_relationship(NPC_A, PLAYER).fear == pytest.approx(before)

    def test_the_floor_is_zero_for_unchecked_tags(self):
        assert fear_floor_for_failure(OptionTag.GOODWILL) == 0.0
        assert fear_floor_for_failure(OptionTag.OBSERVE) == 0.0
        assert fear_floor_for_failure(OptionTag.PRESS) == FEAR_ON_FAILED_PRESS
        assert fear_floor_for_failure(OptionTag.PROBE) == FEAR_ON_FAILED_PROBE


class TestAuthoredValuesWin:
    """An outcome that prices the failure has had this beat thought about."""

    def test_an_authored_fear_change_is_not_topped_up(self, manager):
        """Otherwise every number in docs/15 §4 would quietly inflate."""
        event = _event(
            outcomes={
                "she_told": EventOutcome(outcome_id="she_told"),
                "she_refused": EventOutcome(
                    outcome_id="she_refused", relationship_changes={NPC_A: {"fear": 12}}
                ),
            }
        )
        before = manager.get_relationship(NPC_A, PLAYER).fear

        _resolve_and_apply(manager, event, "press")

        after = manager.get_relationship(NPC_A, PLAYER).fear
        assert after == pytest.approx(before + 12)

    def test_an_authored_trust_change_still_gets_the_fear_floor(self, manager):
        """The two are separate dimensions, not two ends of one axis (docs/14 §4.3).

        An outcome that only lowers trust has said nothing about fear, so the floor
        applies — which is the case M2's ``she_pulls_back`` would hit if it did not
        already name both.
        """
        event = _event(
            outcomes={
                "she_told": EventOutcome(outcome_id="she_told"),
                "she_refused": EventOutcome(
                    outcome_id="she_refused", relationship_changes={NPC_A: {"trust": -8}}
                ),
            }
        )
        rel_before = manager.get_relationship(NPC_A, PLAYER)

        _resolve_and_apply(manager, event, "press")

        rel_after = manager.get_relationship(NPC_A, PLAYER)
        assert rel_after.trust == pytest.approx(rel_before.trust - 8)
        assert rel_after.fear == pytest.approx(rel_before.fear + FEAR_ON_FAILED_PRESS)

    def test_the_floor_stays_within_the_step_limit(self):
        """The Validator would reject anything larger, so the constants must fit."""
        assert FEAR_ON_FAILED_PRESS <= MAX_RELATIONSHIP_STEP
        assert FEAR_ON_FAILED_PROBE <= MAX_RELATIONSHIP_STEP


class TestRunningOutOfPatienceIsNotAFailedCheck:
    """The default outcome looks the same either way, and it should not be priced the same.

    Landing the default because patience ran out is "this went nowhere" (docs/13 §3.1);
    landing it because a probe missed is a caught attempt. Only the second frightens her.
    """

    def test_out_of_patience_carries_no_failed_tag(self):
        from ai_native_rpg.narrative.resolution import resolve_out_of_patience

        resolution = resolve_out_of_patience(_event())

        assert resolution.failed_tag is None

    def test_out_of_patience_does_not_raise_fear(self, manager):
        from ai_native_rpg.narrative.resolution import resolve_out_of_patience

        event = _event()
        resolution = resolve_out_of_patience(event)
        before = manager.get_relationship(NPC_A, PLAYER).fear

        apply_outcome(
            manager,
            event=event,
            outcome=event.outcome_for(resolution.outcome_id),
            player_id=PLAYER,
            failed_tag=resolution.failed_tag,
        )

        assert manager.get_relationship(NPC_A, PLAYER).fear == pytest.approx(before)


class TestThePromptAsksForAllThreeDimensions:
    """The other half. Not a substitute for the floor — a check that the examples are no
    longer trust-only, which is the specific thing docs/14 §4.3 blames.
    """

    def test_the_planning_prompt_mentions_fear_and_respect(self):
        text = _planning_prompt()

        assert "fear" in text
        assert "respect" in text

    def test_it_says_trust_and_fear_are_not_one_axis(self):
        """The misconception that produces trust-only proposals: treating fear as
        negative trust, so moving one is taken to mean the other is handled."""
        text = _planning_prompt()

        assert "trust 和 fear 是两件事" in text

    def test_it_gives_a_concrete_multi_dimension_example(self):
        """An instruction with only trust-shaped examples is what produced the bug."""
        text = _planning_prompt()

        assert '"fear"' in text

    def test_it_names_what_raises_fear(self):
        text = _planning_prompt()

        assert "逼问" in text
        assert "威胁" in text


def _planning_prompt() -> str:
    root = Path(__file__).resolve().parents[1] / "prompts"
    return (root / "npc_planning.txt").read_text(encoding="utf-8")


class TestTheClamUpEndingIsReachable:
    """docs/13 §11: every ending needs a state path somebody has actually walked.

    This is the test that would have caught the bug. It presses repeatedly and checks that
    ``fear`` crosses 70 — the gate docs/14 §4.3 puts the ending behind — using only the
    deterministic floor, with no authored fear values and no model involved.
    """

    def test_repeated_pressing_reaches_the_clam_up_threshold(self, manager):
        event = _event()
        presses = 0

        while manager.get_relationship(NPC_A, PLAYER).fear < CLAMS_UP_AT and presses < 40:
            _resolve_and_apply(manager, event, "probe")
            presses += 1

        assert manager.get_relationship(NPC_A, PLAYER).fear >= CLAMS_UP_AT
        assert presses < 40, "the ending must be reachable, not merely approachable"

    def test_it_takes_several_events_rather_than_one(self, manager):
        """``MAX_RELATIONSHIP_STEP`` is what makes this a sequence (docs/15 §3.1).

        Reaching 70 from 10 in one action would mean a single line of dialogue could end
        the social route, which is the thing the step cap exists to prevent.
        """
        event = _event()
        _resolve_and_apply(manager, event, "probe")

        assert manager.get_relationship(NPC_A, PLAYER).fear < CLAMS_UP_AT

    def test_the_pack_gates_its_ending_on_a_value_that_now_moves(self, manager):
        """The narrower claim, stated as the loader would: the path resolves *and* the
        number it reads is one the game changes."""
        from ai_native_rpg.world.conditions import resolve_path

        before = resolve_path(manager.snapshot(), f"relationships.{NPC_A}.{PLAYER}.fear")

        _resolve_and_apply(manager, _event(), "probe")

        after = resolve_path(manager.snapshot(), f"relationships.{NPC_A}.{PLAYER}.fear")
        assert after > before

    def test_clamming_up_does_not_close_the_other_routes(self, manager):
        """docs/14 §4.3: 闭口不等于失败终局 — it is a cost, not a tombstone.

        With Marta silent the tavern and the forest must still be reachable, which is
        what makes ``loren_that_night``'s stage channel the fallback it was written to be.
        """
        world = manager.snapshot()
        world.relationships[NPC_A][PLAYER].fear = 80.0
        world.player_locations[PLAYER] = "village_square"
        silent = WorldStateManager(world)

        for destination in ("tavern", "village_square", "forest_edge"):
            result = silent.submit(
                ActionProposal(
                    proposal_id=uuid.uuid4().hex,
                    actor_id=PLAYER,
                    action_type="move",
                    target_id=destination,
                )
            )
            assert result.approved, f"{destination} must stay reachable after she clams up"

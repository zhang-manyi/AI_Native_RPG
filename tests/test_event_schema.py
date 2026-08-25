"""Event definitions and option checks (docs/15 §1-§3).

Pure functions over authored data, so this is strict-TDD territory (docs/09 §5).

The distinction pinned hardest here is the one docs/15 §6.2 ends on: an event's
``trigger`` is a hard ``Condition`` (``trust >= 40`` is unmet at 38, full stop),
while an *option's* check is the three-band comparison with a random middle. They
are two rule sets, and conflating them would make every authored threshold behave
differently from what the script says.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
from ai_native_rpg.schemas.events import (
    FLAG_PROBED_ONCE,
    PROBE_PENALTY_AFTER_FAILED_PROBE,
    CheckBand,
    EventDefinition,
    EventOption,
    EventOutcome,
    OptionCheck,
    OptionTag,
    check_band,
    effective_threshold,
    scaled_trust_gain,
)
from ai_native_rpg.schemas.narrative import NarrativeOperator
from ai_native_rpg.world.actions import MAX_RELATIONSHIP_STEP

NPC_A = "npc_a"


def _trust_at_least(value: int) -> Condition:
    return Condition(
        clauses=[
            ConditionClause(
                path=f"relationships.{NPC_A}.player_1.trust", op=ConditionOp.GTE, value=value
            )
        ]
    )


class TestThreeBandCheck:
    """docs/15 §3: below by more than the band is certain failure, above by more
    than the band is certain success, and only the middle is random."""

    def test_far_below_the_threshold_is_certain_failure(self):
        assert check_band(current=10.0, threshold=25.0, band=8.0) is CheckBand.CERTAIN_FAILURE

    def test_far_above_the_threshold_is_certain_success(self):
        assert check_band(current=50.0, threshold=25.0, band=8.0) is CheckBand.CERTAIN_SUCCESS

    def test_within_the_band_is_random(self):
        assert check_band(current=30.0, threshold=25.0, band=8.0) is CheckBand.RANDOM

    def test_exactly_at_the_threshold_is_random_not_success(self):
        """The band is centred on the threshold, so meeting it exactly is a coin flip.

        Stated as a test because the alternative reading — "meeting the threshold
        succeeds" — would make the band asymmetric and silently narrow every
        authored risk by half.
        """
        assert check_band(current=25.0, threshold=25.0, band=8.0) is CheckBand.RANDOM

    def test_the_band_edges_are_inclusive_of_random(self):
        assert check_band(current=17.0, threshold=25.0, band=8.0) is CheckBand.RANDOM
        assert check_band(current=33.0, threshold=25.0, band=8.0) is CheckBand.RANDOM
        # One point further out on each side leaves the band entirely.
        assert check_band(current=16.9, threshold=25.0, band=8.0) is CheckBand.CERTAIN_FAILURE
        assert check_band(current=33.1, threshold=25.0, band=8.0) is CheckBand.CERTAIN_SUCCESS

    def test_a_zero_band_removes_randomness_entirely(self):
        """[示好] and [观察] carry band 0 (docs/15 §3): amplitude differs, nothing rolls."""
        assert check_band(current=25.0, threshold=25.0, band=0.0) is CheckBand.CERTAIN_SUCCESS
        assert check_band(current=24.9, threshold=25.0, band=0.0) is CheckBand.CERTAIN_FAILURE

    def test_m1_probe_fails_by_design_at_opening_trust(self):
        """docs/15 §4 M1: trust 10 against threshold 25 is a 15-point gap.

        The scripted lesson of the opening — press too early and it fails, and the
        failure reads as sense — depends on this being *certain*, not unlucky.
        """
        assert check_band(current=10.0, threshold=25.0, band=8.0) is CheckBand.CERTAIN_FAILURE


class TestProbePenalty:
    """docs/15 §4 M2: a failed [试探] raises every later probe's threshold."""

    def test_a_clean_slate_leaves_the_threshold_alone(self):
        assert effective_threshold(35.0, tag=OptionTag.PROBE, flags=[]) == 35.0

    def test_a_failed_probe_raises_later_probe_thresholds(self):
        raised = effective_threshold(55.0, tag=OptionTag.PROBE, flags=[FLAG_PROBED_ONCE])
        assert raised == 55.0 + PROBE_PENALTY_AFTER_FAILED_PROBE
        # docs/15 §4 M3 states the resulting number outright.
        assert raised == 65.0

    def test_the_penalty_applies_only_to_probes(self):
        """It narrows one road, not the whole map (docs/13 §8: shape, not progress)."""
        for tag in (OptionTag.GOODWILL, OptionTag.PRESS, OptionTag.OBSERVE):
            assert effective_threshold(35.0, tag=tag, flags=[FLAG_PROBED_ONCE]) == 35.0


class TestScaledTrustGain:
    """docs/15 §3.2: [示好] pays more as she grows less wary."""

    @pytest.mark.parametrize(
        ("trust", "expected"),
        [(0.0, 8.0), (24.9, 8.0), (25.0, 12.0), (55.0, 12.0), (55.1, 15.0), (90.0, 15.0)],
    )
    def test_the_curve_accelerates_across_three_bands(self, trust: float, expected: float):
        assert scaled_trust_gain(trust) == expected

    def test_the_top_band_stops_at_the_validator_limit(self):
        """docs/15 §3.1: no single outcome may move a relationship past the cap.

        Pinned against the Validator's own constant rather than the literal 15, so
        that lowering the cap fails here instead of at runtime.
        """
        assert scaled_trust_gain(90.0) == MAX_RELATIONSHIP_STEP


class TestOutcomeShape:
    def test_an_outcome_may_not_exceed_the_relationship_step_limit(self):
        """docs/15 §3.1: the Validator would reject it at runtime; reject it at authoring.

        The Validator is the real gate, but it only speaks when the outcome fires —
        which for a rare branch could be long after the pack shipped. Same stance as
        the scenario loader: turn a silent content bug into a startup error.
        """
        with pytest.raises(ValidationError, match=r"MAX_RELATIONSHIP_STEP|exceeds"):
            EventOutcome(
                outcome_id="too_generous",
                relationship_changes={NPC_A: {"trust": MAX_RELATIONSHIP_STEP + 1}},
            )

    def test_the_limit_applies_to_negative_changes_too(self):
        with pytest.raises(ValidationError, match=r"MAX_RELATIONSHIP_STEP|exceeds"):
            EventOutcome(
                outcome_id="too_harsh",
                relationship_changes={NPC_A: {"trust": -(MAX_RELATIONSHIP_STEP + 1)}},
            )

    def test_a_change_at_the_limit_is_allowed(self):
        """S1 gives exactly +15 (docs/15 §4 支线), so the bound is inclusive."""
        outcome = EventOutcome(
            outcome_id="did_her_a_favour",
            relationship_changes={NPC_A: {"trust": MAX_RELATIONSHIP_STEP}},
        )
        assert outcome.relationship_changes[NPC_A]["trust"] == MAX_RELATIONSHIP_STEP

    def test_unknown_relationship_dimensions_are_rejected(self):
        """A typo would otherwise be dropped by the Validator at fire time."""
        with pytest.raises(ValidationError, match="dimension"):
            EventOutcome(outcome_id="typo", relationship_changes={NPC_A: {"turst": 5}})

    def test_an_outcome_may_change_nothing(self):
        """Most default outcomes are "this did not go anywhere" (docs/13 §3.1).

        Explicitly legal: a default outcome that had to move a number would make
        running out of patience cost something the author never chose.
        """
        outcome = EventOutcome(outcome_id="no_deal")
        assert not outcome.relationship_changes
        assert not outcome.reveals_facts


class TestOptionShape:
    def test_an_unchecked_tag_may_not_carry_a_check(self):
        """docs/15 §2: [示好] and [观察] differ in amplitude, not in success.

        Authoring a threshold on one would produce an option that mysteriously
        fails — the exact frustration §2 calls out as both unfair and off-model.
        """
        with pytest.raises(ValidationError, match=r"never checked|does not take a check"):
            EventOption(
                option_id="warm",
                tag=OptionTag.GOODWILL,
                text="我不是来添麻烦的",
                check=OptionCheck(npc_id=NPC_A, dimension="trust", threshold=25),
                on_success="she_opens",
            )

    def test_a_checked_option_needs_a_failure_outcome(self):
        """Otherwise failing would land nowhere and the event would stall silently."""
        with pytest.raises(ValidationError, match="on_failure"):
            EventOption(
                option_id="press",
                tag=OptionTag.PRESS,
                text="那晚你在外面，对吗",
                check=OptionCheck(npc_id=NPC_A, dimension="trust", threshold=25),
                on_success="she_answers",
            )

    def test_a_checked_option_defaults_to_the_tags_band(self):
        """docs/15 §3: [试探] is ±12, [追问] ±8. The band is design language, so the
        tag carries it and the author writes a threshold only."""
        probe = EventOption(
            option_id="probe",
            tag=OptionTag.PROBE,
            text="我听说那晚有人看见你了",
            check=OptionCheck(npc_id=NPC_A, dimension="trust", threshold=35),
            on_success="she_slips",
            on_failure="she_shuts",
        )
        press = EventOption(
            option_id="press",
            tag=OptionTag.PRESS,
            text="木屑是怎么来的",
            check=OptionCheck(npc_id=NPC_A, dimension="trust", threshold=30),
            on_success="half_told",
            on_failure="deflected",
        )
        assert probe.check is not None and probe.check.effective_band(probe.tag) == 12.0
        assert press.check is not None and press.check.effective_band(press.tag) == 8.0


def _minimal_event(**overrides) -> EventDefinition:
    spec: dict = {
        "event_id": "M_test",
        "operator": NarrativeOperator.REVEAL,
        "trigger": _trust_at_least(20),
        "npc_id": NPC_A,
        "outcomes": {"told": EventOutcome(outcome_id="told")},
        "default_outcome": "told",
        "max_exchanges": 4,
    }
    spec.update(overrides)
    return EventDefinition.model_validate(spec)


class TestEventShape:
    def test_every_option_outcome_reference_must_exist(self):
        """An option pointing at a missing outcome is the loader's business.

        docs/13 §11: the recurring failure is an author writing a path nobody
        verified. A dangling ``on_success`` would raise deep inside a turn, at the
        one moment least recoverable.
        """
        with pytest.raises(ValidationError, match=r"unknown outcome|no_such_outcome"):
            _minimal_event(
                options=[
                    EventOption(
                        option_id="warm",
                        tag=OptionTag.GOODWILL,
                        text="我不是来添麻烦的",
                        on_success="no_such_outcome",
                    )
                ]
            )

    def test_the_default_outcome_must_exist(self):
        with pytest.raises(ValidationError, match="default_outcome"):
            _minimal_event(default_outcome="nowhere")

    def test_an_event_needs_at_least_one_outcome(self):
        """docs/13 §3.1: an event that cannot end is a hang, not a scene."""
        with pytest.raises(ValidationError):
            _minimal_event(outcomes={}, default_outcome="told")

    def test_max_exchanges_must_be_positive(self):
        with pytest.raises(ValidationError):
            _minimal_event(max_exchanges=0)

    def test_an_event_with_one_outcome_set_takes_no_options(self):
        """docs/15 §1: if every option leads to the same outcome set, it is not a choice.

        F1 is the case in the script — it puts a clue in front of the player rather
        than asking anything — and §1 states the criterion is executable. This is it
        being executed.
        """
        with pytest.raises(ValidationError, match=r"single outcome|one outcome"):
            _minimal_event(
                options=[
                    EventOption(
                        option_id="a", tag=OptionTag.GOODWILL, text="甲", on_success="told"
                    ),
                    EventOption(option_id="b", tag=OptionTag.OBSERVE, text="乙", on_success="told"),
                ]
            )

    def test_a_scripted_line_event_is_valid_without_options(self):
        """F1's shape: scripted lines, one outcome, no decision point."""
        event = _minimal_event(
            event_id="F1_ella_things_missing",
            operator=NarrativeOperator.FORESHADOW,
            scripted_lines=["艾拉的外衣不在，她攒的钱也不在。"],
            max_exchanges=2,
        )
        assert not event.options
        assert event.has_options is False

    def test_option_ids_must_be_unique_within_an_event(self):
        """Duplicates would make "which option did the player pick" ambiguous."""
        with pytest.raises(ValidationError, match=r"duplicate|unique"):
            _minimal_event(
                outcomes={
                    "told": EventOutcome(outcome_id="told"),
                    "other": EventOutcome(outcome_id="other"),
                },
                options=[
                    EventOption(
                        option_id="same", tag=OptionTag.GOODWILL, text="甲", on_success="told"
                    ),
                    EventOption(
                        option_id="same", tag=OptionTag.OBSERVE, text="乙", on_success="other"
                    ),
                ],
            )

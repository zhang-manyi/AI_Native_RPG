"""Experience Controller: tension admission, then preference ranking.

Deterministic, so written test-first. The load-bearing test is
``test_a_well_liked_candidate_still_loses_to_pacing``: docs/05 §2.2 and docs/10 §3
argue the two dimensions must not be multiplied, because a product still scores
"on taste but not now" highly. Two stages make that structurally impossible, and
that test is what proves the structure rather than the arithmetic.
"""

from __future__ import annotations

from ai_native_rpg.narrative.controller import select_candidate
from ai_native_rpg.schemas.narrative import (
    EventCandidate,
    NarrativeOperator,
    PlayerProfile,
    StoryBeats,
)


def _candidate(
    operator: NarrativeOperator, *, tag: str = "social", intensity: float = 0.5
) -> EventCandidate:
    return EventCandidate(
        operator=operator,
        event_type=f"{operator.value}_event",
        intensity=intensity,
        preference_tag=tag,
        trigger_reason="test",
    )


def _beats(*operators: NarrativeOperator, turn: int = 5) -> StoryBeats:
    beats = StoryBeats(turn=turn)
    for op in operators:
        beats.record_operator(op)
    return beats


class TestPacingAdmission:
    def test_no_two_reveals_in_a_row(self):
        # docs/10 §3.1, rule 1.
        verdict = select_candidate(
            [_candidate(NarrativeOperator.REVEAL)],
            beats=_beats(NarrativeOperator.REVEAL),
        )

        assert verdict.selected is None
        assert verdict.rejected[0].reason == "no two reveals in consecutive turns"

    def test_a_reveal_is_admissible_once_a_turn_has_passed(self):
        verdict = select_candidate(
            [_candidate(NarrativeOperator.REVEAL)],
            beats=_beats(NarrativeOperator.REVEAL, NarrativeOperator.RELIEVE),
        )

        assert verdict.selected is not None

    def test_escalate_needs_a_turn_between(self):
        # docs/10 §3.1, rule 3.
        verdict = select_candidate(
            [_candidate(NarrativeOperator.ESCALATE)],
            beats=_beats(NarrativeOperator.ESCALATE),
        )

        assert verdict.selected is None
        assert "escalate" in verdict.rejected[0].reason

    def test_escalate_is_admissible_two_turns_later(self):
        verdict = select_candidate(
            [_candidate(NarrativeOperator.ESCALATE)],
            beats=_beats(NarrativeOperator.ESCALATE, NarrativeOperator.RELIEVE),
        )

        assert verdict.selected is not None

    def test_foreshadow_and_reverse_have_no_cooldown(self):
        # Only reveal and escalate are rate-limited in docs/10 §3.1; the trigger
        # rules already constrain the other two (one open loop, one-shot reverse).
        for op in (NarrativeOperator.FORESHADOW, NarrativeOperator.REVERSE):
            verdict = select_candidate([_candidate(op)], beats=_beats(op))
            assert verdict.selected is not None, op

    def test_nothing_is_a_valid_outcome(self):
        """An empty candidate list yields None, not an error.

        docs/05 §3: "this turn nothing should happen" is a legitimate output —
        the implicit `relieve`. Free generation's most common failure is insisting
        something happen every turn.
        """
        verdict = select_candidate([], beats=_beats())

        assert verdict.selected is None
        assert verdict.rejected == []


class TestPreferenceRanking:
    def test_the_players_taste_decides_among_admissible_candidates(self):
        intrigue_lover = PlayerProfile(player_id="p", play_style={"intrigue": 0.9, "social": 0.1})

        verdict = select_candidate(
            [
                _candidate(NarrativeOperator.REVEAL, tag="social"),
                _candidate(NarrativeOperator.REVERSE, tag="intrigue"),
            ],
            beats=_beats(),
            profile=intrigue_lover,
        )

        assert verdict.selected is not None
        assert verdict.selected.preference_tag == "intrigue"

    def test_an_absent_profile_treats_every_taste_as_neutral(self):
        # Slice 4 supplies profiles; until then ranking falls back to intensity,
        # which must not crash or silently prefer one tag.
        verdict = select_candidate(
            [
                _candidate(NarrativeOperator.REVEAL, tag="social", intensity=0.2),
                _candidate(NarrativeOperator.REVERSE, tag="intrigue", intensity=0.9),
            ],
            beats=_beats(),
        )

        assert verdict.selected is not None
        assert verdict.selected.intensity == 0.9

    def test_a_well_liked_candidate_still_loses_to_pacing(self):
        """Taste cannot buy its way past the pacing gate.

        This is the case docs/05 §2.2 uses to reject a single multiplied score: a
        player who loves reveals, having just had one. In a product, the high
        preference weight keeps the candidate near the top; in two stages it is
        already gone before ranking begins.
        """
        reveal_lover = PlayerProfile(player_id="p", play_style={"social": 1.0})

        verdict = select_candidate(
            [_candidate(NarrativeOperator.REVEAL, tag="social", intensity=1.0)],
            beats=_beats(NarrativeOperator.REVEAL),
            profile=reveal_lover,
        )

        assert verdict.selected is None

    def test_ranking_is_stable_for_equal_scores(self):
        # Trigger rules emit payoffs before fresh reveals; ties must preserve that
        # order so behaviour is reproducible from a trace.
        first = _candidate(NarrativeOperator.REVEAL, tag="social")
        second = _candidate(NarrativeOperator.REVERSE, tag="social")

        verdict = select_candidate([first, second], beats=_beats())

        assert verdict.selected is first


class TestStarvationGuard:
    def test_a_long_quiet_stretch_relaxes_the_cooldowns(self):
        """After N silent turns, a rushed beat beats a stalled story.

        docs/10 §3.1 lists "not N turns in a row with no operator" alongside the
        two cooldowns, but it is pressure to act, not a veto — as a hard filter it
        would filter itself empty. So it relaxes the cooldowns instead.
        """
        stalled = _beats(
            NarrativeOperator.REVEAL,
            NarrativeOperator.RELIEVE,
            NarrativeOperator.RELIEVE,
            NarrativeOperator.RELIEVE,
            NarrativeOperator.REVEAL,
        )

        # a reveal one turn after a reveal would normally be inadmissible
        verdict = select_candidate([_candidate(NarrativeOperator.REVEAL)], beats=stalled)

        assert verdict.selected is not None
        assert verdict.starved is True

    def test_a_healthy_rhythm_does_not_trigger_the_guard(self):
        verdict = select_candidate(
            [_candidate(NarrativeOperator.REVEAL)],
            beats=_beats(NarrativeOperator.ESCALATE, NarrativeOperator.RELIEVE),
        )

        assert verdict.starved is False


class TestVerdictIsInspectable:
    def test_rejections_record_what_was_blocked_and_why(self):
        """The panel shows candidates the pacing rules held back (docs/07 §2.3).

        Without this the Engine's most interesting decision — declining to act —
        is invisible, and indistinguishable from no rule having fired at all.

        Note only the escalate is blocked here: a turn records one operator, so
        with history ``reveal, escalate`` the reveal is already two turns back and
        legitimately admissible. Both being blocked at once is unreachable.
        """
        candidates = [
            _candidate(NarrativeOperator.REVEAL),
            _candidate(NarrativeOperator.ESCALATE),
        ]

        verdict = select_candidate(
            candidates, beats=_beats(NarrativeOperator.REVEAL, NarrativeOperator.ESCALATE)
        )

        assert [r.candidate.operator for r in verdict.rejected] == [NarrativeOperator.ESCALATE]
        assert verdict.rejected[0].reason
        assert verdict.selected is not None
        assert verdict.selected.operator is NarrativeOperator.REVEAL

    def test_every_candidate_blocked_yields_no_beat(self):
        verdict = select_candidate(
            [_candidate(NarrativeOperator.REVEAL)], beats=_beats(NarrativeOperator.REVEAL)
        )

        assert verdict.selected is None
        assert verdict.admissible == []
        assert len(verdict.rejected) == 1

    def test_the_admitted_but_unchosen_are_not_reported_as_rejected(self):
        # Losing a ranking is not the same as being blocked; conflating them would
        # make the panel read as though pacing vetoed a runner-up.
        verdict = select_candidate(
            [
                _candidate(NarrativeOperator.REVEAL, intensity=0.9),
                _candidate(NarrativeOperator.REVERSE, intensity=0.1),
            ],
            beats=_beats(),
        )

        assert verdict.selected is not None
        assert verdict.rejected == []
        assert len(verdict.admissible) == 2

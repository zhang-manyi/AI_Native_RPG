"""Experience Controller: which permitted beat to run, if any (docs/05 §2.2).

Two stages, deliberately not one product (docs/10 §3):

    tension admission (hard filter)  ->  preference ranking (soft score)

Tension is the story's clock and changes every beat; preference is the player's
taste and barely moves within a session. Multiplying them lets "on taste but not
now" keep scoring well — a player who loves reveals, one turn after a reveal, still
ranks a reveal top because the preference term is doing the work. Two stages make
that unrepresentable: the candidate is gone before scoring starts.

Returning ``None`` is a normal outcome, not a failure (docs/05 §3). The most common
way generated narrative goes wrong is insisting that every turn contain an event.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas.narrative import EventCandidate, NarrativeOperator, PlayerProfile, StoryBeats

#: Consecutive quiet turns after which the cooldowns relax. See ``_is_starved``.
STARVATION_THRESHOLD = 3

#: Quiet turns required between two firings of the same operator (docs/10 §3.1).
#: ``1`` means "at least one turn in between", i.e. blocked only when the previous
#: turn was that same operator. Both current rules — no consecutive reveals,
#: escalate needs a turn between — are that same shape.
#:
#: Operators absent from this table are unconstrained here because the trigger
#: rules already bound them: one foreshadowing may be open at a time, and a reverse
#: is one-shot.
_COOLDOWNS: dict[NarrativeOperator, int] = {
    NarrativeOperator.REVEAL: 1,
    NarrativeOperator.ESCALATE: 1,
}


@dataclass(frozen=True)
class Rejection:
    """A candidate the pacing rules held back, and the rule that did it.

    Recorded rather than dropped: "the Engine had something and chose to wait" is
    one of the more informative things the debug panel can show (docs/07 §2.3), and
    silently discarding it looks identical to no rule having fired.
    """

    candidate: EventCandidate
    reason: str


@dataclass(frozen=True)
class PacingVerdict:
    """The outcome of one selection: what ran, what was blocked, what was allowed."""

    selected: EventCandidate | None
    admissible: list[EventCandidate] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    starved: bool = False


def _is_starved(beats: StoryBeats) -> bool:
    """Whether the story has been quiet long enough to justify rushing.

    docs/10 §3.1 lists "no more than N turns with nothing happening" next to the
    two cooldowns, but it is not the same kind of rule: the cooldowns veto
    candidates, while this one demands one. Implemented as a relaxation of the
    cooldowns, on the judgement that a stalled story costs more than a slightly
    hurried beat.
    """
    trailing_quiet = 0
    for recorded in reversed(beats.recent_operators):
        if recorded != NarrativeOperator.RELIEVE.value:
            break
        trailing_quiet += 1
    if trailing_quiet >= STARVATION_THRESHOLD:
        return True

    # Also starved when the visible window as a whole is mostly silence. A single
    # beat surrounded by quiet turns is not a rhythm, even if the most recent turn
    # happened to be that beat — which is the case a trailing-run count misses.
    window = beats.recent_operators
    if len(window) >= STARVATION_THRESHOLD + 1:
        quiet_in_window = sum(1 for op in window if op == NarrativeOperator.RELIEVE.value)
        if quiet_in_window >= STARVATION_THRESHOLD:
            return True
    return False


def passes_pacing_rules(
    candidate: EventCandidate, beats: StoryBeats, *, starved: bool = False
) -> str | None:
    """``None`` if admissible, else the reason it is not.

    Returning the reason rather than a bool is what lets the panel explain a quiet
    turn instead of merely showing one.
    """
    cooldown = _COOLDOWNS.get(candidate.operator)
    if cooldown is None:
        return None

    distance = beats.turns_since(candidate.operator)
    if distance is None or distance > cooldown:
        return None

    if starved:
        # The story has stalled; a rushed beat is the lesser problem.
        return None

    if candidate.operator is NarrativeOperator.REVEAL:
        return "no two reveals in consecutive turns"
    return f"escalate needs at least {cooldown} quiet turn(s) before firing again"


def select_candidate(
    candidates: list[EventCandidate],
    *,
    beats: StoryBeats,
    profile: PlayerProfile | None = None,
) -> PacingVerdict:
    """Admit by tension, then rank by preference, then take the top one.

    Ranking is a stable ``max``: ties keep the order the trigger rules produced
    (payoffs ahead of fresh reveals), so a replayed trace selects the same beat.
    """
    starved = _is_starved(beats)

    admissible: list[EventCandidate] = []
    rejected: list[Rejection] = []
    for candidate in candidates:
        reason = passes_pacing_rules(candidate, beats, starved=starved)
        if reason is None:
            admissible.append(candidate)
        else:
            rejected.append(Rejection(candidate=candidate, reason=reason))

    if not admissible:
        return PacingVerdict(selected=None, admissible=[], rejected=rejected, starved=starved)

    def score(candidate: EventCandidate) -> float:
        weight = profile.weight_for(candidate.preference_tag) if profile else 0.5
        return weight * candidate.intensity

    return PacingVerdict(
        selected=max(admissible, key=score),
        admissible=admissible,
        rejected=rejected,
        starved=starved,
    )

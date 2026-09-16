"""Review only facts already visible to the player; no generated clues."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas.world_state import VisibleState


@dataclass(frozen=True)
class ClueReview:
    """What the player knows at the end of a day, grouped.

    Deliberately holds only ids and values the player already has. There is no field
    for "what he is missing *specifically*" — that would be the leak, since naming an
    absent clue describes it.
    """

    day: int
    known: dict[str, object] = field(default_factory=dict)
    unanswered: list[str] = field(default_factory=list)

    @property
    def known_count(self) -> int:
        return len(self.known)


#: Open questions the review can raise, keyed by what the player is missing.
#:
#: Phrased as questions the player could have asked himself, never as descriptions of the
#: answer: "那晚到底是谁从森林那边回来的" is a restatement of what he already knows he does
#: not know, while "去酒馆核对时间线" would be the system playing for him. The distinction
#: is the difference between organising and advancing.
_OPEN_QUESTION_BY_GAP = {
    "who": "那晚从森林那边回来的人，究竟是谁",
    "why": "如果有人在瞒着什么，瞒的是不是同一件事",
    "where": "艾拉现在在哪里",
}


def review_clues(view: VisibleState, *, day: int | None = None) -> ClueReview:
    """Group what the player can see into a review. Adds nothing.

    Takes the projection rather than the world, so the function *cannot* read a hidden
    fact — the constraint is in the signature (docs/13 §5.1). The counting is arithmetic,
    so no model is involved in deciding what is known; a generator may later phrase this,
    but it phrases only what is passed here.
    """
    known = dict(view.visible_facts)

    # Which questions are still open is derived from what is *absent* from the visible
    # set, which is safe in a way that naming the absent facts would not be: the player
    # already knows he cannot answer these.
    unanswered = [
        question for gap, question in _OPEN_QUESTION_BY_GAP.items() if not _is_answered(gap, known)
    ]

    return ClueReview(
        day=day if day is not None else view.time_day,
        known=known,
        unanswered=unanswered,
    )


#: Which visible fact ids settle which open question.
#:
#: Substrings, so a pack adding another clue about the same question needs no registration
#: — but *listed here* rather than inlined in the matching code, because the coupling is
#: invisible otherwise: renaming ``killer_identity`` to ``loren_that_night`` silently made
#: the "who" question unanswerable, and only a test caught it. A pack whose ids share none
#: of these simply keeps every question open, which is the safe direction: the review
#: overstates what is unknown rather than claiming something is settled.
_ANSWERED_BY = {
    "who": ("identity", "that_night", "who_"),
    "why": ("threatened", "hides", "motive"),
    "where": ("whereabouts", "found"),
}


def _is_answered(gap: str, known: dict[str, object]) -> bool:
    """Whether the visible set settles this question."""
    markers = _ANSWERED_BY.get(gap, ())
    return any(marker in fact_id for fact_id in known for marker in markers)

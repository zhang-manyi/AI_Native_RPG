"""The day's wrap-up: review what is known, then read the cards (docs/13 §4.2).

It arrives automatically after the third slot and **costs no slot** — a day is three
spendable slots whether or not the interlude happened. It is two things:

    review_clues()    reorganise what the player already knows
    tarot_reading()   a suggestive, non-committal read on where the case stands

**The review adds no information.** That is the whole constraint: a wrap-up that handed
out a new clue would be a free progress channel and the slot cost established in
docs/13 §4.1 would be refundable by simply waiting for evening. So it groups facts the
player already has into "what I know / what is still missing" and nothing else.

**It takes a ``VisibleState``, never a ``WorldState``.** The same mechanism ``build_scene``
uses (docs/12 §7, docs/07 §2.4): the type signature is the guarantee, so leaking a hidden
fact would require changing the parameter rather than forgetting a check. docs/13 §5.1 is
explicit that this must be mechanical rather than an instruction — an NPC misspeaking is
plot, but the "objective" voice misspeaking is a bug, and the player will believe it.

The tarot is the interesting case. Divination is *allowed* to be vague, so it can read
world-level shape — how many clues are still dark, how high the tension is — and answer in
imagery without naming anything. That makes it one of the few places an LLM can be pointed
at hidden structure without violating the visibility rule: the counts say how much is left,
never what it is. This module produces the *material* for that reading (the counts and the
imagery keys); turning it into prose is the generator's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas.world_state import VisibleState, WorldState

#: How many unrevealed facts count as "most of the case is still dark".
#:
#: A threshold rather than a raw count in the output because the reading must not become
#: a progress bar: "还有 7 条线索没揭露" is a number the player would optimise against,
#: while "大部分还在暗处" is an atmosphere. Same reasoning as docs/15 §2's refusal to
#: print ``[示好 65%]``.
_MOSTLY_DARK = 0.6

#: And the other end: nearly everything is out.
_MOSTLY_LIT = 0.25


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


@dataclass(frozen=True)
class TarotReading:
    """Material for the day's reading: shape, not content.

    ``darkness`` is the fraction of the case still hidden and ``tension`` is where the
    story stands. Both are numbers *about* the world rather than facts *from* it, which
    is what keeps this inside the visibility rule while still being informative.
    """

    day: int
    darkness: float
    tension: float
    imagery: tuple[str, ...] = ()

    @property
    def reads_as(self) -> str:
        """A coarse label for the generator to write from."""
        if self.darkness >= _MOSTLY_DARK:
            return "mostly_dark"
        if self.darkness <= _MOSTLY_LIT:
            return "nearly_clear"
        return "half_lit"


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


def tarot_reading(world: WorldState, view: VisibleState) -> TarotReading:
    """The day's reading: how much is still dark, and how tense it has got.

    This is the one place that reads ``WorldState`` — and it reads only *counts*. The
    numbers say how much is left, never what it is, which is why divination is the form
    that fits: it is allowed to be suggestive, so it can carry real information about
    shape without naming content (docs/13 §4.2).

    Once a day, per docs/13 §4.2, which is what keeps it a ritual rather than a lookup.
    The caller owns that cadence — this function is pure and would happily run twice.
    """
    total = len(world.facts) or 1
    darkness = 1.0 - (len(view.visible_facts) / total)

    return TarotReading(
        day=world.time_day,
        darkness=round(max(0.0, min(1.0, darkness)), 4),
        tension=world.story_beats.tension,
        imagery=_imagery_for(darkness, world.story_beats.tension),
    )


def _imagery_for(darkness: float, tension: float) -> tuple[str, ...]:
    """Keys the generator turns into images. Never fact-derived.

    Chosen from the two scalars only, so no path exists from a hidden fact's *content*
    to the reading. A card that changed with a specific hidden fact would be a leak
    dressed as atmosphere — the failure docs/13 §5.1 warns is worse for the "objective"
    voice than for an NPC.
    """
    cards: list[str] = []
    cards.append("the_moon" if darkness >= _MOSTLY_DARK else "the_star")
    if tension >= 0.6:
        cards.append("the_tower")
    elif tension <= 0.2:
        cards.append("the_hermit")
    return tuple(cards)

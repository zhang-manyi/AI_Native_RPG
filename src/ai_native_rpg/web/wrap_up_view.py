"""The day's wrap-up, as the player sees it (docs/12 §13.3).

A **new interface form**, not a variant of the scene page: no NPC, no sprite, no
composer. The day is over, and what is on screen is the player's own notebook plus a
card reading.

Like ``scene.py``, this module does not import ``WorldState`` and a test asserts that.
The protection it needs is the same and the reason is sharper: ``review_clues`` takes a
``VisibleState``, so leaking a hidden clue into the review would require *changing a
parameter type* rather than forgetting a check (docs/13 §5.1). Re-widening that here
would undo the mechanism, so the models below are built from ``ClueReview`` and
``TarotReading`` — both of which are already the safe projections.

**The reading carries no raw counts, and nothing here reconstructs them.** ``darkness``
is a fraction on purpose: "还剩 7 条" is a number a player optimises against, while "大
部分还在暗处" is atmosphere with real information behind it (docs/15 §2's refusal to
print ``[示好 65%]``). Multiplying the fraction by a total to recover the count would
reintroduce exactly what ``TarotReading`` was shaped to withhold — so no total is
available in this module, which is the structural version of remembering not to.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..narrative.wrap_up import ClueReview, TarotReading


class ReadingView(BaseModel):
    """The card reading: shape, never content.

    A one-to-one image of ``TarotReading``. ``darkness`` and ``tension`` are numbers
    *about* the world rather than facts *from* it, which is what lets a divination read
    hidden structure without naming any of it.
    """

    darkness: float = Field(
        ge=0.0,
        le=1.0,
        description="fraction of the case still unrevealed. A ratio, deliberately not a "
        "count — see the module docstring.",
    )
    tension: float = Field(ge=0.0, le=1.0)
    imagery: list[str] = Field(
        default_factory=list, description="card keys ('the_moon'), never fact-derived"
    )
    reads_as: str = Field(description="coarse label: mostly_dark / half_lit / nearly_clear")


class WrapUpView(BaseModel):
    """What the wrap-up screen shows (docs/13 §4.2).

    ``known`` is only what the player already had — the review reorganises, it never
    adds, or the slot cost of a day would be refundable by waiting for evening.

    ``unanswered`` is passed through **as written**: those are questions the player could
    have asked himself, and the renderer must not append "该去哪查". Turning an open
    question into a lead is the system doing the player's reasoning for him, which is a
    different thing from organising his notes.
    """

    day: int
    known: list[dict[str, Any]] = Field(
        default_factory=list, description="visible facts as {id, value}, in authored order"
    )
    unanswered: list[str] = Field(default_factory=list)
    reading: ReadingView


def build_wrap_up(*, review: ClueReview, reading: TarotReading) -> WrapUpView:
    """Assemble the wrap-up screen from the two halves the engine produced.

    Takes the projections rather than the world for the same reason ``build_scene`` does:
    with no ``WorldState`` in scope there is no path from this code to a hidden value.
    """
    return WrapUpView(
        day=review.day,
        # Same {id, value} shape the scene uses for visible facts, so the page renders
        # both with one helper.
        known=[{"id": k, "value": v} for k, v in review.known.items()],
        unanswered=list(review.unanswered),
        reading=ReadingView(
            darkness=reading.darkness,
            tension=reading.tension,
            imagery=list(reading.imagery),
            reads_as=reading.reads_as,
        ),
    )

"""Daily review of player-visible facts and recorded relationship changes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..narrative.wrap_up import ClueReview


class WrapUpView(BaseModel):
    day: int
    introduction: str = "今天的调查结束了。回家休息前，盘点一下今天的收获吧。"
    known: list[dict[str, Any]] = Field(default_factory=list)
    discovered: list[dict[str, Any]] = Field(default_factory=list)
    relationship_changes: list[dict[str, Any]] = Field(default_factory=list)
    unanswered: list[str] = Field(default_factory=list)
    baseline_available: bool = True


def build_wrap_up(
    *,
    review: ClueReview,
    baseline: dict[str, Any] | None = None,
    relationship_changes: list[dict[str, Any]] | None = None,
) -> WrapUpView:
    """Compare with the saved morning snapshot; old saves never invent daily history."""
    known = [{"id": k, "value": v} for k, v in review.known.items()]
    return WrapUpView(
        day=review.day,
        known=known,
        discovered=[
            f for f in known if baseline is not None and baseline.get(f["id"]) != f["value"]
        ],
        relationship_changes=relationship_changes or [],
        unanswered=list(review.unanswered),
        baseline_available=baseline is not None,
    )

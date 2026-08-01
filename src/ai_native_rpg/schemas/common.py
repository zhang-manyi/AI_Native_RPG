"""Primitives shared across schemas: timestamps, preference tags, conditions.

Kept separate so that ``world_state`` and ``player_model`` can share the
preference-tag vocabulary without importing each other.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Timezone-aware UTC now.

    Used instead of ``datetime.utcnow`` (deprecated in 3.12) so that every
    timestamp in the system is comparable without guessing at tzinfo.
    """
    return datetime.now(UTC)


class PreferenceTag(str, Enum):
    """Shared vocabulary for ``PlayerProfile.play_style`` keys and
    ``EventCandidate.preference_tag``.

    An enum rather than free strings: the Experience Controller scores events by
    looking a tag up in ``play_style``, and a typo there would silently fall back
    to the default weight instead of failing — a scoring bug that is invisible in
    logs. See docs/05_Narrative_Engine.md.
    """

    EXPLORATION = "exploration"
    SOCIAL = "social"
    COMBAT = "combat"
    INTRIGUE = "intrigue"


class ConditionOp(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"


class ConditionClause(BaseModel):
    """One comparison against a dotted path into ``WorldState``.

    e.g. ``{"path": "relationships.npc_a.player_1.trust", "op": "gt", "value": 60}``
    """

    path: str = Field(description="dotted path into WorldState, e.g. 'quests.investigation.stage'")
    op: ConditionOp
    value: Any


class Condition(BaseModel):
    """A structured boolean condition: ``any``/``all`` over clauses.

    Deliberately *not* a string expression like ``"trust > 60 OR stage >= 3"``:
    that would need either an expression parser (which docs/04 rules out as
    over-engineering) or ``eval()`` (which would turn scenario data files into an
    arbitrary-code-execution surface). See docs/04_World_State_Manager.md#32.
    """

    mode: Literal["any", "all"] = "any"
    clauses: list[ConditionClause] = Field(default_factory=list)

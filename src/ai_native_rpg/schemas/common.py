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


#: Relationship dimensions that exist on ``RelationshipState``.
#:
#: Here rather than in ``world/actions.py`` (which re-exports it) because the event
#: schema validates authored deltas against it, and ``schemas`` is the layer every
#: other one may depend on. Importing upward from here would put the whole world
#: package behind a schema import.
RELATIONSHIP_DIMENSIONS = frozenset({"trust", "fear", "respect"})

#: Largest change a single action may make to one relationship dimension.
#:
#: Caps how fast an LLM can move the values gating the whole clue chain; the model
#: can still move them repeatedly across turns, just not in one leap. This is also
#: what makes the social line take a *sequence* of events rather than one generous
#: turn: docs/15 §3.1 notes trust 10 → 85 needs at least five events because of it.
MAX_RELATIONSHIP_STEP = 15.0


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

    #: Membership the other way round: the *path* holds a collection and ``value``
    #: is the element sought. ``IN`` cannot express this — it asks whether a scalar
    #: in the world sits in a set the author wrote. The event layer needs both:
    #: "has M1 been completed" reads a list that lives in the world (docs/13 §7).
    CONTAINS = "contains"


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

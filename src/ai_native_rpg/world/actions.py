"""Action type vocabulary and the payload contract for each one.

Kept in its own module because three layers need to agree on it: the Validator
(what to check), the state applier (what to write), and the NPC Agent's tool
schema (what the LLM is allowed to emit).
"""

from __future__ import annotations

from enum import Enum

#: Actors that may propose actions without existing as NPCs in the world.
SYSTEM_ACTORS = frozenset({"narrative_engine", "system"})

#: Relationship dimensions that ``adjust_relationship`` may touch.
RELATIONSHIP_DIMENSIONS = frozenset({"trust", "fear", "respect"})

#: Largest change a single proposal may make to one relationship dimension.
#: Caps how fast an LLM can move the values that gate the whole clue chain; the
#: model can still move them repeatedly across turns, just not in one leap.
MAX_RELATIONSHIP_STEP = 15.0


class ActionType(str, Enum):
    REVEAL_FACT = "reveal_fact"
    ADJUST_RELATIONSHIP = "adjust_relationship"
    MOVE = "move"
    ADVANCE_QUEST = "advance_quest"


#: No action type bypasses ``reveal_condition`` — not even for system actors.
#:
#: The Narrative Engine drives plot reveals by advancing ``story_beats``, letting
#: the condition table decide what that unlocks, rather than by flipping
#: visibility directly. A bypass action was considered and rejected: the only
#: facts it could unlock that beats cannot are the ones an author *deliberately*
#: gave no beat channel — i.e. endings like ``killer_identity``. See
#: docs/04_World_State_Manager.md#33.
KNOWN_ACTION_TYPES = frozenset(a.value for a in ActionType)

"""Action type vocabulary and the payload contract for each one.

Kept in its own module because three layers need to agree on it: the Validator
(what to check), the state applier (what to write), and the NPC Agent's tool
schema (what the LLM is allowed to emit).
"""

from __future__ import annotations

from enum import Enum

from ..schemas.common import MAX_RELATIONSHIP_STEP, RELATIONSHIP_DIMENSIONS
from ..schemas.narrative import NarrativeOperator

#: Actors that may propose actions without existing as NPCs in the world.
SYSTEM_ACTORS = frozenset({"narrative_engine", "system"})

#: Re-exported from ``schemas.common``, which is where they now live so that the
#: event schema can validate authored deltas against them without importing the
#: world package. Kept importable from here because this module is where the
#: Validator, the state applier and the tool schema look for the action vocabulary.
__all__ = [
    "FORESHADOW_PAYOFF_PATH_PREFIXES",
    "KNOWN_ACTION_TYPES",
    "KNOWN_OPERATORS",
    "MAX_CHAPTER_STEP",
    "MAX_RELATIONSHIP_STEP",
    "NARRATIVE_ACTION_TYPES",
    "PLAYER_ACTION_TYPES",
    "RELATIONSHIP_DIMENSIONS",
    "SYSTEM_ACTORS",
    "ActionType",
]


#: Operator names ``advance_turn`` accepts. Shared with the schema so the two
#: vocabularies cannot drift.
KNOWN_OPERATORS = frozenset(op.value for op in NarrativeOperator)

#: Condition paths a *generated* ``payoff_condition`` may read.
#:
#: Resolvability alone is too weak a gate. ``time_day`` resolves cleanly and would
#: let a loop pay off by merely waiting, with no player involvement; a path into
#: ``facts.*`` would let the model gate one secret on another. These three are the
#: channels the reference scenario actually earns progress through: what the player
#: built with an NPC, how far the investigation got, and where the plot stands.
#: Author-written conditions in a scenario pack are not restricted this way — an
#: author can be trusted with the whole state; a generator cannot.
FORESHADOW_PAYOFF_PATH_PREFIXES = ("relationships.", "quests.", "story_beats.")


class ActionType(str, Enum):
    REVEAL_FACT = "reveal_fact"
    ADJUST_RELATIONSHIP = "adjust_relationship"
    MOVE = "move"
    ADVANCE_QUEST = "advance_quest"

    # --- narrative actions (slice 3) ---------------------------------------
    # An operator is "a set of Action Proposals" (docs/10 §2.1), so operators reach
    # the world through these rather than through a new permission mechanism.
    # System actors only: an NPC that could advance the turn counter would be able
    # to age out the pacing cooldowns that constrain it.
    ADVANCE_TURN = "advance_turn"
    ADVANCE_STORY_BEAT = "advance_story_beat"
    PLANT_FORESHADOWING = "plant_foreshadowing"
    PAY_OFF_FORESHADOWING = "pay_off_foreshadowing"


#: Actions only ``SYSTEM_ACTORS`` may propose.
NARRATIVE_ACTION_TYPES = frozenset(
    {
        ActionType.ADVANCE_TURN.value,
        ActionType.ADVANCE_STORY_BEAT.value,
        ActionType.PLANT_FORESHADOWING.value,
        ActionType.PAY_OFF_FORESHADOWING.value,
    }
)

#: Actions a *player* may propose. Only ``move``.
#:
#: docs/13 §12 gives the player two verbs, and this is the one that touches the world;
#: ending a conversation closes ``active_event``, which is narrative state and stays
#: with the Engine. The list is a whitelist rather than a subtraction because the
#: interesting failure is the other direction: a player-actor ``reveal_fact`` would
#: hand the player the power to unlock his own clues, and a player-actor
#: ``adjust_relationship`` would let him set what an NPC feels about him. Both were
#: unreachable only because ``_actor_must_exist`` happened to reject every player id
#: — an accident, now that players legitimately act (docs/04 §3.3).
PLAYER_ACTION_TYPES = frozenset({ActionType.MOVE.value})

#: Largest chapter jump a single proposal may make.
#:
#: Same reasoning as ``MAX_RELATIONSHIP_STEP``. Chapters are a disclosure channel
#: (docs/04 §3.3): a jump from 1 to 9 would satisfy every chapter-gated condition
#: at once, disabling pacing in one approved proposal. Advancing repeatedly across
#: turns is still allowed.
MAX_CHAPTER_STEP = 1


#: No action type bypasses ``reveal_condition`` — not even for system actors.
#:
#: The Narrative Engine drives plot reveals by advancing ``story_beats``, letting
#: the condition table decide what that unlocks, rather than by flipping
#: visibility directly. A bypass action was considered and rejected: the only
#: facts it could unlock that beats cannot are the ones an author *deliberately*
#: gave no beat channel — i.e. endings like ``loren_that_night``. See
#: docs/04_World_State_Manager.md#33.
KNOWN_ACTION_TYPES = frozenset(a.value for a in ActionType)

"""Player View projection: what one player can currently see.

A pure function over ``WorldState``. This is the whole of the
information-asymmetry mechanism — docs/04 rejects a dedicated "narrative
director" Agent in favour of ``Fact.visibility`` plus this projection, which keeps
the capability while removing an LLM call and an ownership conflict with the
Narrative Engine.

Every branch here fails closed: anything not positively established as visible is
omitted. A false negative delays a clue; a false positive spoils the mystery
irreversibly, and the two are not symmetric in cost.
"""

from __future__ import annotations

from ..schemas.world_state import Fact, Visibility, VisibleState, WorldState
from .conditions import evaluate


def _effective_visibility(fact: Fact, state: WorldState) -> Visibility:
    """Visibility after applying ``reveal_condition``.

    The condition can only *widen* visibility (hidden -> revealed). It never
    narrows an already-revealed fact: un-telling the player something is not a
    coherent operation, and modelling it would invite exactly that bug.
    """
    if fact.visibility is Visibility.REVEALED:
        return Visibility.REVEALED
    if fact.reveal_condition is not None and evaluate(fact.reveal_condition, state):
        return Visibility.REVEALED
    return fact.visibility


def player_view(state: WorldState, player_id: str) -> VisibleState:
    """Project the world down to what ``player_id`` can see.

    Pure: never mutates ``state``. In particular a satisfied reveal condition does
    not persist a visibility change — only the Validator writes state, so that
    "what the player sees" stays reproducible from the stored world.
    """
    visible_facts = {}
    for fact_id, fact in state.facts.items():
        match _effective_visibility(fact, state):
            case Visibility.REVEALED:
                visible_facts[fact_id] = fact.value
            case Visibility.PARTIAL if fact.partial_value is not None:
                visible_facts[fact_id] = fact.partial_value
            case _:
                continue

    player_location = state.player_locations.get(player_id)
    known_npc_locations = {
        npc_id: npc.location
        for npc_id, npc in state.npcs.items()
        if npc.alive and player_location is not None and npc.location == player_location
    }

    return VisibleState(
        player_id=player_id,
        time_day=state.time_day,
        # The clock is not asymmetric information: docs/13 §4.1 makes the slot the unit
        # of cost, and a cost the player cannot see does not shape his choices.
        time_slot=state.story_beats.time_slot,
        visible_facts=visible_facts,
        known_npc_locations=known_npc_locations,
        current_location=player_location,
        reachable_locations=_reachable_from(state, player_location),
        # His own footsteps, and the only record of them (docs/12 §13.2): an interface
        # showing "没去过" reads this rather than keeping a second list.
        visited_locations=list(state.story_beats.visited_locations),
        quest_stages={q_id: q.stage for q_id, q in state.quests.items()},
    )


def _reachable_from(state: WorldState, origin: str | None) -> list[str]:
    """Where the player may go next, in the pack's authored order.

    Kept here rather than in the interface so adjacency has exactly one reader-facing
    copy: docs/12 §13.2 forbids the Web layer re-deriving it, and the reason is that a
    second copy of a rule the Validator also owns is free to drift from it.

    Two exclusions, both matching what the Validator would say: a destination the world
    does not contain (``no such location``) and the place the player already stands in.
    The latter *passes* ``_move_must_be_adjacent`` — moving nowhere is legal — so
    offering it would let the player spend a slot to stay put.
    """
    if origin is None:
        return []
    current = state.locations.get(origin)
    if current is None:
        return []
    return [loc for loc in current.connected_to if loc in state.locations and loc != origin]

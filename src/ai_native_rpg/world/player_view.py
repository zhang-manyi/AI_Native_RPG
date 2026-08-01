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
        visible_facts=visible_facts,
        known_npc_locations=known_npc_locations,
        current_location=player_location,
        quest_stages={q_id: q.stage for q_id, q in state.quests.items()},
    )

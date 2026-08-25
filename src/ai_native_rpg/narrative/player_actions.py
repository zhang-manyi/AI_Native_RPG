"""What the player can *do*, as opposed to say (docs/13 §12).

Until now the player's only verb was speech, so every scrap of progress had to come
out of an NPC's mouth — one of the structural reasons play felt slow. Two verbs fix
the worst of it:

    move_player()          go to a connected location, spending a slot
    end_conversation()     close the active event, landing its default outcome

Both travel the same road as everything else: an ``ActionProposal`` through the
Validator (docs/04's single write path, restated for the player by docs/13 §12). The
temptation was a direct setter on the Manager — the player is not a model, so what is
there to validate? — but adjacency is exactly the kind of rule that rots when there
are two copies of it, and a trace that omits the player's own moves cannot explain how
he got where he is.

**A move costs a slot; arriving does not.** docs/13 §4.1 makes the slot the unit of
cost, and the cost is the choice of *where to spend it*, so the charge belongs to the
decision to go rather than to any specific thing found on arrival. A move that the
Validator rejects costs nothing, which is the honest reading: nothing happened.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..schemas.narrative import TimeSlot
from ..schemas.world_state import ActionProposal, ActionValidationResult
from ..world.actions import ActionType
from ..world.manager import WorldStateManager

#: Actor id the slot charge is submitted under.
#:
#: The clock is narrative state, so advancing it is a narrative action and stays
#: system-only — a player-actor proposal to advance the slot would let a player skip a
#: night he did not want to spend (docs/04 §3.3).
CLOCK_ACTOR = "narrative_engine"


@dataclass(frozen=True)
class PlayerActionResult:
    """What a player action did, for the panel and for the caller.

    ``slot_spent`` is separate from ``approved`` because the two genuinely differ: a
    rejected move spends nothing, and an approved one spends a slot that may end the
    day. A caller showing "你在 X，还剩 N 个时段" needs both answers.
    """

    approved: bool
    reason: str | None = None
    slot_spent: bool = False
    slot: TimeSlot | None = None
    day: int | None = None
    first_visit: bool = False
    out_of_days: bool = False
    proposals: list[ActionValidationResult] = field(default_factory=list)


def move_player(
    manager: WorldStateManager,
    *,
    player_id: str,
    destination: str,
) -> PlayerActionResult:
    """Walk the player to ``destination``, spending one slot if it lands.

    Rejection is a normal answer, not an error: "森林入口 is not reachable from 玛尔塔的家"
    is what a caller should show the player, and the reason string is already written to
    be readable (docs/02 §4.1).

    The order matters. The move is submitted first and the slot charged only on success,
    so an illegal destination cannot burn an evening. Charging first and refunding would
    be equivalent arithmetically and worse in a trace, which would then show a slot
    spent and returned for a move that never happened.
    """
    move = manager.submit(
        ActionProposal(
            proposal_id=uuid.uuid4().hex,
            actor_id=player_id,
            action_type=ActionType.MOVE.value,
            target_id=destination,
        )
    )
    if not move.approved:
        return PlayerActionResult(approved=False, reason=move.reason, proposals=[move])

    changes = move.applied_changes or {}
    first_visit = "story_beats.visited_locations" in changes

    charge = _spend_slot(manager)
    beats = manager.snapshot().story_beats
    day = manager.snapshot().time_day

    return PlayerActionResult(
        approved=True,
        slot_spent=charge.approved,
        slot=beats.time_slot,
        day=day,
        first_visit=first_visit,
        out_of_days=beats.is_out_of_days(current_day=day),
        proposals=[move, charge],
    )


def end_conversation(manager: WorldStateManager, *, spend_slot: bool = False) -> PlayerActionResult:
    """Close the active event, whatever it had reached (docs/13 §12).

    The event lands wherever the world already put it. This deliberately does *not*
    apply the default outcome: walking out is not the same act as running out of
    patience, and applying an outcome here would let a player collect its numbers by
    leaving. ``resolve_player_response`` is the path that lands outcomes; this one only
    shuts the door.

    ``spend_slot`` defaults to False. Ending a conversation is not itself a use of the
    day — the slot was spent on going there — and charging for it would make leaving a
    dead conversation cost the same as a fresh journey.
    """
    finish = _submit_beat(manager, {"finish_event": True})
    proposals = [finish]

    charge_approved = False
    if spend_slot:
        charge = _spend_slot(manager)
        proposals.append(charge)
        charge_approved = charge.approved

    world = manager.snapshot()
    return PlayerActionResult(
        approved=finish.approved,
        reason=finish.reason,
        slot_spent=charge_approved,
        slot=world.story_beats.time_slot,
        day=world.time_day,
        out_of_days=world.story_beats.is_out_of_days(current_day=world.time_day),
        proposals=proposals,
    )


def advance_past_wrap_up(manager: WorldStateManager) -> PlayerActionResult:
    """Step from the wrap-up into the next morning (docs/13 §4.2).

    Refuses outside the wrap-up, because the alternative is a caller able to skip a slot
    it did not want to spend. That the interlude itself costs nothing is a property of
    ``advance_slot``, not of this function: stepping *through* the wrap-up is the one
    advance that does not increment ``slots_spent_today``.
    """
    beats = manager.snapshot().story_beats
    if beats.time_slot is not TimeSlot.WRAP_UP:
        return PlayerActionResult(
            approved=False,
            reason=f"not at the day's wrap-up (it is {beats.time_slot.value})",
            slot=beats.time_slot,
            day=manager.snapshot().time_day,
        )

    result = _spend_slot(manager)
    world = manager.snapshot()
    return PlayerActionResult(
        approved=result.approved,
        reason=result.reason,
        slot_spent=False,  # the interlude is free; this only turns the day over
        slot=world.story_beats.time_slot,
        day=world.time_day,
        out_of_days=world.story_beats.is_out_of_days(current_day=world.time_day),
        proposals=[result],
    )


def _spend_slot(manager: WorldStateManager) -> ActionValidationResult:
    return _submit_beat(manager, {"advance_slot": True})


def _submit_beat(manager: WorldStateManager, payload: dict) -> ActionValidationResult:
    return manager.submit(
        ActionProposal(
            proposal_id=uuid.uuid4().hex,
            actor_id=CLOCK_ACTOR,
            action_type=ActionType.ADVANCE_STORY_BEAT.value,
            payload=payload,
        )
    )

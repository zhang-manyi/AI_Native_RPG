"""World State Manager: Single Source of Truth and the only writer of state.

See docs/04_World_State_Manager.md. Three invariants this class exists to enforce:

1. **No direct mutation.** The live ``WorldState`` is private; reads return deep
   copies. "Agents cannot modify the world directly" becomes a property of the
   code rather than a convention people remember to follow.
2. **Every write is validated.** ``submit`` is the sole write path and always runs
   the Validator first.
3. **Writes are idempotent per proposal_id.** A retried LLM call must not apply a
   relationship delta twice.

Deliberately not a database: an in-memory model plus JSON snapshots, per docs/04
§5. The migration point is behind ``save``/``load``.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..schemas.common import Condition
from ..schemas.narrative import Foreshadowing
from ..schemas.world_state import (
    ActionProposal,
    ActionValidationResult,
    Fact,
    RelationshipState,
    Visibility,
    VisibleState,
    WorldState,
)
from .actions import ActionType
from .player_view import player_view as _project_player_view
from .validator import DEFAULT_RULES, Rule, Validator

# Relationship dimensions are bounded by the schema (-100..100); clamp before
# writing so that a legal sequence of steps cannot end in a validation error.
_REL_MIN, _REL_MAX = -100.0, 100.0


class WorldStateManager:
    """Owns the world. All reads are copies, all writes go through the Validator."""

    def __init__(self, state: WorldState, rules: tuple[Rule, ...] = DEFAULT_RULES) -> None:
        # Copy on the way in too: whoever built the initial state (scenario loader,
        # a test fixture) must not retain a mutable handle on the live world.
        self._state = state.model_copy(deep=True)
        self._validator = Validator(rules)
        self._applied_proposals: set[str] = set()

    # --- reads -------------------------------------------------------------

    def snapshot(self) -> WorldState:
        """A deep copy of the world. Safe to hand to any caller, including LLM
        prompt builders that may want to trim or annotate it."""
        return self._state.model_copy(deep=True)

    def player_view(self, player_id: str) -> VisibleState:
        """Project the world down to what a player can see."""
        return _project_player_view(self._state, player_id)

    def get_relationship(self, npc_id: str, target_id: str) -> RelationshipState:
        """Read-only projection of a relationship.

        This is what ``memory.RelationshipMemory`` is built from — the value lives
        in the world, not in the NPC's memory, so the Validator and the Agent can
        never disagree about it. See docs/04 §2.1.
        """
        existing = self._state.relationships.get(npc_id, {}).get(target_id)
        return existing.model_copy(deep=True) if existing else RelationshipState()

    def get_trust(self, npc_id: str, target_id: str) -> float:
        """Convenience accessor used by Narrative Engine trigger rules."""
        return self.get_relationship(npc_id, target_id).trust

    # --- writes ------------------------------------------------------------

    def dry_run(self, proposal: ActionProposal) -> ActionValidationResult:
        """Validate without applying.

        Exposed so the Agent can ask "would this be allowed?" as a tool call
        during Planning, and so Dialogue Generation can be told the verdict before
        any state changes. Safe because rules are pure.
        """
        return self._validator.validate(proposal, self._state)

    def submit(self, proposal: ActionProposal) -> ActionValidationResult:
        """Validate, then apply if approved. The only write path into the world."""
        if proposal.proposal_id in self._applied_proposals:
            return ActionValidationResult(
                proposal_id=proposal.proposal_id,
                approved=False,
                reason=f"proposal {proposal.proposal_id!r} was already applied",
                rule_name="duplicate_proposal",
            )

        result = self._validator.validate(proposal, self._state)
        if not result.approved:
            return result

        applied = self._apply(proposal)
        self._applied_proposals.add(proposal.proposal_id)
        self._state.last_updated = _now()

        return result.model_copy(update={"applied_changes": applied})

    def _apply(self, proposal: ActionProposal) -> dict[str, object]:
        """Write an approved proposal into the world.

        Assumes validation passed; each branch may rely on the guarantees the
        corresponding rules established (target exists, deltas are numeric, the
        destination is adjacent).
        """
        match ActionType(proposal.action_type):
            case ActionType.REVEAL_FACT:
                return self._apply_reveal(proposal)
            case ActionType.ADJUST_RELATIONSHIP:
                return self._apply_relationship(proposal)
            case ActionType.MOVE:
                return self._apply_move(proposal)
            case ActionType.ADVANCE_QUEST:
                return self._apply_quest(proposal)
            case ActionType.ADVANCE_TURN:
                return self._apply_advance_turn(proposal)
            case ActionType.ADVANCE_STORY_BEAT:
                return self._apply_story_beat(proposal)
            case ActionType.PLANT_FORESHADOWING:
                return self._apply_plant_foreshadowing(proposal)
            case ActionType.PAY_OFF_FORESHADOWING:
                return self._apply_pay_off_foreshadowing(proposal)

    def _apply_reveal(self, proposal: ActionProposal) -> dict[str, object]:
        fact = self._state.facts[proposal.target_id]
        fact.visibility = Visibility.REVEALED
        return {f"facts.{proposal.target_id}.visibility": Visibility.REVEALED.value}

    def _apply_relationship(self, proposal: ActionProposal) -> dict[str, object]:
        actor, target = proposal.actor_id, proposal.target_id
        rel = self._state.relationships.setdefault(actor, {}).setdefault(
            target, RelationshipState()
        )
        changes: dict[str, object] = {}
        for dim, delta in proposal.payload.items():
            updated = min(_REL_MAX, max(_REL_MIN, getattr(rel, dim) + float(delta)))
            setattr(rel, dim, updated)
            changes[f"relationships.{actor}.{target}.{dim}"] = updated
        rel.last_updated = _now()
        return changes

    def _apply_move(self, proposal: ActionProposal) -> dict[str, object]:
        """Move an NPC or a player to the destination.

        Players live in ``player_locations``, not in ``npcs``, and this method used to
        write only the latter — so ``ActionType.MOVE`` existed, validated, reported
        success, and moved nobody whenever the actor was the player. The player could
        therefore never leave his starting location, which silently killed
        ``loren_that_night``'s second channel: ``quests.investigation.stage >= 3`` needs
        clues that only the tavern and the forest hold (docs/13 §12, docs/14 §1.2.1).

        A ``KeyError`` on an unknown actor is deliberate: ``_move_actor_must_have_a_location``
        has already established that the actor is one or the other, so reaching here with
        neither means the rules and the applier disagree, and that should be loud.
        """
        destination = str(proposal.target_id)
        actor = proposal.actor_id

        if actor in self._state.npcs:
            self._state.npcs[actor].location = destination
            return {f"npcs.{actor}.location": destination}

        self._state.player_locations[actor] = destination
        changes: dict[str, object] = {f"player_locations.{actor}": destination}
        # "First arrival at X" is a trigger the script uses (docs/15 §4 M4/M5), and it
        # is only expressible while the visit is being recorded — afterwards the world
        # cannot tell a first visit from a fifth.
        if self._state.story_beats.record_visit(destination):
            changes["story_beats.visited_locations"] = list(
                self._state.story_beats.visited_locations
            )
        return changes

    def _apply_quest(self, proposal: ActionProposal) -> dict[str, object]:
        quest = self._state.quests[proposal.target_id]
        quest.stage += 1
        if quest.status == "not_started":
            quest.status = "active"
        return {
            f"quests.{quest.quest_id}.stage": quest.stage,
            f"quests.{quest.quest_id}.status": quest.status,
        }

    # --- narrative writes --------------------------------------------------

    def _apply_advance_turn(self, proposal: ActionProposal) -> dict[str, object]:
        """Close one turn, recording which operator ran (``relieve`` if none).

        Every turn is recorded. See ``StoryBeats.record_operator`` for why quiet
        turns must occupy a slot rather than be omitted.
        """
        beats = self._state.story_beats
        beats.turn += 1
        beats.record_operator(proposal.payload["operator"])
        return {
            "story_beats.turn": beats.turn,
            "story_beats.recent_operators": list(beats.recent_operators),
        }

    def _apply_story_beat(self, proposal: ActionProposal) -> dict[str, object]:
        beats = self._state.story_beats
        changes: dict[str, object] = {}
        if "chapter" in proposal.payload:
            beats.chapter = int(proposal.payload["chapter"])
            changes["story_beats.chapter"] = beats.chapter
        if "tension" in proposal.payload:
            beats.tension = float(proposal.payload["tension"])
            changes["story_beats.tension"] = beats.tension
        if "spend_one_shot" in proposal.payload:
            key = str(proposal.payload["spend_one_shot"])
            beats.spend_one_shot(key)
            changes[f"story_beats.spent_one_shots.{key}"] = True
        # Sticky: only the first terminal ending reached is recorded. A second
        # ``end_case`` in the same or a later payload must not overwrite which one the
        # player actually got there first (docs/14 §4.3's endings are not equally
        # ordered — a check written to fire on the same tick as another must not win by
        # running later).
        if "end_case" in proposal.payload and beats.ended_at is None:
            beats.ended_at = str(proposal.payload["end_case"])
            changes["story_beats.ended_at"] = beats.ended_at
        changes.update(self._apply_event_lifecycle(proposal))
        return changes

    def _apply_event_lifecycle(self, proposal: ActionProposal) -> dict[str, object]:
        """Open / count / finish the active event, and raise outcome flags.

        Ordering is deliberate: a proposal may both finish one event and raise the
        flags its outcome set, and the flags must survive the finish. Opening is last
        so that a single proposal can close one event and start the next without the
        finish wiping what it just opened.
        """
        payload = proposal.payload
        beats = self._state.story_beats
        changes: dict[str, object] = {}

        for flag in payload.get("raise_flags") or []:
            beats.raise_flag(str(flag))
            changes[f"story_beats.flags.{flag}"] = True

        if payload.get("record_exchange"):
            beats.record_exchange()
            if beats.active_event is not None:
                changes["story_beats.active_event.exchanges"] = beats.active_event.exchanges

        if payload.get("advance_slot"):
            # The one place the clock moves. ``time_slot`` lives on the beats and
            # ``time_day`` on the world (docs/13 §4.1 wanted the existing day field to
            # start moving rather than a parallel one), so both are written here to keep
            # them from drifting a slot apart.
            slot, day = beats.advance_slot(current_day=self._state.time_day)
            self._state.time_day = day
            changes["story_beats.time_slot"] = slot.value
            changes["story_beats.slots_spent_today"] = beats.slots_spent_today
            changes["time_day"] = day

        if payload.get("finish_event"):
            finished = beats.active_event.event_id if beats.active_event else None
            beats.finish_event(close=bool(payload.get("close_event")))
            if finished is not None:
                changes["story_beats.completed_events"] = list(beats.completed_events)
                if payload.get("close_event"):
                    changes["story_beats.closed_events"] = list(beats.closed_events)

        if "open_event" in payload:
            event_id = str(payload["open_event"])
            beats.open_event(event_id, max_exchanges=int(payload["max_exchanges"]))
            changes["story_beats.active_event.event_id"] = event_id

        return changes

    def _apply_plant_foreshadowing(self, proposal: ActionProposal) -> dict[str, object]:
        """Write a hidden Fact and register the debt against it.

        The payoff condition becomes the fact's ``reveal_condition``: the same
        object gates disclosure and settles the ledger, so "time to pay off" and
        "the player may see it" cannot drift apart. This installs a channel rather
        than bypassing one, which is what keeps it inside docs/04 §3.3.
        """
        fact_id = str(proposal.target_id)
        condition = Condition.model_validate(proposal.payload["payoff_condition"])
        beats = self._state.story_beats

        self._state.facts[fact_id] = Fact(
            fact_id=fact_id,
            value=proposal.payload.get("value"),
            visibility=Visibility.HIDDEN,
            partial_value=proposal.payload.get("partial_value"),
            reveal_condition=condition,
        )
        entry = Foreshadowing(
            fact_id=fact_id,
            planted_at_turn=beats.turn,
            payoff_condition=condition,
            note=str(proposal.payload.get("note", "")),
            **(
                {"overdue_after_turns": int(proposal.payload["overdue_after_turns"])}
                if proposal.payload.get("overdue_after_turns")
                else {}
            ),
        )
        beats.open_foreshadowings[fact_id] = entry
        # Counted separately from the ledger's size: settling pops the entry but
        # leaves the fact behind, so only a monotonic count can tell the trigger
        # rules how many loops this story has actually opened.
        beats.planted_total += 1
        return {
            f"facts.{fact_id}": "planted (hidden)",
            f"story_beats.open_foreshadowings.{fact_id}": entry.planted_at_turn,
            "story_beats.planted_total": beats.planted_total,
        }

    def _apply_pay_off_foreshadowing(self, proposal: ActionProposal) -> dict[str, object]:
        fact_id = str(proposal.target_id)
        entry = self._state.story_beats.open_foreshadowings.pop(fact_id)
        return {
            f"story_beats.open_foreshadowings.{fact_id}": "settled",
            "payoff_span_turns": entry.turns_owed(current_turn=self._state.story_beats.turn),
        }

    # --- persistence -------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Snapshot to JSON. Written via a temp file then renamed, so a crash
        mid-write cannot leave a truncated world behind."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self._state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path, rules: tuple[Rule, ...] = DEFAULT_RULES) -> WorldStateManager:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(WorldState.model_validate(raw), rules=rules)


def _now():
    from ..schemas.common import utc_now

    return utc_now()

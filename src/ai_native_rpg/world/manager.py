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

from ..schemas.world_state import (
    ActionProposal,
    ActionValidationResult,
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
        self._state.npcs[proposal.actor_id].location = proposal.target_id
        return {f"npcs.{proposal.actor_id}.location": proposal.target_id}

    def _apply_quest(self, proposal: ActionProposal) -> dict[str, object]:
        quest = self._state.quests[proposal.target_id]
        quest.stage += 1
        if quest.status == "not_started":
            quest.status = "active"
        return {
            f"quests.{quest.quest_id}.stage": quest.stage,
            f"quests.{quest.quest_id}.status": quest.status,
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

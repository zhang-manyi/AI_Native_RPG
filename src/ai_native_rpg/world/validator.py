"""Action Validator: the gate every world mutation passes through.

Per docs/04 §5 this is a *list of rules*, each a pure function, not a rule engine.
Two properties are load-bearing:

- **Pure**: rules never mutate state, so validation can be run speculatively (a
  dry-run tool for the Agent) without side effects.
- **Fail closed**: an action type with no matching rule is rejected. An LLM that
  invents an action name must not slip past the layer that exists to contain it.

Rejection reasons are written to be readable by the Dialogue Generation prompt,
not just by developers — they become the in-character constraint on what the NPC
may say. See docs/02_Sequence_Diagram.md#41.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..schemas.world_state import (
    ActionProposal,
    ActionValidationResult,
    Visibility,
    WorldState,
)
from .actions import (
    KNOWN_ACTION_TYPES,
    MAX_RELATIONSHIP_STEP,
    RELATIONSHIP_DIMENSIONS,
    SYSTEM_ACTORS,
    ActionType,
)
from .conditions import evaluate


@dataclass(frozen=True)
class RuleOutcome:
    """A rule's verdict. ``reason`` is required when rejecting."""

    ok: bool
    reason: str | None = None

    @staticmethod
    def passed() -> RuleOutcome:
        return RuleOutcome(ok=True)

    @staticmethod
    def failed(reason: str) -> RuleOutcome:
        return RuleOutcome(ok=False, reason=reason)


RuleFn = Callable[[ActionProposal, WorldState], RuleOutcome]


@dataclass(frozen=True)
class Rule:
    """A named rule plus the action types it applies to.

    ``applies_to=None`` means the rule runs for every action type (the universal
    preconditions like "the actor exists").
    """

    name: str
    fn: RuleFn
    applies_to: frozenset[str] | None = None

    def is_applicable(self, proposal: ActionProposal) -> bool:
        return self.applies_to is None or proposal.action_type in self.applies_to


# --- universal rules -------------------------------------------------------


def _action_type_is_known(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    if proposal.action_type not in KNOWN_ACTION_TYPES:
        return RuleOutcome.failed(f"unknown action_type {proposal.action_type!r}")
    return RuleOutcome.passed()


def _actor_must_exist(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    if proposal.actor_id in SYSTEM_ACTORS or proposal.actor_id in state.npcs:
        return RuleOutcome.passed()
    return RuleOutcome.failed(f"actor {proposal.actor_id!r} does not exist in the world")


def _actor_must_be_alive(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    npc = state.npcs.get(proposal.actor_id)
    if npc is not None and not npc.alive:
        return RuleOutcome.failed(f"actor {proposal.actor_id!r} is dead and cannot act")
    return RuleOutcome.passed()


# --- reveal_fact -----------------------------------------------------------


def _reveal_target_must_exist(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    if proposal.target_id not in state.facts:
        return RuleOutcome.failed(f"no such fact {proposal.target_id!r}")
    return RuleOutcome.passed()


def _reveal_requires_condition_met(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    """An NPC may only reveal a fact the world says is unlockable.

    This is what stops the model from handing over the mystery's answer because
    the player asked nicely. Note it re-uses the same evaluator as PlayerView, so
    "what may be revealed" and "what is visible" cannot drift apart.
    """
    fact = state.facts[proposal.target_id]  # guarded by _reveal_target_must_exist
    if fact.visibility is Visibility.REVEALED:
        return RuleOutcome.passed()
    if fact.reveal_condition is not None and evaluate(fact.reveal_condition, state):
        return RuleOutcome.passed()
    return RuleOutcome.failed(
        f"fact {proposal.target_id!r} is not yet unlockable: its reveal condition is unmet"
    )


def _target_must_be_present(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    """Every current action type names something it acts on.

    Rejecting a missing target is the point: an ``adjust_relationship`` with
    ``target_id=None`` used to validate cleanly and then write to
    ``relationships[actor][None]``, reporting success while changing nothing any
    player view or ``reveal_condition`` could read. A silent no-op is the worst
    outcome for the layer whose whole job is keeping dialogue and world state in
    agreement, so it fails loudly instead and the NPC is told why.
    """
    if proposal.target_id is None or not str(proposal.target_id).strip():
        return RuleOutcome.failed(
            f"{proposal.action_type} requires a target_id, but none was given"
        )
    return RuleOutcome.passed()


def _relationship_target_must_exist(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    """The target of a relationship must be someone the world knows about.

    A hallucinated id would create a relationship toward nobody: stored, but
    unreachable by every condition path that reads ``relationships.<npc>.<target>``.
    """
    target = proposal.target_id
    if target in state.npcs or target in state.player_locations:
        return RuleOutcome.passed()
    return RuleOutcome.failed(f"no such character {target!r} to hold a relationship with")


# --- adjust_relationship ---------------------------------------------------


def _relationship_dimensions_are_known(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    unknown = set(proposal.payload) - RELATIONSHIP_DIMENSIONS
    if unknown:
        return RuleOutcome.failed(f"unknown relationship dimensions: {sorted(unknown)}")
    if not proposal.payload:
        return RuleOutcome.failed("adjust_relationship requires at least one dimension")
    return RuleOutcome.passed()


def _relationship_step_limit(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    for dim, delta in proposal.payload.items():
        if not isinstance(delta, int | float) or isinstance(delta, bool):
            return RuleOutcome.failed(f"{dim} delta must be numeric, got {delta!r}")
        if abs(delta) > MAX_RELATIONSHIP_STEP:
            return RuleOutcome.failed(
                f"{dim} delta {delta:+.1f} exceeds the per-action limit of "
                f"{MAX_RELATIONSHIP_STEP:.0f}"
            )
    return RuleOutcome.passed()


# --- move ------------------------------------------------------------------


def _move_must_be_adjacent(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    destination = proposal.target_id
    if destination not in state.locations:
        return RuleOutcome.failed(f"no such location {destination!r}")

    npc = state.npcs.get(proposal.actor_id)
    if npc is None:
        return RuleOutcome.failed(f"actor {proposal.actor_id!r} has no location to move from")
    if npc.location == destination:
        return RuleOutcome.passed()

    current = state.locations.get(npc.location)
    if current is None or destination not in current.connected_to:
        return RuleOutcome.failed(f"{destination!r} is not reachable from {npc.location!r}")
    return RuleOutcome.passed()


# --- advance_quest ---------------------------------------------------------


def _quest_must_be_advanceable(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    quest = state.quests.get(proposal.target_id)
    if quest is None:
        return RuleOutcome.failed(f"no such quest {proposal.target_id!r}")
    if quest.status in ("completed", "failed"):
        return RuleOutcome.failed(f"quest {quest.quest_id!r} is already {quest.status}")
    return RuleOutcome.passed()


DEFAULT_RULES: tuple[Rule, ...] = (
    Rule("action_type_must_be_known", _action_type_is_known),
    Rule("actor_must_exist", _actor_must_exist),
    Rule("actor_must_be_alive", _actor_must_be_alive),
    # Ordered before every target-dereferencing rule below, so those can assume a
    # target is present rather than each re-checking it.
    Rule(
        "target_must_be_present",
        _target_must_be_present,
        frozenset(
            {
                ActionType.REVEAL_FACT.value,
                ActionType.ADJUST_RELATIONSHIP.value,
                ActionType.MOVE.value,
                ActionType.ADVANCE_QUEST.value,
            }
        ),
    ),
    Rule(
        "reveal_target_must_exist",
        _reveal_target_must_exist,
        frozenset({ActionType.REVEAL_FACT.value}),
    ),
    Rule(
        "reveal_requires_condition_met",
        _reveal_requires_condition_met,
        frozenset({ActionType.REVEAL_FACT.value}),
    ),
    Rule(
        "relationship_target_must_exist",
        _relationship_target_must_exist,
        frozenset({ActionType.ADJUST_RELATIONSHIP.value}),
    ),
    Rule(
        "relationship_dimensions_are_known",
        _relationship_dimensions_are_known,
        frozenset({ActionType.ADJUST_RELATIONSHIP.value}),
    ),
    Rule(
        "relationship_step_limit",
        _relationship_step_limit,
        frozenset({ActionType.ADJUST_RELATIONSHIP.value}),
    ),
    Rule("move_must_be_adjacent", _move_must_be_adjacent, frozenset({ActionType.MOVE.value})),
    Rule(
        "quest_must_be_advanceable",
        _quest_must_be_advanceable,
        frozenset({ActionType.ADVANCE_QUEST.value}),
    ),
)


class Validator:
    """Runs rules in order and stops at the first rejection.

    Order matters: universal preconditions come first so that later rules can
    assume the actor and target exist, and so the reported reason names the most
    fundamental violation rather than an incidental downstream one.
    """

    def __init__(self, rules: Sequence[Rule] = DEFAULT_RULES) -> None:
        self._rules = tuple(rules)

    def validate(self, proposal: ActionProposal, state: WorldState) -> ActionValidationResult:
        for rule in self._rules:
            if not rule.is_applicable(proposal):
                continue
            outcome = rule.fn(proposal, state)
            if not outcome.ok:
                return ActionValidationResult(
                    proposal_id=proposal.proposal_id,
                    approved=False,
                    reason=outcome.reason,
                    rule_name=rule.name,
                )
        return ActionValidationResult(proposal_id=proposal.proposal_id, approved=True)

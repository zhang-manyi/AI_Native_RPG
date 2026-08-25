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

from ..schemas.common import Condition
from ..schemas.world_state import (
    ActionProposal,
    ActionValidationResult,
    Visibility,
    WorldState,
)
from .actions import (
    FORESHADOW_PAYOFF_PATH_PREFIXES,
    KNOWN_ACTION_TYPES,
    KNOWN_OPERATORS,
    MAX_CHAPTER_STEP,
    MAX_RELATIONSHIP_STEP,
    NARRATIVE_ACTION_TYPES,
    RELATIONSHIP_DIMENSIONS,
    SYSTEM_ACTORS,
    ActionType,
)
from .conditions import UnknownPathError, evaluate, resolve_path


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


# --- narrative actions -----------------------------------------------------
# Operators reach the world as ordinary proposals (docs/10 §2.1), so these rules
# are what keeps "the Engine schedules structure" from becoming "the Engine writes
# whatever it likes".

#: Payload keys ``advance_story_beat`` can act on. ``spend_one_shot`` is here
#: because ``reverse`` has no other effect to carry it: it re-reads information the
#: player already has, so the marker recording that it happened is the entire
#: write. Routing it through a proposal keeps the Manager's single write path
#: intact rather than adding a setter that skips validation.
#:
#: The event-lifecycle keys join it for the same reason. ``active_event`` is state
#: like any other, and docs/13 §11's recurring bug is state whose writer was never
#: wired up — so the writer arrives with the field, and it arrives as a proposal.
_BEAT_ADVANCE_KEYS = frozenset(
    {
        "chapter",
        "tension",
        "spend_one_shot",
        "open_event",
        "record_exchange",
        "finish_event",
        "close_event",
        "raise_flags",
    }
)


def _narrative_actions_are_system_only(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    """Only the Engine may move narrative state.

    An NPC able to advance the turn counter could age out the pacing cooldowns
    that exist to constrain it, and one able to plant a foreshadowing could install
    its own disclosure channel.
    """
    if proposal.actor_id in SYSTEM_ACTORS:
        return RuleOutcome.passed()
    return RuleOutcome.failed(
        f"{proposal.action_type} may only be proposed by the narrative engine, "
        f"not by {proposal.actor_id!r}"
    )


def _operator_must_be_known(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    operator = proposal.payload.get("operator")
    if operator not in KNOWN_OPERATORS:
        return RuleOutcome.failed(
            f"unknown narrative operator {operator!r}; expected one of {sorted(KNOWN_OPERATORS)}"
        )
    return RuleOutcome.passed()


def _beat_advance_must_change_something(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    # A proposal that validates and writes nothing would report success while
    # leaving the world untouched — the silent no-op _target_must_be_present
    # rejects for the same reason.
    if not _BEAT_ADVANCE_KEYS & set(proposal.payload):
        return RuleOutcome.failed(
            f"advance_story_beat requires at least one of {sorted(_BEAT_ADVANCE_KEYS)}, "
            "but none was given"
        )
    return RuleOutcome.passed()


def _event_lifecycle_payload_is_coherent(
    proposal: ActionProposal, state: WorldState
) -> RuleOutcome:
    """Event-lifecycle keys must carry usable values.

    Checked here rather than trusted because the applier writes ``ActiveEvent``, and a
    malformed payload would either raise mid-turn or, worse, open an event with no
    patience budget — one that could never fall to its default outcome and so would
    never end (docs/13 §3.1).
    """
    payload = proposal.payload

    if "open_event" in payload:
        event_id = payload["open_event"]
        if not isinstance(event_id, str) or not event_id.strip():
            return RuleOutcome.failed(f"open_event needs an event id, got {event_id!r}")
        budget = payload.get("max_exchanges")
        if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
            return RuleOutcome.failed(
                f"opening event {event_id!r} requires a positive max_exchanges, got "
                f"{budget!r}; without one the event could never reach its default outcome"
            )

    if "raise_flags" in payload:
        flags = payload["raise_flags"]
        if not isinstance(flags, list) or not all(isinstance(f, str) and f.strip() for f in flags):
            return RuleOutcome.failed(
                f"raise_flags must be a list of non-empty strings, got {flags!r}"
            )

    if payload.get("close_event") and not payload.get("finish_event"):
        return RuleOutcome.failed(
            "close_event only makes sense together with finish_event: closing is how an "
            "event ends permanently, not a state it sits in while still running"
        )

    for key in ("record_exchange", "finish_event", "close_event"):
        if key in payload and not isinstance(payload[key], bool):
            return RuleOutcome.failed(f"{key} must be a boolean, got {payload[key]!r}")

    return RuleOutcome.passed()


def _chapter_advances_monotonically(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    """Chapters move forward, one at a time.

    Backwards would un-tell whatever the chapter unlocked; skipping ahead would
    satisfy every chapter-gated condition in one go. See MAX_CHAPTER_STEP.
    """
    if "chapter" not in proposal.payload:
        return RuleOutcome.passed()

    requested = proposal.payload["chapter"]
    if isinstance(requested, bool) or not isinstance(requested, int):
        return RuleOutcome.failed(f"chapter must be an integer, got {requested!r}")

    current = state.story_beats.chapter
    if not current <= requested <= current + MAX_CHAPTER_STEP:
        return RuleOutcome.failed(
            f"chapter may go from {current} to at most {current + MAX_CHAPTER_STEP}, "
            f"but {requested} was requested"
        )
    return RuleOutcome.passed()


def _tension_must_be_a_unit_fraction(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    if "tension" not in proposal.payload:
        return RuleOutcome.passed()

    tension = proposal.payload["tension"]
    if isinstance(tension, bool) or not isinstance(tension, int | float):
        return RuleOutcome.failed(f"tension must be numeric, got {tension!r}")
    if not 0.0 <= float(tension) <= 1.0:
        return RuleOutcome.failed(f"tension must lie in [0, 1], got {tension}")
    return RuleOutcome.passed()


def _planted_fact_id_must_be_new(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    if proposal.target_id in state.facts:
        return RuleOutcome.failed(
            f"fact {proposal.target_id!r} already exists; planting would overwrite it"
        )
    return RuleOutcome.passed()


def _participants_must_exist(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    """Generated content may only name characters the world actually has.

    The gap this closes was found in play: a run planted a detail about a village
    miller talking to the player, in a scenario whose cast is two NPCs. Every other
    rule passed — the fact id was new, the condition was checkable — because nothing
    inspected the names. The invented character then entered the ledger with a payoff
    condition, owing the player a resolution about someone who does not exist.

    This is docs/10 §5 priority 1 ("never contradict itself"), which the design
    describes as mostly guaranteed by architecture. It is, but only for what the
    architecture can see: the single source of truth stops an NPC *misreporting* the
    world, and says nothing about the Engine adding to its cast.

    Ids and display names are both accepted: the generator is prompted with names
    (ids leak into prose, and a fact's value can *be* an id), so rejecting names here
    would reject the very vocabulary the prompt hands out.

    Empty is fine — a beat need not involve anyone.
    """
    raw = proposal.payload.get("participants")
    if not raw:
        return RuleOutcome.passed()
    if not isinstance(raw, list):
        return RuleOutcome.failed("participants must be a list of npc ids or names")

    known = set(state.npcs) | {npc.display_name for npc in state.npcs.values()}
    unknown = [str(p) for p in raw if str(p) not in known]
    if unknown:
        return RuleOutcome.failed(
            f"participants name characters absent from the world: {unknown}; "
            f"the cast is {sorted(npc.display_name for npc in state.npcs.values())}"
        )
    return RuleOutcome.passed()


def _parse_payoff_condition(proposal: ActionProposal) -> Condition | None:
    raw = proposal.payload.get("payoff_condition")
    if raw is None:
        return None
    if isinstance(raw, Condition):
        return raw
    try:
        return Condition.model_validate(raw)
    except Exception:
        return None


def _payoff_condition_must_be_checkable(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    """The generated condition must be one the ledger can actually evaluate.

    This is the Validator half of "the LLM writes the loop, the Validator gates
    it". Three ways a generated condition fails to be a loop at all: it is empty
    (``evaluate`` fails closed, so it would never come due), it addresses something
    that does not exist, or it reads a channel the player cannot influence.
    """
    condition = _parse_payoff_condition(proposal)
    if condition is None:
        return RuleOutcome.failed(
            "plant_foreshadowing requires a structured payoff_condition "
            "({mode, clauses:[{path, op, value}]})"
        )
    if not condition.clauses:
        return RuleOutcome.failed(
            "payoff_condition has no clauses, so it would never come due "
            "(an empty condition evaluates False)"
        )

    for clause in condition.clauses:
        if not clause.path.startswith(FORESHADOW_PAYOFF_PATH_PREFIXES):
            return RuleOutcome.failed(
                f"payoff_condition path {clause.path!r} is not a channel a foreshadowing may "
                f"wait on; use one of {list(FORESHADOW_PAYOFF_PATH_PREFIXES)}"
            )
        try:
            resolve_path(state, clause.path)
        except UnknownPathError as exc:
            return RuleOutcome.failed(
                f"payoff_condition path {clause.path!r} cannot be resolved, so the ledger "
                f"could never check it: {exc}"
            )
    return RuleOutcome.passed()


def _foreshadowing_must_not_be_due_yet(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    """A loop that is already satisfied is not a loop.

    Planting one would register a debt and immediately be payable, producing a
    payoff rate that looks perfect while nothing was ever actually withheld.
    """
    condition = _parse_payoff_condition(proposal)
    if condition is None:  # reported by _payoff_condition_must_be_checkable
        return RuleOutcome.passed()
    try:
        already_due = evaluate(condition, state)
    except (UnknownPathError, TypeError, ValueError):
        # Also reported by the checkability rule, which runs first.
        return RuleOutcome.passed()
    if already_due:
        return RuleOutcome.failed(
            "payoff_condition already holds, so this foreshadowing would be due the moment "
            "it is planted; pick a threshold the player has yet to reach"
        )
    return RuleOutcome.passed()


def _payoff_target_must_be_open(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    if proposal.target_id not in state.story_beats.open_foreshadowings:
        return RuleOutcome.failed(f"no open foreshadowing {proposal.target_id!r} to pay off")
    return RuleOutcome.passed()


def _payoff_must_be_due(proposal: ActionProposal, state: WorldState) -> RuleOutcome:
    """Settling early would claim a payoff the player has not reached.

    Uses the same evaluator as PlayerView, so "the ledger says settled" and "the
    player can see it" cannot disagree.
    """
    entry = state.story_beats.open_foreshadowings[proposal.target_id]  # guarded above
    if not evaluate(entry.payoff_condition, state):
        return RuleOutcome.failed(
            f"foreshadowing {proposal.target_id!r} is not due yet: its payoff condition is unmet"
        )
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
                # The two narrative actions that name a fact id. advance_turn and
                # advance_story_beat carry their whole request in the payload, so
                # they legitimately have no target.
                ActionType.PLANT_FORESHADOWING.value,
                ActionType.PAY_OFF_FORESHADOWING.value,
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
    # Narrative actions. The system-only check comes first so that the reported
    # reason for an NPC's attempt is "you may not do this" rather than a detail of
    # a payload it should not have been submitting at all.
    Rule(
        "narrative_actions_are_system_only",
        _narrative_actions_are_system_only,
        NARRATIVE_ACTION_TYPES,
    ),
    Rule(
        "operator_must_be_known",
        _operator_must_be_known,
        frozenset({ActionType.ADVANCE_TURN.value}),
    ),
    Rule(
        "beat_advance_must_change_something",
        _beat_advance_must_change_something,
        frozenset({ActionType.ADVANCE_STORY_BEAT.value}),
    ),
    Rule(
        "event_lifecycle_payload_is_coherent",
        _event_lifecycle_payload_is_coherent,
        frozenset({ActionType.ADVANCE_STORY_BEAT.value}),
    ),
    Rule(
        "chapter_advances_monotonically",
        _chapter_advances_monotonically,
        frozenset({ActionType.ADVANCE_STORY_BEAT.value}),
    ),
    Rule(
        "tension_must_be_a_unit_fraction",
        _tension_must_be_a_unit_fraction,
        frozenset({ActionType.ADVANCE_STORY_BEAT.value}),
    ),
    Rule(
        "planted_fact_id_must_be_new",
        _planted_fact_id_must_be_new,
        frozenset({ActionType.PLANT_FORESHADOWING.value}),
    ),
    Rule(
        "participants_must_exist",
        _participants_must_exist,
        frozenset({ActionType.PLANT_FORESHADOWING.value}),
    ),
    Rule(
        "payoff_condition_must_be_checkable",
        _payoff_condition_must_be_checkable,
        frozenset({ActionType.PLANT_FORESHADOWING.value}),
    ),
    Rule(
        "foreshadowing_must_not_be_due_yet",
        _foreshadowing_must_not_be_due_yet,
        frozenset({ActionType.PLANT_FORESHADOWING.value}),
    ),
    Rule(
        "payoff_target_must_be_open",
        _payoff_target_must_be_open,
        frozenset({ActionType.PAY_OFF_FORESHADOWING.value}),
    ),
    Rule(
        "payoff_must_be_due",
        _payoff_must_be_due,
        frozenset({ActionType.PAY_OFF_FORESHADOWING.value}),
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

"""Can each ending actually be reached? (docs/13 §11)

This module exists because the same content bug has now happened three times, and each
time it looked like nothing at all:

* ``quests.investigation.stage`` had an author and readers and no writer, so three
  foreshadowings waited forever on ``stage >= 2`` and tension stayed 0;
* ``loren_that_night`` declared two channels and the "independent investigation" one was
  rubble, because the player could not move;
* ``relationships.npc_a.player_1.fear`` gated the clam-up ending and almost nothing moved
  it, because the planning prompt only ever exemplified trust.

One shape: **an author wrote a road and nobody checked it went anywhere.** The scenario
loader already refuses a ``path`` it cannot resolve — but resolving is far too weak a
test, since every one of the three bugs above resolves perfectly. What was missing is
whether the *value* at that path can ever change.

So a pack declares its endings with the condition each stands behind, and this walks
them. Two questions per clause, both cheap and both static:

    reaches the path?     the existing resolver — a typo is still a typo
    can the value move?   is there any writer for it?

The second is the new one, and it is deliberately conservative: it asks whether some
authored event or engine channel writes that path at all, not whether a particular
playthrough gets there. Proving a threshold attainable would need a search over the whole
event graph and the slot budget, and a checker that ambitious would be wrong in ways
nobody could debug. The narrow version catches all three historical bugs, which is the
bar docs/13 §11 sets: keep this class of silent content bug out at startup.

Top-level rather than inside ``narrative/`` because ``scenario`` calls it and
``narrative.rules`` imports ``scenario`` — putting it in the narrative package makes that
a cycle. It reads schemas and the condition evaluator and nothing else, so it has no
natural home in either package.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .schemas.common import Condition, ConditionClause
from .schemas.events import EventScript
from .schemas.narrative import NarrativeOperator
from .schemas.world_state import WorldState
from .world.conditions import UnknownPathError, clause_holds_for, resolve_path

#: Paths the engine writes regardless of what a pack authors, each with the writer.
#:
#: Enumerated rather than given as one ``story_beats.`` prefix, and that is the whole
#: discipline of this table: a broad prefix asserts "the engine writes everything under
#: here", which is how a dead channel passes. ``story_beats.chapter`` is the live example
#: — it has a writer in ``_apply_story_beat`` but nothing currently proposes it, so a pack
#: gating an ending on the chapter deserves the same warning ``stage`` deserved.
#:
#: The named function is the audit trail. If one of these is ever removed, the entry has
#: to go with it, and the endings resting on it start failing the check.
ENGINE_WRITTEN_PREFIXES: dict[str, str] = {
    # NarrativeEngine._sync_progress_stage, as paced clues get told (docs/10 §3.2).
    "quests.": "the Engine advances the progress quest as paced clues are told",
    # Deliberately NOT here: `story_beats.tension`. The Engine has the writer
    # (`_escalate_effects`), but it only fires for an `escalate` event, so whether tension
    # can move is a property of the *pack* rather than of the engine. Listing it here would
    # be the exact overreach this table warns about — and it would pass a pack whose
    # escalate event has not been written yet, which is the state the village pack is in.
    # `writers_for_paths` adds it when the script actually contains a mover.
    # WorldStateManager._apply_event_lifecycle, via advance_slot.
    "story_beats.time_slot": "the clock advances one slot per player action",
    "story_beats.slots_spent_today": "the clock advances one slot per player action",
    "time_day": "the day turns over at the wrap-up",
    # Same function: the event lifecycle and the flags outcomes raise.
    "story_beats.completed_events": "events are recorded as they finish",
    "story_beats.closed_events": "events that fail permanently are recorded",
    "story_beats.flags": "outcomes raise flags",
    "story_beats.visited_locations": "arriving somewhere new records the visit",
    "story_beats.turn": "every tick closes a turn",
    "story_beats.active_event": "events open and close",
    "story_beats.open_foreshadowings": "foreshadow events register ledger entries",
    "story_beats.planted_total": "foreshadow events register ledger entries",
    "story_beats.recent_operators": "every tick records its operator",
    "story_beats.spent_one_shots": "`reverse` marks itself used",
}


@dataclass(frozen=True)
class UnreachableClause:
    """One clause of an ending's condition that nothing can satisfy.

    Carries the clause and the reason separately so a caller can report the path the
    author wrote alongside what is missing, which is the difference between a message
    that gets fixed and one that gets shrugged at.
    """

    ending_id: str
    path: str
    reason: str

    def __str__(self) -> str:
        return f"ending {self.ending_id!r} reads {self.path!r}, but {self.reason}"


@dataclass(frozen=True)
class Writers:
    """What the pack and the engine can move, derived from the script.

    Relationship dimensions are tracked as ``(npc_id, dimension)`` pairs rather than as
    two independent sets. The distinction is not pedantic: the ``fear`` bug was
    dimension-shaped — ``relationships.npc_a.*`` was written on nearly every turn while
    ``fear`` specifically was not — and a checker that unioned the halves would have said
    "npc_a is written, fear is written" and passed it.
    """

    relationship_pairs: frozenset[tuple[str, str]] = frozenset()
    revealed_facts: frozenset[str] = frozenset()
    prefixes: frozenset[str] = frozenset()


def writers_for_paths(script: EventScript) -> Writers:
    """Everything the pack's events plus the engine can move.

    Derived from the event script rather than declared, so it cannot drift from what the
    events actually do. An outcome that stops changing a value stops appearing here and
    the ending that depended on it starts failing — which is the whole point.
    """
    pairs: set[tuple[str, str]] = set()
    facts: set[str] = set()
    prefixes: set[str] = set(ENGINE_WRITTEN_PREFIXES)

    for event in script.events.values():
        for outcome in event.outcomes.values():
            for npc_id, changes in outcome.relationship_changes.items():
                for dimension in changes:
                    pairs.add((npc_id, dimension))
                if outcome.scales_with_current_trust:
                    pairs.add((npc_id, "trust"))
            if outcome.scales_with_current_trust and event.npc_id is not None:
                pairs.add((event.npc_id, "trust"))
            facts.update(outcome.reveals_facts)
            if outcome.tension_change:
                prefixes.add("story_beats.tension")

        # `escalate` raises tension through the Engine even when no outcome names a
        # change, so the operator is itself a writer. Kept here rather than in the engine
        # table because whether the pack *has* such an event is a property of the pack.
        if event.operator is NarrativeOperator.ESCALATE:
            prefixes.add("story_beats.tension")

        # A checked option raises ``fear`` on failure through the floor in
        # ``resolution.apply_outcome``, whether or not any outcome names it. The floor is
        # charged against the event's own NPC, which is why the pair uses ``event.npc_id``
        # and not the id the check happens to read.
        if event.npc_id is not None and any(
            option.check is not None and option.tag.is_checked for option in event.options
        ):
            pairs.add((event.npc_id, "fear"))

    return Writers(
        relationship_pairs=frozenset(pairs),
        revealed_facts=frozenset(facts),
        prefixes=frozenset(prefixes),
    )


def _clause_is_writable(clause: ConditionClause, writers: Writers) -> str | None:
    """``None`` if this clause can become true, else why not.

    Note the question is *can this clause hold*, not *can this value move* — the two come
    apart for a clause that is *already* true at the start, which needs no writer at all.
    A failure ending is the honest case: "``ella_whereabouts`` is still hidden" asks the
    world to have *not* changed, and demanding a writer for it would report the one ending
    nobody has to earn as unreachable.

    The caller checks the opening state first, so by the time this runs the clause is known
    to be unsatisfied right now, and something therefore has to move it.
    """
    path = clause.path

    if path.startswith("relationships."):
        parts = path.split(".")
        if len(parts) < 4:
            return f"{path!r} is not a complete relationship path (npc, target, dimension)"
        npc_id, dimension = parts[1], parts[3]
        if (npc_id, dimension) in writers.relationship_pairs:
            return None
        moved_for_npc = sorted(dim for npc, dim in writers.relationship_pairs if npc == npc_id)
        if not moved_for_npc:
            return f"no event changes any relationship for {npc_id!r}"
        return (
            f"no event changes {npc_id}'s {dimension!r}; it is read but never written, so "
            f"this gate cannot be crossed (what does move: {moved_for_npc})"
        )

    if path.startswith("facts."):
        fact_id = path.split(".")[1] if "." in path else ""
        if fact_id not in writers.revealed_facts:
            return f"no outcome reveals fact {fact_id!r}"
        return None

    for prefix in writers.prefixes:
        if path.startswith(prefix) or path == prefix:
            return None

    return f"nothing in the pack or the engine writes {path!r}"


def unreachable_clauses(
    endings: dict[str, Condition],
    *,
    world: WorldState,
    script: EventScript,
) -> list[UnreachableClause]:
    """Every ending clause that cannot be satisfied, with the reason.

    Empty means each declared ending has, for every clause, both a resolvable path and
    something able to move it. That is short of "this ending is attainable within the slot
    budget" — see the module docstring on why the stronger claim is not attempted — and it
    is exactly the check that would have caught all three historical bugs.
    """
    writers = writers_for_paths(script)

    problems: list[UnreachableClause] = []
    for ending_id, condition in endings.items():
        if not condition.clauses:
            problems.append(
                UnreachableClause(
                    ending_id=ending_id,
                    path="(none)",
                    reason=(
                        "it declares no clauses, and an empty condition evaluates False "
                        "by design (a vacuous 'all' would unlock everything)"
                    ),
                )
            )
            continue

        for clause in condition.clauses:
            try:
                actual = resolve_path(world, clause.path)
            except UnknownPathError as exc:
                problems.append(
                    UnreachableClause(ending_id=ending_id, path=clause.path, reason=str(exc))
                )
                continue

            # A clause already true in the opening state needs no writer. Failure endings
            # are built out of these — "``ella_whereabouts`` is still hidden" asks the world
            # to have *not* changed — so requiring a writer here would report the one ending
            # nobody has to earn as unreachable. Enums resolve to their member, hence the
            # tolerant comparison, which reports False rather than raising on a type
            # mismatch (a mismatch is a real problem, and the writer check below names it).
            if clause_holds_for(_comparable(actual), clause):
                continue

            reason = _clause_is_writable(clause, writers)
            if reason is not None:
                problems.append(
                    UnreachableClause(ending_id=ending_id, path=clause.path, reason=reason)
                )

    return problems


def _comparable(value: object) -> object:
    """An authored YAML scalar's counterpart for a resolved value.

    ``Visibility`` and ``TimeSlot`` resolve to enum members while a pack writes
    ``hidden`` / ``evening`` as plain strings. ``str`` enums compare equal to their value
    anyway, but being explicit keeps this working if one is ever a plain ``Enum``.
    """
    return value.value if isinstance(value, Enum) else value

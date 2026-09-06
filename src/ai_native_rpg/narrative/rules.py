"""Trigger rules: which authored events the world currently permits (docs/13 §2).

Pure functions over ``WorldState`` plus the pack's event script. No LLM: "has a
threshold been crossed" is a comparison, and docs/01 §1 reserves model calls for
genuinely open-ended work. The output is a candidate *list*, not a decision —
choosing among them is the Experience Controller's job (``controller.py``).

**This used to produce operator candidates, and that was the wrong unit.** docs/13 §1
records what went wrong: ``foreshadow`` / ``reveal`` / ``escalate`` / ``reverse`` are
rhetorical moves, not plot. They have no antecedent and no consequent, so the question
"what should happen this turn?" had nothing to answer from — ``_foreshadow_candidates``
ended up gated on "the ledger has room and we did not plant last turn", which asks
nothing about whether the story needs a hint. Being the lowest-intensity candidate, it
then won only on turns where nothing else qualified, filling exactly the quiet turns
``relieve`` exists for. Events have all three parts, which is what makes them
schedulable.

**No operator-level fallback.** docs/13 §2.1: when no event's condition holds, the turn
is quiet. A small filler beat is still filler, and "nothing happens" is a normal
outcome the architecture already reserved a place for (docs/05 §3). The cost is that the
script must be written out — uncovered world states really do produce silence — and
that cost is accepted deliberately.

**Triggers are hard conditions.** An event's ``trigger`` is evaluated by the existing
evaluator: at trust 38 a ``trust >= 40`` event does not fire and gets no random band.
The three-band check with its random middle applies to *option* checks only
(docs/15 §3). docs/15 §6.2 flags this as the easiest confusion in the design, and it
would show up as authored thresholds behaving differently from the script.

The rules stay in Python while the *content* they read comes from the pack, per
docs/05 §6. The line is whether swapping stories would change it: "no two reveals in a
row" would not, ``clue_2_identity`` would.
"""

from __future__ import annotations

from ..scenario import Ending, NarrativeDirectives
from ..schemas.events import EventDefinition, EventScript
from ..schemas.narrative import EventCandidate
from ..schemas.world_state import Visibility, WorldState
from ..world.conditions import UnknownPathError, evaluate

#: Fallback when no pack directives are supplied: pace nothing, forbid nothing.
#:
#: Empty rather than the village scenario's clue list, which is what used to sit
#: here. Hardcoding one story's fact ids made every other pack silently wrong — and
#: the wording was Chinese, so an English pack would have received Chinese
#: prohibitions from framework code its author never touched.
_NO_DIRECTIVES = NarrativeDirectives()


def _is_unlockable(world: WorldState, fact_id: str) -> bool:
    """Whether the world would let the player know this fact.

    Mirrors ``player_view._effective_visibility`` rather than re-implementing it,
    so "may be told" and "is visible" cannot drift apart. A malformed condition
    reads as *not* unlockable here (the loud failure belongs to the scenario
    loader, which already rejects unresolvable paths at load time).
    """
    fact = world.facts.get(fact_id)
    if fact is None:
        return False
    if fact.visibility is Visibility.REVEALED:
        return True
    if fact.reveal_condition is None:
        return False
    try:
        return evaluate(fact.reveal_condition, world)
    except (UnknownPathError, TypeError, ValueError):
        return False


def _is_told(world: WorldState, fact_id: str) -> bool:
    """Whether someone has actually voiced this fact.

    Stored visibility, deliberately not effective visibility: ``reveal_fact`` is
    what writes ``REVEALED``, so this is the record of having said it.
    """
    fact = world.facts.get(fact_id)
    return fact is not None and fact.visibility is Visibility.REVEALED


def earned_stage(world: WorldState, directives: NarrativeDirectives) -> int:
    """How far in the player has *demonstrably* got: paced clues actually told.

    docs/10 §3.2 names ``quests.<progress>.stage`` as the "逼近答案的程度" channel and
    docs/04 §3.3 permits the Engine to advance it, but nothing was advancing it —
    so it sat at 0 for a whole run while three foreshadowings waited on ``stage >= 2``
    and ``escalate`` (which requires ``stage >= 1``) never fired once. Tension, payoff
    and pacing all stalled on the same missing writer.

    Told clues, deliberately, not unlockable ones. ``_is_unlockable`` answers "may the
    player know this", which trust alone can satisfy in a single generous turn; being
    *told* is a beat that happened. Using stored visibility also makes this monotone —
    nothing un-tells a clue — so the stage never walks backwards and un-gates content.

    Counting is a comparison, so no LLM (docs/01 §1). Pure, and the clue ids come from
    the pack: a story swap changes what counts, not this function.
    """
    return sum(1 for clue in directives.paced_clues if _is_told(world, clue.fact_id))


def progress_quest(world: WorldState, directives: NarrativeDirectives):
    """The quest carrying the stage channel, or ``None`` if the pack names none.

    A pack without ``progress_quest`` gets no stage channel rather than a guess:
    ``world.quests["investigation"]`` used to be read here by name, which is one
    story's id sitting in framework code — the thing docs/09 says ``rules.py`` must
    not contain, and which silently made the channel dead for every other pack.
    """
    if directives.progress_quest is None:
        return None
    return world.quests.get(directives.progress_quest)


def _trigger_holds(world: WorldState, event: EventDefinition) -> bool:
    """Whether this event's hard condition is satisfied right now.

    Malformed conditions read as "does not fire" rather than raising: the loader
    rejects unresolvable paths at startup, so by the time a turn is running a broken
    trigger should cost silence, not the turn.
    """
    try:
        return evaluate(event.trigger, world)
    except (UnknownPathError, TypeError, ValueError):
        return False


def triggerable_events(world: WorldState, script: EventScript | None) -> list[EventDefinition]:
    """Every event the script and the world jointly permit, in script order.

    Order is fixed rather than sorted, because the Controller breaks ranking ties with
    a stable ``max`` — a replayed trace has to select the same beat (docs/07).
    """
    if script is None:
        return []

    beats = world.story_beats
    active_id = beats.active_event.event_id if beats.active_event else None

    permitted: list[EventDefinition] = []
    for event in script.events.values():
        # Closed means never again (docs/15 §3.3); completed usually means "not right
        # now" — an event the player could re-enter every single turn is the filler
        # loop this layer replaced. Re-runnable events say so explicitly, and the slot
        # clock that makes "next slot" meaningful arrives with the time system.
        if beats.is_closed(event.event_id):
            continue
        if beats.has_completed(event.event_id) and not event.repeatable:
            continue
        # Continuing the event in progress is not the same act as triggering a new
        # one, and one conversation at a time is the rule (docs/13 §5.2).
        if event.event_id == active_id:
            continue
        if not _trigger_holds(world, event):
            continue
        permitted.append(event)
    return permitted


def _describe_trigger(event: EventDefinition) -> str:
    """Human-readable trigger, for the panel and the trace.

    docs/07's stance: the panel must be able to explain *why* a beat ran. A clause
    list rendered plainly does that; "trigger satisfied" does not.
    """
    parts = [f"{c.path} {c.op.value} {c.value!r}" for c in event.trigger.clauses]
    joiner = " and " if event.trigger.mode == "all" else " or "
    return f"event {event.event_id!r} triggered: {joiner.join(parts)}"


def check_triggers(
    world: WorldState,
    *,
    player_id: str,
    script: EventScript | None = None,
    directives: NarrativeDirectives | None = None,
) -> list[EventCandidate]:
    """Every event the world currently permits, unranked.

    Pure. Returning several candidates (or none) is normal: narrowing to at most
    one is the Experience Controller's decision, and "nothing should happen" is a
    valid outcome there (docs/05 §3).

    ``script`` is the pack's authored event network and ``directives`` its narrative
    content. Omitting the script yields no candidates rather than falling back to
    operator-level beats — see the module docstring on why that fallback was removed.
    """
    directives = directives or _NO_DIRECTIVES

    return [
        EventCandidate(
            operator=event.operator,
            event_type=event.event_id,
            event_id=event.event_id,
            intensity=event.intensity,
            preference_tag=event.preference_tag,
            trigger_reason=_describe_trigger(event),
            constraints=[*event.constraints, *directives.universal_constraints],
            # Deliberately not ``pays_off``: that field means "this beat settles an
            # open ledger entry now", while ``payoff_target`` is the fact whose future
            # disclosure will settle what this event *plants*. Opposite directions in
            # time, and conflating them would make a foreshadow claim its own payoff.
            plants_toward=event.payoff_target,
        )
        for event in triggerable_events(world, script)
    ]


#: Flag prefix a milestone ending is recorded under (docs/14 §4.3).
#:
#: Not ``story_beats.ended_at``: that field means "the case is over", and a milestone
#: like ``marta_clams_up`` explicitly is not — it closes one social line while the
#: tavern and forest channels stay open. Recording it as a flag instead uses the
#: mechanism already built for "named state an outcome raised" rather than inventing a
#: second one, and keeps a milestone readable by a later ``Condition`` the same way
#: ``probed_once`` is.
MILESTONE_FLAG_PREFIX = "milestone:"


def milestone_flag(ending_id: str) -> str:
    return f"{MILESTONE_FLAG_PREFIX}{ending_id}"


def _checkable_endings(endings: list[Ending]) -> list[Ending]:
    """Endings the game may actually reach right now.

    Excludes ``pending`` ones for the same reason the loader's own reachability check
    does (docs/13 §11): a pending ending's events are not written yet, so its condition
    happening to read true would be a false alarm from an unrelated state, not the road
    it was meant to describe.
    """
    return [e for e in endings if not e.pending]


def check_terminal_ending(endings: list[Ending], world: WorldState) -> str | None:
    """The first terminal ending whose condition now holds, or ``None``.

    Called every tick regardless of what beat ran, because docs/14 §4.3's
    ``never_found_out`` needs no event at all — ``time_day`` alone can cross it, and a
    check wired only into event resolution would never see that happen. Order is
    script order (``endings`` as declared), so a pack that lists several conditions
    that could go true in the same tick picks the outcome deterministically rather than
    by dict iteration order.

    A malformed condition is treated as unmet, matching ``_trigger_holds``: the loader
    already rejects an unresolvable path at startup, so by the time a turn is running a
    broken clause should cost silence, not the turn.
    """
    for ending in _checkable_endings(endings):
        if not ending.terminal:
            continue
        try:
            if evaluate(ending.condition, world):
                return ending.ending_id
        except (UnknownPathError, TypeError, ValueError):
            continue
    return None


def newly_reached_milestones(endings: list[Ending], world: WorldState) -> list[str]:
    """Non-terminal endings whose condition now holds but whose flag is not yet set.

    Only the *new* ones: a milestone flag is set once and never cleared (docs/14 §4.3
    calls closing Marta's line a one-way cost, not a state that can un-happen), so a
    milestone already recorded must not be handed back here to be raised again.
    """
    flags = world.story_beats.flags
    reached: list[str] = []
    for ending in _checkable_endings(endings):
        if ending.terminal:
            continue
        if milestone_flag(ending.ending_id) in flags:
            continue
        try:
            if evaluate(ending.condition, world):
                reached.append(ending.ending_id)
        except (UnknownPathError, TypeError, ValueError):
            continue
    return reached

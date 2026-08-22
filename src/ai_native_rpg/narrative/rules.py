"""Trigger rules: what the world currently permits (docs/05 §2.1).

Pure functions over ``WorldState``. No LLM: "has a threshold been crossed" is a
comparison, and docs/01 §1 reserves model calls for genuinely open-ended work. The
output is a candidate *list*, not a decision — choosing among them is the
Experience Controller's job (``controller.py``).

The *rules* are hard-coded rather than configurable, per docs/05 §6: a rule table
becomes worth building when the rules outgrow one readable file, and six do not. The
*content* they read is not — which clues to pace, what to withhold while paced, and
which fact reverses arrive as ``NarrativeDirectives`` from the pack's ``narrative:``
block. The line between them is whether swapping stories would change it: "no two
reveals in a row" would not, ``clue_2_identity`` would.

This split also makes translation a pack concern. The prohibitions are prose handed
to a model, so when they lived here as Chinese literals an English pack inherited
Chinese instructions from framework code its author never touched.

Two design points worth stating, because both were tempting to get wrong:

**A `reveal` candidate does not unlock anything.** ``PlayerView`` already widens a
fact the instant its ``reveal_condition`` holds, with no action required. So the
operator's subject is not disclosure but *voicing*: a fact that is unlockable while
its stored visibility is still ``hidden`` is one the player is entitled to and
nobody has said out loud. That gap is deterministic, which is what makes it
schedulable. It also means the Engine can never widen anything by itself
(docs/04 §3.3) — it only paces what the condition table already allows.

**Constraints name secrets without quoting them.** ``tools.py`` takes the position
that text absent from the prompt cannot be leaked from it; the same holds here. A
constraint says "do not name the person Marta saw", never the value of
``killer_identity``.
"""

from __future__ import annotations

from ..scenario import NarrativeDirectives
from ..schemas.common import PreferenceTag
from ..schemas.narrative import EventCandidate, NarrativeOperator
from ..schemas.world_state import Visibility, WorldState
from ..world.conditions import UnknownPathError, evaluate

#: Fallback when no pack directives are supplied: pace nothing, forbid nothing.
#:
#: Empty rather than the village scenario's clue list, which is what used to sit
#: here. Hardcoding one story's fact ids made every other pack silently wrong — and
#: the wording was Chinese, so an English pack would have received Chinese
#: prohibitions from framework code its author never touched. Which facts to pace is
#: story; "no two reveals in a row" is structure. Only the latter belongs here.
_NO_DIRECTIVES = NarrativeDirectives()

#: Tension ceiling per investigation stage: how tense the story is allowed to get
#: while the player is only this far in.
#:
#: A single constant was the first attempt and it was wrong. With one threshold,
#: ``escalate`` stops firing as soon as tension clears it, so tension plateaus just
#: above the constant — and any fact gated higher becomes permanently unreachable.
#: An author's threshold that can never be met is precisely the silent content bug
#: the scenario loader exists to prevent, in a form the loader cannot detect: it
#: checks that a path resolves, not that a value is attainable.
#:
#: Scaling with the progress quest's ``stage`` also says the right thing: pressure
#: should keep rising as the player closes in, rather than settling at a constant.
_TENSION_CEILING_BY_STAGE: tuple[float, ...] = (0.0, 0.4, 0.7, 1.0)


def _tension_ceiling(stage: int) -> float:
    """Highest tension ``escalate`` may drive the story to at this stage."""
    index = min(max(stage, 0), len(_TENSION_CEILING_BY_STAGE) - 1)
    return _TENSION_CEILING_BY_STAGE[index]


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
    nothing un-tells a clue — so the stage never walks backwards and un-gates content,
    which is the property ``_chapter_advances_monotonically`` protects for chapters.

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


#: How many unsettled foreshadowings may be open at once.
#:
#: Three, because mystery structure wants several hints per conclusion, not one.
#: Justin Alexander's Three Clue Rule ("for any conclusion you want the players to
#: reach, include at least three clues") argues this from failure rates rather than
#: taste: one hint carrying one reveal is a chokepoint, and every step the player
#: must take to read it is another way the story stalls. That applies harder here
#: than at a table, because the last step is an LLM choosing to use the hook at all.
#:
#: This replaces an earlier cap of one open loop plus a lifetime quota of two. The
#: cap was defended as preventing an unpayable pile of debt, but "the ledger is
#: getting long" is what the ledger is *for* — surfacing it via ``is_overdue``
#: (docs/10 §4) is the design, and refusing to let it grow used the thermometer as
#: a thermostat. Nothing in the craft sources prescribes a simultaneous-plant limit;
#: the number was ours. It stays a bound only as runaway protection.
MAX_OPEN_FORESHADOWINGS = 3

#: Quiet turns required between two plants (see ``_COOLDOWNS`` for the same shape).
#:
#: Raising the cap needs this to replace what the old lifetime quota did by accident.
#: The quota's real service was not limiting the total: it stopped a foreshadow being
#: re-offered on the turn right after a payoff emptied the ledger, where a generator
#: reusing the just-spent fact_id burns an LLM call to be rejected by
#: ``planted_fact_id_must_be_new``. A spacing rule stops that without also stopping
#: the second and third plants a conclusion wants.
FORESHADOW_SPACING = 1


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


def _reveal_candidates(
    world: WorldState, player_id: str, directives: NarrativeDirectives
) -> list[EventCandidate]:
    """Clues the player has earned but has not been told."""
    candidates = []
    for clue in directives.paced_clues:
        fact_id = clue.fact_id
        if fact_id not in world.facts:
            continue
        if _is_told(world, fact_id) or not _is_unlockable(world, fact_id):
            continue
        constraints = [clue.constraint] if clue.constraint else []
        candidates.append(
            EventCandidate(
                operator=NarrativeOperator.REVEAL,
                event_type=fact_id,
                intensity=0.5,
                preference_tag=PreferenceTag.SOCIAL.value,
                trigger_reason=(
                    f"fact {fact_id!r} is unlockable (trust/stage threshold met) but untold"
                ),
                constraints=[*constraints, *directives.universal_constraints],
            )
        )
    return candidates


def _payoff_candidates(
    world: WorldState, player_id: str, directives: NarrativeDirectives
) -> list[EventCandidate]:
    """Open loops whose payoff condition now holds.

    A payoff is a ``reveal`` that also settles a ledger entry, not a fifth
    operator — which keeps the operator table at the four of docs/10 §2.1.
    """
    candidates = []
    for fact_id, entry in world.story_beats.open_foreshadowings.items():
        try:
            due = evaluate(entry.payoff_condition, world)
        except (UnknownPathError, TypeError, ValueError):
            continue
        if not due:
            continue
        candidates.append(
            EventCandidate(
                operator=NarrativeOperator.REVEAL,
                event_type=f"payoff:{fact_id}",
                intensity=0.7,
                preference_tag=PreferenceTag.SOCIAL.value,
                trigger_reason=(
                    f"foreshadowing {fact_id!r} planted at turn {entry.planted_at_turn} is due"
                ),
                constraints=list(directives.universal_constraints),
                pays_off=fact_id,
            )
        )
    return candidates


def _foreshadow_candidates(
    world: WorldState, player_id: str, directives: NarrativeDirectives
) -> list[EventCandidate]:
    """Another hint, while the ledger has room and the last plant has had its turn.

    Two bounds, both about runaway rather than about structure:

    * **``MAX_OPEN_FORESHADOWINGS``** — a ceiling on unsettled debt, not a rule that
      one conclusion gets one hint. Several hints per conclusion is what mystery
      structure asks for; see the constant.
    * **``FORESHADOW_SPACING``** — no two plants back to back, which also keeps a
      payoff from immediately re-offering a plant on the next turn.

    No early-game window: the old ``turn <= 6`` gate assumed the only loop was an
    opening one, so nothing could be planted afterwards. With several hints allowed
    per conclusion, later ones are the normal case — a second hint about the same
    thing lands *after* the player has met the first.
    """
    beats = world.story_beats
    if len(beats.open_foreshadowings) >= MAX_OPEN_FORESHADOWINGS:
        return []

    since_plant = beats.turns_since(NarrativeOperator.FORESHADOW)
    if since_plant is not None and since_plant <= FORESHADOW_SPACING:
        return []

    open_count = len(beats.open_foreshadowings)
    return [
        EventCandidate(
            operator=NarrativeOperator.FORESHADOW,
            event_type="planted_detail",
            intensity=0.3,
            preference_tag=PreferenceTag.EXPLORATION.value,
            trigger_reason=(
                f"{open_count}/{MAX_OPEN_FORESHADOWINGS} loops open at turn {beats.turn}, "
                f"{beats.planted_total} planted so far"
            ),
            constraints=list(directives.universal_constraints),
        )
    ]


def _escalate_candidates(
    world: WorldState, player_id: str, directives: NarrativeDirectives
) -> list[EventCandidate]:
    """Pressure, when the player is visibly getting somewhere but nothing is at stake."""
    quest = progress_quest(world, directives)
    if quest is None or quest.stage < 1:
        return []
    ceiling = _tension_ceiling(quest.stage)
    if world.story_beats.tension >= ceiling:
        return []
    return [
        EventCandidate(
            operator=NarrativeOperator.ESCALATE,
            event_type="investigation_noticed",
            intensity=0.6,
            preference_tag=PreferenceTag.INTRIGUE.value,
            trigger_reason=(
                f"investigation at stage {quest.stage} but tension "
                f"{world.story_beats.tension:.2f} < ceiling {ceiling:.2f}"
            ),
            constraints=list(directives.universal_constraints),
        )
    ]


def _reverse_candidates(
    world: WorldState, player_id: str, directives: NarrativeDirectives
) -> list[EventCandidate]:
    """Re-read what the player already knows, once the motive is visible.

    docs/10 §5 priority 2: with the threat known, Marta's evasions stop reading as
    shielding a killer and start reading as shielding her son. No new information —
    the same facts, a different meaning, which is why it adds no cognitive load.

    One-shot, and it needs an explicit marker to be one: a reverse changes no
    visibility, so unlike a reveal it leaves nothing behind that would retire its
    own trigger.
    """
    reversal_fact = directives.reversal_fact
    if reversal_fact is None:
        return []
    key = f"reverse:{reversal_fact}"
    if world.story_beats.is_spent(key) or not _is_unlockable(world, reversal_fact):
        return []
    return [
        EventCandidate(
            operator=NarrativeOperator.REVERSE,
            event_type=reversal_fact,
            intensity=0.8,
            preference_tag=PreferenceTag.INTRIGUE.value,
            trigger_reason=(
                f"fact {reversal_fact!r} is now knowable; what came before can be re-read"
            ),
            constraints=list(directives.universal_constraints),
        )
    ]


#: Evaluated in order; every rule sees the same immutable world.
_RULES = (
    _payoff_candidates,
    _reveal_candidates,
    _reverse_candidates,
    _escalate_candidates,
    _foreshadow_candidates,
)


def check_triggers(
    world: WorldState,
    *,
    player_id: str,
    directives: NarrativeDirectives | None = None,
) -> list[EventCandidate]:
    """Every operator the world currently permits, unranked.

    Pure. Returning several candidates (or none) is normal: narrowing to at most
    one is the Experience Controller's decision, and "nothing should happen" is a
    valid outcome there (docs/05 §3).

    ``directives`` carries the pack's authored content (which clues to pace, what to
    withhold). Omitting it paces nothing rather than falling back to some story's
    clue list, so a caller that forgets loses reveals visibly instead of inheriting
    another scenario's fact ids.
    """
    directives = directives or _NO_DIRECTIVES
    candidates: list[EventCandidate] = []
    for rule in _RULES:
        candidates.extend(rule(world, player_id, directives))
    return candidates

"""Authored events: what happens, what the player may do, what it changes.

docs/13 §2 adds this layer because operators turned out to be the wrong scheduling
unit: "plant a detail" / "say a clue" / "add pressure" are *how* something is told,
with no antecedent and no consequent, so "what should happen this turn?" had no
grounds to be answered on. An event has all three — a trigger, an outcome set, and
branches — which is what makes it schedulable. Operators stay, demoted to "how this
event is voiced" (docs/13 §2).

**Two rule sets that look alike and are not** (docs/15 §6.2, stated there because
it was the easiest thing in the design to get wrong):

* an event's ``trigger`` is a hard ``Condition``. ``trust >= 40`` at 38 is unmet.
  No band, no roll — the existing evaluator answers it.
* an *option's* ``check`` is the three-band comparison of docs/15 §3, where only
  the middle band is random.

Mixing them would make every authored threshold behave differently from what the
script says, in a way play would surface as "the numbers lie".

**What is fixed here and what is left to the model** (docs/13 §3): the trigger, the
outcome set, and which outcome fires are all authored data, because outcomes change
what is unlocked and docs/04 §3.3 forbids any actor from routing around
``reveal_condition``. The model gets the wording, and the classification of a free
-text reply into one of these outcomes — a mapping, not a decision.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

from .common import (
    MAX_RELATIONSHIP_STEP,
    RELATIONSHIP_DIMENSIONS,
    Condition,
    PreferenceTag,
)
from .narrative import NarrativeOperator

#: ``story_beats`` flag recording that a [试探] has already failed (docs/15 §7).
#:
#: A flag rather than a counter: docs/15 §4 M2 asks for "has this happened", and the
#: penalty does not stack. Lives under ``story_beats.flags`` so a ``Condition`` can
#: read it without a new top-level field (docs/13 §7).
FLAG_PROBED_ONCE = "probed_once"

#: How much a failed probe raises later probe thresholds (docs/15 §4 M2).
#:
#: The point is not the size but the *kind* of consequence: docs/13 §8 wants side
#: content and failure to change the main line's shape rather than its progress.
#: This narrows one road — the risky one — and leaves the others untouched.
PROBE_PENALTY_AFTER_FAILED_PROBE = 10.0

#: Random band per tag (docs/15 §3). The band is design language, not a knob: a
#: [试探] is a gamble and the wide band is how the system says so, while [示好]
#: never rolls at all. So the tag carries it and an author writes only a threshold.
_BAND_BY_TAG: dict[str, float] = {
    "probe": 12.0,
    "press": 8.0,
    "goodwill": 0.0,
    "observe": 0.0,
}

#: Band edges for a [示好]'s amplitude (docs/15 §3.2): ``< 25`` pays 8, ``25–55``
#: pays 12, ``> 55`` pays the cap. Note 25 is in the middle band and 55 is too —
#: the doc's table reads "25–55", so both edges belong to it.
_GOODWILL_LOW_EDGE = 25.0
_GOODWILL_HIGH_EDGE = 55.0


class OptionTag(str, Enum):
    """What this line *does to her* — not a player skill (docs/15 §2).

    There is no skill system: what decides an outcome is her ``trust`` and her
    traits. A tag named for a player ability ("empathy") would promise a system
    that does not exist, which docs/15 §2 calls design debt rather than flavour.

    Deliberately asymmetric in whether they roll. Making [示好] checkable would
    turn it into an action that fails for no visible reason; leaving [观察] free of
    relationship movement makes it the one way to press on without gambling, which
    is what a player facing high ``fear`` needs.
    """

    GOODWILL = "goodwill"
    PRESS = "press"
    PROBE = "probe"
    OBSERVE = "observe"

    @property
    def preference_tag(self) -> str:
        """``PreferenceTag`` this option feeds, for the Controller's ranking.

        [观察] maps to ``exploration`` because it is the only thing an explorer can
        *do* in a room whose content is a conversation — without it that taste
        receives no signal all game (docs/15 §2).
        """
        if self is OptionTag.GOODWILL:
            return PreferenceTag.SOCIAL.value
        if self is OptionTag.OBSERVE:
            return PreferenceTag.EXPLORATION.value
        return PreferenceTag.INTRIGUE.value

    @property
    def is_checked(self) -> bool:
        return self in (OptionTag.PRESS, OptionTag.PROBE)


class CheckBand(str, Enum):
    """Which of the three bands a check fell in (docs/15 §3).

    Named rather than reduced to a bool so a trace can say *why* an option went the
    way it did. docs/07's stance is that the panel must explain a failure; "you were
    15 short of the threshold" explains, "unlucky" does not.
    """

    CERTAIN_FAILURE = "certain_failure"
    RANDOM = "random"
    CERTAIN_SUCCESS = "certain_success"


def check_band(*, current: float, threshold: float, band: float) -> CheckBand:
    """Which band ``current`` sits in relative to ``threshold``.

    The whole point is that the *player* cannot see which band they are in — they
    know she is "still wary", not that trust is 33 rather than 41. So the check is
    deterministic while the experience is uncertain, and the uncertainty comes from
    incomplete knowledge rather than from the system rolling dice (docs/15 §3).

    Meeting the threshold exactly with a non-zero band lands in ``RANDOM``: the band
    is centred on the threshold, and reading equality as success would quietly halve
    every authored risk.

    A ``band`` of 0 collapses the middle instead of leaving a one-point random sliver
    at equality. The unchecked tags never reach this function at all, so the case is
    about being coherent rather than about play: with no band there is no gamble, and
    "she is exactly at the threshold" should read as met.
    """
    gap = current - threshold
    if band <= 0:
        return CheckBand.CERTAIN_SUCCESS if gap >= 0 else CheckBand.CERTAIN_FAILURE
    if gap < -band:
        return CheckBand.CERTAIN_FAILURE
    if gap > band:
        return CheckBand.CERTAIN_SUCCESS
    return CheckBand.RANDOM


def effective_threshold(threshold: float, *, tag: OptionTag, flags: list[str]) -> float:
    """The threshold after state-dependent penalties (docs/15 §4 M2).

    Only probes are penalised, and only by having already failed one. This is the
    smallest instance of docs/13 §8's rule that consequences should change the main
    line's *shape*: nothing got lower, one road got narrower.
    """
    if tag is OptionTag.PROBE and FLAG_PROBED_ONCE in flags:
        return threshold + PROBE_PENALTY_AFTER_FAILED_PROBE
    return threshold


#: What a failed check costs in ``fear`` when the outcome does not say (docs/14 §4.3).
#:
#: The "玛尔塔彻底闭口" ending is gated on ``fear >= 70`` and ``fear`` barely moved in
#: play: NPCs proposing ``adjust_relationship`` almost only touched ``trust``, because
#: the planning prompt's examples were all about trust. A prompt fix alone would not be
#: enough — the model has no reason to know the ending exists, and an ending reachable
#: only when a model happens to volunteer the right dimension is the same dead channel
#: docs/13 §11 records twice.
#:
#: So pressing has a floor that does not depend on a model choosing to apply it. Small
#: (a failed press is not a threat) and only on *checked* tags, which is what makes it
#: legible: the player learns that pushing costs something, which is the design's own
#: claim about [追问] and [试探] in docs/15 §2.
FEAR_ON_FAILED_PRESS = 5.0

#: And on a failed probe, which docs/15 §2 calls the gamble.
#:
#: Larger than a press for the reason the band is wider: a probe that misses is a caught
#: attempt at manoeuvring her, not just an unwelcome question.
FEAR_ON_FAILED_PROBE = 8.0

#: Fear floor by tag, applied only when an outcome names no ``fear`` change itself.
#:
#: Authored values always win. An outcome that says ``fear: 12`` means the author has
#: thought about this beat, and a floor that added to it would silently inflate every
#: number docs/15 §4 lists.
FEAR_FLOOR_BY_TAG: dict[str, float] = {
    OptionTag.PRESS.value: FEAR_ON_FAILED_PRESS,
    OptionTag.PROBE.value: FEAR_ON_FAILED_PROBE,
}


def fear_floor_for_failure(tag: OptionTag) -> float:
    """How much ``fear`` a failed check of this tag raises at minimum.

    Zero for the unchecked tags: [示好] and [观察] never fail, so there is no failure to
    charge for, and charging them would break the asymmetry docs/15 §2 built on purpose
    — [观察] is specifically the option a frightened player reaches for.
    """
    return FEAR_FLOOR_BY_TAG.get(tag.value, 0.0)


def scaled_trust_gain(current_trust: float) -> float:
    """What a [示好] is worth at this level of wariness (docs/15 §3.2).

    Accelerating on purpose, for the two reasons given there: trust does build faster
    once someone has decided you are worth hearing, and a flat low rate is what made
    the first draft unable to reach M3 within the slot budget at all — "进展慢"
    reproduced as arithmetic. The top band is ``MAX_RELATIONSHIP_STEP`` by design, not
    coincidence: it is the most any single outcome may move a relationship.
    """
    if current_trust < _GOODWILL_LOW_EDGE:
        return 8.0
    if current_trust <= _GOODWILL_HIGH_EDGE:
        return 12.0
    return MAX_RELATIONSHIP_STEP


class EventOutcome(BaseModel):
    """One way an event can land, with its consequences written out.

    Authored, never generated. docs/13 §3 derives this from docs/04 §3.3: outcomes
    change relationship values and set flags, and those gate ``reveal_condition``,
    so a model that could choose an outcome could choose what gets unlocked. The
    model may pick *words*; it may not pick consequences.

    Every change is bounded by ``MAX_RELATIONSHIP_STEP`` at authoring time. The
    Validator enforces it anyway — it is the real gate — but a rare branch could
    ship broken and only fail the first time a player found it, so the loader-style
    stance applies: make it a startup error (docs/15 §3.1).
    """

    outcome_id: str
    summary: str = Field(
        default="", description="short label for the debug panel, e.g. '她隔门应答'"
    )

    relationship_changes: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="npc_id -> {dimension: delta}. Applied as ordinary "
        "adjust_relationship proposals, so the Validator sees each one.",
    )
    reveals_facts: list[str] = Field(
        default_factory=list,
        description="fact ids this outcome voices. Still subject to reveal_condition: "
        "an outcome names what to say, it does not grant permission to say it.",
    )
    stage_advance: bool = Field(
        default=False, description="whether this outcome moves the progress quest a stage"
    )
    tension_change: float = Field(default=0.0, ge=-1.0, le=1.0)
    sets_flags: list[str] = Field(
        default_factory=list,
        description=f"story_beats flags to raise, e.g. {FLAG_PROBED_ONCE!r}",
    )
    closes_event: bool = Field(
        default=False,
        description="permanently close the event, not merely finish it (docs/15 §3.3). "
        "Only M3 and M5 use it, and both have another route to the same information: "
        "closure is a cost, not a dead end.",
    )
    advances_conversation: bool = Field(
        default=True,
        description="False for outcomes that give the player something without moving the "
        "scene on — [观察] in M3 is the case (docs/15 §4).",
    )
    scales_with_current_trust: bool = Field(
        default=False,
        description="apply the [示好] curve of docs/15 §3.2 to this outcome's trust gain "
        "instead of a fixed delta. Authored as a flag rather than as three separate "
        "outcomes because the branch is on the *same* dramatic beat: she heard you, and "
        "what that is worth depends on how wary she still is. docs/15 §6.1's slot budget "
        "is computed from this curve, so the two must not drift.",
    )

    @model_validator(mode="after")
    def _changes_must_be_within_the_step_limit(self) -> Self:
        for npc_id, changes in self.relationship_changes.items():
            for dimension, delta in changes.items():
                if dimension not in RELATIONSHIP_DIMENSIONS:
                    raise ValueError(
                        f"outcome {self.outcome_id!r} changes unknown relationship dimension "
                        f"{dimension!r} on {npc_id!r}; known: {sorted(RELATIONSHIP_DIMENSIONS)}"
                    )
                if abs(delta) > MAX_RELATIONSHIP_STEP:
                    raise ValueError(
                        f"outcome {self.outcome_id!r} changes {npc_id}.{dimension} by "
                        f"{delta:+.1f}, which exceeds MAX_RELATIONSHIP_STEP "
                        f"({MAX_RELATIONSHIP_STEP:.0f}); the Validator would reject it at "
                        "runtime, so it cannot ship"
                    )
        return self


class OptionCheck(BaseModel):
    """The three-band check an option resolves through (docs/15 §3).

    ``threshold`` is authored; the band comes from the tag unless overridden. Note
    this is *not* the same machinery as an event trigger — see the module docstring.
    """

    npc_id: str
    dimension: str = Field(default="trust")
    threshold: float
    band: float | None = Field(
        default=None,
        description="override for the tag's band. Normally absent: the band is design "
        "language (docs/15 §3), so it belongs to the tag rather than to each option.",
    )

    @model_validator(mode="after")
    def _dimension_must_be_known(self) -> Self:
        if self.dimension not in RELATIONSHIP_DIMENSIONS:
            raise ValueError(
                f"check reads unknown relationship dimension {self.dimension!r}; "
                f"known: {sorted(RELATIONSHIP_DIMENSIONS)}"
            )
        return self

    def effective_band(self, tag: OptionTag) -> float:
        return self.band if self.band is not None else _BAND_BY_TAG[tag.value]


class EventOption(BaseModel):
    """One tagged thing the player can do here (docs/15 §1, §2).

    Options are a *vocabulary*, not a menu: they tell the player that warmth and
    probing are available here, and free text doing the same thing resolves the same
    way (docs/15 §1.1). Without that, typing would be strictly worse than clicking —
    a button has a defined check and prose would not — and everyone would click,
    killing the open-input path docs/03 §5 exists to keep.
    """

    option_id: str
    tag: OptionTag
    text: str = Field(description="what the player says, shown as-is")

    check: OptionCheck | None = Field(
        default=None, description="absent for tags that never roll (docs/15 §2)"
    )
    on_success: str = Field(description="outcome_id when the check passes, or the only outcome")
    on_failure: str | None = Field(
        default=None,
        description="outcome_id when the check fails. Required with a check, since "
        "failing would otherwise land nowhere and stall the event.",
    )

    @model_validator(mode="after")
    def _check_must_match_the_tag(self) -> Self:
        if self.check is not None and not self.tag.is_checked:
            raise ValueError(
                f"option {self.option_id!r} is tagged {self.tag.value!r}, which is never "
                "checked (docs/15 §2: it does not take a check, only an amplitude), yet it "
                "carries one; an option that fails for no visible reason is the frustration "
                "that asymmetry exists to avoid"
            )
        if self.check is not None and self.on_failure is None:
            raise ValueError(
                f"option {self.option_id!r} has a check but no on_failure outcome, so a "
                "failed check would land nowhere"
            )
        return self

    @property
    def outcome_ids(self) -> set[str]:
        return {self.on_success} | ({self.on_failure} if self.on_failure else set())


class EventDefinition(BaseModel):
    """One authored event: antecedent, consequent, branches (docs/13 §2).

    The three things an operator lacked, which is why operators could not schedule
    anything: ``trigger`` is the antecedent, ``outcomes`` the consequent, and several
    outcomes make it branch.

    ``max_exchanges`` and ``default_outcome`` together answer "how does this end"
    without letting the NPC decide (docs/13 §3.1). Running out of patience lands the
    default outcome — usually "this went nowhere" — rather than having her suddenly
    volunteer the truth on line five because a counter tripped. The player would
    notice that, and it would also hand the model a say over consequences.
    """

    event_id: str
    operator: NarrativeOperator = Field(
        description="how this event is voiced. Demoted from scheduling unit to "
        "presentation (docs/13 §2), which is all it was ever able to describe."
    )
    trigger: Condition = Field(
        description="hard condition, evaluated by the existing evaluator. NOT the "
        "three-band check: at 38 a 'trust >= 40' trigger is simply unmet (docs/15 §6.2)."
    )
    npc_id: str | None = Field(
        default=None, description="the NPC this event is with; None for events with no one present"
    )

    outcomes: dict[str, EventOutcome] = Field(min_length=1)
    default_outcome: str = Field(
        description="where the event lands at max_exchanges with nothing else triggered"
    )
    max_exchanges: int = Field(
        gt=0,
        description="patience in player turns; authored per event, since small talk and a "
        "confession warrant different amounts (docs/13 §3.1)",
    )

    scripted_lines: list[str] = Field(
        default_factory=list,
        description="lines the player says unprompted (docs/15 §1). Used where nothing "
        "diverges: it moves the scene without spending the player's decision budget, and "
        "keeps tagged options rare enough to still mean something.",
    )
    options: list[EventOption] = Field(default_factory=list)

    intensity: float = Field(
        default=0.5, ge=0.0, le=1.0, description="dramatic weight, for the Controller's ranking"
    )
    preference_tag: str = Field(
        default=PreferenceTag.SOCIAL.value,
        description="which taste this event serves, for ranking",
    )
    payoff_target: str | None = Field(
        default=None,
        description="for foreshadow events: the fact whose disclosure settles this. Authored "
        "rather than model-written, which is what reconnects the ledger to the clue chain "
        "(docs/13 §9) — a generated payoff condition could point anywhere, and did.",
    )
    constraints: list[str] = Field(
        default_factory=list, description="what may not be said while voicing this event"
    )
    closes_permanently_on_failure: bool = Field(
        default=False,
        description="docs/15 §3.3. Distinct from finishing: closed means it never triggers "
        "again. Only for events with an alternative route to the same information.",
    )
    repeatable: bool = Field(
        default=False,
        description="whether a completed event may trigger again. Default False, which is "
        "the conservative reading of docs/15 §4's '下个时段可重来': until the slot clock "
        "exists, 'again' could only mean 'next turn', and an event re-entered every turn is "
        "the filler loop the event layer replaced. The time system in the next batch turns "
        "this into a per-slot allowance.",
    )

    @model_validator(mode="after")
    def _references_must_resolve(self) -> Self:
        if self.default_outcome not in self.outcomes:
            raise ValueError(
                f"event {self.event_id!r} names default_outcome {self.default_outcome!r}, "
                f"which is not among its outcomes {sorted(self.outcomes)}"
            )

        seen: set[str] = set()
        for option in self.options:
            if option.option_id in seen:
                raise ValueError(
                    f"event {self.event_id!r} has duplicate option id {option.option_id!r}; "
                    "ids must be unique so the chosen option is unambiguous"
                )
            seen.add(option.option_id)
            unknown = sorted(option.outcome_ids - set(self.outcomes))
            if unknown:
                raise ValueError(
                    f"event {self.event_id!r} option {option.option_id!r} points at unknown "
                    f"outcome(s) {unknown}; known: {sorted(self.outcomes)}"
                )

        # docs/15 §1: options whose outcomes do not diverge are not a choice. Stated
        # there as an executable criterion, so it is executed here rather than left
        # as advice — the alternative is an interface that asks the player to decide
        # something the world does not branch on.
        if self.options:
            reachable = {oid for option in self.options for oid in option.outcome_ids}
            if len(reachable) == 1:
                raise ValueError(
                    f"event {self.event_id!r} offers {len(self.options)} options that all lead "
                    f"to the single outcome {reachable.pop()!r}; per docs/15 §1 that position "
                    "should be a scripted line instead"
                )
        return self

    @property
    def has_options(self) -> bool:
        return bool(self.options)

    def option(self, option_id: str) -> EventOption | None:
        return next((o for o in self.options if o.option_id == option_id), None)

    def outcome_for(self, outcome_id: str) -> EventOutcome:
        """The named outcome, falling back to the default.

        Tolerant on purpose: this is called with an id that may have come from a
        model's classification of free text. An unrecognised label means "nothing
        recognisable happened", which is exactly what the default outcome is for.
        """
        return self.outcomes.get(outcome_id) or self.outcomes[self.default_outcome]


class EventScript(BaseModel):
    """A pack's whole event network, indexed by id."""

    events: dict[str, EventDefinition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ids_must_agree(self) -> Self:
        for key, event in self.events.items():
            if key != event.event_id:
                raise ValueError(
                    f"event stored under {key!r} declares event_id {event.event_id!r}; "
                    "the two must agree or lookups will miss"
                )
        return self

    def get(self, event_id: str) -> EventDefinition | None:
        return self.events.get(event_id)

    def fact_ids(self) -> set[str]:
        """Every fact id the script names, for the loader's cross-reference."""
        ids: set[str] = set()
        for event in self.events.values():
            if event.payoff_target:
                ids.add(event.payoff_target)
            for outcome in event.outcomes.values():
                ids.update(outcome.reveals_facts)
        return ids

    def npc_ids(self) -> set[str]:
        ids = {event.npc_id for event in self.events.values() if event.npc_id}
        for event in self.events.values():
            for outcome in event.outcomes.values():
                ids.update(outcome.relationship_changes)
            for option in event.options:
                if option.check is not None:
                    ids.add(option.check.npc_id)
        return ids


def outcome_payload(outcome: EventOutcome) -> dict[str, Any]:
    """Flatten an outcome for a trace entry. Presentation only."""
    return {
        "outcome_id": outcome.outcome_id,
        "summary": outcome.summary,
        "relationship_changes": outcome.relationship_changes,
        "reveals_facts": list(outcome.reveals_facts),
        "sets_flags": list(outcome.sets_flags),
        "stage_advance": outcome.stage_advance,
        "tension_change": outcome.tension_change,
        "closes_event": outcome.closes_event,
    }

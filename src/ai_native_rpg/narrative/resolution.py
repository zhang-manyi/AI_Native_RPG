"""Resolving a player response into an authored outcome, and applying it.

Two steps, deliberately separate:

    resolve_option()   pick which authored outcome this response lands on
    apply_outcome()    submit that outcome's consequences as Action Proposals

The split matters because only the first is allowed to involve a model at all, and
only in a narrow way. docs/13 §3 divides the labour: the trigger, the outcome set, and
which outcome fires are authored, because outcomes move the values gating
``reveal_condition`` and docs/04 §3.3 lets no actor route around that. What the model
may do is *classify* free text as one of the options already on offer — a mapping, not
a decision. If it could invent an outcome it could choose what gets unlocked, and the
whole information-asymmetry mechanism would be advisory.

**Resolution is deterministic except inside the band.** docs/15 §3: the gap between the
current value and the threshold decides the result outright unless it falls within the
tag's band, and only then is there a roll. The player cannot see which band they are in
— they know she is "still wary", not that trust is 33 rather than 41 — so the mechanism
is reproducible while the experience is uncertain. That is what lets the debug panel say
"15 short of the threshold" instead of "unlucky", and what makes failure teachable
rather than arbitrary.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass

from ..schemas.events import (
    CheckBand,
    EventDefinition,
    EventOutcome,
    OptionTag,
    check_band,
    effective_threshold,
    fear_floor_for_failure,
    scaled_trust_gain,
)
from ..schemas.world_state import ActionProposal, ActionValidationResult
from ..world.actions import ActionType
from ..world.conditions import UnknownPathError, evaluate
from ..world.manager import WorldStateManager

#: Actor id outcome effects are submitted under.
#:
#: The event layer is part of the Narrative Engine, so its writes carry the Engine's
#: identity and are gated by ``narrative_actions_are_system_only`` like every other
#: narrative action. Relationship changes are *not* narrative-only, but attributing
#: them here keeps "the event did this" distinguishable in a trace from "the NPC did
#: this during the event".
EVENT_ACTOR = "narrative_engine"


@dataclass(frozen=True)
class OptionResolution:
    """Which outcome a response landed on, and why.

    ``band`` and ``margin`` exist for the panel. docs/07's position is that a trace
    must explain a failure; recording only the outcome id would leave "why did the
    probe fail" answerable solely by re-deriving the arithmetic by hand.
    """

    outcome_id: str
    band: CheckBand
    checked: bool
    option_id: str | None = None
    threshold: float | None = None
    current_value: float | None = None
    #: The tag whose check failed, if one did. Carries the fear floor of docs/14 §4.3 to
    #: ``apply_outcome`` — the outcome id alone cannot say *why* it was reached, and a
    #: default outcome looks identical whether it came from a failed probe or from
    #: running out of patience.
    failed_tag: OptionTag | None = None

    @property
    def margin(self) -> float | None:
        """How far the checked value sat from the threshold, signed."""
        if self.threshold is None or self.current_value is None:
            return None
        return self.current_value - self.threshold


def resolve_option(
    event: EventDefinition,
    option_id: str,
    world,
    *,
    player_id: str,
    rng: random.Random | None = None,
) -> OptionResolution:
    """Resolve one option (or a classified free-text reply) to an outcome id.

    An unrecognised ``option_id`` lands the default outcome rather than raising. That
    is the path a model's classification arrives on, and "this matched nothing we
    wrote" is exactly what the default outcome means (docs/13 §3.1) — not a crash, and
    not a guess at what the player meant.

    ``rng`` is injectable so a trace can be replayed. A fresh ``Random()`` otherwise.
    """
    rng = rng or random.Random()
    option = event.option(option_id)

    if option is not None and not _requirement_met(option.requires, world):
        # Not offered right now, so a classification or a stale click landing on it is
        # the same "this matched nothing available" case an unrecognised id is
        # (docs/15 §7 M7): the default outcome, not a bypass of the gate.
        option = None

    if option is None:
        return OptionResolution(
            outcome_id=event.default_outcome, band=CheckBand.CERTAIN_FAILURE, checked=False
        )

    if option.check is None:
        # Unchecked tags never fail (docs/15 §2). Reported as CERTAIN_SUCCESS rather
        # than a fourth band value: from the panel's side "it landed, no roll" is the
        # same statement, and a separate value would need handling everywhere.
        return OptionResolution(
            outcome_id=option.on_success,
            band=CheckBand.CERTAIN_SUCCESS,
            checked=False,
            option_id=option.option_id,
        )

    check = option.check
    current = _current_value(world, check.npc_id, player_id, check.dimension)
    threshold = effective_threshold(
        check.threshold, tag=option.tag, flags=list(world.story_beats.flags)
    )
    band = check_band(current=current, threshold=threshold, band=check.effective_band(option.tag))

    if band is CheckBand.CERTAIN_SUCCESS:
        succeeded = True
    elif band is CheckBand.CERTAIN_FAILURE:
        succeeded = False
    else:
        succeeded = rng.random() < 0.5

    # on_failure is guaranteed present alongside a check (validated on the option).
    outcome_id = option.on_success if succeeded else option.on_failure
    return OptionResolution(
        outcome_id=str(outcome_id),
        band=band,
        checked=True,
        option_id=option.option_id,
        threshold=threshold,
        current_value=current,
        # Recorded on failure only. Pressing her and getting somewhere is not what
        # frightens her; pressing her and missing is (docs/14 §4.3).
        failed_tag=None if succeeded else option.tag,
    )


def resolve_out_of_patience(event: EventDefinition) -> OptionResolution:
    """Where an event lands when its exchange budget runs out (docs/13 §3.1).

    Always the authored default outcome. The alternative — letting the NPC push the
    plot along at the limit — would hand a model the choice of consequences, and a
    player would spot it: she suddenly volunteers the truth on line five because a
    counter tripped.
    """
    return OptionResolution(
        outcome_id=event.default_outcome, band=CheckBand.CERTAIN_FAILURE, checked=False
    )


def _requirement_met(requires, world) -> bool:
    """Whether an option's ``requires`` gate is satisfied. ``None`` always is.

    A malformed condition reads as unmet, matching every other gate in this codebase
    (``_trigger_holds``, ``_is_unlockable``): the loader already rejects an unresolvable
    path at startup, so a broken clause in play should hide the option, not crash the turn.
    """
    if requires is None:
        return True
    try:
        return evaluate(requires, world)
    except (UnknownPathError, TypeError, ValueError):
        return False


def _current_value(world, npc_id: str, player_id: str, dimension: str) -> float:
    """What the check reads: this NPC's feeling toward this player.

    A missing relationship reads as 0 rather than raising. Absent pairs default to
    all-zero in the world schema too, so "no relationship yet" and "trust 0" are the
    same state — and a check against a stranger should evaluate, not crash.
    """
    state = world.relationships.get(npc_id, {}).get(player_id)
    return float(getattr(state, dimension)) if state is not None else 0.0


def apply_outcome(
    manager: WorldStateManager,
    *,
    event: EventDefinition,
    outcome: EventOutcome,
    player_id: str,
    failed_tag: OptionTag | None = None,
) -> list[ActionValidationResult]:
    """Submit an outcome's consequences, each as its own proposal.

    Every effect goes through the Validator; nothing is written directly. That is
    docs/04's single write path, and it is what keeps the authored numbers honest —
    ``MAX_RELATIONSHIP_STEP`` is enforced here at runtime even though the event schema
    already rejects over-large deltas at load.

    A revealed fact is still subject to its ``reveal_condition``: an outcome names what
    to voice, it does not grant permission to voice it. So an outcome may legitimately
    be applied and have its reveal rejected — the clue is one the player has not earned
    yet, and the rejection is the correct answer rather than an error to work around.

    ``failed_tag`` names the tag whose check just failed, if one did, and adds the fear
    floor of docs/14 §4.3 when the outcome does not price the failure itself. See
    ``fear_floor_for_failure``: without it, "玛尔塔彻底闭口" is gated on a value nothing
    reliably moves.
    """
    results: list[ActionValidationResult] = []
    floor = fear_floor_for_failure(failed_tag) if failed_tag is not None else 0.0

    for npc_id, changes in outcome.relationship_changes.items():
        payload = dict(changes)
        if outcome.scales_with_current_trust:
            payload["trust"] = scaled_trust_gain(manager.get_trust(npc_id, player_id))
        # Authored values win: an outcome that names a fear change has had this beat
        # thought about, and adding a floor on top would inflate every number docs/15 §4
        # lists. The floor exists for the outcomes that say nothing.
        if floor and npc_id == event.npc_id and "fear" not in payload:
            payload["fear"] = floor
        results.append(
            _submit(
                manager,
                ActionType.ADJUST_RELATIONSHIP,
                actor_id=npc_id,
                target_id=player_id,
                payload=payload,
            )
        )

    # A failed check whose outcome moves nothing at all still costs her something. This
    # is the common case in practice — the default outcome of most events is "这次没谈成"
    # with no deltas — and it is exactly where fear used to fail to accumulate.
    if floor and event.npc_id is not None and event.npc_id not in outcome.relationship_changes:
        results.append(
            _submit(
                manager,
                ActionType.ADJUST_RELATIONSHIP,
                actor_id=event.npc_id,
                target_id=player_id,
                payload={"fear": floor},
            )
        )

    # An outcome may scale trust without naming the NPC in relationship_changes at all
    # — "she heard you" is one beat whose worth depends on her current wariness, and
    # spelling out a zero delta just to carry the flag would read as a change nobody
    # intended.
    if outcome.scales_with_current_trust and not outcome.relationship_changes:
        npc_id = event.npc_id
        if npc_id is not None:
            results.append(
                _submit(
                    manager,
                    ActionType.ADJUST_RELATIONSHIP,
                    actor_id=npc_id,
                    target_id=player_id,
                    payload={"trust": scaled_trust_gain(manager.get_trust(npc_id, player_id))},
                )
            )

    beat_payload: dict[str, object] = {}
    if outcome.tension_change:
        current = manager.snapshot().story_beats.tension
        beat_payload["tension"] = round(min(1.0, max(0.0, current + outcome.tension_change)), 4)
    if outcome.sets_flags:
        beat_payload["raise_flags"] = list(outcome.sets_flags)
    if beat_payload:
        results.append(_submit(manager, ActionType.ADVANCE_STORY_BEAT, payload=beat_payload))

    # A decision may unlock its evidence. Apply its flags before checking each reveal.
    for fact_id in outcome.reveals_facts:
        results.append(_submit(manager, ActionType.REVEAL_FACT, target_id=fact_id))

    return results


def _submit(
    manager: WorldStateManager,
    action_type: ActionType,
    *,
    actor_id: str = EVENT_ACTOR,
    target_id: str | None = None,
    payload: dict | None = None,
) -> ActionValidationResult:
    return manager.submit(
        ActionProposal(
            proposal_id=uuid.uuid4().hex,
            actor_id=actor_id,
            action_type=action_type.value,
            target_id=target_id,
            payload=payload or {},
        )
    )

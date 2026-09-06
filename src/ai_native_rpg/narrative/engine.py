"""Narrative Engine: schedule structure deterministically, generate content with an LLM.

One ``tick`` is the whole of docs/05 §3:

    check_triggers()  ->  select_candidate()  ->  generate_content()  ->  proposals
    [rules]               [controller]           [LLM, only if selected]  [Validator]

Three properties this module exists to hold:

**The turn always closes.** Every tick submits ``advance_turn``, recording
``relieve`` when nothing fired. The pacing rules reason about adjacency, so a turn
that leaves no trace would make a beat five turns back look like it just happened.

**Generation is off the critical path.** A tick runs *after* a player turn and its
content is consumed by the *next* one (``pending_event``). docs/02 §4 budgets
narrative generation as event-triggered and asynchronous; calling it inline would
turn a 1-2 call interaction into three, past the §5 latency budget.

**The Engine cannot write state, or widen visibility.** Effects travel as ordinary
Action Proposals (docs/10 §2.1), and none of them bypasses ``reveal_condition``: a
``reveal`` only voices what the condition table already unlocked, and ``escalate``
moves ``tension`` while the scenario's conditions decide what that unlocks
(docs/04 §3.3). The Engine therefore never knows what it revealed.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..agent.harness import PromptLibrary
from ..llm.base import LLMClient, Message
from ..scenario import Ending, NarrativeDirectives
from ..schemas.common import Condition
from ..schemas.events import CheckBand, EventDefinition, EventOption, EventScript
from ..schemas.narrative import (
    EventCandidate,
    NarrativeEvent,
    NarrativeOperator,
    PlayerProfile,
    TimeSlot,
)
from ..schemas.world_state import ActionProposal, ActionValidationResult
from ..world.actions import FORESHADOW_PAYOFF_PATH_PREFIXES, ActionType
from ..world.conditions import UnknownPathError, evaluate
from ..world.manager import WorldStateManager
from .controller import PacingVerdict, Rejection, select_candidate
from .player_actions import (
    PlayerActionResult,
    advance_past_wrap_up,
    conclude_case,
    end_conversation,
    move_player,
)
from .resolution import apply_outcome, resolve_option, resolve_out_of_patience
from .rules import (
    check_terminal_ending,
    check_triggers,
    earned_stage,
    milestone_flag,
    newly_reached_milestones,
    progress_quest,
)
from .wrap_up import ClueReview, TarotReading, review_clues, tarot_reading

#: The actor id every narrative proposal carries. In ``SYSTEM_ACTORS``, and gated
#: by ``narrative_actions_are_system_only``.
ENGINE_ACTOR = "narrative_engine"

#: How much one ``escalate`` adds to tension. Small on purpose: tension is a
#: condition channel, and a large step would clear several author thresholds at
#: once — the reason MAX_CHAPTER_STEP exists, applied to the continuous axis.
TENSION_STEP = 0.2


class GeneratedContent(BaseModel):
    """The generator's output contract (docs/05 §2.3).

    Structured rather than free text because the consumer is another prompt, not a
    screen: ``dialogue_hook`` is handed to the NPC's Dialogue Generation stage as
    material, not shown to the player (docs/06 §3).

    The three ``planted_*`` fields apply only to ``foreshadow``; the alternative —
    a separate schema per operator — would mean a second request shape and a second
    parse path for one extra field.
    """

    summary: str = Field(
        description="one short line on what happened, for developers; 30 characters or so"
    )
    dialogue_hook: str = Field(
        description="one line the NPC can work from, 40 characters or so; not a conclusion"
    )
    participants: list[str] = Field(
        default_factory=list, description="display names of the characters involved, usually one"
    )

    planted_fact_id: str | None = None
    planted_value: str | None = Field(default=None, description="the detail itself, one short line")
    payoff_condition: Condition | None = Field(
        default=None, description="a single clause is enough; no explanation of the threshold"
    )


class EventResolutionRecord(BaseModel):
    """What one player response did inside an event, for the panel and for Eval.

    ``band``, ``threshold`` and ``current_value`` are recorded because docs/07 asks the
    panel to explain a failure rather than display one. "15 below a threshold of 25"
    explains; "the probe failed" leaves the player and the developer with the same
    guess, and the whole reason the check is three-band rather than a dice roll is that
    failure should be legible after the fact (docs/15 §3).
    """

    event_id: str
    outcome_id: str | None = Field(
        default=None, description="None while the event continues without a recognised option"
    )
    option_id: str | None = None
    band: str | None = None
    threshold: float | None = None
    current_value: float | None = None

    exchanges: int = 0
    finished: bool = False
    closed: bool = False
    proposals: list[ActionValidationResult] = Field(default_factory=list)

    @property
    def margin(self) -> float | None:
        """How far the checked value sat from the threshold, signed.

        Mirrors ``OptionResolution.margin`` so a caller holding only the record can
        still say "15 short" rather than re-deriving it. Absent for unchecked options,
        where there is no threshold to be short of.
        """
        if self.threshold is None or self.current_value is None:
            return None
        return self.current_value - self.threshold


@dataclass(frozen=True)
class DayWrapUp:
    """The day's interlude: what is known, and how the cards read (docs/13 §4.2).

    A dataclass rather than a Pydantic model because both halves already are what they
    are and nothing here is parsed from outside. Grouped into one object so a caller
    cannot show the reading while forgetting the review — they are one beat.
    """

    day: int
    review: ClueReview
    reading: TarotReading


class NarrativeTick(BaseModel):
    """A full record of one narrative decision, for the panel and for Eval.

    Mirrors ``AgentTrace``'s role for NPC turns. The rejected candidates matter as
    much as the selected one: "the Engine had something and pacing held it back" is
    invisible otherwise, and indistinguishable from no rule having fired
    (docs/07 §2.3).
    """

    tick_id: str
    turn: int
    player_id: str

    candidates: list[EventCandidate] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(
        default_factory=list, description="{operator, event_type, reason} per blocked candidate"
    )
    selected: EventCandidate | None = None
    starved: bool = False

    event: NarrativeEvent | None = None
    proposals: list[ActionValidationResult] = Field(default_factory=list)

    latency_ms: float = 0.0
    model_used: str | None = Field(default=None, description="None on a quiet turn: no call made")
    token_usage: dict[str, int] | None = None


class NarrativeEngine:
    """Runs one narrative decision per player turn.

    Dependencies are injected for the same reason the Harness's are: the whole loop
    has to be exercisable against ``MockLLMClient`` and an in-memory world.
    """

    def __init__(
        self,
        *,
        manager: WorldStateManager,
        llm: LLMClient,
        prompts: PromptLibrary | None = None,
        directives: NarrativeDirectives | None = None,
        script: EventScript | None = None,
        endings: list[Ending] | None = None,
    ) -> None:
        self._manager = manager
        self._llm = llm
        self._prompts = prompts or PromptLibrary()
        # The pack's authored narrative content. Absent means "pace nothing", not
        # "fall back to some scenario's clues" — see rules.check_triggers.
        self._directives = directives or NarrativeDirectives()
        # The pack's event network. Absent means quiet turns: docs/13 §2.1 removed the
        # operator-level fallback, so with no event to hand nothing happens rather than
        # a filler beat being invented to occupy the slot.
        self._script = script or EventScript()
        # The pack's declared endings. Absent means no case ever ends — the same "no
        # content, no behaviour" stance as an empty script, not a framework-level default
        # ending that would apply to every story regardless of what it actually declares.
        self._endings = endings or []
        self._pending_event: dict[str, Any] | None = None

    @property
    def script(self) -> EventScript:
        """The authored event network this engine runs."""
        return self._script

    def active_event_options(self) -> list[dict[str, str]]:
        """The active event's options as ``{id, text}``, for the NPC call to classify against.

        Ids and player-facing text only. The outcomes are deliberately withheld: a model
        shown what each option leads to would be tempted to pick by preferred consequence
        rather than by what the player said, and consequences are authored (docs/13 §3).

        Filtered by ``requires`` (docs/15 §7 M7): an option gated on state the player has
        not earned is not offered to the classifier either, so free text cannot land on a
        choice the buttons do not show.
        """
        return [{"id": o.option_id, "text": o.text} for o in self.visible_event_options()]

    def visible_event_options(self) -> list[EventOption]:
        """The active event's options whose ``requires`` (if any) currently holds.

        The single filter both ``active_event_options`` (for the classifier) and the Web
        layer's ``SceneOption`` list read, so a gated option cannot be offered to one and
        hidden from the other — docs/15 §7 M7's whole point is that an option a player has
        not earned must not appear as a button that does nothing.
        """
        definition = self.active_event()
        if definition is None:
            return []
        world = self._manager.snapshot()
        return [o for o in definition.options if self._option_available(o, world)]

    def _option_available(self, option: EventOption, world) -> bool:
        """Whether ``option.requires`` (if any) currently holds.

        A malformed condition reads as unmet, matching every other gate in this module:
        the loader already rejects an unresolvable path at startup.
        """
        if option.requires is None:
            return True
        try:
            return evaluate(option.requires, world)
        except (UnknownPathError, TypeError, ValueError):
            return False

    def active_event(self) -> EventDefinition | None:
        """The event definition currently in progress, if any.

        Read from the script by the id the world holds, rather than cached: the world
        is the single source of truth for *which* event is running (docs/04), and the
        script is the source of truth for what that event is.
        """
        active = self._manager.snapshot().story_beats.active_event
        return self._script.get(active.event_id) if active else None

    def reached_ending(self) -> Ending | None:
        """The terminal ending the case has landed on, or ``None`` while still open.

        Looks up ``story_beats.ended_at`` against the pack's own list rather than
        against a cached copy, for the same reason ``active_event`` reads the script by
        id: the world says *which* ending, the pack says what it *is*.
        """
        ended_at = self._manager.snapshot().story_beats.ended_at
        if ended_at is None:
            return None
        return next((e for e in self._endings if e.ending_id == ended_at), None)

    # --- player actions (docs/13 §12) ---------------------------------------

    def move_player(self, *, player_id: str, destination: str) -> PlayerActionResult:
        """Take the player somewhere, spending a slot.

        Exposed here so a caller has one object to drive a turn through, but the work is
        in ``player_actions`` and goes through the Validator like everything else — the
        Engine is not a second write path (docs/04).
        """
        result = move_player(self._manager, player_id=player_id, destination=destination)
        if result.approved:
            self._check_endings()
        return result

    def end_conversation(self) -> PlayerActionResult:
        """Close the active event without landing an outcome (docs/13 §12).

        Walking out is not the same act as running out of patience, so no outcome is
        applied: a player who could collect an outcome's numbers by leaving would have a
        free move.
        """
        result = end_conversation(self._manager)
        self._check_endings()
        return result

    def conclude_case(self) -> PlayerActionResult:
        """Open the conclusion event on the player's own initiative (docs/13 §12, docs/15 §4 M7).

        Reads which event is "the conclusion" from ``directives.conclusion_event`` rather
        than a hardcoded id, for the reason ``progress_quest`` is named the same way: that
        binding is the pack's business. A pack that has not written one yet refuses rather
        than guessing, which is the same "no content, no behaviour" stance ``check_triggers``
        takes when a pack ships no script at all.
        """
        event_id = self._directives.conclusion_event
        if event_id is None:
            return PlayerActionResult(
                approved=False, reason="this pack declares no conclusion event"
            )
        definition = self._script.get(event_id)
        if definition is None:
            return PlayerActionResult(
                approved=False, reason=f"conclusion event {event_id!r} is not in the script"
            )
        result = conclude_case(
            self._manager, event_id=event_id, max_exchanges=definition.max_exchanges
        )
        self._check_endings()
        return result

    # --- the wrap-up (docs/13 §4.2) -----------------------------------------

    def is_wrapping_up(self) -> bool:
        """Whether the clock is at the day's interlude."""
        return self._manager.snapshot().story_beats.time_slot is TimeSlot.WRAP_UP

    def wrap_up(self, *, player_id: str) -> DayWrapUp | None:
        """The day's review and reading, or ``None`` outside the wrap-up.

        Returning ``None`` rather than computing it anyway keeps the once-a-day cadence
        docs/13 §4.2 asks for in one place: the ritual is what makes it feel like a ritual,
        and a review available on demand every turn is just a screen.

        The review is handed only the player's projection, so it *cannot* read a hidden
        fact (docs/13 §5.1). The reading gets the world, but reads counts rather than
        content — how much is still dark, never what it is.
        """
        world = self._manager.snapshot()
        if world.story_beats.time_slot is not TimeSlot.WRAP_UP:
            return None

        view = self._manager.player_view(player_id)
        return DayWrapUp(
            day=world.time_day,
            review=review_clues(view),
            reading=tarot_reading(world, view),
        )

    def close_out_day(self) -> PlayerActionResult:
        """Step past the wrap-up into the next morning.

        Separate from ``wrap_up`` so that reading the interlude and dismissing it are
        distinct: a caller that computed the review as a side effect of advancing the
        clock could never show it.

        Checked for endings unconditionally, approved or not: this is the one place
        ``time_day`` moves (docs/13 §4.2), and ``never_found_out`` (docs/14 §4.3) is
        gated on it alone — a check that ran only on approval would miss the day the
        deadline was actually crossed if the call somehow failed to advance it.
        """
        result = advance_past_wrap_up(self._manager)
        self._check_endings()
        return result

    # --- pending event ------------------------------------------------------

    @property
    def pending_event(self) -> dict[str, Any] | None:
        """Content waiting to be woven into the next NPC turn, if any."""
        return self._pending_event

    def take_pending_event(self) -> dict[str, Any] | None:
        """Hand over the pending content and clear it.

        Consumed once: a hook re-offered every turn would read as an NPC stuck on
        one line.
        """
        event, self._pending_event = self._pending_event, None
        return event

    # --- the tick ----------------------------------------------------------

    def tick(self, *, player_id: str, profile: PlayerProfile | None = None) -> NarrativeTick:
        """Decide and apply this turn's beat. Returns the record of what happened."""
        started = time.perf_counter()
        world = self._manager.snapshot()
        turn = world.story_beats.turn

        candidates = check_triggers(
            world, player_id=player_id, script=self._script, directives=self._directives
        )
        verdict = select_candidate(candidates, beats=world.story_beats, profile=profile)

        event: NarrativeEvent | None = None
        model_used: str | None = None
        token_usage: dict[str, int] | None = None

        if verdict.selected is not None:
            event, model_used, token_usage = self._generate(verdict.selected, world, player_id)

        results, landed = self._apply_effects(verdict, event, player_id)

        # Only a beat that landed may speak. Generation happens before the
        # Validator has ruled, so content exists for beats that turn out not to
        # have happened; handing that to the next turn would have an NPC allude to
        # a detail no one planted — and for a foreshadow, one with no ledger entry
        # and therefore no payoff ever coming. Same condition that decides whether
        # the operator is recorded, because it is the same question.
        if event is not None and landed:
            self._pending_event = event.generated_content

        return NarrativeTick(
            tick_id=uuid.uuid4().hex,
            turn=turn,
            player_id=player_id,
            candidates=candidates,
            rejected=[_render_rejection(r) for r in verdict.rejected],
            selected=verdict.selected,
            starved=verdict.starved,
            event=event,
            proposals=results,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            model_used=model_used,
            token_usage=token_usage,
        )

    # --- player response ---------------------------------------------------

    def resolve_player_response(
        self,
        *,
        player_id: str,
        option_id: str | None,
        rng: random.Random | None = None,
    ) -> EventResolutionRecord | None:
        """Land the player's response on one authored outcome, and apply it.

        ``option_id`` is either the option the player clicked or the label a model
        assigned to their free text. The two travel the same path on purpose
        (docs/15 §1.1): if typing had no defined check while clicking did, typing would
        be strictly worse and everyone would click, which kills the open-input path
        docs/03 §5 exists to protect. It also makes the options a *vocabulary* — here
        is what you can do — rather than a menu.

        Returns ``None`` when no event is in progress; a reply outside an event is just
        conversation, and the NPC handles it.

        The event ends when its outcome closes it or its patience runs out, and running
        out lands the authored default rather than asking the NPC to advance the plot
        (docs/13 §3.1).
        """
        world = self._manager.snapshot()
        active = world.story_beats.active_event
        if active is None:
            return None

        definition = self._script.get(active.event_id)
        if definition is None:
            # The world names an event this script does not have — a pack swapped under
            # a save. Close it rather than wedging every later turn on a lookup that
            # cannot succeed.
            self._submit(ActionType.ADVANCE_STORY_BEAT, payload={"finish_event": True})
            return None

        self._submit(ActionType.ADVANCE_STORY_BEAT, payload={"record_exchange": True})
        world = self._manager.snapshot()

        resolution = (
            resolve_option(definition, option_id, world, player_id=player_id, rng=rng)
            if option_id
            else None
        )

        # No recognised option and patience left: the scene simply continues. Falling to
        # the default here instead would end an event on the player's first ordinary
        # remark, which is the "conversation over in one line" failure inverted.
        out_of_patience = (
            world.story_beats.active_event is not None
            and world.story_beats.active_event.is_out_of_patience
        )
        if resolution is None and not out_of_patience:
            return EventResolutionRecord(
                event_id=definition.event_id,
                outcome_id=None,
                exchanges=world.story_beats.active_event.exchanges
                if world.story_beats.active_event
                else 0,
                finished=False,
            )

        if resolution is None:
            resolution = resolve_out_of_patience(definition)

        outcome = definition.outcome_for(resolution.outcome_id)
        results = apply_outcome(
            self._manager,
            event=definition,
            outcome=outcome,
            player_id=player_id,
            failed_tag=resolution.failed_tag,
        )
        results.extend(self._sync_progress_stage())
        results.extend(self._check_endings())

        if outcome.stage_advance:
            quest = progress_quest(self._manager.snapshot(), self._directives)
            if quest is not None:
                results.append(self._submit(ActionType.ADVANCE_QUEST, target_id=quest.quest_id))

        # An outcome that gives the player something without moving the scene on leaves
        # the event open — [观察] in M3 is the authored case (docs/15 §4).
        finished = outcome.advances_conversation or out_of_patience
        if finished:
            close = outcome.closes_event or (
                definition.closes_permanently_on_failure
                and resolution.band is CheckBand.CERTAIN_FAILURE
            )
            payload: dict[str, Any] = {"finish_event": True}
            if close:
                payload["close_event"] = True
            results.append(self._submit(ActionType.ADVANCE_STORY_BEAT, payload=payload))

        beats = self._manager.snapshot().story_beats
        return EventResolutionRecord(
            event_id=definition.event_id,
            outcome_id=outcome.outcome_id,
            option_id=resolution.option_id,
            band=resolution.band.value,
            threshold=resolution.threshold,
            current_value=resolution.current_value,
            exchanges=beats.active_event.exchanges if beats.active_event else active.exchanges + 1,
            finished=finished,
            closed=beats.is_closed(definition.event_id),
            proposals=results,
        )

    # --- generation --------------------------------------------------------

    def _generate(
        self, candidate: EventCandidate, world, player_id: str
    ) -> tuple[NarrativeEvent, str | None, dict[str, int] | None]:
        messages = self._generation_messages(candidate, world, player_id)
        resp = self._llm.complete(messages, schema=GeneratedContent)
        content: GeneratedContent = resp.parsed  # type: ignore[assignment]

        event = NarrativeEvent(
            event_id=uuid.uuid4().hex,
            operator=candidate.operator,
            event_type=candidate.event_type,
            participants=list(content.participants),
            generated_content=content.model_dump(exclude_none=True),
            constraints=list(candidate.constraints),
        )
        return event, resp.model, resp.token_usage

    def _generation_messages(
        self, candidate: EventCandidate, world, player_id: str
    ) -> list[Message]:
        """Assemble the generator's prompt.

        Only *visible* facts go in. An undisclosed value in this prompt would be
        one instruction away from appearing in the output, which is the failure
        ``tools.py`` avoids by keeping secrets out of context rather than asking the
        model to keep them.

        The cast and the player's location are stated because omitting them was
        answered with invention: a run produced a village miller speaking to the
        player, when the pack has two NPCs and the player was inside one of their
        houses. A model cannot honour a boundary nobody described. The Validator
        rejects such content too, but a prompt that never provokes it is cheaper than
        a call spent being refused.
        """
        system = self._prompts.load("narrative_generate.txt")
        view = self._manager.player_view(player_id)

        lines = [
            f"本回合的算子：{candidate.operator.value}",
            f"戏剧功能对象：{candidate.event_type}",
            f"强度：{candidate.intensity:.1f}",
            f"触发原因：{candidate.trigger_reason}",
        ]
        if candidate.pays_off:
            lines.append(f"这次是在回收之前埋下的伏笔：{candidate.pays_off}")
        lines += [
            "",
            f"当前章节：{world.story_beats.chapter}，张力：{world.story_beats.tension:.2f}，"
            f"回合：{world.story_beats.turn}",
        ]

        if self._directives.language:
            lines += ["", f"用{self._directives.language}写。"]

        lines += ["", *self._cast_and_place_lines(world, player_id), "", "玩家已经知道的事："]
        lines.extend(f"  - {value}" for value in view.visible_facts.values())
        if not view.visible_facts:
            lines.append("  - （还什么都不知道）")

        lines += ["", "本场禁止（绝对约束）："]
        lines.extend(f"  - {c}" for c in candidate.constraints)

        if candidate.operator is NarrativeOperator.FORESHADOW:
            lines += self._foreshadow_guidance(world, player_id)

        return [
            Message(role="system", content=system),
            Message(role="user", content="\n".join(lines)),
        ]

    def _foreshadow_guidance(self, world, player_id: str) -> list[str]:
        """What a plant may hinge on, and what is already hanging.

        The open ledger is here because without it every plant was a fresh
        invention. One run, with the player inside Marta's house, planted a scrap of
        red cloth on the mill wheel, then a drag mark by the millrace, then wax on
        her windowsill — three unrelated objects in three different places, none
        picking the previous one up. The generator was told the count of open loops
        ("1/3 loops open") and never their content, so "another hint toward the same
        conclusion" was not something it could aim at. Naming them turns the second
        plant into a second angle on the first, which is what the Three Clue Rule
        behind ``MAX_OPEN_FORESHADOWINGS`` actually asks for.

        The example paths are built from this world rather than written in. They used
        to read ``relationships.npc_a.player_1.trust 或 quests.investigation.stage``,
        which is one story's ids in framework code — wrong for any other pack, and
        wrong here the moment the pack renames a quest.
        """
        allowed = ", ".join(f"{p}*" for p in FORESHADOW_PAYOFF_PATH_PREFIXES)
        lines = ["", f"payoff_condition 的 path 只能用这几类：{allowed}"]

        examples = []
        npc_id = next(iter(world.npcs), None)
        if npc_id:
            examples.append(f"relationships.{npc_id}.{player_id}.trust")
        quest = progress_quest(world, self._directives)
        if quest is not None:
            examples.append(f"quests.{quest.quest_id}.stage")
        if examples:
            lines.append(f"（例如 {' 或 '.join(examples)}）")

        open_loops = world.story_beats.open_foreshadowings
        if open_loops:
            lines += ["", "已经埋下、还没回收的细节："]
            lines.extend(f"  - {entry.note or entry.fact_id}" for entry in open_loops.values())
            lines.append(
                "这一条要和上面某一条指向同一个结论——是同一件事的另一个侧面，"
                "不是又一件不相干的东西。不要重复已经埋过的细节。"
            )
        return lines

    def _cast_and_place_lines(self, world, player_id: str) -> list[str]:
        """Who may appear, and where the player is standing.

        Names, not ids. Two reasons, and the second one is why this matters:

        * ids leak into prose — a model handed ``npc_a`` will occasionally write it;
        * a fact's *value* can be an id. In the reference scenario
          a fact's value may be nothing but an NPC id, in which case listing the cast
          by id puts an undisclosed fact's value in the prompt as a side effect. Which
          characters exist is public; what one of them did that night is not, and only
          the second is a secret. Names keep the two apart.

        The reference pack no longer has an id-valued secret (``loren_that_night`` is a
        sentence now), but the fixture in ``tests/conftest.py`` keeps one deliberately —
        the guarantee is about the shape of the leak, not about one pack's wording.

        Living NPCs only: a dead one is still in ``world.npcs`` and would otherwise
        read as available. The player's location is stated because a beat happens
        somewhere, and the one place it cannot happen is somewhere the player is not.
        """
        cast = [npc for npc in world.npcs.values() if npc.alive]
        here = world.player_locations.get(player_id, "")
        location = world.locations.get(here)
        place = location.name if location is not None else here

        names = [npc.display_name for npc in cast]
        lines = [f"可以出场的角色，只有这些：{'、'.join(names) if names else '（没有）'}"]
        if place:
            lines.append(f"玩家现在在：{place}")
            present = [npc.display_name for npc in cast if npc.location == here]
            lines.append(f"在场的角色：{'、'.join(present) if present else '（只有玩家）'}")
        lines.append("不要发明新角色，也不要让不在场的角色当面说话。")
        return lines

    # --- effects -----------------------------------------------------------

    def _apply_effects(
        self, verdict: PacingVerdict, event: NarrativeEvent | None, player_id: str
    ) -> tuple[list[ActionValidationResult], bool]:
        """Land the beat's effects, then close the turn.

        Returns the proposal results and whether the beat actually landed. The
        caller needs that second value: it decides both whether the operator is
        recorded and whether the generated content may be spoken, and those two
        must agree — a turn recorded as ``relieve`` whose hook still ships is a turn
        the NPC narrates and the world denies.

        Ordering matters: effects first, ``advance_turn`` last. The turn counter is
        what ledger entries are stamped with, so a foreshadowing planted this turn
        must be stamped with the turn it happened in, not the next one.
        """
        results: list[ActionValidationResult] = []
        candidate = verdict.selected

        if candidate is not None:
            results.extend(self._effect_proposals(candidate, event))

        operator = candidate.operator if candidate is not None else NarrativeOperator.RELIEVE
        # A beat whose effects were all rejected did not happen; recording it would
        # start a cooldown for a turn where nothing changed.
        landed = candidate is not None and (not results or any(r.approved for r in results))
        if not landed:
            operator = NarrativeOperator.RELIEVE

        # After the beat, before the turn closes: a reveal that just landed makes the
        # player one clue closer, and the stage should say so while this turn is still
        # the current one.
        results.extend(self._sync_progress_stage())
        results.extend(self._check_endings())

        results.append(self._submit(ActionType.ADVANCE_TURN, payload={"operator": operator.value}))
        return results, landed

    def _sync_progress_stage(self) -> list[ActionValidationResult]:
        """Advance the progress quest toward the stage the player has earned.

        This is the writer ``quests.<progress>.stage`` never had. docs/10 §3.2 makes
        the stage the "逼近答案的程度" input to tension and docs/04 §3.3 explicitly
        permits the Engine to advance it, but no code did — so a real run held three
        foreshadowings due at ``stage >= 2`` while the stage stayed 0 and ``escalate``
        (gated on ``stage >= 1``) never fired. The channel was authored on all sides
        and connected on none.

        One step per tick, even when several clues were told at once. ``advance_quest``
        adds exactly one stage, and stepping repeatedly here would clear several author
        thresholds within a single turn — the same objection ``MAX_CHAPTER_STEP`` exists
        for. Falling behind is self-correcting: the next tick advances again.

        Not part of the beat, so its result is appended but never counted toward
        ``landed``. A quiet turn that happens to close a stage gap is still a quiet
        turn; letting this approval mark the turn as eventful would start a cooldown
        for a beat that never ran and hand the next turn a hook nobody planted.
        """
        world = self._manager.snapshot()
        quest = progress_quest(world, self._directives)
        if quest is None or quest.stage >= earned_stage(world, self._directives):
            return []
        return [self._submit(ActionType.ADVANCE_QUEST, target_id=quest.quest_id)]

    def _check_endings(self) -> list[ActionValidationResult]:
        """Record a terminal ending or a newly-reached milestone, if either is now true.

        Called from every place the world might have just crossed a declared threshold
        (a tick, a resolved event response, a move, closing out a day) rather than only
        from ``tick``: ``never_found_out`` (docs/14 §4.3) needs no event at all —
        ``time_day`` alone crosses it at a wrap-up — so a check wired only into event
        resolution would never see that ending happen.

        Milestones are checked even after a terminal ending is recorded (``ended_at``
        does not short-circuit this), because a terminal ending closing the case must
        not silently swallow a milestone flag that would otherwise never be set — a
        later reader asking "did she ever clam up" deserves an answer independent of
        how the case ended.
        """
        if not self._endings:
            return []
        world = self._manager.snapshot()
        results: list[ActionValidationResult] = []

        if world.story_beats.ended_at is None:
            ending_id = check_terminal_ending(self._endings, world)
            if ending_id is not None:
                results.append(
                    self._submit(ActionType.ADVANCE_STORY_BEAT, payload={"end_case": ending_id})
                )

        for milestone_id in newly_reached_milestones(self._endings, world):
            results.append(
                self._submit(
                    ActionType.ADVANCE_STORY_BEAT,
                    payload={"raise_flags": [milestone_flag(milestone_id)]},
                )
            )
        return results

    def _effect_proposals(
        self, candidate: EventCandidate, event: NarrativeEvent | None
    ) -> list[ActionValidationResult]:
        """Start the selected event. Its outcomes land later, when the player answers.

        This is the shape change the event layer brings. An operator's whole effect
        used to happen at selection time, because a rhetorical move has no other moment
        — there was nothing to wait for. An event does: it opens, the player responds,
        and the response selects which authored outcome fires (docs/13 §3). So the tick
        opens the event and the resolution path applies consequences.

        A ``foreshadow`` event still registers its ledger entry now. What it plants is
        the *telling*, and the debt is owed from the moment it is spoken; waiting for a
        response would mean an event with no options (F1 has none) never opened a loop
        at all.
        """
        definition = self._script.get(candidate.event_id or "")
        if definition is None:
            return []

        results: list[ActionValidationResult] = []

        # The plant goes first, and a failed plant stops the event opening at all. For a
        # foreshadow event the ledger entry is not a side effect but the substance: an
        # event that got voiced without one has the NPC allude to a detail that owes the
        # player a resolution nothing will ever deliver. Better to leave the turn quiet
        # and let the rejection show in the panel (docs/07 §2.3).
        if definition.operator is NarrativeOperator.FORESHADOW:
            results.extend(self._plant_effects(definition, event))
            if results and not any(r.approved for r in results):
                return results

        results.append(
            self._submit(
                ActionType.ADVANCE_STORY_BEAT,
                payload={
                    "open_event": definition.event_id,
                    "max_exchanges": definition.max_exchanges,
                },
            )
        )
        return results

    def _plant_effects(
        self, definition: EventDefinition, event: NarrativeEvent | None
    ) -> list[ActionValidationResult]:
        """Register the ledger entry a foreshadow event owes.

        The payoff condition is the *target fact's own* reveal condition, not something
        the model wrote. That is the correction of docs/13 §9: a generated condition let
        the model decide both what to bury and when it counted as recovered, so what it
        buried led nowhere. Reusing the target's condition means the loop comes due
        exactly when the truth it points at becomes knowable.
        """
        target_id = definition.payoff_target
        if target_id is None:
            return []

        world = self._manager.snapshot()
        if target_id in world.story_beats.open_foreshadowings:
            return []

        target = world.facts.get(target_id)
        if target is None or target.reveal_condition is None:
            return [
                ActionValidationResult(
                    proposal_id=uuid.uuid4().hex,
                    approved=False,
                    reason=f"event {definition.event_id!r} plants toward {target_id!r}, which has "
                    "no reveal_condition, so the ledger entry could never come due",
                    rule_name="payoff_target_has_no_condition",
                )
            ]

        return [
            self._submit(
                ActionType.PLANT_FORESHADOWING,
                target_id=f"hint:{definition.event_id}",
                payload={
                    "value": (event.generated_content.get("summary", "") if event else ""),
                    "payoff_condition": target.reveal_condition.model_dump(),
                    "note": f"{definition.event_id} → {target_id}",
                    "participants": list(event.participants) if event else [],
                },
            )
        ]

    def _escalate_effects(self) -> list[ActionValidationResult]:
        current = self._manager.snapshot().story_beats.tension
        return [
            self._submit(
                ActionType.ADVANCE_STORY_BEAT,
                payload={"tension": round(min(1.0, current + TENSION_STEP), 4)},
            )
        ]

    def _foreshadow_effects(self, event: NarrativeEvent | None) -> list[ActionValidationResult]:
        """Plant what the model wrote, letting the Validator judge it.

        A model that omits the fields, or writes a loop that is already due, is
        rejected rather than worked around: the rejection is the useful signal
        (docs/07 §2.3, §3) and inventing a fallback loop here would hide it.
        """
        if event is None:
            return []
        content = event.generated_content
        fact_id = content.get("planted_fact_id")
        condition = content.get("payoff_condition")
        if not fact_id or not condition:
            return [
                ActionValidationResult(
                    proposal_id=uuid.uuid4().hex,
                    approved=False,
                    reason="the generator omitted planted_fact_id or payoff_condition, "
                    "so there is nothing to plant",
                    rule_name="generator_output_incomplete",
                )
            ]
        return [
            self._submit(
                ActionType.PLANT_FORESHADOWING,
                target_id=str(fact_id),
                payload={
                    "value": content.get("planted_value", content.get("summary", "")),
                    "payoff_condition": condition,
                    "note": content.get("summary", ""),
                    # Carried so the Validator can check them. Generated names are
                    # the one part of a proposal no rule could previously see.
                    "participants": list(event.participants),
                },
            )
        ]

    def _reverse_effects(self, candidate: EventCandidate) -> list[ActionValidationResult]:
        """Mark the reversal as used, through the normal write path.

        A reverse changes no visibility — it re-reads what the player already has —
        so nothing in the world would otherwise stop it recurring every turn. The
        marker rides on ``advance_turn`` rather than a direct write: a setter on the
        Manager would be a second write path around the Validator, which is the one
        thing this layer exists to prevent.
        """
        return [
            self._submit(
                ActionType.ADVANCE_STORY_BEAT,
                payload={"spend_one_shot": f"reverse:{candidate.event_type}"},
            )
        ]

    def _submit(
        self,
        action_type: ActionType,
        *,
        target_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ActionValidationResult:
        return self._manager.submit(
            ActionProposal(
                proposal_id=uuid.uuid4().hex,
                actor_id=ENGINE_ACTOR,
                action_type=action_type.value,
                target_id=target_id,
                payload=payload or {},
            )
        )


def _render_rejection(rejection: Rejection) -> dict[str, Any]:
    return {
        "operator": rejection.candidate.operator.value,
        "event_type": rejection.candidate.event_type,
        "reason": rejection.reason,
    }


def default_prompts_root() -> Path:
    """Where the shared prompt templates live. Exposed for the demo's header."""
    return Path(__file__).resolve().parents[3] / "prompts"

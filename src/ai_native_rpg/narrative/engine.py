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

import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..agent.harness import PromptLibrary
from ..llm.base import LLMClient, Message
from ..scenario import NarrativeDirectives
from ..schemas.common import Condition
from ..schemas.narrative import (
    EventCandidate,
    NarrativeEvent,
    NarrativeOperator,
    PlayerProfile,
)
from ..schemas.world_state import ActionProposal, ActionValidationResult
from ..world.actions import FORESHADOW_PAYOFF_PATH_PREFIXES, ActionType
from ..world.manager import WorldStateManager
from .controller import PacingVerdict, Rejection, select_candidate
from .rules import check_triggers

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
    ) -> None:
        self._manager = manager
        self._llm = llm
        self._prompts = prompts or PromptLibrary()
        # The pack's authored narrative content. Absent means "pace nothing", not
        # "fall back to some scenario's clues" — see rules.check_triggers.
        self._directives = directives or NarrativeDirectives()
        self._pending_event: dict[str, Any] | None = None

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

        candidates = check_triggers(world, player_id=player_id, directives=self._directives)
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
            allowed = ", ".join(f"{p}*" for p in FORESHADOW_PAYOFF_PATH_PREFIXES)
            lines += [
                "",
                f"payoff_condition 的 path 只能用这几类：{allowed}",
                f"（例如 relationships.npc_a.{player_id}.trust 或 quests.investigation.stage）",
            ]

        return [
            Message(role="system", content=system),
            Message(role="user", content="\n".join(lines)),
        ]

    def _cast_and_place_lines(self, world, player_id: str) -> list[str]:
        """Who may appear, and where the player is standing.

        Names, not ids. Two reasons, and the second one is why this matters:

        * ids leak into prose — a model handed ``npc_a`` will occasionally write it;
        * a fact's *value* can be an id. In the reference scenario
          ``killer_identity`` is literally ``npc_b``, so listing the cast by id would
          put an undisclosed fact's value in the prompt as a side effect. Which
          character exists is public; which one is the killer is not, and only the
          second is a secret. Names keep the two apart.

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

        results.append(self._submit(ActionType.ADVANCE_TURN, payload={"operator": operator.value}))
        return results, landed

    def _effect_proposals(
        self, candidate: EventCandidate, event: NarrativeEvent | None
    ) -> list[ActionValidationResult]:
        match candidate.operator:
            case NarrativeOperator.REVEAL:
                return self._reveal_effects(candidate)
            case NarrativeOperator.ESCALATE:
                return self._escalate_effects()
            case NarrativeOperator.FORESHADOW:
                return self._foreshadow_effects(event)
            case NarrativeOperator.REVERSE:
                return self._reverse_effects(candidate)
            case _:
                return []

    def _reveal_effects(self, candidate: EventCandidate) -> list[ActionValidationResult]:
        """Voice the clue, and settle its ledger entry when it had one.

        ``reveal_fact`` still goes through ``reveal_requires_condition_met``, so
        this cannot disclose anything the world had not already unlocked.
        """
        fact_id = candidate.pays_off or candidate.event_type
        results = [self._submit(ActionType.REVEAL_FACT, target_id=fact_id)]
        if candidate.pays_off:
            results.append(
                self._submit(ActionType.PAY_OFF_FORESHADOWING, target_id=candidate.pays_off)
            )
        return results

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

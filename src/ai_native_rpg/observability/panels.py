"""Narrative State Panel contents as plain data (docs/07 §2.3, docs/12 §4.4).

Pure functions over ``WorldState`` / ``AgentTrace`` / ``NarrativeTick``. No I/O and
no rendering: callers turn these models into terminal text or JSON.

``chat_demo.py``'s ``print_beats`` / ``print_ledger`` / ``print_unlock_board``
already held the right logic, but as printing functions their *judgements* — is
this loop overdue, does this clause hold, which facts count as gated — could not be
reused by a second front end. Rewriting them for the Web panel would have put the
same three judgements in two places, and the symptom of drift is a panel that
lies, which is the one thing a debug panel may not do. So the judgements live here
and return models; the terminal formats them into text and the Web serialises them.

Reads ``WorldState`` directly and never ``PlayerView``: showing what the player
cannot see is the entire point (docs/07 §2.4). The player-facing path never
imports this module.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..narrative.engine import NarrativeTick
from ..schemas.agent_trace import AgentTrace
from ..schemas.narrative import SLOTS_PER_DAY, NarrativeOperator
from ..schemas.world_state import Visibility, WorldState
from ..world.conditions import UnknownPathError, clause_holds_for, resolve_path


class ClauseProgress(BaseModel):
    """One clause of a ``reveal_condition``, with its current value.

    This is the evaluator's intermediate result made visible — the whole of "why
    can't the player see X yet" (docs/07 §2.3 block 2).
    """

    path: str
    label: str = Field(description="last path segment, e.g. 'trust'; the panel's column")
    op: str
    expected: Any
    actual: Any | None = None
    resolvable: bool = Field(default=True, description="False when the path does not resolve")
    met: bool = False


class UnlockRow(BaseModel):
    """A gated fact and how close the player is to earning it."""

    fact_id: str
    mode: Literal["any", "all"]
    clauses: list[ClauseProgress] = Field(default_factory=list)
    visibility: str = Field(description="stored visibility: 'hidden' or 'partial'")

    @property
    def unlockable(self) -> bool:
        """Whether the condition currently holds.

        Mirrors ``Condition``'s semantics including the empty-clause case, which is
        False for both modes (``conditions.evaluate`` fails closed on purpose).
        """
        if not self.clauses:
            return False
        met = (c.met for c in self.clauses)
        return any(met) if self.mode == "any" else all(met)

    @property
    def progress(self) -> float:
        """Fraction of clauses satisfied, for the panel's bar. Not a probability."""
        if not self.clauses:
            return 0.0
        return sum(1 for c in self.clauses if c.met) / len(self.clauses)


class LedgerRow(BaseModel):
    """One open foreshadowing: a debt the story owes the player (docs/07 §2.3 block 1)."""

    fact_id: str
    label: str = Field(description="the entry's note, falling back to fact_id")
    planted_at_turn: int
    turns_owed: int
    overdue_after_turns: int
    overdue: bool
    payoff_clauses: list[ClauseProgress] = Field(default_factory=list)
    payoff_mode: Literal["any", "all"] = "any"


class RejectedProposal(BaseModel):
    """A proposal the Validator refused (docs/07 §2.3 block 3).

    The most under-rated block: seeing "the LLM tried to name the killer and a rule
    stopped it" is direct evidence for why a deterministic layer exists.
    """

    turn: int
    actor: str = Field(description="npc id, or 'narrative_engine' for a beat's effect")
    action_type: str = ""
    target_id: str | None = None
    rule_name: str | None = None
    reason: str | None = None


class OperatorEntry(BaseModel):
    """One turn on the operator timeline (docs/07 §2.3 block 4)."""

    turn: int
    operator: str
    event_type: str = ""
    trigger_reason: str = ""
    blocked: list[dict[str, Any]] = Field(
        default_factory=list, description="candidates pacing held back: {operator, reason}"
    )
    starved: bool = False
    hook: str = Field(default="", description="the generated dialogue_hook, if a beat landed")
    latency_ms: float = 0.0
    model_used: str | None = None
    tick_id: str | None = None


class RelationshipPoint(BaseModel):
    """One sample of the relationship values, for the trend line."""

    turn: int
    trust: float
    fear: float
    respect: float


class MemoryHit(BaseModel):
    """One retrieved memory, as the trace recorded it."""

    memory_id: str
    text: str
    score: float = Field(description="importance for episodic, confidence for semantic")
    kind: Literal["episodic", "semantic"]


class TurnCost(BaseModel):
    """What the last turn spent (docs/07 §2.1)."""

    llm_calls: int = 0
    tokens: int = 0
    total_latency_ms: float = 0.0
    trace_id: str | None = None


class BeatsView(BaseModel):
    """Narrative progress: chapter, tension, and what has been spent."""

    chapter: int
    turn: int
    tension: float
    recent_operators: list[str] = Field(default_factory=list)
    spent_one_shots: list[str] = Field(default_factory=list)
    planted_total: int = 0


class SlotBudget(BaseModel):
    """How much of the case's clock is left (docs/12 §13.1).

    A different *kind* of panel content from everything else here: the other blocks
    measure the shape of the story, this one measures how many chances the player has
    left. Worth its own block for the same reason the ledger is — a budget you have to
    work out by hand is a budget nobody checks while tuning.

    ``total`` is ``SLOTS_PER_DAY × day_limit`` and never a literal: ``day_limit`` is a
    *field*, so a pack may declare a shorter case, and a hardcoded 12 would keep
    agreeing with docs/15 §6 right up until one did.
    """

    day: int
    day_limit: int
    slot: str = Field(description="the current TimeSlot's value, wrap-up included")
    slots_spent_today: int
    spent_total: int
    total: int
    remaining: int
    wrapping_up: bool = Field(
        default=False, description="at the day's free interlude, which spends nothing"
    )
    out_of_days: bool = False


class TraceStepView(BaseModel):
    """One step of an NPC turn, already interpreted for the panel (docs/07 §2.1).

    The trace records steps as free-form summaries; this decides, once, what each kind of
    step *means* — "tool X with these arguments returned this", "the Validator approved
    or refused this action under this rule" — so the web panel and the terminal can both
    render it without either one re-deriving the reading from raw keys.
    """

    name: str = Field(description="the trace's own step_name")
    kind: Literal[
        "memory", "tool", "plan", "validation", "event", "expression", "reflection", "other"
    ]
    title: str = Field(description="one line: what happened at this step")
    status: Literal["ok", "rejected", "error", "info"] = "info"
    detail: dict[str, Any] = Field(
        default_factory=dict,
        description="the parts worth reading, keyed for display (arguments, result, rule…)",
    )
    latency_ms: float = 0.0
    model_used: str | None = None
    tokens: int = 0


class TraceView(BaseModel):
    """The last NPC turn as a readable chain: retrieve → tools → plan → validate → speak."""

    trace_id: str
    npc_id: str
    final_dialogue: str = ""
    total_latency_ms: float = 0.0
    steps: list[TraceStepView] = Field(default_factory=list)


class PanelView(BaseModel):
    """Everything the developer panel shows, in one snapshot.

    A snapshot rather than a diff: "what shape is the story in" is inherently
    snapshot-shaped, and a front end maintaining its own incremental mirror could
    disagree with the world — which would defeat the panel's purpose (docs/12 §3.3).
    """

    beats: BeatsView
    npcs: list[dict[str, Any]] = Field(
        default_factory=list, description="All NPCs, each with its own state, memories and activity"
    )
    slot_budget: SlotBudget | None = None
    ledger: list[LedgerRow] = Field(default_factory=list)
    unlock_board: list[UnlockRow] = Field(default_factory=list)
    rejected_proposals: list[RejectedProposal] = Field(default_factory=list)
    operator_timeline: list[OperatorEntry] = Field(default_factory=list)
    relationship_trend: list[RelationshipPoint] = Field(default_factory=list)
    memory: list[MemoryHit] = Field(default_factory=list)
    memory_counts: dict[str, int] = Field(default_factory=dict)
    last_turn_cost: TurnCost | None = None
    last_trace: TraceView | None = Field(
        default=None,
        description="the last NPC turn, step by step, with tool results and rulings",
    )
    pending_hook: str = Field(
        default="",
        description="content generated last turn, waiting to be woven into the next one. "
        "Visible here because 'the Engine planted something' is otherwise only observable "
        "one turn later, in an NPC's line.",
    )


def _clause_progress(world: WorldState, clause) -> ClauseProgress:
    """Resolve one clause against the world, reporting rather than raising.

    Uses ``clause_holds_for`` instead of ``evaluate_clause`` deliberately: the strict
    evaluator raises on a bad path or type, which is right during gameplay (a silent
    False becomes a clue that never unlocks) and wrong here, where one malformed
    clause would hide the twenty well-formed rows around it.
    """
    label = clause.path.rsplit(".", 1)[-1]
    try:
        actual = resolve_path(world, clause.path)
    except UnknownPathError:
        return ClauseProgress(
            path=clause.path,
            label=label,
            op=clause.op.value,
            expected=clause.value,
            actual=None,
            resolvable=False,
            met=False,
        )
    return ClauseProgress(
        path=clause.path,
        label=label,
        op=clause.op.value,
        expected=clause.value,
        actual=actual,
        resolvable=True,
        met=clause_holds_for(actual, clause),
    )


def build_beats(world: WorldState) -> BeatsView:
    """Chapter, tension and the operator history (docs/07 §2.3 block 4)."""
    beats = world.story_beats
    return BeatsView(
        chapter=beats.chapter,
        turn=beats.turn,
        tension=beats.tension,
        recent_operators=list(beats.recent_operators),
        spent_one_shots=list(beats.spent_one_shots),
        planted_total=beats.planted_total,
    )


def build_slot_budget(world: WorldState) -> SlotBudget:
    """How many slots the case has left (docs/12 §13.1).

    Spent is counted as *closed-out days plus today's slots*, never derived from the
    position in the day. The wrap-up is the fourth entry in ``TimeSlot`` but costs
    nothing (docs/13 §4.2), so reading the position would overcount by one every
    evening — "已用 4 / 12" at a moment when three slots have been spent, which is the
    trap docs/12 §13.1 names explicitly.

    ``out_of_days`` defers to ``StoryBeats.is_out_of_days`` rather than comparing here:
    "the case is over" is a judgement, and this module exists so judgements have one
    copy.
    """
    beats = world.story_beats
    total = SLOTS_PER_DAY * beats.day_limit
    spent = SLOTS_PER_DAY * max(0, world.time_day - 1) + beats.slots_spent_today

    return SlotBudget(
        day=world.time_day,
        day_limit=beats.day_limit,
        slot=beats.time_slot.value,
        slots_spent_today=beats.slots_spent_today,
        spent_total=spent,
        total=total,
        # Floored: past the limit the day counter keeps climbing, and a negative
        # "slots left" beside ``out_of_days`` reads as a bug rather than as an ending.
        remaining=max(0, total - spent),
        wrapping_up=not beats.time_slot.is_spendable,
        out_of_days=beats.is_out_of_days(current_day=world.time_day),
    )


def build_ledger(world: WorldState) -> list[LedgerRow]:
    """The foreshadowing ledger (docs/07 §2.3 block 1).

    Turns "the model planted something and forgot it" from a thing you find by
    re-reading transcripts into a thing you see at a glance. Ordered most overdue
    first: the panel's job is to surface the debt, so the worst debt goes on top.
    """
    beats = world.story_beats
    rows = [
        LedgerRow(
            fact_id=fact_id,
            label=entry.note or fact_id,
            planted_at_turn=entry.planted_at_turn,
            turns_owed=entry.turns_owed(current_turn=beats.turn),
            overdue_after_turns=entry.overdue_after_turns,
            overdue=entry.is_overdue(current_turn=beats.turn),
            payoff_mode=entry.payoff_condition.mode,
            payoff_clauses=[_clause_progress(world, c) for c in entry.payoff_condition.clauses],
        )
        for fact_id, entry in beats.open_foreshadowings.items()
    ]
    rows.sort(key=lambda r: (not r.overdue, -r.turns_owed))
    return rows


def build_unlock_board(world: WorldState) -> list[UnlockRow]:
    """Per-clause progress toward every gated fact (docs/07 §2.3 block 2).

    "Gated" means not yet stored as revealed *and* carrying a condition. A fact with
    no condition never auto-reveals, so it has no progress to show; an already
    revealed one has nothing left to earn.

    Tuning a threshold is otherwise pure guesswork — is 40 too high, will the player
    stall forever — and this is the evidence for that call.
    """
    rows: list[UnlockRow] = []
    for fact in world.facts.values():
        if fact.visibility is Visibility.REVEALED or fact.reveal_condition is None:
            continue
        rows.append(
            UnlockRow(
                fact_id=fact.fact_id,
                mode=fact.reveal_condition.mode,
                visibility=fact.visibility.value,
                clauses=[_clause_progress(world, c) for c in fact.reveal_condition.clauses],
            )
        )
    # Closest to unlocking first: that is the row an author is about to tune.
    rows.sort(key=lambda r: -r.progress)
    return rows


def rejected_from_tick(tick: NarrativeTick) -> list[RejectedProposal]:
    """Proposals the Validator refused while landing a beat's effects."""
    from ..narrative.engine import ENGINE_ACTOR

    return [
        RejectedProposal(
            turn=tick.turn,
            actor=ENGINE_ACTOR,
            action_type=tick.selected.event_type if tick.selected else "",
            rule_name=result.rule_name,
            reason=result.reason,
        )
        for result in tick.proposals
        if not result.approved
    ]


def rejected_from_trace(trace: AgentTrace, *, turn: int) -> list[RejectedProposal]:
    """The NPC's own rejected action, if this turn proposed one and lost.

    ``turn`` is passed in because a trace records an interaction, not a narrative
    turn: ``story_beats.turn`` lives in the world, and the Harness never reads it.
    """
    rejected = []
    for step in trace.steps:
        if step.step_name != "action_validation":
            continue
        if step.output_summary.get("approved"):
            continue
        rejected.append(
            RejectedProposal(
                turn=turn,
                actor=trace.npc_id,
                action_type=str(step.input_summary.get("action_type", "")),
                target_id=step.input_summary.get("target_id"),
                rule_name=step.output_summary.get("rule_name"),
                reason=step.output_summary.get("reason"),
            )
        )
    return rejected


def operator_entry(tick: NarrativeTick) -> OperatorEntry:
    """One timeline row for a tick, including the decision to do nothing.

    A quiet turn is recorded rather than skipped: "pacing held a candidate back" is
    the Engine's least visible and most informative outcome, and it is
    indistinguishable from "no rule fired" unless both are shown (docs/07 §2.3).
    """
    if tick.selected is not None:
        operator = tick.selected.operator.value
        event_type = tick.selected.event_type
        trigger_reason = tick.selected.trigger_reason
    else:
        operator = NarrativeOperator.RELIEVE.value
        event_type = ""
        trigger_reason = ""

    hook = ""
    if tick.event is not None:
        hook = str(tick.event.generated_content.get("dialogue_hook", ""))

    return OperatorEntry(
        turn=tick.turn,
        operator=operator,
        event_type=event_type,
        trigger_reason=trigger_reason,
        blocked=list(tick.rejected),
        starved=tick.starved,
        hook=hook,
        latency_ms=tick.latency_ms,
        model_used=tick.model_used,
        tick_id=tick.tick_id,
    )


def turn_cost(trace: AgentTrace) -> TurnCost:
    """LLM calls, tokens and latency for one interaction (docs/07 §2.1)."""
    tokens = 0
    for step in trace.steps:
        usage = step.token_usage or {}
        tokens += usage.get("total_tokens", 0) or (
            usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        )
    return TurnCost(
        llm_calls=sum(1 for s in trace.steps if s.model_used is not None),
        tokens=tokens,
        total_latency_ms=trace.total_latency_ms,
        trace_id=trace.trace_id,
    )


def _step_tokens(usage: dict[str, int] | None) -> int:
    usage = usage or {}
    return usage.get("total_tokens", 0) or (
        usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    )


def _call(tool: str, arguments: Any) -> str:
    if isinstance(arguments, dict) and arguments:
        args = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    else:
        args = ""
    return f"{tool}({args})"


def _proposal_line(p: dict[str, Any]) -> str:
    """One authored effect and its ruling (an ``ActionValidationResult`` dump).

    A result carries no action type, so an approved one is named by what it changed
    and a refused one by the rule that stopped it.
    """
    if p.get("approved", True):
        changes = p.get("applied_changes") or {}
        what = ", ".join(f"{k}={v}" for k, v in changes.items()) or "已应用"
        return f"✓ {what}"
    rule = p.get("rule_name") or "被拒"
    reason = p.get("reason")
    return f"✗ {rule} — {reason}" if reason else f"✗ {rule}"


def _step_view(step) -> TraceStepView:
    """Read one TraceStep. Unknown step names still render, as ``other``."""
    name = step.step_name
    inp = step.input_summary or {}
    out = step.output_summary or {}
    base = {
        "name": name,
        "latency_ms": step.latency_ms,
        "model_used": step.model_used,
        "tokens": _step_tokens(step.token_usage),
    }

    if name == "memory_retrieval":
        return TraceStepView(
            **base,
            kind="memory",
            title=f"检索记忆：情景 {out.get('episodic_hits', 0)} 条 · "
            f"语义 {out.get('semantic_hits', 0)} 条",
            detail={"查询": inp.get("observation", "")},
        )
    if name == "tool_request":
        return TraceStepView(
            **base, kind="tool", title=f"模型请求调用 {out.get('tool_count', 0)} 个工具"
        )
    if name == "tool_call":
        call = _call(str(inp.get("tool", "?")), inp.get("arguments"))
        if out.get("ok", False):
            return TraceStepView(
                **base, kind="tool", status="ok", title=call, detail={"结果": out.get("result")}
            )
        return TraceStepView(
            **base, kind="tool", status="error", title=call, detail={"错误": out.get("error")}
        )
    if name == "planning":
        detail = {"策略": out.get("strategy", "")}
        if inp.get("tool_iterations"):
            detail["工具轮数"] = inp["tool_iterations"]
        return TraceStepView(
            **base,
            kind="plan",
            title="规划：提出了行动" if out.get("proposes_action") else "规划：只说话，不提行动",
            detail=detail,
        )
    if name == "action_validation":
        action = str(inp.get("action_type", "?"))
        target = inp.get("target_id")
        approved = bool(out.get("approved"))
        detail = {"规则": out.get("rule_name"), "理由": out.get("reason")}
        return TraceStepView(
            **base,
            kind="validation",
            status="ok" if approved else "rejected",
            title=f"裁决：{action}{f'({target})' if target else ''} "
            f"{'通过' if approved else '被拒'}",
            detail={k: v for k, v in detail.items() if v},
        )
    if name == "event_resolution":
        detail = {
            "选项": out.get("option_id") or inp.get("option_id"),
            "结果": out.get("outcome_id"),
            "检定": out.get("band"),
        }
        if out.get("threshold") is not None:
            detail["阈值"] = f"{out.get('current_value')} / {out.get('threshold')}"
        proposals = out.get("proposals") or []
        rejected = [p for p in proposals if not p.get("approved", True)]
        if proposals:
            detail["提议"] = [_proposal_line(p) for p in proposals]
        return TraceStepView(
            **base,
            kind="event",
            status="rejected" if rejected else "ok",
            title=f"事件结算：{out.get('event_id', '?')} → {out.get('outcome_id') or '继续'}",
            detail={k: v for k, v in detail.items() if v not in (None, "", [])},
        )
    if name == "intent_classification":
        matched = out.get("matched_option_id")
        return TraceStepView(
            **base,
            kind="event",
            title=f"意图归类：{matched}" if matched else "意图归类：未匹配任何选项",
        )
    if name in ("dialogue_generation", "expression_evidence", "rewrite_evidence"):
        return TraceStepView(
            **base,
            kind="expression",
            title={
                "dialogue_generation": "生成台词",
                "expression_evidence": "整理可公开的依据",
                "rewrite_evidence": "补充改写依据",
            }[name],
            detail=dict(out),
        )
    if name == "expression_fallback":
        return TraceStepView(
            **base,
            kind="expression",
            status="error",
            title="台词生成失败，使用作者写好的回复",
            detail={"错误类型": out.get("error_type")},
        )
    if name == "reflection":
        return TraceStepView(
            **base, kind="reflection", title=f"写回记忆 {out.get('episodic_written', 0)} 条"
        )
    return TraceStepView(**base, kind="other", title=name, detail={**inp, **out})


def build_trace_view(trace: AgentTrace) -> TraceView:
    """The panel's reading of one trace (docs/07 §2.1), shared by every renderer."""
    return TraceView(
        trace_id=trace.trace_id,
        npc_id=trace.npc_id,
        final_dialogue=trace.final_dialogue,
        total_latency_ms=trace.total_latency_ms,
        steps=[_step_view(step) for step in trace.steps],
    )


def memory_hits(trace: AgentTrace) -> list[MemoryHit]:
    """Which memories this turn retrieved, in rank order (docs/07 §2.2)."""
    hits: list[MemoryHit] = []
    for step in trace.steps:
        if step.step_name != "memory_retrieval":
            continue
        out = step.output_summary
        for item in out.get("episodic", []):
            hits.append(
                MemoryHit(
                    memory_id=str(item.get("id", "")),
                    text=str(item.get("text", "")),
                    score=float(item.get("importance", 0.0)),
                    kind="episodic",
                )
            )
        for item in out.get("semantic", []):
            hits.append(
                MemoryHit(
                    memory_id=str(item.get("id", "")),
                    text=str(item.get("text", "")),
                    score=float(item.get("confidence", 0.0)),
                    kind="semantic",
                )
            )
    return hits

"""Panel judgements (docs/07 §2.3, docs/12 §4.4).

These are the deterministic half of the developer panel: whether a loop is overdue,
whether a clause holds, which facts count as gated. docs/09 §5 puts this class of
code under "strict test-first" — the functions are pure and the failure mode is a
panel that quietly shows the wrong thing, which no amount of manual clicking
catches.
"""

from __future__ import annotations

from ai_native_rpg.narrative.engine import ENGINE_ACTOR, NarrativeTick
from ai_native_rpg.observability.panels import (
    build_beats,
    build_ledger,
    build_slot_budget,
    build_unlock_board,
    memory_hits,
    operator_entry,
    rejected_from_tick,
    rejected_from_trace,
    turn_cost,
)
from ai_native_rpg.schemas.agent_trace import AgentTrace, TraceStep
from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
from ai_native_rpg.schemas.narrative import (
    SLOTS_PER_DAY,
    EventCandidate,
    Foreshadowing,
    NarrativeEvent,
    NarrativeOperator,
    TimeSlot,
)
from ai_native_rpg.schemas.world_state import ActionValidationResult, Fact, Visibility

PLAYER = "player_1"
NPC_A = "npc_a"

TRUST_PATH = f"relationships.{NPC_A}.{PLAYER}.trust"


def _trust_at_least(value: int) -> Condition:
    return Condition(
        mode="any", clauses=[ConditionClause(path=TRUST_PATH, op=ConditionOp.GTE, value=value)]
    )


# --- unlock board ----------------------------------------------------------


def test_unlock_board_lists_only_gated_facts(world):
    """Revealed facts and facts with no condition have nothing left to earn."""
    rows = {row.fact_id for row in build_unlock_board(world)}

    assert "clue_1" in rows
    assert "loren_that_night" in rows
    # visibility=revealed: already visible, no progress to show.
    assert "victim_name" not in rows


def test_unlock_board_shows_actual_value_against_threshold(world):
    """The evaluator's intermediate result is the whole point of this block."""
    row = next(r for r in build_unlock_board(world) if r.fact_id == "clue_1")

    clause = row.clauses[0]
    assert clause.label == "trust"
    assert clause.actual == 20.0  # the fixture's starting trust
    assert clause.expected == 40
    assert clause.met is False
    assert row.unlockable is False


def test_unlock_board_reports_partial_visibility_separately(world):
    """A partial fact is still gated: the player has the hint, not the value."""
    row = next(r for r in build_unlock_board(world) if r.fact_id == "loren_that_night")
    assert row.visibility == "partial" or row.visibility == "hidden"
    assert row.fact_id in {f.fact_id for f in world.facts.values()}


def test_unlock_row_all_mode_needs_every_clause(world):
    """``npc_a_threatened`` is mode=all with fear below its ceiling."""
    row = next(r for r in build_unlock_board(world) if r.fact_id == "npc_a_threatened")
    assert row.mode == "all"

    # fear=10 <= 20 holds, trust=20 >= 70 does not.
    fear = next(c for c in row.clauses if c.label == "fear")
    trust = next(c for c in row.clauses if c.label == "trust")
    assert fear.met is True
    assert trust.met is False
    assert row.unlockable is False
    assert row.progress == 0.5


def test_unlock_row_becomes_unlockable_when_condition_holds(world):
    world.relationships[NPC_A][PLAYER].trust = 55.0

    row = next(r for r in build_unlock_board(world) if r.fact_id == "clue_1")
    assert row.clauses[0].met is True
    assert row.unlockable is True
    assert row.progress == 1.0


def test_unresolvable_path_is_reported_not_raised(world):
    """One malformed clause must not hide the well-formed rows around it.

    ``resolve_path`` raises by design during gameplay; the board deliberately uses
    the tolerant variant so a typo in one condition costs one row, not the panel.
    """
    world.facts["typo_fact"] = Fact(
        fact_id="typo_fact",
        value="whatever",
        visibility=Visibility.HIDDEN,
        reveal_condition=Condition(
            mode="any",
            clauses=[
                ConditionClause(path="relationships.nobody.at.all", op=ConditionOp.GTE, value=1)
            ],
        ),
    )

    rows = build_unlock_board(world)
    row = next(r for r in rows if r.fact_id == "typo_fact")

    assert row.clauses[0].resolvable is False
    assert row.clauses[0].met is False
    assert row.unlockable is False
    # The other rows survived.
    assert "clue_1" in {r.fact_id for r in rows}


def test_empty_clause_list_never_unlocks(world):
    """Matches ``conditions.evaluate``: an empty ``all`` is False, not vacuously True.

    Failing closed is the right default for an information-asymmetry mechanism — the
    alternative reveals every fact that forgot to declare a condition.
    """
    world.facts["no_clauses"] = Fact(
        fact_id="no_clauses",
        value="x",
        visibility=Visibility.HIDDEN,
        reveal_condition=Condition(mode="all", clauses=[]),
    )

    row = next(r for r in build_unlock_board(world) if r.fact_id == "no_clauses")
    assert row.unlockable is False
    assert row.progress == 0.0


# --- ledger ----------------------------------------------------------------


def test_ledger_empty_when_nothing_is_owed(world):
    assert build_ledger(world) == []


def test_ledger_counts_turns_owed(world):
    world.story_beats.turn = 12
    world.story_beats.open_foreshadowings["rain_stopped_early"] = Foreshadowing(
        fact_id="rain_stopped_early",
        planted_at_turn=3,
        payoff_condition=_trust_at_least(45),
        overdue_after_turns=8,
        note="玛尔塔提到那晚雨停得早",
    )

    row = build_ledger(world)[0]
    assert row.turns_owed == 9
    assert row.overdue is True  # 9 > 8
    assert row.label == "玛尔塔提到那晚雨停得早"


def test_ledger_label_falls_back_to_fact_id(world):
    world.story_beats.open_foreshadowings["curtain_moved"] = Foreshadowing(
        fact_id="curtain_moved", planted_at_turn=1, payoff_condition=_trust_at_least(45)
    )
    assert build_ledger(world)[0].label == "curtain_moved"


def test_ledger_orders_overdue_first(world):
    """The panel exists to surface debt, so the worst debt goes on top."""
    world.story_beats.turn = 10
    world.story_beats.open_foreshadowings = {
        "fresh": Foreshadowing(
            fact_id="fresh", planted_at_turn=9, payoff_condition=_trust_at_least(45)
        ),
        "stale": Foreshadowing(
            fact_id="stale",
            planted_at_turn=1,
            payoff_condition=_trust_at_least(45),
            overdue_after_turns=4,
        ),
    }

    rows = build_ledger(world)
    assert [r.fact_id for r in rows] == ["stale", "fresh"]
    assert rows[0].overdue is True
    assert rows[1].overdue is False


def test_ledger_shows_payoff_progress(world):
    """ "Is it time yet?" is answerable only because the condition is structured."""
    world.relationships[NPC_A][PLAYER].trust = 50.0
    world.story_beats.open_foreshadowings["curtain_moved"] = Foreshadowing(
        fact_id="curtain_moved", planted_at_turn=1, payoff_condition=_trust_at_least(45)
    )

    clause = build_ledger(world)[0].payoff_clauses[0]
    assert clause.actual == 50.0
    assert clause.met is True


# --- beats -----------------------------------------------------------------


def test_beats_carries_progress_and_spent_one_shots(world):
    world.story_beats.chapter = 2
    world.story_beats.turn = 7
    world.story_beats.tension = 0.4
    world.story_beats.recent_operators = ["relieve", "foreshadow", "relieve"]
    world.story_beats.spent_one_shots = ["reverse:npc_a_threatened"]

    view = build_beats(world)
    assert (view.chapter, view.turn, view.tension) == (2, 7, 0.4)
    assert view.recent_operators == ["relieve", "foreshadow", "relieve"]
    # A one-shot that has fired must stay visible: 'has the reversal happened yet'
    # is otherwise unanswerable from the panel.
    assert view.spent_one_shots == ["reverse:npc_a_threatened"]


# --- slot budget (docs/12 §13.1) -------------------------------------------
#
# A new *kind* of panel content: every other block measures the shape of the story,
# this one measures how many chances the player has left. Test-first because the
# arithmetic has two ways to be quietly wrong — a hardcoded 12, and counting the
# wrap-up as a fourth slot.


def test_budget_total_is_days_times_slots_not_a_literal(world):
    """docs/12 §13.1: ``SLOTS_PER_DAY × day_limit`` are the sources, never a literal."""
    world.story_beats.day_limit = 4

    budget = build_slot_budget(world)

    assert budget.total == SLOTS_PER_DAY * 4 == 12
    assert budget.day_limit == 4


def test_budget_follows_a_pack_that_shortens_the_case(world):
    """``day_limit`` is a field, so a pack can declare a shorter case (docs/12 §13.1).

    The literal-12 version of this function passes the test above and fails here,
    which is why both exist.
    """
    world.story_beats.day_limit = 2

    assert build_slot_budget(world).total == SLOTS_PER_DAY * 2 == 6


def test_budget_counts_days_already_closed_out(world):
    """Spent = whole days behind us + today's slots. Day 1 is the first day, not a gap."""
    world.time_day = 3
    world.story_beats.slots_spent_today = 1

    budget = build_slot_budget(world)

    assert budget.spent_total == SLOTS_PER_DAY * 2 + 1 == 7
    assert budget.remaining == budget.total - budget.spent_total == 5


def test_the_wrap_up_is_not_a_fourth_slot(world):
    """docs/12 §13.1's named trap: at the wrap-up "已用 3 / 12" is correct.

    The interlude costs nothing (docs/13 §4.2), so deriving spent from the *position*
    in the day — wrap_up being the fourth entry — would overcount by one every day and
    tell the player they are a quarter further through the case than they are.
    """
    world.time_day = 1
    world.story_beats.slots_spent_today = SLOTS_PER_DAY
    world.story_beats.time_slot = TimeSlot.WRAP_UP

    budget = build_slot_budget(world)

    assert budget.wrapping_up is True
    assert budget.spent_total == 3
    assert budget.remaining == 9


def test_budget_reports_the_current_slot_and_day(world):
    world.time_day = 2
    world.story_beats.time_slot = TimeSlot.AFTERNOON

    budget = build_slot_budget(world)

    assert (budget.day, budget.slot) == (2, "afternoon")
    assert budget.wrapping_up is False


def test_budget_never_reports_negative_remaining(world):
    """Past the limit the day counter keeps going; the budget floors at zero.

    ``is_out_of_days`` is the real signal, and a "-2 slots left" beside it would read
    as a bug rather than as an ending.
    """
    world.story_beats.day_limit = 2
    world.time_day = 4

    budget = build_slot_budget(world)

    assert budget.remaining == 0
    assert budget.out_of_days is True


def test_out_of_days_reuses_the_beats_own_judgement(world):
    """One copy of "the case is over": ``StoryBeats.is_out_of_days`` (docs/12 §13.1)."""
    world.story_beats.day_limit = 4

    world.time_day = 4
    assert build_slot_budget(world).out_of_days is False
    world.time_day = 5
    assert build_slot_budget(world).out_of_days is True


# --- rejected proposals ----------------------------------------------------


def _tick(**kwargs) -> NarrativeTick:
    base = {"tick_id": "t1", "turn": 4, "player_id": PLAYER}
    return NarrativeTick(**{**base, **kwargs})


def test_rejected_from_tick_keeps_only_refusals():
    tick = _tick(
        selected=EventCandidate(
            operator=NarrativeOperator.REVEAL,
            event_type="loren_that_night",
            intensity=0.9,
            preference_tag="intrigue",
            trigger_reason="unlockable but unspoken",
        ),
        proposals=[
            ActionValidationResult(proposal_id="p1", approved=True),
            ActionValidationResult(
                proposal_id="p2",
                approved=False,
                reason="条件未满足 (trust 32 < 85)",
                rule_name="reveal_requires_condition_met",
            ),
        ],
    )

    rejected = rejected_from_tick(tick)
    assert len(rejected) == 1
    assert rejected[0].actor == ENGINE_ACTOR
    assert rejected[0].rule_name == "reveal_requires_condition_met"
    assert rejected[0].turn == 4


def test_rejected_from_trace_captures_the_npcs_blocked_action():
    """This is the panel block that evidences the architecture's central claim."""
    trace = AgentTrace(
        trace_id="tr1",
        npc_id=NPC_A,
        player_id=PLAYER,
        session_id="s1",
        steps=[
            TraceStep(
                step_name="action_validation",
                input_summary={"action_type": "reveal_fact", "target_id": "loren_that_night"},
                output_summary={
                    "approved": False,
                    "reason": "条件未满足 (trust 32 < 85)",
                    "rule_name": "reveal_requires_condition_met",
                },
            )
        ],
    )

    rejected = rejected_from_trace(trace, turn=4)
    assert len(rejected) == 1
    assert rejected[0].actor == NPC_A
    assert rejected[0].action_type == "reveal_fact"
    assert rejected[0].target_id == "loren_that_night"


def test_approved_action_is_not_listed(world):
    trace = AgentTrace(
        trace_id="tr2",
        npc_id=NPC_A,
        player_id=PLAYER,
        session_id="s1",
        steps=[
            TraceStep(
                step_name="action_validation",
                input_summary={"action_type": "adjust_relationship"},
                output_summary={"approved": True, "reason": None},
            )
        ],
    )
    assert rejected_from_trace(trace, turn=1) == []


# --- operator timeline -----------------------------------------------------


def test_quiet_turn_is_recorded_as_relieve():
    """A turn that left no trace would make a beat five turns back look adjacent."""
    entry = operator_entry(_tick(selected=None))
    assert entry.operator == "relieve"
    assert entry.model_used is None  # zero LLM calls on a quiet turn


def test_blocked_candidates_are_kept_on_the_timeline():
    """ "Had something and waited" must be distinguishable from "nothing fired"."""
    entry = operator_entry(
        _tick(
            selected=None,
            rejected=[{"operator": "reveal", "reason": "no two reveals in consecutive turns"}],
        )
    )
    assert entry.operator == "relieve"
    assert entry.blocked[0]["reason"] == "no two reveals in consecutive turns"


def test_timeline_carries_the_generated_hook():
    entry = operator_entry(
        _tick(
            selected=EventCandidate(
                operator=NarrativeOperator.FORESHADOW,
                event_type="rain_stopped_early",
                intensity=0.5,
                preference_tag="intrigue",
                trigger_reason="ledger has room",
            ),
            event=NarrativeEvent(
                event_id="e1",
                operator=NarrativeOperator.FORESHADOW,
                event_type="rain_stopped_early",
                generated_content={"dialogue_hook": "……那天的雨，停得比往常早些。"},
            ),
            latency_ms=8600.0,
            model_used="gpt-5.6-sol",
        )
    )
    assert entry.hook == "……那天的雨，停得比往常早些。"
    assert entry.latency_ms == 8600.0
    assert entry.operator == "foreshadow"


# --- cost and memory -------------------------------------------------------


def test_turn_cost_sums_tokens_and_counts_llm_calls():
    trace = AgentTrace(
        trace_id="tr3",
        npc_id=NPC_A,
        player_id=PLAYER,
        session_id="s1",
        total_latency_ms=7400.0,
        steps=[
            TraceStep(step_name="memory_retrieval"),  # deterministic: not an LLM call
            TraceStep(
                step_name="planning",
                model_used="deepseek-v4-flash",
                token_usage={"total_tokens": 2100},
            ),
            TraceStep(
                step_name="dialogue_generation",
                model_used="deepseek-v4-flash",
                # No total_tokens: must fall back to prompt+completion.
                token_usage={"prompt_tokens": 900, "completion_tokens": 200},
            ),
        ],
    )

    cost = turn_cost(trace)
    assert cost.llm_calls == 2
    assert cost.tokens == 3200
    assert cost.total_latency_ms == 7400.0


def test_memory_hits_preserve_rank_order_and_kind():
    trace = AgentTrace(
        trace_id="tr4",
        npc_id=NPC_A,
        player_id=PLAYER,
        session_id="s1",
        steps=[
            TraceStep(
                step_name="memory_retrieval",
                output_summary={
                    "episodic": [
                        {"id": "npc_a_sighting", "importance": 0.95, "text": "看见有人走回村里"},
                        {"id": "npc_a_search", "importance": 0.5, "text": "村民搜森林"},
                    ],
                    "semantic": [{"id": "belief_1", "confidence": 0.8, "text": "说出去很危险"}],
                },
            )
        ],
    )

    hits = memory_hits(trace)
    assert [h.memory_id for h in hits] == ["npc_a_sighting", "npc_a_search", "belief_1"]
    assert hits[0].kind == "episodic"
    assert hits[0].score == 0.95
    assert hits[-1].kind == "semantic"
    assert hits[-1].score == 0.8

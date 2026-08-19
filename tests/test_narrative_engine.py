"""NarrativeEngine.tick(): contract tests against the mock client.

Per docs/09 §5 the LLM half gets contract tests — JSON parsing, call counts, prompt
assembly, proposal routing — and no assertions about the prose. Whether generated
content is any *good* is the Eval suite's question (docs/08).

The two properties most worth holding onto here:

* a turn with no admissible candidate makes **zero** model calls. docs/02 §4 budgets
  narrative generation as event-triggered, not per-turn; a tick that always calls
  would quietly turn one interaction into three requests.
* the generator's prompt never contains an undisclosed fact's value. Same stance as
  ``tools.py``: keeping the secret out of the context beats instructing the model
  not to repeat it.
"""

from __future__ import annotations

from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.narrative.engine import GeneratedContent, NarrativeEngine
from ai_native_rpg.narrative.rules import MAX_OPEN_FORESHADOWINGS
from ai_native_rpg.schemas.narrative import NarrativeOperator, StoryBeats
from ai_native_rpg.schemas.world_state import Visibility, WorldState
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"
NPC_A = "npc_a"


def _content(**overrides) -> dict:
    payload = {
        "summary": "玛尔塔在门口停了很久才开口",
        "dialogue_hook": "……那晚的雨停得很突然。",
        "participants": [NPC_A],
    }
    payload.update(overrides)
    return payload


def _engine(
    world: WorldState, llm: MockLLMClient, directives=None
) -> tuple[NarrativeEngine, WorldStateManager]:
    """Build an engine over ``world``.

    ``directives`` is what a pack supplies; omitting it paces nothing, which is the
    right default for tests about quiet turns and the wrong one for anything that
    needs a reveal to exist.
    """
    manager = WorldStateManager(world)
    return NarrativeEngine(manager=manager, llm=llm, directives=directives), manager


def _fill_ledger(world: WorldState) -> None:
    """Leave no room for another plant, so a turn can be genuinely quiet.

    Several loops may be open at once now, so "nothing to do this turn" has to
    include a full ledger — otherwise a foreshadow is always available and no turn
    is ever quiet. The loops wait on trust 99, which this world never reaches, so
    they never come due as payoffs either.
    """
    from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
    from ai_native_rpg.schemas.narrative import Foreshadowing

    for index in range(MAX_OPEN_FORESHADOWINGS):
        fact_id = f"owed_{index}"
        world.story_beats.open_foreshadowings[fact_id] = Foreshadowing(
            fact_id=fact_id,
            planted_at_turn=0,
            payoff_condition=Condition(
                clauses=[
                    ConditionClause(
                        path=f"relationships.{NPC_A}.{PLAYER}.trust", op=ConditionOp.GTE, value=99
                    )
                ]
            ),
        )


class TestQuietTurns:
    def test_a_turn_with_no_candidate_makes_no_llm_call(self, world: WorldState):
        # trust 20 so nothing is unlockable, and a full ledger so nothing can be planted
        world.story_beats = StoryBeats(turn=99)
        _fill_ledger(world)
        llm = MockLLMClient([])

        engine, _ = _engine(world, llm)
        tick = engine.tick(player_id=PLAYER)

        assert llm.call_count == 0
        assert tick.selected is None
        assert tick.event is None

    def test_a_quiet_turn_still_closes_the_turn_as_relieve(self, world: WorldState):
        world.story_beats = StoryBeats(turn=99)
        _fill_ledger(world)

        engine, manager = _engine(world, MockLLMClient([]))
        engine.tick(player_id=PLAYER)

        beats = manager.snapshot().story_beats
        assert beats.turn == 100
        assert beats.last_operator == NarrativeOperator.RELIEVE.value

    def test_a_blocked_candidate_is_reported_and_the_turn_stays_quiet(
        self, world: WorldState, directives
    ):
        # an unlockable-but-untold clue, one turn after a reveal
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        _fill_ledger(world)  # so the reveal is the only candidate, and it is blocked
        world.story_beats.record_operator(NarrativeOperator.REVEAL)
        llm = MockLLMClient([])

        engine, manager = _engine(world, llm, directives)
        tick = engine.tick(player_id=PLAYER)

        assert llm.call_count == 0
        assert tick.selected is None
        assert tick.rejected  # the panel needs to show what was held back
        assert manager.snapshot().story_beats.last_operator == NarrativeOperator.RELIEVE.value


class TestGeneration:
    def test_a_selected_candidate_costs_exactly_one_call(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        llm = MockLLMClient([_content()])

        engine, _ = _engine(world, llm)
        tick = engine.tick(player_id=PLAYER)

        assert llm.call_count == 1
        assert tick.selected is not None
        assert tick.event is not None
        assert tick.event.generated_content["dialogue_hook"]

    def test_the_generated_content_is_requested_as_structured_output(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        llm = MockLLMClient([_content()])

        engine, _ = _engine(world, llm)
        engine.tick(player_id=PLAYER)

        assert llm.calls[0].schema_name == GeneratedContent.__name__

    def test_the_prompt_states_the_operator_and_its_constraints(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        llm = MockLLMClient([_content()])

        engine, _ = _engine(world, llm)
        tick = engine.tick(player_id=PLAYER)

        prompt = "\n".join(m.content for m in llm.calls[0].messages)
        assert tick.selected is not None
        assert tick.selected.operator.value in prompt
        # docs/10 §2.3: prohibitions are stated, not left to inference
        for constraint in tick.selected.constraints:
            assert constraint in prompt

    def test_the_prompt_never_carries_an_undisclosed_facts_value(
        self, world: WorldState, directives
    ):
        """A secret absent from the context cannot be generated out of it.

        killer_identity is unlockable at trust 70 / stage 3; at trust 45 its value
        must not appear in the generator's prompt at all.
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        llm = MockLLMClient([_content()])

        engine, _ = _engine(world, llm, directives)
        engine.tick(player_id=PLAYER)

        prompt = "\n".join(m.content for m in llm.calls[0].messages)
        assert str(world.facts["killer_identity"].value) not in prompt

    def test_no_tools_are_offered_to_the_generator(self, world: WorldState):
        # The Engine is handed its inputs; a tool loop here would be a second read
        # path into the world with no bound on what it pulls into context.
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        llm = MockLLMClient([_content()])

        engine, _ = _engine(world, llm)
        engine.tick(player_id=PLAYER)

        assert llm.calls[0].tools is None


class TestEffectsGoThroughTheValidator:
    def test_a_reveal_records_the_operator_for_the_turn(self, world: WorldState, directives):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, manager = _engine(world, MockLLMClient([_content()]), directives)
        engine.tick(player_id=PLAYER)

        assert manager.snapshot().story_beats.last_operator == NarrativeOperator.REVEAL.value

    def test_a_reveal_marks_the_clue_as_told(self, world: WorldState, directives):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, manager = _engine(world, MockLLMClient([_content()]), directives)
        engine.tick(player_id=PLAYER)

        # stored visibility is the record of having voiced it, which is what stops
        # the same clue from being re-scheduled every turn
        assert manager.snapshot().facts["clue_1"].visibility is Visibility.REVEALED

    def test_an_escalate_raises_tension_without_turning_the_chapter(self, world: WorldState):
        world.quests["investigation"].stage = 2
        world.story_beats = StoryBeats(turn=99, tension=0.1)

        engine, manager = _engine(world, MockLLMClient([_content()]))
        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is not None
        assert tick.selected.operator is NarrativeOperator.ESCALATE
        beats = manager.snapshot().story_beats
        assert beats.tension > 0.1
        assert beats.chapter == 1

    def test_a_foreshadow_plants_a_hidden_fact_and_a_ledger_entry(self, world: WorldState):
        world.story_beats = StoryBeats(turn=1)
        world.story_beats.open_foreshadowings.clear()
        llm = MockLLMClient(
            [
                _content(
                    planted_fact_id="rain_stopped_early",
                    planted_value="森林入口的脚印被雨水冲过一半",
                    payoff_condition={
                        "mode": "any",
                        "clauses": [
                            {
                                "path": f"relationships.{NPC_A}.{PLAYER}.trust",
                                "op": "gte",
                                "value": 50,
                            }
                        ],
                    },
                )
            ]
        )

        engine, manager = _engine(world, llm)
        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is not None
        assert tick.selected.operator is NarrativeOperator.FORESHADOW
        beats = manager.snapshot().story_beats
        assert "rain_stopped_early" in beats.open_foreshadowings
        assert manager.snapshot().facts["rain_stopped_early"].visibility is Visibility.HIDDEN

    def test_a_foreshadow_with_an_already_due_condition_is_rejected_not_crashed(
        self, world: WorldState
    ):
        """A rejected proposal is a normal outcome, and must be visible.

        The model proposing an unusable loop is exactly what the Validator is for;
        the tick records the rejection and the turn still closes.
        """
        world.story_beats = StoryBeats(turn=1)
        world.story_beats.open_foreshadowings.clear()
        llm = MockLLMClient(
            [
                _content(
                    planted_fact_id="already_true",
                    planted_value="v",
                    payoff_condition={
                        "mode": "any",
                        "clauses": [
                            {
                                "path": f"relationships.{NPC_A}.{PLAYER}.trust",
                                "op": "gte",
                                "value": 5,  # trust is already 20
                            }
                        ],
                    },
                )
            ]
        )

        engine, manager = _engine(world, llm)
        tick = engine.tick(player_id=PLAYER)

        assert any(not p.approved for p in tick.proposals)
        assert "already_true" not in manager.snapshot().story_beats.open_foreshadowings
        # the turn still advances: a failed beat must not stall the clock
        assert manager.snapshot().story_beats.turn == 2

    def test_a_payoff_settles_the_ledger_entry(self, world: WorldState):
        from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
        from ai_native_rpg.schemas.narrative import Foreshadowing

        world.story_beats = StoryBeats(turn=99)
        world.story_beats.open_foreshadowings["clue_1"] = Foreshadowing(
            fact_id="clue_1",
            planted_at_turn=90,
            payoff_condition=Condition(
                clauses=[
                    ConditionClause(
                        path=f"relationships.{NPC_A}.{PLAYER}.trust", op=ConditionOp.GTE, value=40
                    )
                ]
            ),
        )
        world.relationships[NPC_A][PLAYER].trust = 45.0

        engine, manager = _engine(world, MockLLMClient([_content()]))
        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is not None
        assert tick.selected.pays_off == "clue_1"
        assert manager.snapshot().story_beats.open_foreshadowings == {}

    def test_a_reverse_is_spent_so_it_cannot_repeat(self, world: WorldState, directives):
        world.story_beats = StoryBeats(turn=99)
        world.facts["npc_a_threatened"] = world.facts["victim_name"].model_copy(
            update={"fact_id": "npc_a_threatened", "visibility": Visibility.REVEALED}
        )

        engine, manager = _engine(world, MockLLMClient([_content()]), directives)
        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is not None
        assert tick.selected.operator is NarrativeOperator.REVERSE
        assert manager.snapshot().story_beats.is_spent("reverse:npc_a_threatened")

    def test_the_engine_never_writes_state_directly(self, world: WorldState, directives):
        """Every effect travels as a proposal (docs/04 §2, docs/10 §2.1).

        An operator is a set of Action Proposals, so the Engine needs no new
        permission mechanism — and must not have one.
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, manager = _engine(world, MockLLMClient([_content()]), directives)
        before = manager.snapshot()
        tick = engine.tick(player_id=PLAYER)

        # every change is attributable to a validated proposal
        assert tick.proposals
        assert all(p.proposal_id for p in tick.proposals)
        assert all(p.approved for p in tick.proposals), [p.reason for p in tick.proposals]
        # and the world moved only where an approved proposal said it would
        after = manager.snapshot()
        assert after.story_beats.turn == before.story_beats.turn + 1
        assert {k for p in tick.proposals for k in (p.applied_changes or {})}

    def test_an_npc_cannot_borrow_the_engines_authority(self, world: WorldState):
        # The engine submits as ENGINE_ACTOR; the same proposal from an NPC is
        # refused, which is what makes "only the Engine paces the story" a property
        # of the code rather than of who happens to call it.
        from ai_native_rpg.narrative.engine import ENGINE_ACTOR
        from ai_native_rpg.schemas.world_state import ActionProposal

        manager = WorldStateManager(world)
        assert ENGINE_ACTOR == "narrative_engine"

        result = manager.submit(
            ActionProposal(
                proposal_id="x",
                actor_id=NPC_A,
                action_type="advance_turn",
                payload={"operator": "reveal"},
            )
        )

        assert not result.approved
        assert result.rule_name == "narrative_actions_are_system_only"


class TestPendingEvent:
    def test_the_generated_event_is_held_for_the_next_turn(self, world: WorldState, directives):
        """Generation runs off the player's critical path (docs/02 §4).

        The content is produced after a turn closes and consumed by the *next*
        one, so the player never waits on this call.
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, _ = _engine(world, MockLLMClient([_content()]), directives)
        engine.tick(player_id=PLAYER)

        pending = engine.pending_event
        assert pending is not None
        assert pending["dialogue_hook"]

    def test_taking_the_pending_event_clears_it(self, world: WorldState, directives):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, _ = _engine(world, MockLLMClient([_content()]), directives)
        engine.tick(player_id=PLAYER)

        assert engine.take_pending_event() is not None
        # consumed once: a hook repeated on every later turn would read as the NPC
        # being stuck on one line
        assert engine.take_pending_event() is None

    def test_a_quiet_tick_leaves_nothing_pending(self, world: WorldState):
        world.story_beats = StoryBeats(turn=99)
        _fill_ledger(world)

        engine, _ = _engine(world, MockLLMClient([]))
        engine.tick(player_id=PLAYER)

        assert engine.pending_event is None

    def test_a_beat_whose_effects_were_all_rejected_leaves_nothing_pending(self, world: WorldState):
        """Content exists for beats that did not happen; it must not be spoken.

        Generation precedes the Validator's ruling, so a rejected beat still has a
        hook sitting in hand. Shipping it would have the NPC allude to a detail
        nobody planted — and since the ledger entry was rejected too, to a loop with
        no payoff coming. The turn is recorded as ``relieve``, and the pending
        content has to agree with that.
        """
        world.story_beats = StoryBeats(turn=1)
        world.story_beats.open_foreshadowings.clear()
        # a foreshadow missing planted_fact_id: nothing to plant, so every effect fails
        llm = MockLLMClient([_content(dialogue_hook="一个从未被埋下的细节")])

        engine, manager = _engine(world, llm)
        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is not None, "the turn had a beat to run"
        assert not any(p.approved for p in tick.proposals[:-1]), "and all its effects failed"
        assert manager.snapshot().story_beats.recent_operators == [NarrativeOperator.RELIEVE.value]
        assert engine.pending_event is None


class TestTickIsPersistable:
    def test_a_tick_round_trips_through_the_store(self, world: WorldState, tmp_path):
        from ai_native_rpg.observability import NarrativeTickStore

        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, _ = _engine(world, MockLLMClient([_content()]))
        tick = engine.tick(player_id=PLAYER)

        store = NarrativeTickStore(tmp_path / "narrative")
        path = store.save(tick)

        assert path.is_file()
        assert store.load(tick.tick_id) == tick

    def test_the_tick_records_candidates_rejections_and_verdicts(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, _ = _engine(world, MockLLMClient([_content()]))
        tick = engine.tick(player_id=PLAYER)

        assert tick.turn == 99
        assert tick.candidates
        assert tick.proposals
        assert tick.latency_ms >= 0.0
        assert tick.model_used == "mock-model"

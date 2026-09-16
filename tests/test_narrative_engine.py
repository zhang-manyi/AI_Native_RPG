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

Candidates are authored **events** now, not operators (docs/13 §2). A quiet turn is
consequently much easier to construct than it was: with no event whose trigger holds,
nothing happens, because docs/13 §2.1 removed the operator-level fallback that used to
fill those turns with a ``foreshadow`` nobody asked for.
"""

from __future__ import annotations

from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.narrative.engine import GeneratedContent, NarrativeEngine
from ai_native_rpg.narrative.rules import earned_stage
from ai_native_rpg.scenario import PacedClue
from ai_native_rpg.schemas.common import Condition, ConditionClause, ConditionOp
from ai_native_rpg.schemas.events import (
    EventDefinition,
    EventOption,
    EventOutcome,
    EventScript,
    OptionTag,
)
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


def _trust_at_least(value: int) -> Condition:
    return Condition(
        clauses=[
            ConditionClause(
                path=f"relationships.{NPC_A}.{PLAYER}.trust", op=ConditionOp.GTE, value=value
            )
        ]
    )


def _reveal_event(
    event_id: str = "E_reveal", *, fact_id: str = "clue_1", **overrides
) -> EventDefinition:
    """An event that voices a paced clue when the player answers warmly."""
    spec: dict = {
        "event_id": event_id,
        "operator": NarrativeOperator.REVEAL,
        "trigger": _trust_at_least(40),
        "npc_id": NPC_A,
        "outcomes": {
            "told": EventOutcome(outcome_id="told", reveals_facts=[fact_id]),
            "withheld": EventOutcome(outcome_id="withheld"),
        },
        "default_outcome": "withheld",
        "max_exchanges": 3,
        "options": [
            EventOption(
                option_id="goodwill",
                tag=OptionTag.GOODWILL,
                text="我不是来添麻烦的",
                on_success="told",
            ),
            EventOption(
                option_id="watch", tag=OptionTag.OBSERVE, text="（看着她）", on_success="withheld"
            ),
        ],
    }
    spec.update(overrides)
    return EventDefinition.model_validate(spec)


def _script(*events: EventDefinition) -> EventScript:
    return EventScript(events={e.event_id: e for e in events})


def test_generated_foreshadow_is_never_sent_to_the_content_model(world):
    event = _reveal_event(
        "legacy_hint",
        operator=NarrativeOperator.FORESHADOW,
        trigger=_trust_at_least(0),
        payoff_target="loren_that_night",
    )
    llm = MockLLMClient([])
    engine, manager = _engine(world, llm, script=_script(event))
    before = manager.snapshot().facts
    tick = engine.tick(player_id=PLAYER)
    assert llm.call_count == 0
    assert tick.selected is None
    assert manager.snapshot().facts == before
    assert manager.snapshot().story_beats.open_foreshadowings == {}


def _engine(
    world: WorldState, llm: MockLLMClient, directives=None, script: EventScript | None = None
) -> tuple[NarrativeEngine, WorldStateManager]:
    """Build an engine over ``world``.

    ``directives`` is what a pack supplies; omitting it paces nothing. ``script`` is the
    pack's event network, and omitting *that* now means every turn is quiet — which is
    the honest consequence of removing the operator fallback (docs/13 §2.1).
    """
    manager = WorldStateManager(world)
    engine = NarrativeEngine(manager=manager, llm=llm, directives=directives, script=script)
    return engine, manager


class TestQuietTurns:
    def test_a_turn_with_no_event_makes_no_llm_call(self, world: WorldState):
        """docs/13 §2.1: no event to hand means nothing happens, not a filler beat.

        This is the case the old implementation could not produce. ``foreshadow`` had
        the lowest intensity of the five operator candidates, so it lost whenever
        anything else was available and won on exactly the turns that should have been
        quiet — occupying the slot docs/05 reserved for "nothing happens".
        """
        world.story_beats = StoryBeats(turn=99)
        llm = MockLLMClient([])

        engine, _ = _engine(world, llm, script=_script(_reveal_event()))
        tick = engine.tick(player_id=PLAYER)

        assert llm.call_count == 0
        assert tick.selected is None
        assert tick.event is None

    def test_a_pack_with_no_script_is_always_quiet(self, world: WorldState):
        llm = MockLLMClient([])

        engine, _ = _engine(world, llm)
        tick = engine.tick(player_id=PLAYER)

        assert llm.call_count == 0
        assert tick.candidates == []

    def test_a_quiet_turn_still_closes_the_turn_as_relieve(self, world: WorldState):
        world.story_beats = StoryBeats(turn=99)

        engine, manager = _engine(world, MockLLMClient([]), script=_script(_reveal_event()))
        engine.tick(player_id=PLAYER)

        beats = manager.snapshot().story_beats
        assert beats.turn == 100
        assert beats.last_operator == NarrativeOperator.RELIEVE.value

    def test_a_blocked_candidate_is_reported_and_the_turn_stays_quiet(
        self, world: WorldState, directives
    ):
        """Pacing still acts on the operator an event is voiced with (docs/13 §2).

        The cooldowns did not change; what changed is that they now constrain the
        rhythm of real plot rather than the rhythm of filler.
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        world.story_beats.record_operator(NarrativeOperator.REVEAL)
        llm = MockLLMClient([])

        engine, manager = _engine(world, llm, directives, script=_script(_reveal_event()))
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

        engine, _ = _engine(world, llm, script=_script(_reveal_event()))
        tick = engine.tick(player_id=PLAYER)

        assert llm.call_count == 1
        assert tick.selected is not None
        assert tick.event is not None
        assert tick.event.generated_content["dialogue_hook"]

    def test_the_generated_content_is_requested_as_structured_output(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        llm = MockLLMClient([_content()])

        engine, _ = _engine(world, llm, script=_script(_reveal_event()))
        engine.tick(player_id=PLAYER)

        assert llm.calls[0].schema_name == GeneratedContent.__name__

    def test_the_prompt_states_the_operator_and_its_constraints(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        llm = MockLLMClient([_content()])

        engine, _ = _engine(
            world,
            llm,
            script=_script(_reveal_event(constraints=["不要说出那个人是谁"])),
        )
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

        loren_that_night is unlockable at trust 70 / stage 3; at trust 45 its value
        must not appear in the generator's prompt at all.
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        llm = MockLLMClient([_content()])

        engine, _ = _engine(world, llm, directives, script=_script(_reveal_event()))
        engine.tick(player_id=PLAYER)

        prompt = "\n".join(m.content for m in llm.calls[0].messages)
        assert str(world.facts["loren_that_night"].value) not in prompt

    def test_no_tools_are_offered_to_the_generator(self, world: WorldState):
        # The Engine is handed its inputs; a tool loop here would be a second read
        # path into the world with no bound on what it pulls into context.
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        llm = MockLLMClient([_content()])

        engine, _ = _engine(world, llm, script=_script(_reveal_event()))
        engine.tick(player_id=PLAYER)

        assert llm.calls[0].tools is None


class TestEffectsGoThroughTheValidator:
    def test_selecting_an_event_opens_it_and_records_the_operator(
        self, world: WorldState, directives
    ):
        """The shape change: a tick *starts* an event rather than finishing a beat.

        An operator's whole effect happened at selection time because there was nothing
        to wait for. An event waits for the player, which is what gives their answer
        something to change (docs/13 §3).
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, manager = _engine(
            world, MockLLMClient([_content()]), directives, script=_script(_reveal_event())
        )
        engine.tick(player_id=PLAYER)

        beats = manager.snapshot().story_beats
        assert beats.last_operator == NarrativeOperator.REVEAL.value
        assert beats.active_event is not None
        assert beats.active_event.event_id == "E_reveal"

    def test_a_clue_is_told_only_once_the_player_answers(self, world: WorldState, directives):
        """Selection does not disclose anything; the outcome does.

        Stated as a test because it is the load-bearing half of docs/13 §3: if merely
        scheduling an event revealed its facts, the model choosing among candidates
        would be choosing what the player learns.
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, manager = _engine(
            world, MockLLMClient([_content()]), directives, script=_script(_reveal_event())
        )
        engine.tick(player_id=PLAYER)

        assert manager.snapshot().facts["clue_1"].visibility is Visibility.HIDDEN

        engine.resolve_player_response(player_id=PLAYER, option_id="goodwill")

        assert manager.snapshot().facts["clue_1"].visibility is Visibility.REVEALED

    def test_telling_a_clue_advances_the_progress_stage(self, world: WorldState, directives):
        """The reveal that just landed makes the player one stage closer.

        Closes the gap that stalled a whole run: the stage is what the tension ceiling
        reads and what payoff conditions are written against, but no code advanced it.
        A clue told and a stage unchanged is the state in which ``stage >= 2``
        foreshadowings can never come due.
        """
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)
        assert world.quests["investigation"].stage == 0

        engine, manager = _engine(
            world, MockLLMClient([_content()]), directives, script=_script(_reveal_event())
        )
        engine.tick(player_id=PLAYER)
        engine.resolve_player_response(player_id=PLAYER, option_id="goodwill")

        assert manager.snapshot().quests["investigation"].stage == 1

    def test_the_stage_advances_one_step_per_tick(self, world: WorldState, directives):
        """Several clues told at once still buy one stage this turn.

        ``advance_quest`` adds exactly one, and looping here would clear several
        author thresholds inside a single turn — the objection ``MAX_CHAPTER_STEP``
        exists for. Falling behind is self-correcting: the next tick advances again.
        """
        world.facts["clue_1"].visibility = Visibility.REVEALED
        world.facts["loren_that_night"].visibility = Visibility.REVEALED
        paced = [*directives.paced_clues, PacedClue(fact_id="loren_that_night")]
        two_clues = directives.model_copy(update={"paced_clues": paced})
        world.story_beats = StoryBeats(turn=99)

        engine, manager = _engine(world, MockLLMClient([]), two_clues)
        assert earned_stage(world, two_clues) == 2

        engine.tick(player_id=PLAYER)
        assert manager.snapshot().quests["investigation"].stage == 1

        # and the next tick closes the rest of the gap
        engine.tick(player_id=PLAYER)
        assert manager.snapshot().quests["investigation"].stage == 2

    def test_a_quiet_turn_that_closes_a_stage_gap_stays_quiet(self, world: WorldState, directives):
        """Syncing the stage is not a beat, so it must not mark the turn eventful.

        ``landed`` decides both the recorded operator and whether the generated hook
        may be spoken. If this approval counted, a turn where nothing was scheduled
        would start a cooldown and hand the next turn a hook nobody planted — the
        failure docs/09 records for ``pending_event``.
        """
        world.facts["clue_1"].visibility = Visibility.REVEALED
        world.story_beats = StoryBeats(turn=99)

        engine, manager = _engine(world, MockLLMClient([]), directives)
        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is None
        beats = manager.snapshot().story_beats
        assert beats.last_operator == NarrativeOperator.RELIEVE.value
        assert manager.snapshot().quests["investigation"].stage == 1
        assert engine.pending_event is None

    def test_an_outcome_raises_tension_without_turning_the_chapter(
        self, world: WorldState, directives
    ):
        """``escalate`` is now an event's operator, and tension is an outcome's effect.

        Which is the point of the inversion: pressure arrives because something
        happened, not because a rule noticed the story was quiet.
        """
        world.story_beats = StoryBeats(turn=99, tension=0.1)
        event = _reveal_event(
            "E_escalate",
            operator=NarrativeOperator.ESCALATE,
            trigger=_trust_at_least(10),
            outcomes={
                "noticed": EventOutcome(outcome_id="noticed", tension_change=0.2),
                "quiet": EventOutcome(outcome_id="quiet"),
            },
            default_outcome="quiet",
            options=[
                EventOption(
                    option_id="push", tag=OptionTag.GOODWILL, text="我会小心", on_success="noticed"
                ),
                EventOption(
                    option_id="wait", tag=OptionTag.OBSERVE, text="（等着）", on_success="quiet"
                ),
            ],
        )

        engine, manager = _engine(
            world, MockLLMClient([_content()]), directives, script=_script(event)
        )
        tick = engine.tick(player_id=PLAYER)
        engine.resolve_player_response(player_id=PLAYER, option_id="push")

        assert tick.selected is not None
        assert tick.selected.operator is NarrativeOperator.ESCALATE
        beats = manager.snapshot().story_beats
        assert beats.tension > 0.1
        assert beats.chapter == 1

    def test_a_foreshadow_event_plants_a_ledger_entry_against_its_authored_target(
        self, world: WorldState
    ):
        """docs/13 §9: what the loop waits on is the target fact's own condition.

        Not a model-written condition. That is the whole correction — previously the
        generator decided both what to bury and when it counted as recovered, so what
        it buried pointed nowhere.
        """
        world.story_beats = StoryBeats(turn=1)
        event = _reveal_event(
            "F_hint",
            delivery="narration",
            operator=NarrativeOperator.FORESHADOW,
            trigger=_trust_at_least(10),
            payoff_target="loren_that_night",
            options=[],
            outcomes={"planted": EventOutcome(outcome_id="planted")},
            default_outcome="planted",
        )

        engine, manager = _engine(world, MockLLMClient([_content()]), script=_script(event))
        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is not None
        ledger = manager.snapshot().story_beats.open_foreshadowings
        assert ledger
        entry = next(iter(ledger.values()))
        assert entry.payoff_condition == world.facts["loren_that_night"].reveal_condition

    def test_a_foreshadow_toward_an_ungated_fact_is_rejected_not_crashed(self, world: WorldState):
        """A target with no condition could never come due, so the plant is refused.

        Rejection rather than a workaround: the refusal is the useful signal
        (docs/07 §2.3), and inventing a condition here would put the engine back in the
        business of writing its own payoff terms.
        """
        world.story_beats = StoryBeats(turn=1)
        event = _reveal_event(
            "F_bad",
            delivery="narration",
            operator=NarrativeOperator.FORESHADOW,
            trigger=_trust_at_least(10),
            payoff_target="victim_name",  # revealed, no reveal_condition
            options=[],
            outcomes={"planted": EventOutcome(outcome_id="planted")},
            default_outcome="planted",
        )

        engine, manager = _engine(world, MockLLMClient([_content()]), script=_script(event))
        tick = engine.tick(player_id=PLAYER)

        assert tick.selected is not None
        assert any(p.rule_name == "payoff_target_has_no_condition" for p in tick.proposals)
        assert manager.snapshot().story_beats.open_foreshadowings == {}

    def test_the_engine_never_writes_state_directly(self, world: WorldState, directives):
        """Every effect travels as a proposal (docs/04 §2, docs/10 §2.1)."""
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, manager = _engine(
            world, MockLLMClient([_content()]), directives, script=_script(_reveal_event())
        )
        before = manager.snapshot()
        tick = engine.tick(player_id=PLAYER)

        assert tick.proposals
        assert all(p.proposal_id for p in tick.proposals)
        assert all(p.approved for p in tick.proposals), [p.reason for p in tick.proposals]
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

        engine, _ = _engine(
            world, MockLLMClient([_content()]), directives, script=_script(_reveal_event())
        )
        engine.tick(player_id=PLAYER)

        pending = engine.pending_event
        assert pending is not None
        assert pending["dialogue_hook"]

    def test_taking_the_pending_event_clears_it(self, world: WorldState, directives):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, _ = _engine(
            world, MockLLMClient([_content()]), directives, script=_script(_reveal_event())
        )
        engine.tick(player_id=PLAYER)

        assert engine.take_pending_event() is not None
        # consumed once: a hook repeated on every later turn would read as the NPC
        # being stuck on one line
        assert engine.take_pending_event() is None

    def test_a_quiet_tick_leaves_nothing_pending(self, world: WorldState):
        world.story_beats = StoryBeats(turn=99)

        engine, _ = _engine(world, MockLLMClient([]))
        engine.tick(player_id=PLAYER)

        assert engine.pending_event is None

    def test_a_beat_whose_effects_were_all_rejected_leaves_nothing_pending(self, world: WorldState):
        """Content exists for beats that did not happen; it must not be spoken.

        Generation precedes the Validator's ruling, so a rejected beat still has a hook
        in hand. Shipping it would have the NPC allude to a loop with no payoff coming.
        The turn is recorded as ``relieve``, and the pending content has to agree.
        """
        world.story_beats = StoryBeats(turn=1)
        event = _reveal_event(
            "F_bad",
            delivery="narration",
            operator=NarrativeOperator.FORESHADOW,
            trigger=_trust_at_least(10),
            payoff_target="victim_name",
            options=[],
            outcomes={"planted": EventOutcome(outcome_id="planted")},
            default_outcome="planted",
        )
        llm = MockLLMClient([_content(dialogue_hook="一个从未被埋下的细节")])

        engine, manager = _engine(world, llm, script=_script(event))
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

        engine, _ = _engine(world, MockLLMClient([_content()]), script=_script(_reveal_event()))
        tick = engine.tick(player_id=PLAYER)

        store = NarrativeTickStore(tmp_path / "narrative")
        path = store.save(tick)

        assert path.is_file()
        assert store.load(tick.tick_id) == tick

    def test_the_tick_records_candidates_rejections_and_verdicts(self, world: WorldState):
        world.relationships[NPC_A][PLAYER].trust = 45.0
        world.story_beats = StoryBeats(turn=99)

        engine, _ = _engine(world, MockLLMClient([_content()]), script=_script(_reveal_event()))
        tick = engine.tick(player_id=PLAYER)

        assert tick.turn == 99
        assert tick.candidates
        assert tick.proposals

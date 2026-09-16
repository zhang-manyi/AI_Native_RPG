"""Free text resolving like an option, on the NPC call that already happens.

docs/13 §3 and docs/15 §1.1. Two things make this worth its own file:

**It must not cost a request.** The mapping rides on the planning call's structured
output, the same trick docs/03 §5 uses for ``player_intent_tag``. A second call to
classify one label would be a third round trip in a turn budgeted for two (docs/02 §5).

**It must be classification, not decision.** The model says "these words amount to that
option"; the option's outcome and every consequence are authored. If the model could
choose the outcome it would be choosing what gets unlocked, and docs/04 §3.3 permits no
actor to route around ``reveal_condition``.

Contract tests only, per docs/09 §5: prompt assembly, call counts, and what is refused.
Whether the classification is *accurate* is an Eval question (docs/08).
"""

from __future__ import annotations

from ai_native_rpg.agent.harness import Harness, PromptLibrary
from ai_native_rpg.agent.memory_store import MemoryStore
from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.schemas.npc_agent import NPCGoal, NPCPersona, NPCState
from ai_native_rpg.schemas.world_state import WorldState
from ai_native_rpg.world.manager import WorldStateManager

PLAYER = "player_1"
NPC_A = "npc_a"

OPTIONS = [
    {"id": "goodwill", "text": "我不是来添麻烦的"},
    {"id": "press_that_night", "text": "那晚你在外面，对吗"},
    {"id": "observe_hands", "text": "（她的手一直按着门框）"},
]


def _plan(**overrides) -> dict:
    payload = {
        "reasoning": "他说得客气，但我还是不敢开门",
        "strategy": "guarded",
        "dialogue": "……你先说你是谁。",
        "action": None,
    }
    payload.update(overrides)
    return payload


def _harness(world: WorldState, llm: MockLLMClient) -> Harness:
    npc = NPCState(
        npc_id=NPC_A,
        persona=NPCPersona(background="接生婆", traits={"fearful": 0.9}),
        goal=NPCGoal(primary="不说出那晚看见的事"),
        emotion="afraid",
    )
    return Harness(
        npc_state=npc,
        manager=WorldStateManager(world),
        llm=llm,
        memory=MemoryStore(NPC_A),
        prompts=PromptLibrary(),
    )


class TestNoExtraRequest:
    def test_classifying_costs_no_additional_call(self, world: WorldState):
        llm = MockLLMClient([_plan(matched_option_id="goodwill")])

        response, _ = _harness(world, llm).respond(
            "我只是想问几句话，不会给你添麻烦", player_id=PLAYER, event_options=OPTIONS
        )

        assert llm.call_count == 1
        assert response.matched_option_id == "goodwill"

    def test_the_field_is_optional_so_an_ordinary_turn_is_unaffected(self, world: WorldState):
        llm = MockLLMClient([_plan()])

        response, _ = _harness(world, llm).respond("你好", player_id=PLAYER)

        assert llm.call_count == 1
        assert response.matched_option_id is None


class TestThePrompt:
    def test_the_options_reach_the_planning_prompt(self, world: WorldState):
        llm = MockLLMClient([_plan()])

        _harness(world, llm).respond("我不会为难你", player_id=PLAYER, event_options=OPTIONS)

        prompt = "\n".join(m.content for m in llm.calls[0].messages)
        for option in OPTIONS:
            assert option["id"] in prompt
            assert option["text"] in prompt

    def test_no_option_block_appears_when_there_is_no_event(self, world: WorldState):
        """An empty heading is something a model tries to account for."""
        llm = MockLLMClient([_plan()])

        _harness(world, llm).respond("你好", player_id=PLAYER)

        prompt = "\n".join(m.content for m in llm.calls[0].messages)
        assert "matched_option_id" not in prompt

    def test_the_option_block_states_only_ids_and_player_facing_text(self, world: WorldState):
        """Outcomes are withheld so the model classifies rather than campaigns.

        Told that one option raises trust and another sets a flag, a model asked to label
        the player's words has a reason to prefer an answer — and those labels feed
        consequences it is not entitled to choose (docs/13 §3).

        Asserted against the option block rather than the whole prompt: the shared
        planning template legitimately names ``trust`` when it lists the action
        vocabulary, so a prompt-wide substring check would be testing the wrong text.
        """
        block = Harness._option_block(OPTIONS)

        assert block
        for option in OPTIONS:
            assert option["id"] in block
            assert option["text"] in block
        for forbidden in ("trust", "fear", "outcome", "she_opens", "probed_once"):
            assert forbidden not in block


class TestInventedLabelsAreRefused:
    def test_an_option_id_nobody_offered_is_dropped(self, world: WorldState):
        """Fails closed, and the specific harm is worth naming.

        An unrecognised id reaching ``resolve_player_response`` lands the *default*
        outcome, which usually ends the event — so a hallucinated label would not be
        merely ignored, it would close the scene on the player.
        """
        llm = MockLLMClient([_plan(matched_option_id="charm_her_completely")])

        response, _ = _harness(world, llm).respond(
            "我来帮你", player_id=PLAYER, event_options=OPTIONS
        )

        assert response.matched_option_id is None

    def test_a_label_offered_with_no_options_is_dropped(self, world: WorldState):
        llm = MockLLMClient([_plan(matched_option_id="goodwill")])

        response, _ = _harness(world, llm).respond("我来帮你", player_id=PLAYER)

        assert response.matched_option_id is None

    def test_null_stays_null_so_the_scene_continues(self, world: WorldState):
        """ "Nothing recognisable" must not resolve the event (docs/13 §3.1)."""
        llm = MockLLMClient([_plan(matched_option_id=None)])

        response, _ = _harness(world, llm).respond(
            "外面在下雨吗", player_id=PLAYER, event_options=OPTIONS
        )

        assert response.matched_option_id is None


class TestTheEngineSuppliesTheOptions:
    def test_the_active_events_options_are_exposed_as_id_and_text(self):
        from ai_native_rpg.narrative.engine import NarrativeEngine
        from ai_native_rpg.scenario import load_event_script, load_scenario

        manager = WorldStateManager(load_scenario("village_disappearance"))
        engine = NarrativeEngine(
            manager=manager,
            llm=MockLLMClient([{"summary": "s", "dialogue_hook": "h", "participants": ["玛尔塔"]}]),
            script=load_event_script("village_disappearance"),
        )
        engine.tick(player_id=PLAYER)

        options = engine.active_event_options()

        assert {o["id"] for o in options} == {
            "goodwill",
            "press_that_night",
        }
        assert all(set(o) == {"id", "text"} for o in options)

    def test_no_active_event_yields_no_options(self):
        from ai_native_rpg.narrative.engine import NarrativeEngine
        from ai_native_rpg.scenario import load_scenario

        engine = NarrativeEngine(
            manager=WorldStateManager(load_scenario("village_disappearance")),
            llm=MockLLMClient([]),
        )

        assert engine.active_event_options() == []


class TestTheContractHoldsEndToEnd:
    def test_a_classified_reply_resolves_the_event_like_a_click(self, world: WorldState):
        """The point of docs/15 §1.1: typing is not worse than pressing a button.

        If prose had no defined check while a button did, everyone would use buttons and
        the open-input path docs/03 §5 protects would die of disuse.
        """
        from ai_native_rpg.narrative.engine import NarrativeEngine
        from ai_native_rpg.scenario import load_event_script, load_scenario

        manager = WorldStateManager(load_scenario("village_disappearance"))
        engine = NarrativeEngine(
            manager=manager,
            llm=MockLLMClient([{"summary": "s", "dialogue_hook": "h", "participants": ["玛尔塔"]}]),
            script=load_event_script("village_disappearance"),
        )
        engine.tick(player_id=PLAYER)
        before = manager.get_trust(NPC_A, PLAYER)

        npc_llm = MockLLMClient([_plan(matched_option_id="goodwill")])
        world_for_npc = manager.snapshot()
        response, _ = _harness(world_for_npc, npc_llm).respond(
            "我只是想问几句，不会给你添麻烦",
            player_id=PLAYER,
            event_options=engine.active_event_options(),
        )
        record = engine.resolve_player_response(
            player_id=PLAYER, option_id=response.matched_option_id
        )

        assert record is not None
        assert record.outcome_id == "she_opens"
        assert manager.get_trust(NPC_A, PLAYER) == before + 3.0

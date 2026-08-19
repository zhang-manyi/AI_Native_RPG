"""Pending narrative content reaching the NPC's turn (docs/05 §6, docs/06 §3).

Contract tests: they assert the material is *present* in the prompts and that the
turn shape is unchanged, never that the dialogue mentions it. Whether the NPC uses
the hook well is an Eval question (docs/08).

The subtle requirement is that the hook reach **both** prompts. A no-action turn
takes the fast path and uses call #1's dialogue directly, so injecting only into
the dialogue prompt would silently drop the event on exactly the turns that are
most common.
"""

from __future__ import annotations

from ai_native_rpg.agent import Harness, MemoryStore, PlanningOutput
from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.scenario import load_personas, load_scenario
from ai_native_rpg.world import WorldStateManager

SCENARIO = "village_disappearance"
PLAYER = "player_1"

_EVENT = {
    "summary": "玛尔塔提到那晚的雨停得很突然",
    "dialogue_hook": "……那天的雨，停得比往常早。",
}


def _harness(llm: MockLLMClient) -> tuple[Harness, WorldStateManager]:
    manager = WorldStateManager(load_scenario(SCENARIO))
    martha = load_personas(SCENARIO)["npc_a"]
    harness = Harness(npc_state=martha, manager=manager, llm=llm, memory=MemoryStore(martha.npc_id))
    return harness, manager


def _prompt_text(call) -> str:
    return "\n".join(m.content for m in call.messages)


class TestFastPath:
    def test_the_hook_reaches_the_planning_prompt(self):
        """The no-action path never makes a second call, so call #1 must carry it."""
        llm = MockLLMClient([PlanningOutput(reasoning="r", strategy="s", dialogue="……雨停得早。")])
        harness, _ = _harness(llm)

        harness.respond("那晚天气怎么样？", player_id=PLAYER, narrative_event=_EVENT)

        assert _EVENT["dialogue_hook"] in _prompt_text(llm.calls[0])

    def test_the_turn_shape_is_unchanged(self):
        llm = MockLLMClient([PlanningOutput(reasoning="r", strategy="s", dialogue="d")])
        harness, _ = _harness(llm)

        response, trace = harness.respond("你好", player_id=PLAYER, narrative_event=_EVENT)

        assert llm.call_count == 1
        assert response.dialogue == "d"
        assert [s.step_name for s in trace.steps] == [
            "memory_retrieval",
            "planning",
            "reflection",
        ]


class TestActionPath:
    def test_the_hook_reaches_the_dialogue_prompt_too(self):
        # On an action turn the dialogue is regenerated under the verdict, so the
        # hook has to survive into call #2 or it is lost from the line the player
        # actually hears.
        llm = MockLLMClient(
            [
                PlanningOutput(
                    reasoning="r",
                    strategy="s",
                    dialogue="(draft)",
                    action={
                        "action_type": "adjust_relationship",
                        "target_id": PLAYER,
                        "payload": {"trust": 5},
                    },
                ),
                {"dialogue": "……那天的雨停得早，我记得。"},
            ]
        )
        harness, _ = _harness(llm)

        harness.respond("那晚天气怎么样？", player_id=PLAYER, narrative_event=_EVENT)

        assert llm.call_count == 2
        assert _EVENT["dialogue_hook"] in _prompt_text(llm.calls[1])


class TestWithoutAnEvent:
    def test_no_event_leaves_the_prompts_as_they_were(self):
        """Slice 2 behaviour is the default, not a special case.

        Most turns carry no pending content; those prompts must not grow an empty
        narrative section that the model then tries to account for.
        """
        llm = MockLLMClient([PlanningOutput(reasoning="r", strategy="s", dialogue="d")])
        harness, _ = _harness(llm)

        harness.respond("你好", player_id=PLAYER)

        prompt = _prompt_text(llm.calls[0])
        assert "剧情铺垫" not in prompt

    def test_an_empty_event_is_treated_as_no_event(self):
        # The Engine returns None on a quiet tick, but a caller passing {} should
        # not produce a section with nothing under it.
        llm = MockLLMClient([PlanningOutput(reasoning="r", strategy="s", dialogue="d")])
        harness, _ = _harness(llm)

        harness.respond("你好", player_id=PLAYER, narrative_event={})

        assert "剧情铺垫" not in _prompt_text(llm.calls[0])


class TestWhatIsPassedThrough:
    def test_developer_only_fields_are_not_sent_to_the_model(self):
        """``summary`` is for the panel; the model gets material, not notes.

        Sending the summary invites the NPC to narrate the beat ("我提到那晚的雨停得
        很突然") instead of playing it.
        """
        llm = MockLLMClient([PlanningOutput(reasoning="r", strategy="s", dialogue="d")])
        harness, _ = _harness(llm)

        harness.respond("你好", player_id=PLAYER, narrative_event=_EVENT)

        prompt = _prompt_text(llm.calls[0])
        assert _EVENT["dialogue_hook"] in prompt
        assert _EVENT["summary"] not in prompt

    def test_a_planted_secret_is_not_handed_to_the_npc(self):
        """A foreshadow's planted value is hidden world state, not NPC knowledge.

        It reaches the player through the condition table when it comes due. Putting
        it in the prompt now would make the NPC's context hold a fact the world says
        is not yet knowable.
        """
        llm = MockLLMClient([PlanningOutput(reasoning="r", strategy="s", dialogue="d")])
        harness, _ = _harness(llm)

        harness.respond(
            "你好",
            player_id=PLAYER,
            narrative_event={
                "dialogue_hook": "……地上的脚印看不清了。",
                "planted_fact_id": "prints_washed",
                "planted_value": "脚印是被人故意扫过的",
            },
        )

        prompt = _prompt_text(llm.calls[0])
        assert "脚印是被人故意扫过的" not in prompt
        assert "prints_washed" not in prompt

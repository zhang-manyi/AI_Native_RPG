"""Slice 1 delivery shape, end to end (.claude/plans/slice1-agent-harness.md):

    load_scenario + load_personas
      -> WorldStateManager + MemoryStore + MockLLMClient
      -> Harness.respond()
      -> dialogue + a Trace on disk

The per-module tests cover each link's behaviour; this file only asserts the links
actually join up — in particular that a trace produced by a real Harness turn
survives a TraceStore round-trip, which no single-module test exercises. Uses the
real scenario pack and the mock LLM, so it makes no network request.
"""

from __future__ import annotations

from ai_native_rpg.agent import Harness, MemoryStore, PlanningOutput
from ai_native_rpg.llm import MockLLMClient
from ai_native_rpg.observability import TraceStore
from ai_native_rpg.scenario import load_personas, load_scenario
from ai_native_rpg.world import WorldStateManager

SCENARIO = "village_disappearance"
PLAYER = "player_1"


def _harness(llm: MockLLMClient) -> tuple[Harness, WorldStateManager]:
    manager = WorldStateManager(load_scenario(SCENARIO))
    martha = load_personas(SCENARIO)["npc_a"]
    harness = Harness(npc_state=martha, manager=manager, llm=llm, memory=MemoryStore(martha.npc_id))
    return harness, manager


class TestFullChain:
    def test_ask_a_question_get_a_line_and_a_persisted_trace(self, tmp_path):
        llm = MockLLMClient(
            [
                PlanningOutput(
                    reasoning="她害怕，先回避", strategy="deflect", dialogue="我那晚睡得很沉。"
                )
            ],
            model="mock-model",
            token_usage={"prompt_tokens": 120, "completion_tokens": 18},
        )
        harness, _ = _harness(llm)
        store = TraceStore(tmp_path / "traces")

        response, trace = harness.respond("那晚你听到什么了吗？", player_id=PLAYER)
        path = store.save(trace)

        assert response.dialogue == "我那晚睡得很沉。"
        assert path.is_file()
        # the trace that reaches disk is the trace the turn produced
        assert store.load(trace.trace_id) == trace
        assert store.list_trace_ids() == [trace.trace_id]

    def test_persisted_trace_keeps_the_whole_decision_chain(self, tmp_path):
        # an action turn: the reveal is rejected (trust 10 << 85), so the chain has
        # every step including validation — the shape the debug panel reads.
        llm = MockLLMClient(
            [
                PlanningOutput(
                    reasoning="玩家逼问",
                    strategy="blurt_it_out",
                    dialogue="（初稿）",
                    action={"action_type": "reveal_fact", "target_id": "killer_identity"},
                ),
                {"dialogue": "我不知道你在说什么。"},
            ]
        )
        harness, manager = _harness(llm)
        store = TraceStore(tmp_path / "traces")

        _, trace = harness.respond("凶手是谁？", player_id=PLAYER)
        store.save(trace)
        loaded = store.load(trace.trace_id)

        assert [s.step_name for s in loaded.steps] == [
            "memory_retrieval",
            "planning",
            "action_validation",
            "dialogue_generation",
            "reflection",
        ]
        assert loaded.final_dialogue == "我不知道你在说什么。"
        # rejected action: the world on disk-adjacent state is untouched
        assert "killer_identity" not in manager.player_view(PLAYER).visible_facts

    def test_persisted_trace_carries_provenance_but_no_secret(self, tmp_path):
        llm = MockLLMClient(
            [PlanningOutput(reasoning="r", strategy="s", dialogue="d")],
            model="mock-model",
            token_usage={"prompt_tokens": 90, "completion_tokens": 12},
        )
        harness, _ = _harness(llm)
        store = TraceStore(tmp_path / "traces")

        _, trace = harness.respond("你好", player_id=PLAYER)
        raw = store.save(trace).read_text(encoding="utf-8")

        planning = next(s for s in trace.steps if s.step_name == "planning")
        assert planning.model_used == "mock-model"
        assert planning.token_usage == {"prompt_tokens": 90, "completion_tokens": 12}
        # docs/06 §5: only model name and token counts may reach a trace. Full
        # prompt text (which is where a key would ride along) must not.
        assert "sk-" not in raw
        assert "DEEPSEEK_API_KEY" not in raw
        # no system-prompt body from prompts/*.txt rides along in the trace
        assert "You are role-playing an NPC" not in raw

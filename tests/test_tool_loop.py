"""Contract tests for the tool-use loop wrapped around LLM call #1.

Control flow only — no assertions on generated prose (docs/09 §5). What the
architecture promises here:

  * the model may request tools before producing a plan, and each result is fed
    back so the next call can use it;
  * the loop is bounded: a model that keeps asking for tools is forced to answer
    rather than looping on a player's turn;
  * a bad tool call (unknown name, invalid arguments) is reported back to the
    model as a result, costing one iteration instead of failing the turn;
  * every tool call lands in the trace, which is where docs/08's Tool Use Success
    Rate comes from;
  * a turn that requests no tools behaves exactly as it did in slice 1.
"""

from __future__ import annotations

import pytest

from ai_native_rpg.agent import Harness, MemoryStore, PlanningOutput
from ai_native_rpg.agent.tools import build_npc_tools
from ai_native_rpg.llm import MockLLMClient, ToolCall
from ai_native_rpg.scenario import load_personas, load_scenario
from ai_native_rpg.world import WorldStateManager

SCENARIO = "village_disappearance"
PLAYER = "player_1"


@pytest.fixture
def manager() -> WorldStateManager:
    return WorldStateManager(load_scenario(SCENARIO))


@pytest.fixture
def martha():
    return load_personas(SCENARIO)["npc_a"]


def _harness(martha, manager, llm, *, memory=None, **kwargs) -> Harness:
    memory = memory or MemoryStore(martha.npc_id)
    tools = build_npc_tools(npc_id=martha.npc_id, manager=manager, memory=memory, player_id=PLAYER)
    return Harness(
        npc_state=martha,
        manager=manager,
        llm=llm,
        memory=memory,
        tools=tools,
        **kwargs,
    )


def _plan(**kwargs) -> PlanningOutput:
    base = {"reasoning": "r", "strategy": "s", "dialogue": "d"}
    return PlanningOutput(**{**base, **kwargs})


class TestToolsAreOffered:
    def test_tool_specs_are_passed_on_the_planning_call(self, martha, manager):
        llm = MockLLMClient([_plan()])
        _harness(martha, manager, llm).respond("你好", player_id=PLAYER)

        assert set(llm.calls[0].tool_names) == {
            "query_relationship",
            "query_memory",
            "check_public_fact",
        }

    def test_no_tools_offered_when_registry_absent(self, martha, manager):
        """Slice 1 behaviour: without a registry the request carries no tools."""
        llm = MockLLMClient([_plan()])
        Harness(
            npc_state=martha,
            manager=manager,
            llm=llm,
            memory=MemoryStore(martha.npc_id),
        ).respond("你好", player_id=PLAYER)

        assert llm.calls[0].tools is None


class TestSingleToolIteration:
    def test_action_dialogue_keeps_recalled_and_tool_evidence(self, martha, manager):
        memory = MemoryStore(martha.npc_id)
        from ai_native_rpg.schemas.memory import EpisodicMemory

        memory.add_episodic(
            EpisodicMemory(
                memory_id="notebook",
                npc_id=martha.npc_id,
                event_description="玩家把笔记叫做青石手记",
                importance=0.8,
                occurred_at_day=1,
            )
        )
        llm = MockLLMClient(
            [
                [
                    ToolCall(
                        id="fact", name="check_public_fact", arguments={"fact_id": "victim_name"}
                    )
                ],
                _plan(action={"action_type": "adjust_relationship", "payload": {"trust": 5}}),
                {"dialogue": "d"},
            ],
            token_usage={"prompt_tokens": 10, "completion_tokens": 2},
        )
        _, trace = _harness(
            martha, manager, llm, memory=memory, retain_dialogue_evidence=True
        ).respond("笔记名字和孩子姓名？", player_id=PLAYER)
        regeneration = llm.calls[-1].messages[-1].content
        assert "青石手记" in regeneration
        assert "艾拉" in regeneration
        assert "工具返回" in regeneration
        assert (
            sum(s.token_usage.get("prompt_tokens", 0) for s in trace.steps if s.token_usage) == 30
        )
        tool_step = next(s for s in trace.steps if s.step_name == "tool_call")
        assert tool_step.output_summary["result"]["known"] is True

    def test_result_is_fed_back_and_loop_converges(self, martha, manager):
        llm = MockLLMClient(
            [
                [ToolCall(id="c1", name="query_relationship", arguments={"target_id": PLAYER})],
                _plan(dialogue="我记得你。"),
            ]
        )
        response, _ = _harness(martha, manager, llm).respond("记得我吗", player_id=PLAYER)

        assert llm.call_count == 2
        assert response.dialogue == "我记得你。"

        # the tool result must reach the second call as a tool message
        second = llm.calls[1].messages
        tool_msgs = [m for m in second if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].tool_call_id == "c1"
        assert "trust" in tool_msgs[0].content

        # and the assistant turn that requested it must be there too, or the
        # provider rejects an orphaned tool message
        assistant_msgs = [m for m in second if m.role == "assistant" and m.tool_calls]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].tool_calls[0].id == "c1"

    def test_two_sequential_tool_iterations(self, martha, manager):
        llm = MockLLMClient(
            [
                [ToolCall(id="c1", name="query_relationship", arguments={"target_id": PLAYER})],
                [ToolCall(id="c2", name="query_memory", arguments={"query": "那晚"})],
                _plan(),
            ]
        )
        _, trace = _harness(martha, manager, llm).respond("那晚呢", player_id=PLAYER)

        assert llm.call_count == 3
        tool_steps = [s for s in trace.steps if s.step_name == "tool_call"]
        assert [s.input_summary["tool"] for s in tool_steps] == [
            "query_relationship",
            "query_memory",
        ]

    def test_parallel_tool_calls_in_one_response(self, martha, manager):
        """A provider may return several calls at once; all must be executed and
        answered, each keyed to its own id."""
        llm = MockLLMClient(
            [
                [
                    ToolCall(id="c1", name="query_relationship", arguments={"target_id": PLAYER}),
                    ToolCall(id="c2", name="query_memory", arguments={"query": "那晚"}),
                ],
                _plan(),
            ]
        )
        _, trace = _harness(martha, manager, llm).respond("q", player_id=PLAYER)

        tool_msgs = [m for m in llm.calls[1].messages if m.role == "tool"]
        assert {m.tool_call_id for m in tool_msgs} == {"c1", "c2"}
        assert len([s for s in trace.steps if s.step_name == "tool_call"]) == 2


class TestBoundedLoop:
    def test_forced_convergence_at_the_iteration_cap(self, martha, manager):
        """A model that always asks for tools must still produce a line."""
        tool_request = [ToolCall(id="c", name="query_memory", arguments={"query": "x"})]
        llm = MockLLMClient([tool_request, tool_request, tool_request, _plan(dialogue="够了。")])

        response, _ = _harness(martha, manager, llm, max_tool_iterations=3).respond(
            "q", player_id=PLAYER
        )

        # 3 tool rounds + 1 final call, and the final call must forbid more tools
        assert llm.call_count == 4
        assert llm.calls[-1].tools is None
        assert response.dialogue == "够了。"

    def test_exceeding_the_cap_does_not_raise(self, martha, manager):
        tool_request = [ToolCall(id="c", name="query_memory", arguments={"query": "x"})]
        llm = MockLLMClient([tool_request, _plan()])
        response, _ = _harness(martha, manager, llm, max_tool_iterations=1).respond(
            "q", player_id=PLAYER
        )
        assert response.dialogue == "d"


class TestToolErrorsAreRecoverable:
    def test_unknown_tool_is_reported_back_and_turn_completes(self, martha, manager):
        llm = MockLLMClient(
            [
                [ToolCall(id="c1", name="delete_world", arguments={})],
                _plan(dialogue="我不懂你的意思。"),
            ]
        )
        response, trace = _harness(martha, manager, llm).respond("q", player_id=PLAYER)

        assert llm.call_count == 2
        assert response.dialogue == "我不懂你的意思。"

        tool_msg = next(m for m in llm.calls[1].messages if m.role == "tool")
        assert "unknown tool" in tool_msg.content

        step = next(s for s in trace.steps if s.step_name == "tool_call")
        assert step.output_summary["ok"] is False

    def test_invalid_arguments_are_reported_back(self, martha, manager):
        llm = MockLLMClient(
            [
                [ToolCall(id="c1", name="query_memory", arguments={"top_k": "many"})],
                _plan(),
            ]
        )
        _, trace = _harness(martha, manager, llm).respond("q", player_id=PLAYER)

        tool_msg = next(m for m in llm.calls[1].messages if m.role == "tool")
        assert "invalid arguments" in tool_msg.content
        step = next(s for s in trace.steps if s.step_name == "tool_call")
        assert step.output_summary["ok"] is False


class TestTraceAndRegression:
    def test_tool_steps_record_name_args_and_latency(self, martha, manager):
        llm = MockLLMClient(
            [
                [ToolCall(id="c1", name="query_relationship", arguments={"target_id": PLAYER})],
                _plan(),
            ]
        )
        _, trace = _harness(martha, manager, llm).respond("q", player_id=PLAYER)

        step = next(s for s in trace.steps if s.step_name == "tool_call")
        assert step.input_summary["tool"] == "query_relationship"
        assert step.input_summary["arguments"] == {"target_id": PLAYER}
        assert step.output_summary["ok"] is True
        assert step.latency_ms >= 0.0
        # deterministic step: no model, no tokens
        assert step.model_used is None

    def test_tool_steps_precede_planning_in_the_trace(self, martha, manager):
        llm = MockLLMClient(
            [[ToolCall(id="c1", name="query_memory", arguments={"query": "x"})], _plan()]
        )
        _, trace = _harness(martha, manager, llm).respond("q", player_id=PLAYER)

        names = [s.step_name for s in trace.steps]
        assert names.index("tool_call") < names.index("planning")

    def test_no_tool_turn_matches_slice1_exactly(self, martha, manager):
        """Regression: offering tools must not change a turn that ignores them."""
        llm = MockLLMClient([_plan(dialogue="你想问什么？")])
        response, trace = _harness(martha, manager, llm).respond("闲聊", player_id=PLAYER)

        assert llm.call_count == 1
        assert response.dialogue == "你想问什么？"
        assert [s.step_name for s in trace.steps] == [
            "memory_retrieval",
            "planning",
            "reflection",
        ]

    def test_tools_still_work_on_an_action_turn(self, martha, manager):
        """A tool round, then an action, then dialogue regenerated post-validation."""
        llm = MockLLMClient(
            [
                [ToolCall(id="c1", name="query_relationship", arguments={"target_id": PLAYER})],
                _plan(
                    action={
                        "action_type": "adjust_relationship",
                        "target_id": PLAYER,
                        "payload": {"trust": 5},
                    }
                ),
                {"dialogue": "好吧。"},
            ]
        )
        response, trace = _harness(martha, manager, llm).respond("q", player_id=PLAYER)

        assert llm.call_count == 3
        assert response.dialogue == "好吧。"
        assert [s.step_name for s in trace.steps] == [
            "memory_retrieval",
            "tool_request",
            "tool_call",
            "planning",
            "action_validation",
            "dialogue_generation",
            "reflection",
        ]

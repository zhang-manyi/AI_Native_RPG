"""Tests for TraceStore: round-trip persistence of AgentTrace as JSON."""

from __future__ import annotations

from ai_native_rpg.observability import TraceStore
from ai_native_rpg.schemas.agent_trace import AgentTrace, TraceStep


def _trace(trace_id: str = "t1") -> AgentTrace:
    return AgentTrace(
        trace_id=trace_id,
        npc_id="npc_a",
        player_id="player_1",
        session_id="s1",
        steps=[
            TraceStep(step_name="memory_retrieval", latency_ms=8.0),
            TraceStep(
                step_name="planning",
                model_used="mock-model",
                latency_ms=12.0,
                token_usage={"prompt_tokens": 100, "completion_tokens": 20},
            ),
        ],
        total_latency_ms=20.0,
        final_dialogue="我那晚睡得很沉。",
    )


class TestTraceStore:
    def test_save_creates_directory_and_returns_path(self, tmp_path):
        store = TraceStore(tmp_path / "traces")
        path = store.save(_trace())
        assert path.is_file()
        assert path.parent.name == "traces"

    def test_round_trips(self, tmp_path):
        store = TraceStore(tmp_path / "traces")
        original = _trace()
        store.save(original)
        loaded = store.load("t1")
        assert loaded == original
        assert loaded.steps[1].model_used == "mock-model"
        assert loaded.final_dialogue == "我那晚睡得很沉。"

    def test_lists_saved_trace_ids(self, tmp_path):
        store = TraceStore(tmp_path / "traces")
        store.save(_trace("a"))
        store.save(_trace("b"))
        assert store.list_trace_ids() == ["a", "b"]

    def test_list_on_missing_dir_is_empty(self, tmp_path):
        assert TraceStore(tmp_path / "nope").list_trace_ids() == []

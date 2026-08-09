"""Tests for the LLM seam's tool-call vocabulary.

Slice 1's ``Message`` covered system/user/assistant and a single parsed result.
Function calling needs two more shapes on the same seam: an assistant turn that
*requests* tools, and a ``tool`` turn carrying a result back. These stay
provider-neutral — DeepSeek's ``function.arguments`` arrives as a JSON *string*,
and unpacking it into a dict is the client's job, not the Harness's, so nothing
above the seam has to know the wire format.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from ai_native_rpg.llm import LLMResponse, Message, ToolCall


class Plan(BaseModel):
    strategy: str


class TestToolCall:
    def test_carries_name_and_parsed_arguments(self):
        call = ToolCall(id="call_1", name="query_relationship", arguments={"target_id": "player_1"})
        assert call.arguments["target_id"] == "player_1"

    def test_arguments_default_to_empty(self):
        """A no-argument tool is legitimate; the model may send ``{}`` or omit it."""
        assert ToolCall(id="c", name="query_memory").arguments == {}

    def test_round_trips(self):
        call = ToolCall(id="c", name="t", arguments={"a": 1, "b": ["x"]})
        assert ToolCall.model_validate_json(call.model_dump_json()) == call


class TestMessageRoles:
    def test_tool_role_is_allowed(self):
        msg = Message(role="tool", content="trust=10", tool_call_id="call_1")
        assert msg.role == "tool"
        assert msg.tool_call_id == "call_1"

    def test_assistant_can_request_tools(self):
        msg = Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call_1", name="query_memory", arguments={"query": "那晚"})],
        )
        assert msg.tool_calls is not None
        assert msg.tool_calls[0].name == "query_memory"

    def test_slice1_messages_still_construct_unchanged(self):
        """Backward compatibility: the two-field form used throughout slice 1."""
        msg = Message(role="system", content="persona")
        assert msg.tool_calls is None
        assert msg.tool_call_id is None

    def test_tool_message_requires_a_call_id(self):
        """A tool result the model cannot match to its request is unusable — the
        wire format keys them by id, so an unkeyed tool turn is a bug."""
        with pytest.raises(ValidationError):
            Message(role="tool", content="trust=10")

    def test_unknown_role_rejected(self):
        with pytest.raises(ValidationError):
            Message(role="function", content="x")


class TestLLMResponse:
    def test_parsed_result_without_tool_calls(self):
        resp = LLMResponse(parsed=Plan(strategy="avoid"), model="m")
        assert resp.tool_calls == []
        assert resp.wants_tools is False

    def test_tool_request_without_parsed_result(self):
        """When the model asks for a tool there is no final answer yet, so
        ``parsed`` is legitimately absent."""
        resp = LLMResponse(
            model="m",
            tool_calls=[ToolCall(id="c", name="query_relationship")],
        )
        assert resp.parsed is None
        assert resp.wants_tools is True

    def test_response_must_carry_something(self):
        """Neither a parsed result nor a tool request means the turn produced
        nothing — silently returning that would strand the Harness's loop."""
        with pytest.raises(ValidationError):
            LLMResponse(model="m")

    def test_slice1_construction_still_valid(self):
        resp = LLMResponse(parsed=Plan(strategy="x"), model="mock-model", token_usage={"p": 1})
        assert resp.parsed.strategy == "x"
        assert resp.token_usage == {"p": 1}

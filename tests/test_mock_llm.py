"""Contract tests for the LLM seam: MockLLMClient plays a script, parses into the
requested schema, and records what it was asked. No network, ever."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from ai_native_rpg.llm import Message, MockLLMClient, MockLLMError


class Plan(BaseModel):
    strategy: str


class Dialogue(BaseModel):
    text: str


class TestScriptPlayback:
    def test_returns_scripted_responses_in_order(self):
        client = MockLLMClient([Plan(strategy="avoid"), Dialogue(text="我不知道。")])

        first = client.complete([Message(role="user", content="?")], schema=Plan)
        second = client.complete([Message(role="user", content="?")], schema=Dialogue)

        assert isinstance(first.parsed, Plan)
        assert first.parsed.strategy == "avoid"
        assert isinstance(second.parsed, Dialogue)
        assert second.parsed.text == "我不知道。"

    def test_accepts_dict_payloads_and_validates_them(self):
        client = MockLLMClient([{"strategy": "reveal"}])
        resp = client.complete([Message(role="user", content="?")], schema=Plan)
        assert resp.parsed.strategy == "reveal"

    def test_exhausted_script_raises(self):
        client = MockLLMClient([Plan(strategy="x")])
        client.complete([Message(role="user", content="?")], schema=Plan)
        with pytest.raises(MockLLMError, match="exhausted"):
            client.complete([Message(role="user", content="?")], schema=Plan)

    def test_payload_not_matching_schema_raises(self):
        client = MockLLMClient([{"unexpected_field": 1}])
        with pytest.raises(MockLLMError, match="does not fit requested schema"):
            client.complete([Message(role="user", content="?")], schema=Plan)


class TestRecording:
    def test_records_messages_schema_and_temperature(self):
        client = MockLLMClient([Plan(strategy="x")])
        msgs = [Message(role="system", content="persona"), Message(role="user", content="q")]
        client.complete(msgs, schema=Plan, temperature=0.2)

        assert client.call_count == 1
        call = client.calls[0]
        assert [m.role for m in call.messages] == ["system", "user"]
        assert call.schema_name == "Plan"
        assert call.temperature == pytest.approx(0.2)

    def test_reports_model_and_token_usage(self):
        client = MockLLMClient(
            [Plan(strategy="x")], model="mock-v2", token_usage={"prompt_tokens": 5}
        )
        resp = client.complete([Message(role="user", content="?")], schema=Plan)
        assert resp.model == "mock-v2"
        assert resp.token_usage["prompt_tokens"] == 5

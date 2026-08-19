"""Contract tests for OpenAICompatibleClient, served entirely by httpx.MockTransport.

No network: every request is answered by a local handler, which is stronger than
mocking our own code — the request that would go on the wire is built for real
and then asserted on. What matters here is the contract, not DeepSeek's prose:

  * the request carries JSON mode, the schema, and any tools;
  * a tool-call response becomes ``ToolCall`` objects with decoded arguments;
  * malformed JSON is retried once with the error appended, because a reparse is
    cheaper than failing a player's turn;
  * 429/5xx are retried with backoff and give up after a bounded number of tries;
  * the API key reaches the Authorization header and nothing else — not a
    traceback, not a repr, not an error message.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from ai_native_rpg.llm.base import Message
from ai_native_rpg.llm.openai_compatible import LLMAPIError, OpenAICompatibleClient

KEY = "sk-test-do-not-log-me"


class Plan(BaseModel):
    strategy: str
    dialogue: str


def _chat_response(content: str, *, usage: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chat-1",
            "model": "deepseek-v4-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": usage or {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
        },
    )


def _tool_call_response(name: str, arguments: str, *, call_id: str = "call_abc") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chat-2",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5},
        },
    )


def _client(handler, **kwargs) -> OpenAICompatibleClient:
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleClient(
        api_key=SecretStr(KEY),
        base_url="https://api.deepseek.test",
        model="deepseek-v4-flash",
        transport=transport,
        # keep retry tests fast; behaviour under test is the retry, not the wait
        backoff_base=0.0,
        **kwargs,
    )


def _ask(client: OpenAICompatibleClient, **kwargs):
    return client.complete(
        [Message(role="user", content="你那晚看到什么了？")], schema=Plan, **kwargs
    )


class TestRequestShape:
    def test_sends_json_mode_schema_and_auth(self):
        seen: list[httpx.Request] = []

        def handler(request):
            seen.append(request)
            return _chat_response('{"strategy": "avoid", "dialogue": "我睡了。"}')

        _ask(_client(handler))

        assert len(seen) == 1
        request = seen[0]
        assert request.url.path == "/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {KEY}"

        body = json.loads(request.content)
        assert body["model"] == "deepseek-v4-flash"
        assert body["response_format"] == {"type": "json_object"}
        # JSON mode guarantees valid JSON, not the right fields, so the schema
        # itself must travel in the prompt.
        prompt = " ".join(m["content"] for m in body["messages"] if m["content"])
        assert "strategy" in prompt
        assert "dialogue" in prompt

    def test_omits_tools_when_none_given(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return _chat_response('{"strategy": "s", "dialogue": "d"}')

        _ask(_client(handler))
        assert "tools" not in seen[0]

    def test_forwards_tools_and_temperature(self):
        seen = []
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "query_relationship",
                    "description": "look up trust",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        def handler(request):
            seen.append(json.loads(request.content))
            return _chat_response('{"strategy": "s", "dialogue": "d"}')

        _ask(_client(handler), tools=tools, temperature=0.3)

        assert seen[0]["tools"] == tools
        assert seen[0]["temperature"] == pytest.approx(0.3)


class TestResponseParsing:
    def test_parses_content_into_schema(self):
        def handler(request):
            return _chat_response('{"strategy": "deflect", "dialogue": "我不知道。"}')

        resp = _ask(_client(handler))

        assert isinstance(resp.parsed, Plan)
        assert resp.parsed.strategy == "deflect"
        assert resp.model == "deepseek-v4-flash"
        assert resp.token_usage == {
            "prompt_tokens": 30,
            "completion_tokens": 12,
            "total_tokens": 42,
        }
        assert resp.wants_tools is False

    def test_tolerates_json_wrapped_in_code_fences(self):
        """Models fence JSON even in JSON mode; salvaging it is cheaper than a retry."""

        def handler(request):
            return _chat_response('```json\n{"strategy": "s", "dialogue": "d"}\n```')

        assert _ask(_client(handler)).parsed.strategy == "s"

    def test_parses_tool_calls_with_decoded_arguments(self):
        def handler(request):
            return _tool_call_response("query_relationship", '{"target_id": "player_1"}')

        resp = _ask(_client(handler))

        assert resp.wants_tools is True
        assert resp.parsed is None
        assert len(resp.tool_calls) == 1
        call = resp.tool_calls[0]
        assert call.id == "call_abc"
        assert call.name == "query_relationship"
        # decoded here, so nothing above the seam sees a JSON string
        assert call.arguments == {"target_id": "player_1"}

    def test_tool_call_with_empty_arguments(self):
        def handler(request):
            return _tool_call_response("query_memory", "")

        assert _ask(_client(handler)).tool_calls[0].arguments == {}


class TestReparseRetry:
    def test_retries_once_on_unparseable_content_and_succeeds(self):
        calls = []

        def handler(request):
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                return _chat_response("sorry, I can't do JSON")
            return _chat_response('{"strategy": "s", "dialogue": "d"}')

        resp = _ask(_client(handler))

        assert len(calls) == 2
        assert resp.parsed.strategy == "s"
        # the retry must tell the model what was wrong, else it repeats itself
        retry_prompt = " ".join(m["content"] for m in calls[1]["messages"] if m["content"])
        assert "JSON" in retry_prompt or "json" in retry_prompt

    def test_retries_when_json_is_valid_but_schema_is_not(self):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return _chat_response('{"wrong_field": 1}')
            return _chat_response('{"strategy": "s", "dialogue": "d"}')

        assert _ask(_client(handler)).parsed.dialogue == "d"
        assert len(calls) == 2

    def test_gives_up_after_the_reparse_retry(self):
        calls = []

        def handler(request):
            calls.append(request)
            return _chat_response("still not json")

        with pytest.raises(LLMAPIError, match="schema"):
            _ask(_client(handler))

        # one original + one reparse: not an unbounded loop on a player's turn
        assert len(calls) == 2


class TestTransportRetries:
    def test_retries_on_429_then_succeeds(self):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, json={"error": "rate limited"})
            return _chat_response('{"strategy": "s", "dialogue": "d"}')

        assert _ask(_client(handler)).parsed.strategy == "s"
        assert len(calls) == 2

    def test_retries_on_500_then_succeeds(self):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) < 3:
                return httpx.Response(503, json={"error": "unavailable"})
            return _chat_response('{"strategy": "s", "dialogue": "d"}')

        assert _ask(_client(handler)).parsed.dialogue == "d"
        assert len(calls) == 3

    def test_gives_up_after_max_attempts(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(503, json={"error": "unavailable"})

        with pytest.raises(LLMAPIError):
            _ask(_client(handler, max_attempts=3))

        assert len(calls) == 3

    def test_does_not_retry_on_401(self):
        """A bad key will still be bad on attempt three; retrying only wastes time."""
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(401, json={"error": "invalid key"})

        with pytest.raises(LLMAPIError):
            _ask(_client(handler))

        assert len(calls) == 1

    def test_retries_on_timeout_then_succeeds(self):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ReadTimeout("too slow", request=request)
            return _chat_response('{"strategy": "s", "dialogue": "d"}')

        assert _ask(_client(handler)).parsed.strategy == "s"
        assert len(calls) == 2

    def test_timeout_exhausted_raises(self):
        def handler(request):
            raise httpx.ReadTimeout("too slow", request=request)

        with pytest.raises(LLMAPIError, match=r"timed out|timeout"):
            _ask(_client(handler, max_attempts=2))


class TestSecretNeverLeaks:
    def test_key_absent_from_repr(self):
        def handler(request):
            return _chat_response('{"strategy": "s", "dialogue": "d"}')

        client = _client(handler)
        assert KEY not in repr(client)

    @pytest.mark.parametrize("status", [401, 429, 503])
    def test_key_absent_from_error_messages(self, status):
        def handler(request):
            return httpx.Response(status, json={"error": "boom"})

        client = _client(handler, max_attempts=2)
        with pytest.raises(LLMAPIError) as exc:
            _ask(client)

        assert KEY not in str(exc.value)
        assert KEY not in repr(exc.value)

    def test_key_absent_from_parse_failure_message(self):
        """The failure path that quotes model output must not quote the request."""

        def handler(request):
            return _chat_response("not json at all")

        with pytest.raises(LLMAPIError) as exc:
            _ask(_client(handler))

        assert KEY not in str(exc.value)

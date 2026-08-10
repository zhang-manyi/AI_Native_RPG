"""DeepSeekClient: the real backend behind the ``LLMClient`` Protocol.

Talks the OpenAI-compatible ``chat/completions`` surface over ``httpx`` rather
than through a vendor SDK, because docs/06_NPC_Agent_Spec.md#5 makes the Protocol
the only seam and ``httpx`` is already a dependency. The practical payoff is
testability: ``httpx.MockTransport`` lets the whole request/retry/parse path run
offline against the bytes that would really go on the wire.

Three behaviours are deliberate:

  * **Schema in the prompt.** JSON mode guarantees syntactically valid JSON, not
    the *right* JSON, so the requested schema travels in a system message and the
    result is validated locally.
  * **One reparse retry.** A malformed payload is re-requested once with the
    validation error attached. A player is already waiting, so the choice is
    between one extra call and a failed turn — but only one, never a loop.
  * **Bounded transport retries.** 429/5xx/timeouts back off and retry; 4xx like
    401 do not, because a rejected key stays rejected.

The API key lives in a ``SecretStr`` and appears only in the Authorization
header — never in ``repr``, never in an exception message (docs/06 §5).
"""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from .base import LLMResponse, Message, ToolCall

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE = 0.5

#: Statuses worth retrying: rate limits and transient server faults. Everything
#: else (401 bad key, 400 bad request) will fail identically on a retry.
_RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

_SCHEMA_INSTRUCTION = (
    "Reply with a single JSON object and nothing else — no prose, no code fence.\n"
    "It must validate against this JSON Schema:\n{schema}"
)


class DeepSeekAPIError(RuntimeError):
    """A request failed after exhausting retries, or its payload never fit the
    requested schema. Never carries the API key."""


def _strip_fence(text: str) -> str:
    """Unwrap ```json ... ``` fencing.

    Models fence JSON even when asked not to. Salvaging it costs one regex; the
    alternative is spending a whole extra round trip on a formatting habit.
    """
    match = _FENCE.match(text)
    return match.group(1) if match else text


class DeepSeekClient:
    """OpenAI-compatible chat client for DeepSeek.

    ``transport`` exists for tests: passing ``httpx.MockTransport`` keeps the
    entire path offline while still exercising real request construction.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = backoff_base
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            headers={
                # The one and only place the key is materialised.
                "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
        )

    def __repr__(self) -> str:
        # No key: a repr can reach a log line or a pytest failure dump.
        return f"DeepSeekClient(base_url={self._base_url!r}, model={self._model!r})"

    @property
    def model(self) -> str:
        return self._model

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DeepSeekClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- public API --------------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel],
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Run one completion, returning parsed output or a tool-call request."""
        payload_messages = self._wire_messages(messages, schema=schema)
        raw = self._post_with_retries(payload_messages, temperature=temperature, tools=tools)

        tool_calls = self._extract_tool_calls(raw)
        if tool_calls:
            # The model wants data before answering; there is nothing to parse yet.
            return LLMResponse(
                model=raw.get("model", self._model),
                token_usage=self._usage(raw),
                tool_calls=tool_calls,
            )

        content = self._content(raw)
        try:
            parsed = self._parse(content, schema)
        except DeepSeekAPIError as first_error:
            # One reparse, with the failure quoted back so the model can correct
            # itself rather than repeat the same mistake.
            retry_messages = [
                *payload_messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "That was not valid JSON for the required schema "
                        f"({first_error}). Reply with only the JSON object."
                    ),
                },
            ]
            raw = self._post_with_retries(retry_messages, temperature=temperature, tools=tools)
            parsed = self._parse(self._content(raw), schema)

        return LLMResponse(
            parsed=parsed,
            model=raw.get("model", self._model),
            token_usage=self._usage(raw),
        )

    # --- request construction ----------------------------------------------

    def _wire_messages(
        self, messages: list[Message], *, schema: type[BaseModel]
    ) -> list[dict[str, Any]]:
        """Convert neutral ``Message`` objects to the OpenAI wire shape, appending
        the schema instruction."""
        wire: list[dict[str, Any]] = []
        for msg in messages:
            item: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in msg.tool_calls
                ]
            if msg.tool_call_id:
                item["tool_call_id"] = msg.tool_call_id
            wire.append(item)

        wire.append(
            {
                "role": "system",
                "content": _SCHEMA_INSTRUCTION.format(
                    schema=json.dumps(schema.model_json_schema(), ensure_ascii=False)
                ),
            }
        )
        return wire

    def _post_with_retries(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if tools:
            body["tools"] = tools

        last_error: str = "no attempt was made"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post("/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                last_error = f"request timed out after {self._timeout}s ({type(exc).__name__})"
            except httpx.HTTPError as exc:
                # Only the exception type: an httpx message can echo the URL and
                # in some cases request headers.
                last_error = f"transport error ({type(exc).__name__})"
            else:
                if response.status_code == 200:
                    return response.json()
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in _RETRY_STATUSES:
                    # Deterministic failure (bad key, bad request): retrying just
                    # delays the error.
                    raise DeepSeekAPIError(f"DeepSeek request failed: {last_error}")

            if attempt < self._max_attempts:
                self._sleep_backoff(attempt)

        raise DeepSeekAPIError(
            f"DeepSeek request failed after {self._max_attempts} attempts: {last_error}"
        )

    def _sleep_backoff(self, attempt: int) -> None:
        if self._backoff_base <= 0:
            return
        # Exponential with jitter, so concurrent NPC turns do not retry in lockstep.
        delay = self._backoff_base * (2 ** (attempt - 1))
        time.sleep(delay * (0.5 + random.random() / 2))  # jitter, not cryptographic

    # --- response handling -------------------------------------------------

    @staticmethod
    def _message(raw: dict[str, Any]) -> dict[str, Any]:
        choices = raw.get("choices") or []
        if not choices:
            raise DeepSeekAPIError("DeepSeek response contained no choices")
        return choices[0].get("message") or {}

    def _content(self, raw: dict[str, Any]) -> str:
        return self._message(raw).get("content") or ""

    def _extract_tool_calls(self, raw: dict[str, Any]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for item in self._message(raw).get("tool_calls") or []:
            function = item.get("function") or {}
            raw_args = function.get("arguments") or ""
            try:
                # DeepSeek sends arguments as a JSON *string*; decoding it here
                # keeps the wire format below the seam.
                arguments = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                # A malformed argument blob is the tool layer's problem to report
                # back to the model, not a reason to fail the turn here.
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            calls.append(
                ToolCall(
                    id=item.get("id") or "",
                    name=function.get("name") or "",
                    arguments=arguments,
                )
            )
        return calls

    @staticmethod
    def _usage(raw: dict[str, Any]) -> dict[str, int]:
        usage = raw.get("usage") or {}
        return {k: v for k, v in usage.items() if isinstance(v, int)}

    @staticmethod
    def _parse(content: str, schema: type[BaseModel]) -> BaseModel:
        text = _strip_fence(content)
        if not text.strip():
            raise DeepSeekAPIError(f"empty response body, expected {schema.__name__} schema")
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            # Quote only the model's own output and the error; never the request.
            raise DeepSeekAPIError(
                f"response did not match the {schema.__name__} schema: "
                f"{exc.error_count()} validation error(s); got {text[:200]!r}"
            ) from exc

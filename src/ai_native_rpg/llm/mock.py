"""MockLLMClient: scripted, offline, deterministic structured output.

Every test uses this instead of a real backend, so the Harness's loop, JSON
parsing and branching are all unit-testable with no network request. It plays
back a pre-set script of responses in order (Planning first, Dialogue second in a
two-call turn) and records the messages it received so tests can assert how the
prompt was assembled without asserting on generated prose.

The mock is intentionally strict: an exhausted script or a scripted payload that
does not fit the requested schema raises, because a silent wrong-shape response
is exactly the failure a real client's parsing must also surface.
"""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from .base import LLMClient, LLMResponse, Message


class MockLLMError(RuntimeError):
    """The mock was asked for more responses than scripted, or a scripted payload
    did not match the schema the caller requested."""


class RecordedCall(BaseModel):
    """What the Harness sent on one ``complete`` call, kept for assertions."""

    messages: list[Message]
    schema_name: str
    temperature: float

    model_config = {"arbitrary_types_allowed": True}


class MockLLMClient(LLMClient):
    """Plays back ``responses`` in order.

    Each scripted response may be a ``BaseModel`` instance or a plain ``dict``; in
    both cases it is re-validated against the schema the caller passed to
    ``complete``, so the script cannot smuggle in a shape the real contract would
    reject.
    """

    def __init__(
        self,
        responses: list[BaseModel | dict],
        *,
        model: str = "mock-model",
        token_usage: dict[str, int] | None = None,
    ) -> None:
        self._responses = list(responses)
        self._cursor = 0
        self._model = model
        self._token_usage = token_usage or {"prompt_tokens": 0, "completion_tokens": 0}
        self.calls: list[RecordedCall] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel],
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.calls.append(
            RecordedCall(
                messages=list(messages), schema_name=schema.__name__, temperature=temperature
            )
        )

        if self._cursor >= len(self._responses):
            raise MockLLMError(
                f"MockLLMClient script exhausted: asked for response #{self._cursor + 1} "
                f"but only {len(self._responses)} were scripted"
            )

        raw = self._responses[self._cursor]
        self._cursor += 1

        payload = raw.model_dump() if isinstance(raw, BaseModel) else raw
        try:
            parsed = schema.model_validate(payload)
        except ValidationError as exc:
            raise MockLLMError(
                f"scripted response #{self._cursor} does not fit requested schema "
                f"{schema.__name__}: {exc}"
            ) from exc

        return LLMResponse(parsed=parsed, model=self._model, token_usage=dict(self._token_usage))

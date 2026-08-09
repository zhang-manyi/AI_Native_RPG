"""LLM Client Protocol: the single seam between the Agent and any model.

The Harness accesses models only through ``LLMClient``, never a vendor SDK
directly. Two implementations sit behind it: ``DeepSeekClient`` (OpenAI-compatible,
added in slice 2) and ``MockLLMClient`` (returns scripted structured output, used
by every test so no network request is ever made). This is also the future hook
for a Model Router. See docs/06_NPC_Agent_Spec.md#5.

The contract is *structured output*: the caller passes the Pydantic ``schema`` it
expects back, and the client returns an instance of it already parsed. This keeps
JSON-mode parsing, retries and timeouts on the client side of the seam where they
can be unit-tested against the mock.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    """A tool the model asked to run, provider-neutral.

    ``arguments`` is a decoded dict: DeepSeek sends ``function.arguments`` as a
    JSON *string*, and unpacking it belongs on the client side of the seam so the
    Harness never touches a wire format. ``id`` must be echoed back on the
    matching ``tool`` message.
    """

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """One chat message. Provider-neutral; the client maps it to its wire format.

    Four shapes travel over this seam: ``system``/``user`` prompts, an
    ``assistant`` turn that either answers or requests tools via ``tool_calls``,
    and a ``tool`` turn returning one result keyed by ``tool_call_id``.
    """

    role: Role
    content: str
    tool_calls: list[ToolCall] | None = Field(
        default=None, description="set on an assistant turn that requests tools"
    )
    tool_call_id: str | None = Field(
        default=None, description="required on a tool turn; echoes ToolCall.id"
    )

    @model_validator(mode="after")
    def _tool_results_are_keyed(self) -> Message:
        # A tool result the model cannot match to its request is unusable: the
        # wire protocol pairs them by id.
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("a message with role='tool' requires tool_call_id")
        return self


class LLMResponse(BaseModel):
    """A model response: either a payload parsed into the requested schema, or a
    request to run tools.

    ``model`` and ``token_usage`` are the only cost/provenance data that may reach
    a Trace — never the API key or raw secret-bearing prompt text
    (docs/06_NPC_Agent_Spec.md#5).
    """

    parsed: BaseModel | None = None
    model: str
    token_usage: dict[str, int] = Field(default_factory=dict)
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @model_validator(mode="after")
    def _carries_something(self) -> LLMResponse:
        # A turn with neither an answer nor a tool request produced nothing;
        # returning it would strand the Harness's loop with no way forward.
        if self.parsed is None and not self.tool_calls:
            raise ValueError("LLMResponse needs either parsed output or tool_calls")
        return self

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMClient(Protocol):
    """The only interface the Harness knows about.

    ``schema`` is the Pydantic model the response must conform to; the client is
    responsible for producing an instance of it (via JSON mode / structured
    output) or raising if it cannot after its own retries. When ``tools`` is
    passed the model may instead return ``tool_calls``, which the caller executes
    and feeds back as ``tool`` messages.
    """

    def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel],
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

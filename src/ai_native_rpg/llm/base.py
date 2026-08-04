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

from typing import Literal, Protocol

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    """One chat message. Provider-neutral; the client maps it to its wire format."""

    role: Role
    content: str


class LLMResponse(BaseModel):
    """A model response whose payload has already been parsed into the requested
    schema.

    ``model`` and ``token_usage`` are the only cost/provenance data that may reach
    a Trace — never the API key or raw secret-bearing prompt text
    (docs/06_NPC_Agent_Spec.md#5).
    """

    parsed: BaseModel
    model: str
    token_usage: dict[str, int] = Field(default_factory=dict)


class LLMClient(Protocol):
    """The only interface the Harness knows about.

    ``schema`` is the Pydantic model the response must conform to; the client is
    responsible for producing an instance of it (via JSON mode / structured
    output) or raising if it cannot after its own retries.
    """

    def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel],
        temperature: float = 0.7,
    ) -> LLMResponse: ...

"""LLM access layer. The Harness depends only on the ``LLMClient`` Protocol here,
never on a vendor SDK. See docs/06_NPC_Agent_Spec.md#5.
"""

from .base import LLMClient, LLMResponse, Message, Role, ToolCall
from .mock import MockLLMClient, MockLLMError

__all__ = [
    "LLMClient",
    "LLMResponse",
    "Message",
    "MockLLMClient",
    "MockLLMError",
    "Role",
    "ToolCall",
]

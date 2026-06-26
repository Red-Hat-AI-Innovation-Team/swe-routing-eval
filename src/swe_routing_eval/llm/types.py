"""Provider-agnostic types for the LLMClient abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class ToolDef:
    """Provider-agnostic tool definition."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass
class ToolCall:
    """A tool invocation returned by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result of executing a tool call."""

    tool_call_id: str
    content: str


@dataclass
class Message:
    """Single message in a conversation.

    Dispatch on ``role`` + which optional fields are populated:
    - role="system"    content only
    - role="user"      content only, OR tool_results only
    - role="assistant" content and/or tool_calls
    """

    role: Literal["system", "user", "assistant"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_results: list[ToolResult] | None = None


@dataclass
class LLMResponse:
    """Normalized response from any provider."""

    content: str | None
    tool_calls: list[ToolCall] | None = None
    stop_reason: str | None = None
    finished: bool = False
    tokens_in: int = 0
    tokens_out: int = 0

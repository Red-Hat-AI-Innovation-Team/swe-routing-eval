"""AnthropicVertex LLMClient implementation."""

from __future__ import annotations

from typing import Any

import anthropic
from anthropic.types import ToolUseBlock

from swe_routing_eval.llm.base import LLMClient
from swe_routing_eval.llm.types import LLMResponse, Message, ToolCall, ToolDef


class AnthropicVertexClient(LLMClient):
    """LLMClient backed by Anthropic's Vertex AI Model Garden."""

    def __init__(self, project_id: str, region: str) -> None:
        self._client = anthropic.AnthropicVertex(
            project_id=project_id,
            region=region,
        )

    def chat(
        self,
        model_id: str,
        messages: list[Message],
        tools: list[ToolDef],
        max_tokens: int,
    ) -> LLMResponse:
        system, api_messages = _split_system(messages)
        response = self._client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=system or anthropic.NOT_GIVEN,
            tools=[_tool_def_to_anthropic(td) for td in tools],  # type: ignore[misc]
            messages=api_messages,  # type: ignore[arg-type]
        )
        return _parse_response(response)


# -- message translation -----------------------------------------------------


def _split_system(
    messages: list[Message],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Extract system content and translate remaining messages to Anthropic format."""
    system_parts: list[str] = []
    api_messages: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content)
        else:
            api_messages.append(_message_to_anthropic(msg))
    return "\n\n".join(system_parts), api_messages


def _message_to_anthropic(msg: Message) -> dict[str, Any]:
    """Translate a single non-system Message to Anthropic dict."""
    # assistant with tool calls
    if msg.role == "assistant" and msg.tool_calls:
        content_blocks: list[dict[str, Any]] = []
        if msg.content:
            content_blocks.append({"type": "text", "text": msg.content})
        for tc in msg.tool_calls:
            content_blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.arguments,
            })
        return {"role": "assistant", "content": content_blocks}

    # user with tool results
    if msg.role == "user" and msg.tool_results:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r.tool_call_id,
                    "content": r.content,
                }
                for r in msg.tool_results
            ],
        }

    # plain text (user or assistant)
    return {"role": msg.role, "content": msg.content or ""}


# -- tool translation ---------------------------------------------------------


def _tool_def_to_anthropic(td: ToolDef) -> dict[str, Any]:
    """Translate ToolDef to Anthropic tool format."""
    return {
        "name": td.name,
        "description": td.description,
        "input_schema": td.parameters,
    }


# -- response parsing --------------------------------------------------------


def _parse_response(response: anthropic.types.Message) -> LLMResponse:
    """Normalize an Anthropic response into LLMResponse."""
    content_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    for block in response.content:
        if isinstance(block, ToolUseBlock):
            arguments = block.input if isinstance(block.input, dict) else {}
            tool_calls.append(ToolCall(
                id=block.id,
                name=block.name,
                arguments=arguments,
            ))
        elif hasattr(block, "text"):
            content_parts.append(block.text)

    content = "\n".join(content_parts) if content_parts else None

    return LLMResponse(
        content=content,
        tool_calls=tool_calls if tool_calls else None,
        finished=response.stop_reason == "end_turn",
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
    )

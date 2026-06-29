"""OpenAI chat completions LLMClient implementation."""

from __future__ import annotations

import json
from typing import Any

import openai
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall

from swe_routing_eval.llm.base import LLMClient
from swe_routing_eval.llm.types import LLMResponse, Message, ToolCall, ToolDef
from swe_routing_eval.openai_config import OpenAIConfig


class OpenAIClient(LLMClient):
    """LLMClient backed by OpenAI chat completions API.

    Compatible with any OpenAI-compatible endpoint (DeepSeek, vLLM, Azure, etc.)
    via ``OpenAIConfig.base_url``.
    """

    def __init__(self, config: OpenAIConfig) -> None:
        self._client = openai.OpenAI(api_key=config.api_key, base_url=config.base_url)

    def chat(
        self,
        model_id: str,
        messages: list[Message],
        tools: list[ToolDef],
        max_tokens: int,
    ) -> LLMResponse:
        api_messages: list[dict[str, Any]] = []
        for msg in messages:
            api_messages.extend(_message_to_openai(msg))
        kwargs: dict[str, Any] = dict(
            model=model_id,
            messages=api_messages,
            max_completion_tokens=max_tokens,
        )
        if tools:
            kwargs["tools"] = _to_openai_tools(tools)

        response = self._client.chat.completions.create(**kwargs)
        return _parse_response(response)


# -- message translation -----------------------------------------------------


def _message_to_openai(msg: Message) -> list[dict[str, Any]]:
    """Translate a single Message to OpenAI dict(s).

    Returns a list because user-with-tool-results expands to one
    ``role=tool`` message per result.
    """
    # user with tool results -> one "tool" message per result
    if msg.role == "user" and msg.tool_results:
        return [
            {
                "role": "tool",
                "tool_call_id": r.tool_call_id,
                "content": r.content,
            }
            for r in msg.tool_results
        ]

    # assistant with tool calls
    if msg.role == "assistant" and msg.tool_calls:
        return [{
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in msg.tool_calls
            ],
        }]

    # plain text (user or assistant)
    if msg.content is None:
        raise ValueError(f"Message with role={msg.role!r} has no content and no tool data")
    return [{"role": msg.role, "content": msg.content}]


# -- tool translation ---------------------------------------------------------


def _to_openai_tools(tools: list[ToolDef]) -> list[dict[str, Any]]:
    """Translate ToolDef list to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


# -- response parsing --------------------------------------------------------


def _parse_response(response: openai.types.chat.ChatCompletion) -> LLMResponse:
    """Normalize an OpenAI ChatCompletion into LLMResponse."""
    choice = response.choices[0]
    msg = choice.message

    tool_calls: list[ToolCall] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            if not isinstance(tc, ChatCompletionMessageToolCall):
                continue
            arguments = json.loads(tc.function.arguments) if tc.function.arguments else {}
            tool_calls.append(ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=arguments,
            ))

    return LLMResponse(
        content=msg.content,
        tool_calls=tool_calls if tool_calls else None,
        stop_reason=choice.finish_reason,
        finished=choice.finish_reason == "stop",
        tokens_in=response.usage.prompt_tokens if response.usage else 0,
        tokens_out=response.usage.completion_tokens if response.usage else 0,
    )

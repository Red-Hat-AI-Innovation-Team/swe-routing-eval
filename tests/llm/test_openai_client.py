"""Tests for llm/openai_client.py: message/tool translation and response parsing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from swe_routing_eval.llm.openai_client import (
    OpenAIChatCompletionsClient,
    _message_to_openai,
    _parse_response,
    _to_openai_tools,
)
from swe_routing_eval.llm.types import Message, ToolCall, ToolDef, ToolResult
from swe_routing_eval.openai_config import OpenAIConfig

_TEST_CONFIG = OpenAIConfig(api_key="test-key", base_url="https://test.example.com")

# ---------------------------------------------------------------------------
# _message_to_openai
# ---------------------------------------------------------------------------


def test_message_plain_user() -> None:
    msg = Message(role="user", content="Hello")
    assert _message_to_openai(msg) == [{"role": "user", "content": "Hello"}]


def test_message_plain_assistant() -> None:
    msg = Message(role="assistant", content="Sure thing.")
    assert _message_to_openai(msg) == [{"role": "assistant", "content": "Sure thing."}]


def test_message_user_none_content_raises() -> None:
    msg = Message(role="user")
    with pytest.raises(ValueError, match="no content"):
        _message_to_openai(msg)


def test_message_assistant_with_tool_calls_only() -> None:
    msg = Message(
        role="assistant",
        tool_calls=[ToolCall(id="tc_1", name="bash", arguments={"command": "ls"})],
    )
    result = _message_to_openai(msg)
    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] is None
    assert len(result[0]["tool_calls"]) == 1
    tc = result[0]["tool_calls"][0]
    assert tc == {
        "id": "tc_1",
        "type": "function",
        "function": {"name": "bash", "arguments": json.dumps({"command": "ls"})},
    }


def test_message_assistant_with_content_and_tool_calls() -> None:
    msg = Message(
        role="assistant",
        content="Let me check.",
        tool_calls=[ToolCall(id="tc_1", name="bash", arguments={"command": "pwd"})],
    )
    result = _message_to_openai(msg)
    assert len(result) == 1
    assert result[0]["content"] == "Let me check."
    assert len(result[0]["tool_calls"]) == 1


def test_message_user_with_tool_results_expands() -> None:
    """User messages with tool_results expand to individual role=tool messages."""
    msg = Message(
        role="user",
        tool_results=[
            ToolResult(tool_call_id="tc_1", content="/home"),
            ToolResult(tool_call_id="tc_2", content="ok"),
        ],
    )
    result = _message_to_openai(msg)
    assert len(result) == 2
    assert result[0] == {"role": "tool", "tool_call_id": "tc_1", "content": "/home"}
    assert result[1] == {"role": "tool", "tool_call_id": "tc_2", "content": "ok"}


# ---------------------------------------------------------------------------
# _to_openai_tools
# ---------------------------------------------------------------------------


def test_tool_def_translation() -> None:
    td = ToolDef(
        name="bash",
        description="Run a command.",
        parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
    )
    result = _to_openai_tools([td])
    assert result == [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run a command.",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
            },
        }
    ]


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


def _mock_completion(
    content: str | None = None,
    tool_calls: list[ChatCompletionMessageToolCall] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish_reason

    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def test_parse_response_text_only_stop() -> None:
    resp = _mock_completion(content="All done.", finish_reason="stop")
    result = _parse_response(resp)
    assert result.content == "All done."
    assert result.tool_calls is None
    assert result.finished is True
    assert result.stop_reason == "stop"
    assert result.tokens_in == 100
    assert result.tokens_out == 50


def test_parse_response_tool_calls() -> None:
    tc = ChatCompletionMessageToolCall(
        id="call_1", type="function",
        function=Function(name="bash", arguments=json.dumps({"command": "ls"})),
    )
    resp = _mock_completion(tool_calls=[tc], finish_reason="tool_calls")
    result = _parse_response(resp)
    assert result.content is None
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "bash"
    assert result.tool_calls[0].arguments == {"command": "ls"}
    assert result.finished is False


def test_parse_response_text_and_tool_calls() -> None:
    tc = ChatCompletionMessageToolCall(
        id="call_1", type="function",
        function=Function(name="bash", arguments=json.dumps({"command": "pwd"})),
    )
    resp = _mock_completion(content="Checking...", tool_calls=[tc], finish_reason="tool_calls")
    result = _parse_response(resp)
    assert result.content == "Checking..."
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1


def test_parse_response_token_counts() -> None:
    resp = _mock_completion(
        content="hi", finish_reason="stop",
        prompt_tokens=500, completion_tokens=42,
    )
    result = _parse_response(resp)
    assert result.tokens_in == 500
    assert result.tokens_out == 42


def test_parse_response_multiple_tool_calls() -> None:
    tcs = [
        ChatCompletionMessageToolCall(
            id="call_1", type="function",
            function=Function(name="bash", arguments=json.dumps({"command": "ls"})),
        ),
        ChatCompletionMessageToolCall(
            id="call_2", type="function",
            function=Function(name="bash", arguments=json.dumps({"command": "pwd"})),
        ),
    ]
    resp = _mock_completion(tool_calls=tcs, finish_reason="tool_calls")
    result = _parse_response(resp)
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[1].id == "call_2"


def test_parse_response_no_usage() -> None:
    resp = _mock_completion(content="hi")
    resp.usage = None
    result = _parse_response(resp)
    assert result.tokens_in == 0
    assert result.tokens_out == 0


def test_parse_response_empty_arguments() -> None:
    tc = ChatCompletionMessageToolCall(
        id="call_1", type="function",
        function=Function(name="finish", arguments=""),
    )
    resp = _mock_completion(tool_calls=[tc], finish_reason="tool_calls")
    result = _parse_response(resp)
    assert result.tool_calls is not None
    assert result.tool_calls[0].arguments == {}


# ---------------------------------------------------------------------------
# chat() integration (mocked API)
# ---------------------------------------------------------------------------


def test_chat_wires_translation_and_parsing() -> None:
    tc = ChatCompletionMessageToolCall(
        id="call_1", type="function",
        function=Function(name="bash", arguments=json.dumps({"command": "ls"})),
    )
    mock_resp = _mock_completion(
        tool_calls=[tc], finish_reason="tool_calls",
        prompt_tokens=200, completion_tokens=30,
    )

    with patch("swe_routing_eval.llm.openai_client.openai") as mock_mod:
        mock_client_instance = MagicMock()
        mock_mod.OpenAI.return_value = mock_client_instance
        mock_client_instance.chat.completions.create.return_value = mock_resp

        client = OpenAIChatCompletionsClient(_TEST_CONFIG)
        result = client.chat(
            model_id="gpt-4o",
            messages=[
                Message(role="system", content="Be helpful."),
                Message(role="user", content="Fix the bug."),
            ],
            tools=[ToolDef(name="bash", description="Run cmd.", parameters={"type": "object"})],
            max_tokens=4096,
        )

    # verify API was called with translated args
    call_kwargs = mock_client_instance.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o"
    assert call_kwargs["max_completion_tokens"] == 4096
    # system prepended, then user message
    assert len(call_kwargs["messages"]) == 2
    assert call_kwargs["messages"][0] == {"role": "system", "content": "Be helpful."}
    assert call_kwargs["messages"][1] == {"role": "user", "content": "Fix the bug."}
    assert call_kwargs["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run cmd.",
                "parameters": {"type": "object"},
            },
        },
    ]

    # verify response was parsed
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "bash"
    assert result.tokens_in == 200
    assert result.tokens_out == 30


def test_chat_with_tool_results_in_history() -> None:
    mock_resp = _mock_completion(content="Got it.", finish_reason="stop")

    with patch("swe_routing_eval.llm.openai_client.openai") as mock_mod:
        mock_client_instance = MagicMock()
        mock_mod.OpenAI.return_value = mock_client_instance
        mock_client_instance.chat.completions.create.return_value = mock_resp

        client = OpenAIChatCompletionsClient(_TEST_CONFIG)
        client.chat(
            model_id="gpt-4o",
            messages=[
                Message(role="system", content="System."),
                Message(role="user", content="Fix it."),
                Message(
                    role="assistant",
                    tool_calls=[ToolCall(id="tc_1", name="bash", arguments={"command": "ls"})],
                ),
                Message(
                    role="user",
                    tool_results=[ToolResult(tool_call_id="tc_1", content="file.txt")],
                ),
            ],
            tools=[ToolDef(name="bash", description="Run cmd.", parameters={"type": "object"})],
            max_tokens=4096,
        )

    call_kwargs = mock_client_instance.chat.completions.create.call_args.kwargs
    msgs = call_kwargs["messages"]
    # system + user + assistant-with-tools + tool-result = 4
    assert len(msgs) == 4
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["tool_calls"][0]["function"]["arguments"] == json.dumps({"command": "ls"})
    assert msgs[3]["role"] == "tool"
    assert msgs[3]["tool_call_id"] == "tc_1"
    assert msgs[3]["content"] == "file.txt"

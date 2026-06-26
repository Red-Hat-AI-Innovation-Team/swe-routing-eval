"""Tests for llm/anthropic_vertex.py: message/tool translation and response parsing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from anthropic.types import TextBlock, ToolUseBlock, Usage

from swe_routing_eval.llm.anthropic_vertex import (
    AnthropicVertexClient,
    _message_to_anthropic,
    _parse_response,
    _split_system,
    _tool_def_to_anthropic,
)
from swe_routing_eval.llm.types import Message, ToolCall, ToolDef, ToolResult
from swe_routing_eval.vertex import VertexConfig  # noqa: I001

# ---------------------------------------------------------------------------
# _split_system
# ---------------------------------------------------------------------------


def test_split_system_extracts_single_system_message() -> None:
    messages = [
        Message(role="system", content="You are helpful."),
        Message(role="user", content="Hi"),
    ]
    system, api_msgs = _split_system(messages)
    assert system == "You are helpful."
    assert len(api_msgs) == 1
    assert api_msgs[0] == {"role": "user", "content": "Hi"}


def test_split_system_joins_multiple_system_messages() -> None:
    messages = [
        Message(role="system", content="First."),
        Message(role="system", content="Second."),
        Message(role="user", content="Hi"),
    ]
    system, api_msgs = _split_system(messages)
    assert system == "First.\n\nSecond."
    assert len(api_msgs) == 1


def test_split_system_returns_empty_string_when_no_system() -> None:
    messages = [Message(role="user", content="Hi")]
    system, api_msgs = _split_system(messages)
    assert system == ""
    assert len(api_msgs) == 1


def test_split_system_skips_none_content_system() -> None:
    messages = [
        Message(role="system"),
        Message(role="user", content="Hi"),
    ]
    system, api_msgs = _split_system(messages)
    assert system == ""
    assert len(api_msgs) == 1


# ---------------------------------------------------------------------------
# _message_to_anthropic
# ---------------------------------------------------------------------------


def test_message_plain_user() -> None:
    msg = Message(role="user", content="Hello")
    assert _message_to_anthropic(msg) == {"role": "user", "content": "Hello"}


def test_message_plain_assistant() -> None:
    msg = Message(role="assistant", content="Sure thing.")
    assert _message_to_anthropic(msg) == {"role": "assistant", "content": "Sure thing."}


def test_message_user_none_content_raises() -> None:
    msg = Message(role="user")
    with pytest.raises(ValueError, match="no content"):
        _message_to_anthropic(msg)


def test_message_assistant_with_tool_calls_only() -> None:
    msg = Message(
        role="assistant",
        tool_calls=[ToolCall(id="tc_1", name="bash", arguments={"command": "ls"})],
    )
    result = _message_to_anthropic(msg)
    assert result["role"] == "assistant"
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block == {"type": "tool_use", "id": "tc_1", "name": "bash", "input": {"command": "ls"}}


def test_message_assistant_with_content_and_tool_calls() -> None:
    msg = Message(
        role="assistant",
        content="Let me check.",
        tool_calls=[ToolCall(id="tc_1", name="bash", arguments={"command": "pwd"})],
    )
    result = _message_to_anthropic(msg)
    assert result["role"] == "assistant"
    assert len(result["content"]) == 2
    assert result["content"][0] == {"type": "text", "text": "Let me check."}
    assert result["content"][1]["type"] == "tool_use"


def test_message_user_with_tool_results() -> None:
    msg = Message(
        role="user",
        tool_results=[
            ToolResult(tool_call_id="tc_1", content="/home"),
            ToolResult(tool_call_id="tc_2", content="ok"),
        ],
    )
    result = _message_to_anthropic(msg)
    assert result["role"] == "user"
    assert len(result["content"]) == 2
    assert result["content"][0] == {
        "type": "tool_result", "tool_use_id": "tc_1", "content": "/home",
    }
    assert result["content"][1] == {
        "type": "tool_result", "tool_use_id": "tc_2", "content": "ok",
    }


# ---------------------------------------------------------------------------
# _tool_def_to_anthropic
# ---------------------------------------------------------------------------


def test_tool_def_translation() -> None:
    td = ToolDef(
        name="bash",
        description="Run a command.",
        parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
    )
    result = _tool_def_to_anthropic(td)
    assert result == {
        "name": "bash",
        "description": "Run a command.",
        "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
    }


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


def _mock_response(
    content_blocks: list[TextBlock | ToolUseBlock],
    stop_reason: str = "tool_use",
    tokens_in: int = 100,
    tokens_out: int = 50,
) -> MagicMock:
    resp = MagicMock()
    resp.content = content_blocks
    resp.stop_reason = stop_reason
    resp.usage = Usage(input_tokens=tokens_in, output_tokens=tokens_out)
    return resp


def test_parse_response_text_only_end_turn() -> None:
    resp = _mock_response(
        [TextBlock(type="text", text="All done.")],
        stop_reason="end_turn",
    )
    result = _parse_response(resp)
    assert result.content == "All done."
    assert result.tool_calls is None
    assert result.finished is True
    assert result.tokens_in == 100
    assert result.tokens_out == 50


def test_parse_response_tool_calls() -> None:
    resp = _mock_response([
        ToolUseBlock(type="tool_use", id="tu_1", name="bash", input={"command": "ls"}),
    ])
    result = _parse_response(resp)
    assert result.content is None
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "tu_1"
    assert result.tool_calls[0].name == "bash"
    assert result.tool_calls[0].arguments == {"command": "ls"}
    assert result.finished is False


def test_parse_response_text_and_tool_calls() -> None:
    resp = _mock_response([
        TextBlock(type="text", text="Checking..."),
        ToolUseBlock(type="tool_use", id="tu_1", name="bash", input={"command": "pwd"}),
    ])
    result = _parse_response(resp)
    assert result.content == "Checking..."
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1


def test_parse_response_token_counts() -> None:
    resp = _mock_response(
        [TextBlock(type="text", text="hi")],
        stop_reason="end_turn",
        tokens_in=500,
        tokens_out=42,
    )
    result = _parse_response(resp)
    assert result.tokens_in == 500
    assert result.tokens_out == 42


def test_parse_response_multiple_tool_calls() -> None:
    resp = _mock_response([
        ToolUseBlock(type="tool_use", id="tu_1", name="bash", input={"command": "ls"}),
        ToolUseBlock(type="tool_use", id="tu_2", name="bash", input={"command": "pwd"}),
    ])
    result = _parse_response(resp)
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].id == "tu_1"
    assert result.tool_calls[1].id == "tu_2"


# ---------------------------------------------------------------------------
# chat() integration (mocked API)
# ---------------------------------------------------------------------------


def test_chat_wires_translation_and_parsing() -> None:
    mock_anthropic_resp = _mock_response(
        [ToolUseBlock(type="tool_use", id="tu_1", name="bash", input={"command": "ls"})],
        stop_reason="tool_use",
        tokens_in=200,
        tokens_out=30,
    )

    with patch("swe_routing_eval.llm.anthropic_vertex.anthropic") as mock_mod:
        mock_client_instance = MagicMock()
        mock_mod.AnthropicVertex.return_value = mock_client_instance
        mock_client_instance.messages.create.return_value = mock_anthropic_resp
        mock_mod.NOT_GIVEN = None

        config = VertexConfig(
            project_id="proj", region="us-east5",
            opus_model_id="", sonnet_model_id="", haiku_model_id="",
        )
        client = AnthropicVertexClient(config)
        result = client.chat(
            model_id="claude-sonnet-4-6-20251001",
            messages=[
                Message(role="system", content="Be helpful."),
                Message(role="user", content="Fix the bug."),
            ],
            tools=[ToolDef(name="bash", description="Run cmd.", parameters={"type": "object"})],
            max_tokens=4096,
        )

    # verify API was called with translated args
    call_kwargs = mock_client_instance.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6-20251001"
    assert call_kwargs["max_tokens"] == 4096
    assert call_kwargs["system"] == "Be helpful."
    assert len(call_kwargs["messages"]) == 1  # system extracted, only user remains
    assert call_kwargs["messages"][0] == {"role": "user", "content": "Fix the bug."}
    assert call_kwargs["tools"] == [
        {"name": "bash", "description": "Run cmd.", "input_schema": {"type": "object"}},
    ]

    # verify response was parsed
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "bash"
    assert result.tokens_in == 200
    assert result.tokens_out == 30

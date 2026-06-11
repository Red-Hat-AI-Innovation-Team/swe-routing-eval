"""Tests for scaffold.py: agent loop, tool dispatch, telemetry, constants."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from swe_routing_eval.ingest import SWEbenchInstance
from swe_routing_eval.scaffold import (
    MAX_TURNS,
    SCAFFOLD_VERSION,
    SYSTEM_PROMPT,
    TOOLS,
    AttemptResult,
    _bash,
    _run_cli,
    _run_loop,
)
from swe_routing_eval.vertex import VertexConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

INSTANCE = SWEbenchInstance(
    instance_id="kubectl-42",
    repo="kubernetes/kubectl",
    base_commit="deadbeef",
    patch="",
    test_patch="",
    problem_statement="Fix nil pointer in get command",
    repo_language="go",
    product="kubectl",
    fix_merge_date="2024-03-01",
    provenance="github",
    link_confidence=0.9,
    n_fail_to_pass=1,
    patch_lines=3,
    files_touched=1,
    cross_file=False,
    env_spec_hash="sha256:abc",
    image_name="swebench/kubectl:deadbeef",
    compiled=True,
    n_runs=3,
    quarantined_tests=[],
    decontam_overlap=False,
)

CONFIG = VertexConfig(
    project_id="test-proj",
    region="us-east5",
    opus_model_id="claude-opus-4-8-20251001",
    sonnet_model_id="claude-sonnet-4-6-20251001",
    haiku_model_id="claude-haiku-4-5-20251001",
)


def _tool_use_block(name: str, tool_id: str, input_data: dict[str, object]) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.id = tool_id
    block.input = input_data
    # Make isinstance(block, ToolUseBlock) work via spec
    from anthropic.types import ToolUseBlock
    block.__class__ = ToolUseBlock
    return block


def _text_block(text: str) -> MagicMock:
    from anthropic.types import TextBlock
    block = MagicMock()
    block.type = "text"
    block.text = text
    block.__class__ = TextBlock
    return block


def _response(
    content: list[MagicMock],
    stop_reason: str = "tool_use",
    tokens_in: int = 100,
    tokens_out: int = 50,
) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.stop_reason = stop_reason
    resp.usage.input_tokens = tokens_in
    resp.usage.output_tokens = tokens_out
    return resp


# ---------------------------------------------------------------------------
# Scaffold constants (issue #8 — must not change between runs)
# ---------------------------------------------------------------------------


def test_scaffold_version_is_string() -> None:
    assert isinstance(SCAFFOLD_VERSION, str) and SCAFFOLD_VERSION


def test_max_turns_is_positive_int() -> None:
    assert isinstance(MAX_TURNS, int) and MAX_TURNS > 0


def test_system_prompt_is_fixed_string() -> None:
    assert isinstance(SYSTEM_PROMPT, str) and len(SYSTEM_PROMPT) > 50


def test_tools_contains_bash_and_finish() -> None:
    names = {t["name"] for t in TOOLS}
    assert "bash" in names
    assert "finish" in names


def test_tools_schema_is_stable() -> None:
    """Tool definitions must not change — any change would require a new scaffold version."""
    bash = next(t for t in TOOLS if t["name"] == "bash")
    assert "command" in bash["input_schema"]["properties"]


# ---------------------------------------------------------------------------
# _run_loop: finish tool (issue #8)
# ---------------------------------------------------------------------------


def test_run_loop_stops_on_finish_tool(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()  # minimal git marker for _git_diff
    client = MagicMock()

    bash_resp = _response([
        _tool_use_block("bash", "tu_1", {"command": "ls"}),
    ])
    finish_resp = _response([
        _tool_use_block("finish", "tu_2", {}),
    ])
    client.messages.create.side_effect = [bash_resp, finish_resp]

    with patch("swe_routing_eval.scaffold._bash", return_value="main.go"):
        with patch("swe_routing_eval.scaffold._git_diff", return_value="diff --git ..."):
            result = _run_loop(client, INSTANCE, tmp_path, "some-model-id", seed=7)

    assert isinstance(result, AttemptResult)
    assert result.model_id == "some-model-id"
    assert result.scaffold_version == SCAFFOLD_VERSION
    assert result.seed == 7
    assert result.turns == 2
    assert result.tool_calls == 2  # bash + finish
    assert result.candidate_patch == "diff --git ..."


def test_run_loop_stops_on_end_turn(tmp_path: Path) -> None:
    client = MagicMock()
    client.messages.create.return_value = _response(
        [_text_block("I have finished.")],
        stop_reason="end_turn",
    )
    with patch("swe_routing_eval.scaffold._git_diff", return_value="diff ..."):
        result = _run_loop(client, INSTANCE, tmp_path, "model-id", seed=0)

    assert result.turns == 1
    assert result.tool_calls == 0
    assert result.candidate_patch == "diff ..."


def test_run_loop_accumulates_token_counts(tmp_path: Path) -> None:
    client = MagicMock()
    bash_resp = _response(
        [_tool_use_block("bash", "tu_1", {"command": "pwd"})],
        tokens_in=200,
        tokens_out=30,
    )
    finish_resp = _response(
        [_tool_use_block("finish", "tu_2", {})],
        tokens_in=300,
        tokens_out=10,
    )
    client.messages.create.side_effect = [bash_resp, finish_resp]

    with patch("swe_routing_eval.scaffold._bash", return_value=""):
        with patch("swe_routing_eval.scaffold._git_diff", return_value=""):
            result = _run_loop(client, INSTANCE, tmp_path, "model-id", seed=1)

    assert result.tokens_in == 500
    assert result.tokens_out == 40


def test_run_loop_respects_max_turns(tmp_path: Path) -> None:
    client = MagicMock()
    # Always returns a bash tool call — loop should stop at MAX_TURNS
    client.messages.create.return_value = _response([
        _tool_use_block("bash", "tu_1", {"command": "echo hi"}),
    ])

    with patch("swe_routing_eval.scaffold._bash", return_value="hi"):
        with patch("swe_routing_eval.scaffold._git_diff", return_value=""):
            result = _run_loop(client, INSTANCE, tmp_path, "model-id", seed=0)

    assert result.turns == MAX_TURNS


# ---------------------------------------------------------------------------
# _bash helper (issue #9 — truncation + timeout)
# ---------------------------------------------------------------------------


def test_bash_truncates_long_output(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="x" * 10_000,
            stderr="",
            returncode=0,
        )
        output = _bash("echo hi", tmp_path)
    assert len(output) <= 8_000


def test_bash_appends_stderr(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="out",
            stderr="err",
            returncode=0,
        )
        output = _bash("cmd", tmp_path)
    assert "err" in output


def test_bash_handles_timeout(tmp_path: Path) -> None:
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=120)):
        output = _bash("sleep 999", tmp_path)
    assert "timed out" in output


# ---------------------------------------------------------------------------
# _run_cli: CLIScaffold (Cursor agent CLI)
# ---------------------------------------------------------------------------


def test_run_cli_parses_json_result(tmp_path: Path) -> None:
    json_output = (
        '{"type":"result","subtype":"success","is_error":false,'
        '"duration_ms":5000,"result":"done",'
        '"usage":{"inputTokens":10000,"outputTokens":200}}'
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=json_output,
            stderr="",
            returncode=0,
        )
        with patch("swe_routing_eval.scaffold._git_diff", return_value="diff --git a/f"):
            result = _run_cli("fix the bug", tmp_path, "gpt-5.4-medium", seed=0)

    assert isinstance(result, AttemptResult)
    assert result.model_id == "gpt-5.4-medium"
    assert result.tokens_in == 10000
    assert result.tokens_out == 200
    assert result.candidate_patch == "diff --git a/f"
    assert result.scaffold_version == SCAFFOLD_VERSION


def test_run_cli_handles_multiline_output(tmp_path: Path) -> None:
    stdout = (
        'some debug output\n'
        'more output\n'
        '{"type":"result","usage":{"inputTokens":5000,"outputTokens":100}}'
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=stdout, stderr="", returncode=0)
        with patch("swe_routing_eval.scaffold._git_diff", return_value=""):
            result = _run_cli("prompt", tmp_path, "gpt-5.4-medium", seed=0)

    assert result.tokens_in == 5000
    assert result.tokens_out == 100


def test_run_cli_handles_timeout(tmp_path: Path) -> None:
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="agent", timeout=1800)):
        with patch("swe_routing_eval.scaffold._git_diff", return_value="partial diff"):
            result = _run_cli("prompt", tmp_path, "gpt-5.4-medium", seed=0)

    assert result.tokens_in == 0
    assert result.tokens_out == 0
    assert result.candidate_patch == "partial diff"


def test_run_cli_handles_malformed_json(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="not json", stderr="", returncode=0)
        with patch("swe_routing_eval.scaffold._git_diff", return_value=""):
            result = _run_cli("prompt", tmp_path, "gpt-5.4-medium", seed=0)

    assert result.tokens_in == 0
    assert result.tokens_out == 0

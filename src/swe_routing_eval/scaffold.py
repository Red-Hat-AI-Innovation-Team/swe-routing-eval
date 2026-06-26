"""Fixed SWE-agent-style scaffold for Go patch generation (issues #8, #9).

Every element that could vary across models is held constant:
  SYSTEM_PROMPT, TOOLS, MAX_TURNS, SCAFFOLD_VERSION

The model ID is the only variable. seed is logged for reproducibility but
is not passed to the API (the Anthropic API has no seed parameter).

CLIScaffold delegates to the Cursor `agent` CLI for non-Anthropic models.
"""

from __future__ import annotations

import json as _json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from swe_routing_eval.cursor_usage import CursorUsageClient, sum_events
from swe_routing_eval.ingest import SWEbenchInstance
from swe_routing_eval.llm import LLMClient, Message, ToolDef, ToolResult

SCAFFOLD_VERSION = "v0.1.0"

MAX_TURNS = 30

SYSTEM_PROMPT = """\
You are an expert Go software engineer. You will be given a bug description and \
access to a Git repository checked out at the buggy commit.

Your task is to produce a minimal patch that fixes the described bug.

Workflow:
1. Use `bash` to explore the repository and understand the relevant code.
2. Make targeted edits with `bash` (e.g. using sed, patch, or direct file writes).
3. Verify the code compiles: `go build ./...`
4. Call `finish` when done.

Rules:
- Do NOT modify test files (*_test.go or anything under testdata/ directories).
- Keep the fix minimal — change only what is necessary.
- Do not add new external dependencies.
"""

# Tool definitions are fixed and identical across all model tiers.
TOOLS: list[ToolDef] = [
    ToolDef(
        name="bash",
        description=(
            "Run a shell command in the repository working directory. "
            "Returns combined stdout and stderr (truncated to 8 000 chars)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."}
            },
            "required": ["command"],
        },
    ),
    ToolDef(
        name="finish",
        description=(
            "Submit your patch. Call this when you have finished making changes. "
            "The patch is captured automatically via `git diff HEAD`."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
    ),
]

_BASH_TIMEOUT_S = 120
_OUTPUT_LIMIT = 8_000


@dataclass
class AttemptResult:
    """Telemetry and output for a single scaffold attempt."""

    candidate_patch: str
    model_id: str
    seed: int
    scaffold_version: str
    tokens_in: int
    tokens_out: int
    turns: int
    tool_calls: int
    wall_clock_s: float
    cost_cents: float | None = None


class Scaffold:
    """Model-agnostic fixed scaffold (issues #8, #9).

    System prompt, tools, and max_turns are constants in this module.
    The LLMClient handles all provider-specific translation.
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def run(
        self,
        instance: SWEbenchInstance,
        workspace_dir: Path,
        model_id: str,
        seed: int,
    ) -> AttemptResult:
        """Run one attempt for the given instance.

        Args:
            instance: The SWE-bench instance to fix.
            workspace_dir: Repo checked out at base_commit; commands run here.
            model_id: Model ID — recorded verbatim in telemetry.
            seed: Logged for reproducibility; not sent to the API.

        Returns:
            AttemptResult with the candidate patch (git diff HEAD) and telemetry.
        """
        return _run_loop(self._llm, instance, workspace_dir, model_id, seed)


def _run_loop(
    llm: LLMClient,
    instance: SWEbenchInstance,
    workspace_dir: Path,
    model_id: str,
    seed: int,
) -> AttemptResult:
    """Inner agent loop — separated to make the client injectable in tests."""
    start = time.monotonic()
    messages: list[Message] = [
        Message(role="system", content=SYSTEM_PROMPT),
        Message(
            role="user",
            content=(
                f"Repository: {instance.repo}\n\n"
                f"Problem statement:\n{instance.problem_statement}"
            ),
        ),
    ]

    tokens_in = tokens_out = turns = tool_call_count = 0
    candidate_patch = ""

    for _ in range(MAX_TURNS):
        response = llm.chat(
            model_id=model_id,
            messages=messages,
            tools=TOOLS,
            max_tokens=4096,
        )
        tokens_in += response.tokens_in
        tokens_out += response.tokens_out
        turns += 1

        if response.finished:
            candidate_patch = _git_diff(workspace_dir)
            break

        if not response.tool_calls:
            # No tool use and not finished — stop gracefully
            candidate_patch = _git_diff(workspace_dir)
            break

        tool_results: list[ToolResult] = []
        submitted = False

        for tc in response.tool_calls:
            tool_call_count += 1

            if tc.name == "finish":
                candidate_patch = _git_diff(workspace_dir)
                submitted = True
                break

            if tc.name == "bash":
                command = str(tc.arguments.get("command", ""))
                output = _bash(command, workspace_dir)
                tool_results.append(ToolResult(tool_call_id=tc.id, content=output))

        if submitted:
            break

        if not tool_results and not submitted:
            candidate_patch = _git_diff(workspace_dir)
            break

        messages.append(Message(
            role="assistant", content=response.content, tool_calls=response.tool_calls,
        ))
        messages.append(Message(role="user", tool_results=tool_results))
    else:
        candidate_patch = _git_diff(workspace_dir)

    return AttemptResult(
        candidate_patch=candidate_patch,
        model_id=model_id,
        seed=seed,
        scaffold_version=SCAFFOLD_VERSION,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        turns=turns,
        tool_calls=tool_call_count,
        wall_clock_s=time.monotonic() - start,
    )


def _bash(command: str, cwd: Path) -> str:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_BASH_TIMEOUT_S,
        )
        out = proc.stdout
        if proc.stderr:
            out += f"\n[stderr]\n{proc.stderr}"
        if proc.returncode != 0:
            out += f"\n[exit {proc.returncode}]"
        return out[:_OUTPUT_LIMIT]
    except subprocess.TimeoutExpired:
        return f"[timed out after {_BASH_TIMEOUT_S}s]"


def _git_diff(workspace_dir: Path) -> str:
    proc = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=str(workspace_dir),
        capture_output=True,
        text=True,
    )
    return proc.stdout


_CLI_TIMEOUT_S = 1800


class CLIScaffold:
    """Scaffold that delegates to the Cursor `agent` CLI for non-Anthropic models."""

    def __init__(
        self,
        usage_client: CursorUsageClient | None = None,
    ) -> None:
        self._usage_client = usage_client

    def run(
        self,
        instance: SWEbenchInstance,
        workspace_dir: Path,
        model_id: str,
        seed: int,
    ) -> AttemptResult:
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"Repository: {instance.repo}\n\n"
            f"Problem statement:\n{instance.problem_statement}"
        )
        return _run_cli(prompt, workspace_dir, model_id, seed, self._usage_client)


def _run_cli(
    prompt: str,
    workspace_dir: Path,
    model_id: str,
    seed: int,
    usage_client: CursorUsageClient | None = None,
) -> AttemptResult:
    """Run the Cursor agent CLI and parse structured JSON output."""
    start = time.monotonic()
    start_ms = int(time.time() * 1000)

    cmd = [
        "agent", "-p",
        "--model", model_id,
        "--output-format", "json",
        "--trust", "--yolo",
        "--workspace", str(workspace_dir),
        prompt,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT_S,
        )
        stdout = proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return AttemptResult(
            candidate_patch=_git_diff(workspace_dir),
            model_id=model_id,
            seed=seed,
            scaffold_version=SCAFFOLD_VERSION,
            tokens_in=0,
            tokens_out=0,
            turns=0,
            tool_calls=0,
            wall_clock_s=time.monotonic() - start,
        )

    tokens_in = tokens_out = 0
    if stdout:
        last_line = stdout.rsplit("\n", 1)[-1]
        try:
            result = _json.loads(last_line)
            usage = result.get("usage", {})
            tokens_in = usage.get("inputTokens", 0)
            tokens_out = usage.get("outputTokens", 0)
        except (ValueError, KeyError):
            pass

    cost_cents: float | None = None
    if usage_client is not None:
        end_ms = int(time.time() * 1000)
        events = usage_client.get_events()
        session = sum_events(
            events, model=model_id, after_ms=start_ms, before_ms=end_ms,
        )
        if session.event_count > 0:
            cost_cents = session.total_cents
            tokens_in = session.input_tokens
            tokens_out = session.output_tokens

    candidate_patch = _git_diff(workspace_dir)

    return AttemptResult(
        candidate_patch=candidate_patch,
        model_id=model_id,
        seed=seed,
        scaffold_version=SCAFFOLD_VERSION,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        turns=0,
        tool_calls=0,
        wall_clock_s=time.monotonic() - start,
        cost_cents=cost_cents,
    )

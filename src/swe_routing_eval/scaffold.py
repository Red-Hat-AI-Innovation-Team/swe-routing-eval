"""Fixed SWE-agent-style scaffold for Go patch generation (issues #8, #9).

Every element that could vary across models is held constant:
  SYSTEM_PROMPT, TOOLS, MAX_TURNS, SCAFFOLD_VERSION

The model ID is the only variable. seed is logged for reproducibility but
is not passed to the API (the Anthropic API has no seed parameter).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
from anthropic.types import ToolUseBlock

from swe_routing_eval.ingest import SWEbenchInstance
from swe_routing_eval.vertex import VertexConfig

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
TOOLS: list[dict[str, Any]] = [
    {
        "name": "bash",
        "description": (
            "Run a shell command in the repository working directory. "
            "Returns combined stdout and stderr (truncated to 8 000 chars)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."}
            },
            "required": ["command"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Submit your patch. Call this when you have finished making changes. "
            "The patch is captured automatically via `git diff HEAD`."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

_BASH_TIMEOUT_S = 120
_OUTPUT_LIMIT = 8_000


@dataclass
class AttemptResult:
    """Telemetry and output for a single scaffold attempt."""

    candidate_patch: str
    seed: int
    scaffold_version: str
    tokens_in: int
    tokens_out: int
    turns: int
    tool_calls: int
    wall_clock_s: float


class Scaffold:
    """Anthropic-Vertex-backed fixed scaffold (issues #8, #9).

    Uses AnthropicVertex with the pinned model ID from VertexConfig.
    System prompt, tools, and max_turns are constants in this module.
    """

    def __init__(self, config: VertexConfig) -> None:
        self._config = config

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
            model_id: Pinned Vertex Model Garden ID — recorded verbatim in telemetry.
            seed: Logged for reproducibility; not sent to the API.

        Returns:
            AttemptResult with the candidate patch (git diff HEAD) and telemetry.
        """
        client = anthropic.AnthropicVertex(
            project_id=self._config.project_id,
            region=self._config.region,
        )
        return _run_loop(client, instance, workspace_dir, model_id, seed)


def _run_loop(
    client: anthropic.AnthropicVertex,
    instance: SWEbenchInstance,
    workspace_dir: Path,
    model_id: str,
    seed: int,
) -> AttemptResult:
    """Inner agent loop — separated to make the client injectable in tests."""
    start = time.monotonic()
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Repository: {instance.repo}\n\n"
                f"Problem statement:\n{instance.problem_statement}"
            ),
        }
    ]

    tokens_in = tokens_out = turns = tool_calls = 0
    candidate_patch = ""

    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=model_id,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
        )
        tokens_in += response.usage.input_tokens
        tokens_out += response.usage.output_tokens
        turns += 1

        if response.stop_reason == "end_turn":
            candidate_patch = _git_diff(workspace_dir)
            break

        tool_results: list[dict[str, Any]] = []
        submitted = False

        for block in response.content:
            if not isinstance(block, ToolUseBlock):
                continue
            tool_calls += 1

            if block.name == "finish":
                candidate_patch = _git_diff(workspace_dir)
                submitted = True
                break

            if block.name == "bash":
                raw_input = block.input if isinstance(block.input, dict) else {}
                command = str(raw_input.get("command", ""))
                output = _bash(command, workspace_dir)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })

        if submitted:
            break

        if tool_results:
            messages.append({"role": "assistant", "content": list(response.content)})
            messages.append({"role": "user", "content": tool_results})
        else:
            # No tool use and not end_turn — stop gracefully
            candidate_patch = _git_diff(workspace_dir)
            break

    return AttemptResult(
        candidate_patch=candidate_patch,
        seed=seed,
        scaffold_version=SCAFFOLD_VERSION,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        turns=turns,
        tool_calls=tool_calls,
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

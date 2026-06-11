"""Tests for scripts/eval_sweep.py: --dry-run flag, budget exit codes, arg validation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

# Load main() from the script
_SCRIPT = Path(__file__).parent.parent / "scripts" / "eval_sweep.py"
_spec = importlib.util.spec_from_file_location("eval_sweep", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
main = _mod.main
_workspace_cleanup = _mod._workspace_cleanup

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_OPUS_ID = "claude-opus-4-8-20251001"
_SONNET_ID = "claude-sonnet-4-6-20251001"

_INSTANCE: dict[str, object] = {
    "instance_id": "kubectl-1",
    "repo": "kubernetes/kubectl",
    "base_commit": "abc",
    "patch": "diff --git a/foo.go ...",
    "test_patch": "",
    "problem_statement": "Bug",
    "repo_language": "go",
    "product": "kubectl",
    "fix_merge_date": "2024-01-01",
    "provenance": "github",
    "link_confidence": 0.9,
    "n_fail_to_pass": 1,
    "patch_lines": 5,
    "files_touched": 1,
    "cross_file": False,
    "env_spec_hash": "sha256:x",
    "image_name": "img:abc",
    "compiled": True,
    "n_runs": 3,
    "quarantined_tests": [],
    "decontam_overlap": False,
}

_PRICES = {
    _OPUS_ID: {"input_per_1k_tokens": 0.015, "output_per_1k_tokens": 0.075},
    _SONNET_ID: {"input_per_1k_tokens": 0.003, "output_per_1k_tokens": 0.015},
}

_VERTEX_ENV = {
    "ANTHROPIC_VERTEX_PROJECT_ID": "proj",
    "CLOUD_ML_REGION": "us-east5",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": _OPUS_ID,
    "ANTHROPIC_DEFAULT_SONNET_MODEL": _SONNET_ID,
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5-20251001",
}


def _write_jsonl(tmp_path: Path) -> Path:
    p = tmp_path / "instances.jsonl"
    p.write_text(json.dumps(_INSTANCE))
    return p


def _write_prices(tmp_path: Path, prices: dict[str, object] | None = None) -> Path:
    p = tmp_path / "prices.json"
    p.write_text(json.dumps(prices or _PRICES))
    return p


# ---------------------------------------------------------------------------
# --dry-run exits 0 within budget, no inference
# ---------------------------------------------------------------------------


def test_dry_run_exits_zero_within_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for k, v in _VERTEX_ENV.items():
        monkeypatch.setenv(k, v)

    instances_path = _write_jsonl(tmp_path)
    prices_path = _write_prices(tmp_path)

    with patch("swe_routing_eval.scaffold.Scaffold.run") as mock_run:
        rc = main([
            str(instances_path),
            "--tiers", "sonnet",
            "--k", "1",
            "--price-table", str(prices_path),
            "--max-spend-usd", "100.0",
            "--dry-run",
        ])

    assert rc == 0
    mock_run.assert_not_called()
    out = capsys.readouterr().out
    assert "sonnet" in out
    assert "$" in out
    assert "Within budget" in out


def test_dry_run_exits_one_over_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for k, v in _VERTEX_ENV.items():
        monkeypatch.setenv(k, v)

    instances_path = _write_jsonl(tmp_path)
    prices_path = _write_prices(tmp_path)

    with patch("swe_routing_eval.scaffold.Scaffold.run") as mock_run:
        rc = main([
            str(instances_path),
            "--tiers", "opus",
            "--k", "1000",
            "--price-table", str(prices_path),
            "--max-spend-usd", "0.01",
            "--dry-run",
        ])

    assert rc == 1
    mock_run.assert_not_called()


def test_dry_run_no_inference_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run must never touch the scaffold or grader."""
    for k, v in _VERTEX_ENV.items():
        monkeypatch.setenv(k, v)

    instances_path = _write_jsonl(tmp_path)
    prices_path = _write_prices(tmp_path)

    with patch("swe_routing_eval.scaffold.Scaffold.run") as mock_scaffold, \
         patch("swe_routing_eval.grading.SubprocessGrader.grade") as mock_grader:
        main([
            str(instances_path),
            "--tiers", "sonnet",
            "--k", "1",
            "--price-table", str(prices_path),
            "--max-spend-usd", "100.0",
            "--dry-run",
        ])

    mock_scaffold.assert_not_called()
    mock_grader.assert_not_called()


# ---------------------------------------------------------------------------
# Arg validation
# ---------------------------------------------------------------------------


def test_invalid_tier_returns_exit_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in _VERTEX_ENV.items():
        monkeypatch.setenv(k, v)

    instances_path = _write_jsonl(tmp_path)
    prices_path = _write_prices(tmp_path)

    rc = main([
        str(instances_path),
        "--tiers", "invalid-tier",
        "--price-table", str(prices_path),
        "--dry-run",
    ])
    assert rc == 2


def test_missing_vertex_env_returns_exit_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k in _VERTEX_ENV:
        monkeypatch.delenv(k, raising=False)

    instances_path = _write_jsonl(tmp_path)
    prices_path = _write_prices(tmp_path)

    rc = main([
        str(instances_path),
        "--tiers", "sonnet",
        "--price-table", str(prices_path),
        "--dry-run",
    ])
    assert rc == 2


# ---------------------------------------------------------------------------
# _workspace_cleanup
# ---------------------------------------------------------------------------


def test_workspace_cleanup_removes_directory(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "test-worktree"
    workspace_dir.mkdir()
    (workspace_dir / "file.txt").write_text("hello")

    _workspace_cleanup(workspace_dir)

    assert not workspace_dir.exists()


def test_workspace_cleanup_ignores_missing_directory(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "does-not-exist"
    _workspace_cleanup(workspace_dir)  # should not raise

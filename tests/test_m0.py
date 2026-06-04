"""Tests for scripts/m0_coverage_check.py: output, exit codes, F2P matching."""

from __future__ import annotations

# Import main() from the script directly
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from swe_routing_eval.grading import GradeResult, TestResult

_SCRIPT = Path(__file__).parent.parent / "scripts" / "m0_coverage_check.py"
_spec = importlib.util.spec_from_file_location("m0_coverage_check", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
main = _mod.main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_INSTANCE: dict[str, object] = {
    "instance_id": "kubectl-1",
    "repo": "kubernetes/kubectl",
    "base_commit": "abc",
    "patch": "diff --git a/foo.go b/foo.go\n--- a/foo.go\n+++ b/foo.go\n",
    "test_patch": "",
    "problem_statement": "Bug",
    "repo_language": "go",
    "product": "kubectl",
    "fix_merge_date": "2024-01-01",
    "provenance": "github",
    "link_confidence": 0.9,
    "n_fail_to_pass": 2,
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


def _jsonl(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    p = tmp_path / "instances.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records))
    return p


def _grade_result(
    resolved: bool = True,
    compiled: bool = True,
    f2p_count: int = 2,
) -> GradeResult:
    return GradeResult(
        resolved=resolved,
        compiled=compiled,
        f2p_results=[TestResult(f"TestFoo{i}", passed=True) for i in range(f2p_count)],
        p2p_results=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_m0_pass_all_resolve(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _jsonl(tmp_path, [_VALID_INSTANCE])
    with patch("swe_routing_eval.grading.SubprocessGrader.grade", return_value=_grade_result()):
        rc = main([str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "M0 PASSED" in out
    assert "PASS" in out


def test_m0_fail_gold_does_not_resolve(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = _jsonl(tmp_path, [_VALID_INSTANCE])
    with patch(
        "swe_routing_eval.grading.SubprocessGrader.grade",
        return_value=_grade_result(resolved=False, compiled=True, f2p_count=0),
    ):
        rc = main([str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "M0 FAILED" in out
    assert "FAIL" in out


def test_m0_fail_f2p_count_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Instance expects 2 F2P but grade returns 1
    p = _jsonl(tmp_path, [_VALID_INSTANCE])
    with patch(
        "swe_routing_eval.grading.SubprocessGrader.grade",
        return_value=_grade_result(resolved=True, f2p_count=1),
    ):
        rc = main([str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "F2P count mismatch" in out


def test_m0_fail_compile_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _jsonl(tmp_path, [_VALID_INSTANCE])
    with patch(
        "swe_routing_eval.grading.SubprocessGrader.grade",
        return_value=_grade_result(resolved=False, compiled=False, f2p_count=0),
    ):
        rc = main([str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "did not compile" in out


def test_m0_partial_failure_reports_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inst2 = {**_VALID_INSTANCE, "instance_id": "kubectl-2"}
    p = _jsonl(tmp_path, [_VALID_INSTANCE, inst2])
    results = [_grade_result(resolved=True), _grade_result(resolved=False, f2p_count=0)]
    with patch("swe_routing_eval.grading.SubprocessGrader.grade", side_effect=results):
        rc = main([str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "1/2" in out
    assert "kubectl-1" in out or "kubectl-2" in out

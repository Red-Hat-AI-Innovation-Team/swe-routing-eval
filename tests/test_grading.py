"""Tests for grading.py: outcome model, anti-reward-hacking, quarantine, pipeline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from swe_routing_eval.grading import (
    GraderError,
    GradeResult,
    SubprocessGrader,
    TestResult,
    apply_quarantine,
    safe_grade,
    touches_test_files,
)
from swe_routing_eval.ingest import SWEbenchInstance

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_INSTANCE = SWEbenchInstance(
    instance_id="kubectl-12345",
    repo="kubernetes/kubectl",
    base_commit="abc123",
    patch="",
    test_patch="",
    problem_statement="Fix nil pointer",
    repo_language="go",
    product="kubectl",
    fix_merge_date="2024-01-15",
    provenance="github",
    link_confidence=0.95,
    n_fail_to_pass=2,
    patch_lines=10,
    files_touched=1,
    cross_file=False,
    env_spec_hash="sha256:dead",
    image_name="swebench/kubectl:abc123",
    compiled=True,
    n_runs=3,
    quarantined_tests=[],
    decontam_overlap=False,
)


def _etcd_instance(**overrides: object) -> SWEbenchInstance:
    base = dict(
        instance_id="etcd-99",
        repo="etcd-io/etcd",
        base_commit="fff000",
        patch="",
        test_patch="",
        problem_statement="Fix raft bug",
        repo_language="go",
        product="etcd",
        fix_merge_date="2024-02-01",
        provenance="github",
        link_confidence=0.9,
        n_fail_to_pass=1,
        patch_lines=5,
        files_touched=1,
        cross_file=False,
        env_spec_hash="sha256:beef",
        image_name="swebench/etcd:fff000",
        compiled=True,
        n_runs=3,
        quarantined_tests=[],
        decontam_overlap=False,
    )
    return SWEbenchInstance(**{**base, **overrides})


# ---------------------------------------------------------------------------
# touches_test_files
# ---------------------------------------------------------------------------


def test_touches_test_files_detects_test_go() -> None:
    patch = (
        "diff --git a/pkg/cmd/foo_test.go b/pkg/cmd/foo_test.go\n"
        "--- a/pkg/cmd/foo_test.go\n"
        "+++ b/pkg/cmd/foo_test.go\n"
        "@@ -1 +1 @@\n"
        "+// changed\n"
    )
    assert touches_test_files(patch) is True


def test_touches_test_files_detects_testdata() -> None:
    patch = (
        "diff --git a/pkg/testdata/fixture.yaml b/pkg/testdata/fixture.yaml\n"
        "--- a/pkg/testdata/fixture.yaml\n"
        "+++ b/pkg/testdata/fixture.yaml\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    assert touches_test_files(patch) is True


def test_touches_test_files_clean_implementation_patch() -> None:
    patch = (
        "diff --git a/pkg/cmd/foo.go b/pkg/cmd/foo.go\n"
        "--- a/pkg/cmd/foo.go\n"
        "+++ b/pkg/cmd/foo.go\n"
        "@@ -10,6 +10,7 @@\n"
        "+\tif x == nil { return }\n"
    )
    assert touches_test_files(patch) is False


def test_touches_test_files_empty_patch() -> None:
    assert touches_test_files("") is False


# ---------------------------------------------------------------------------
# GradeResult outcome model
# ---------------------------------------------------------------------------


def test_grade_result_resolved() -> None:
    result = GradeResult(
        resolved=True,
        compiled=True,
        f2p_results=[TestResult("TestFoo", passed=True)],
        p2p_results=[TestResult("TestBar", passed=True)],
    )
    assert result.resolved is True
    assert result.compiled is True
    assert result.rejected_test_edit is False


def test_grade_result_compile_fail() -> None:
    result = GradeResult(resolved=False, compiled=False)
    assert result.compiled is False
    assert result.resolved is False


def test_grade_result_test_fail() -> None:
    result = GradeResult(
        resolved=False,
        compiled=True,
        f2p_results=[TestResult("TestFoo", passed=False)],
    )
    assert result.compiled is True
    assert result.resolved is False


def test_grade_result_rejected() -> None:
    result = GradeResult(resolved=False, compiled=False, rejected_test_edit=True)
    assert result.rejected_test_edit is True


# ---------------------------------------------------------------------------
# apply_quarantine
# ---------------------------------------------------------------------------


def test_apply_quarantine_removes_quarantined_test() -> None:
    inst = SWEbenchInstance(**{**MINIMAL_INSTANCE.model_dump(), "quarantined_tests": ["TestFlaky"]})
    result = GradeResult(
        resolved=True,
        compiled=True,
        f2p_results=[TestResult("TestFlaky", passed=True), TestResult("TestStable", passed=True)],
        p2p_results=[],
    )
    filtered = apply_quarantine(result, inst)
    assert len(filtered.f2p_results) == 1
    assert filtered.f2p_results[0].name == "TestStable"
    assert filtered.resolved is True


def test_apply_quarantine_quarantined_fail_becomes_resolved() -> None:
    """A previously-failing quarantined test should not block resolution."""
    inst = SWEbenchInstance(**{**MINIMAL_INSTANCE.model_dump(), "quarantined_tests": ["TestFlaky"]})
    result = GradeResult(
        resolved=False,
        compiled=True,
        f2p_results=[TestResult("TestFlaky", passed=False), TestResult("TestStable", passed=True)],
        p2p_results=[],
    )
    filtered = apply_quarantine(result, inst)
    assert filtered.resolved is True


def test_apply_quarantine_etcd_strips_integration_tests() -> None:
    inst = _etcd_instance()
    result = GradeResult(
        resolved=True,
        compiled=True,
        f2p_results=[
            TestResult("TestRaftLeaderIntegration", passed=True),
            TestResult("TestApplyEntry", passed=True),
        ],
        p2p_results=[],
    )
    filtered = apply_quarantine(result, inst)
    names = {r.name for r in filtered.f2p_results}
    assert "TestRaftLeaderIntegration" not in names
    assert "TestApplyEntry" in names


def test_apply_quarantine_etcd_strips_e2e_tests() -> None:
    inst = _etcd_instance()
    result = GradeResult(
        resolved=True,
        compiled=True,
        f2p_results=[TestResult("TestWatch/e2e/basic", passed=True)],
        p2p_results=[],
    )
    filtered = apply_quarantine(result, inst)
    assert filtered.f2p_results == []
    assert filtered.resolved is True  # empty F2P passes by default


def test_apply_quarantine_kubectl_keeps_all_tests() -> None:
    """kubectl should not have the etcd unit-package restriction."""
    result = GradeResult(
        resolved=True,
        compiled=True,
        f2p_results=[TestResult("TestGetIntegration", passed=True)],
        p2p_results=[],
    )
    filtered = apply_quarantine(result, MINIMAL_INSTANCE)
    assert len(filtered.f2p_results) == 1


def test_apply_quarantine_unresolved_when_f2p_fails() -> None:
    result = GradeResult(
        resolved=True,
        compiled=True,
        f2p_results=[TestResult("TestFoo", passed=False)],
        p2p_results=[],
    )
    filtered = apply_quarantine(result, MINIMAL_INSTANCE)
    assert filtered.resolved is False


# ---------------------------------------------------------------------------
# SubprocessGrader
# ---------------------------------------------------------------------------


def _make_grade_output(
    resolved: bool = True,
    compiled: bool = True,
    f2p: list[dict[str, object]] | None = None,
    p2p: list[dict[str, object]] | None = None,
) -> str:
    return json.dumps({
        "resolved": resolved,
        "compiled": compiled,
        "f2p": f2p or [{"name": "TestFoo", "passed": True}],
        "p2p": p2p or [],
        "telemetry": {"wall_clock_s": 12.3},
    })


def test_subprocess_grader_parses_output() -> None:
    grader = SubprocessGrader(binary="swe-grade")
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = _make_grade_output()
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        result = grader.grade(MINIMAL_INSTANCE, "diff --git a/foo.go ...")

    assert result.resolved is True
    assert result.compiled is True
    assert len(result.f2p_results) == 1
    assert result.f2p_results[0].name == "TestFoo"
    assert result.telemetry["wall_clock_s"] == pytest.approx(12.3)


def test_subprocess_grader_raises_on_missing_binary() -> None:
    grader = SubprocessGrader(binary="no-such-binary")
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(GraderError, match="Grade binary not found"):
            grader.grade(MINIMAL_INSTANCE, "patch")


def test_subprocess_grader_raises_on_nonzero_exit() -> None:
    grader = SubprocessGrader()
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "docker error"
    with patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(GraderError, match="exited 1"):
            grader.grade(MINIMAL_INSTANCE, "patch")


def test_subprocess_grader_raises_on_invalid_json() -> None:
    grader = SubprocessGrader()
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "not json"
    mock_proc.stderr = ""
    with patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(GraderError, match="invalid JSON"):
            grader.grade(MINIMAL_INSTANCE, "patch")


def test_subprocess_grader_raises_on_timeout() -> None:
    import subprocess as sp

    grader = SubprocessGrader()
    with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="swe-grade", timeout=600)):
        with pytest.raises(GraderError, match="timed out"):
            grader.grade(MINIMAL_INSTANCE, "patch")


# ---------------------------------------------------------------------------
# safe_grade pipeline
# ---------------------------------------------------------------------------


def test_safe_grade_rejects_test_edit() -> None:
    test_patch = (
        "diff --git a/foo_test.go b/foo_test.go\n"
        "--- a/foo_test.go\n"
        "+++ b/foo_test.go\n"
        "+// tampered\n"
    )
    mock_grader = MagicMock()
    result = safe_grade(MINIMAL_INSTANCE, test_patch, mock_grader)
    assert result.rejected_test_edit is True
    assert result.resolved is False
    mock_grader.grade.assert_not_called()


def test_safe_grade_applies_quarantine() -> None:
    inst = SWEbenchInstance(**{**MINIMAL_INSTANCE.model_dump(), "quarantined_tests": ["TestFlaky"]})
    mock_grader = MagicMock()
    mock_grader.grade.return_value = GradeResult(
        resolved=False,
        compiled=True,
        f2p_results=[TestResult("TestFlaky", passed=False), TestResult("TestStable", passed=True)],
        p2p_results=[],
    )
    result = safe_grade(inst, "diff --git a/foo.go ...", mock_grader)
    names = {r.name for r in result.f2p_results}
    assert "TestFlaky" not in names
    assert result.resolved is True


def test_safe_grade_passes_through_resolved() -> None:
    mock_grader = MagicMock()
    mock_grader.grade.return_value = GradeResult(
        resolved=True,
        compiled=True,
        f2p_results=[TestResult("TestFoo", passed=True)],
        p2p_results=[],
    )
    result = safe_grade(MINIMAL_INSTANCE, "diff --git a/foo.go ...", mock_grader)
    assert result.resolved is True

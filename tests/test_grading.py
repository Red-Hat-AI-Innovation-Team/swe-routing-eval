"""Tests for grading.py: outcome model and anti-reward-hacking helpers."""

from __future__ import annotations

from swe_routing_eval.grading import GradeResult, TestResult, touches_test_files


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
    assert result.telemetry == {}


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
    assert result.resolved is False

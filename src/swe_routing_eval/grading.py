"""Grading engine types, interfaces, and anti-reward-hacking helpers (issues #4–#6).

The production Grader implementation invokes the SWE-benchify grade binary as a
subprocess (issue #2); this module stays decoupled from that detail via Protocol.

Pipeline: touches_test_files check → SubprocessGrader.grade() → apply_quarantine()
All three stages are composed by safe_grade().
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Protocol

from swe_routing_eval.ingest import SWEbenchInstance


@dataclass
class TestResult:
    name: str
    passed: bool


@dataclass
class GradeResult:
    resolved: bool
    compiled: bool
    f2p_results: list[TestResult] = field(default_factory=list)
    p2p_results: list[TestResult] = field(default_factory=list)
    rejected_test_edit: bool = False
    telemetry: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Anti-reward-hacking helpers (issue #5)
# ---------------------------------------------------------------------------


def touches_test_files(patch: str) -> bool:
    """Return True if the patch modifies any *_test.go or testdata/ path.

    Parses unified diff headers (--- / +++ lines) to extract file paths.
    Attempts that touch test files are rejected before grading.
    """
    for line in patch.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        path = line[4:].strip()
        if path.endswith("_test.go") or "/testdata/" in path:
            return True
    return False


# ---------------------------------------------------------------------------
# Grader protocol (issue #4)
# ---------------------------------------------------------------------------


class Grader(Protocol):
    """Interface to the producer's grade() API (issue #2).

    Wraps SWE-benchify's deterministic Docker validation path:
      apply candidate_patch + canonical test_patch at base_commit → run → check F2P/P2P.

    The subprocess-backed implementation slots in here without changing call sites.
    """

    def grade(
        self,
        instance: SWEbenchInstance,
        candidate_patch: str,
    ) -> GradeResult: ...


class GraderError(Exception):
    """Raised when the grade binary fails or returns invalid output."""


class SubprocessGrader:
    """Calls the SWE-benchify grade binary via stdin/stdout JSON (issue #4).

    Full contract: docs/grade-binary-contract.md

    Summary:
      stdin:  JSON — instance metadata + candidate_patch
      stdout: JSON — resolved, compiled, f2p[], p2p[], telemetry
      The binary applies patches in order: base_commit → candidate → test_patch.
      It strips test-file hunks from the candidate before applying (grader-side
      enforcement of the anti-reward-hacking rule; this evaluator also checks via
      touches_test_files() before calling the binary — both must agree).
      stderr: logged on non-zero exit (infrastructure failure only)

    Raises GraderError for: missing binary, timeout, non-zero exit, bad JSON.
    """

    _TIMEOUT_S = 600

    def __init__(self, binary: str = "swe-grade") -> None:
        self._binary = binary

    def grade(
        self,
        instance: SWEbenchInstance,
        candidate_patch: str,
    ) -> GradeResult:
        payload = {
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "base_commit": instance.base_commit,
            "test_patch": instance.test_patch,
            "image_name": instance.image_name,
            "env_spec_hash": instance.env_spec_hash,
            "candidate_patch": candidate_patch,
        }
        try:
            proc = subprocess.run(
                [self._binary],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=self._TIMEOUT_S,
            )
        except FileNotFoundError:
            raise GraderError(
                f"Grade binary not found: {self._binary!r}. "
                "Ensure the SWE-benchify grade binary is on PATH (issue #2)."
            ) from None
        except subprocess.TimeoutExpired:
            raise GraderError(
                f"Grade binary timed out after {self._TIMEOUT_S}s"
            ) from None

        if proc.returncode != 0:
            raise GraderError(
                f"Grade binary exited {proc.returncode}:\n{proc.stderr}"
            )

        try:
            out: dict[str, Any] = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GraderError(
                f"Grade binary produced invalid JSON: {exc}\nstdout: {proc.stdout[:500]}"
            ) from exc

        f2p = [TestResult(name=r["name"], passed=bool(r["passed"])) for r in out.get("f2p", [])]
        p2p = [TestResult(name=r["name"], passed=bool(r["passed"])) for r in out.get("p2p", [])]
        return GradeResult(
            resolved=bool(out.get("resolved", False)),
            compiled=bool(out.get("compiled", False)),
            f2p_results=f2p,
            p2p_results=p2p,
            telemetry=out.get("telemetry", {}),
        )


# ---------------------------------------------------------------------------
# Flake-quarantine application (issue #6)
# ---------------------------------------------------------------------------


def _is_unit_test(test_name: str) -> bool:
    """Return True if the test name does not belong to an integration or e2e package."""
    lower = test_name.lower()
    return "integration" not in lower and "/e2e" not in lower


def apply_quarantine(result: GradeResult, instance: SWEbenchInstance) -> GradeResult:
    """Filter quarantined tests and apply etcd unit-package F2P restriction.

    Rules (inherited from the producer's quarantine metadata):
    - Quarantined tests (any product) never count toward F2P.
    - For etcd, F2P is additionally restricted to unit packages:
      tests whose name contains 'integration' or '/e2e' are excluded.

    Recomputes resolved from the filtered F2P + original P2P.
    """
    quarantine_set = set(instance.quarantined_tests)

    def keep_f2p(r: TestResult) -> bool:
        if r.name in quarantine_set:
            return False
        if instance.product == "etcd" and not _is_unit_test(r.name):
            return False
        return True

    filtered_f2p = [r for r in result.f2p_results if keep_f2p(r)]

    all_f2p_pass = all(r.passed for r in filtered_f2p) if filtered_f2p else True
    all_p2p_pass = all(r.passed for r in result.p2p_results)
    new_resolved = result.compiled and all_f2p_pass and all_p2p_pass

    return GradeResult(
        resolved=new_resolved,
        compiled=result.compiled,
        f2p_results=filtered_f2p,
        p2p_results=result.p2p_results,
        rejected_test_edit=result.rejected_test_edit,
        telemetry=result.telemetry,
    )


# ---------------------------------------------------------------------------
# Full grading pipeline (issues #4 + #5 + #6)
# ---------------------------------------------------------------------------


def safe_grade(
    instance: SWEbenchInstance,
    candidate_patch: str,
    grader: Grader,
) -> GradeResult:
    """Full grading pipeline: anti-reward-hacking → grade → quarantine filter.

    1. Reject patches that touch *_test.go or testdata/ (issue #5).
    2. Call the grader with the candidate patch (issue #4).
    3. Filter quarantined tests and apply etcd unit-package restriction (issue #6).
    """
    if touches_test_files(candidate_patch):
        return GradeResult(resolved=False, compiled=False, rejected_test_edit=True)
    result = grader.grade(instance, candidate_patch)
    return apply_quarantine(result, instance)

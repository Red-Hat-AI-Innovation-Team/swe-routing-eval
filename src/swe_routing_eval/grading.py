"""Grading engine types, interfaces, and anti-reward-hacking helpers (issues #4–#6).

The production Grader implementation invokes the SWE-benchify grade binary as a
subprocess (issue #2); this module stays decoupled from that detail via Protocol.
"""

from __future__ import annotations

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


def touches_test_files(patch: str) -> bool:
    """Return True if the patch modifies any *_test.go or testdata/ path.

    Parses unified diff headers (--- / +++ lines) to extract file paths.
    Used for anti-reward-hacking: attempts that touch test files are rejected
    before grading (issue #5).
    """
    for line in patch.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        path = line[4:].strip()
        if path.endswith("_test.go") or "/testdata/" in path:
            return True
    return False


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

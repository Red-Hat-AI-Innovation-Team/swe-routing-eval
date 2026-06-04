"""GoJSONParser interface and import stub (issue #25).

The engineering plan (§1.2) requires the evaluator to import the *identical*
GoJSONParser module from SWE-benchify so grading and validation cannot
disagree about what "test X passed" means.

Status: BLOCKED on issue #2 (SWE-benchify must factor the parser into a
standalone importable module first). This file defines the Protocol and
ParsedTestResult that the real implementation must satisfy, and provides
a stub that raises ImportError with a clear message until #2 lands.

Once #2 is complete:
1. Add `swebenchify` (or whatever the package is named) to pyproject.toml dependencies.
2. Replace the stub below with the real import.
3. The Protocol check in tests will confirm the imported class is compatible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ParsedTestResult:
    """A single Go test outcome as parsed from `go test -json` output."""

    name: str
    passed: bool
    elapsed_s: float | None = None
    output: str | None = None


@runtime_checkable
class GoJSONParserProtocol(Protocol):
    """Interface the shared GoJSONParser module must satisfy.

    Both the evaluator and the SWE-benchify grade binary must use an
    implementation of this protocol so "test X passed" means the same
    thing on both sides (see docs/grade-binary-contract.md §GoJSONParser).
    """

    def parse(self, go_test_json_output: str) -> list[ParsedTestResult]:
        """Parse the output of `go test -json` into per-test results.

        Args:
            go_test_json_output: The raw newline-delimited JSON produced
                by `go test -json ./...`.

        Returns:
            One ParsedTestResult per test function that was run.
        """
        ...


def get_parser() -> GoJSONParserProtocol:
    """Return the shared GoJSONParser instance.

    Raises ImportError with a clear message until issue #2 lands and the
    real SWE-benchify parser module is available.

    Replace this stub with the real import once #2 is complete::

        from swebenchify.parser import GoJSONParser
        return GoJSONParser()
    """
    raise ImportError(
        "GoJSONParser is not yet available: SWE-benchify has not yet exposed "
        "the parser as a standalone importable module (issue #2). "
        "This function will be implemented once that dependency lands."
    )

"""Tests for parser.py: Protocol structure, ParsedTestResult, and get_parser stub."""

from __future__ import annotations

import pytest

from swe_routing_eval.parser import (
    GoJSONParserProtocol,
    ParsedTestResult,
    get_parser,
)

# ---------------------------------------------------------------------------
# ParsedTestResult
# ---------------------------------------------------------------------------


def test_parsed_test_result_fields() -> None:
    r = ParsedTestResult(name="TestFoo", passed=True, elapsed_s=0.12)
    assert r.name == "TestFoo"
    assert r.passed is True
    assert r.elapsed_s == pytest.approx(0.12)
    assert r.output is None


def test_parsed_test_result_minimal() -> None:
    r = ParsedTestResult(name="TestBar", passed=False)
    assert r.elapsed_s is None
    assert r.output is None


# ---------------------------------------------------------------------------
# GoJSONParserProtocol — structural conformance
# ---------------------------------------------------------------------------


class _ConformingParser:
    """A local implementation that satisfies the Protocol — used to verify
    the Protocol is correctly specified before the real parser lands."""

    def parse(self, go_test_json_output: str) -> list[ParsedTestResult]:
        return [ParsedTestResult(name="TestFoo", passed=True)]


def test_conforming_parser_satisfies_protocol() -> None:
    parser = _ConformingParser()
    assert isinstance(parser, GoJSONParserProtocol)


def test_non_conforming_class_does_not_satisfy_protocol() -> None:
    class _BadParser:
        def parse_output(self, text: str) -> list[ParsedTestResult]:  # wrong method name
            return []

    assert not isinstance(_BadParser(), GoJSONParserProtocol)


def test_conforming_parser_returns_correct_type() -> None:
    parser = _ConformingParser()
    results = parser.parse("{}")
    assert isinstance(results, list)
    assert all(isinstance(r, ParsedTestResult) for r in results)


# ---------------------------------------------------------------------------
# get_parser stub
# ---------------------------------------------------------------------------


def test_get_parser_raises_import_error_until_issue_2_lands() -> None:
    with pytest.raises(ImportError, match="issue #2"):
        get_parser()


def test_get_parser_error_message_is_actionable() -> None:
    with pytest.raises(ImportError) as exc_info:
        get_parser()
    msg = str(exc_info.value)
    assert "swebenchify" in msg or "SWE-benchify" in msg
    assert "#2" in msg

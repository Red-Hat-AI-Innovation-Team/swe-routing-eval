"""Tests for ingest.py: JSONL loading and schema validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_routing_eval.ingest import SchemaError, load

VALID_RECORD: dict[str, object] = {
    "instance_id": "kubectl-12345",
    "repo": "kubernetes/kubectl",
    "base_commit": "abc123def456",
    "patch": "diff --git a/pkg/foo.go b/pkg/foo.go\n--- a/pkg/foo.go\n+++ b/pkg/foo.go\n",
    "test_patch": "diff --git a/pkg/foo_test.go b/pkg/foo_test.go\n",
    "problem_statement": "Fix nil pointer dereference in get command",
    "repo_language": "go",
    "product": "kubectl",
    "fix_merge_date": "2024-01-15",
    "provenance": "github",
    "link_confidence": 0.95,
    "n_fail_to_pass": 2,
    "patch_lines": 10,
    "files_touched": 1,
    "cross_file": False,
    "env_spec_hash": "sha256:deadbeef",
    "image_name": "swebench/kubectl:abc123",
    "compiled": True,
    "n_runs": 3,
    "quarantined_tests": [],
    "decontam_overlap": False,
}


def _write_jsonl(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    p = tmp_path / "instances.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records))
    return p


def test_load_valid_record(tmp_path: Path) -> None:
    p = _write_jsonl(tmp_path, [VALID_RECORD])
    instances = load(p)
    assert len(instances) == 1
    assert instances[0].instance_id == "kubectl-12345"
    assert instances[0].product == "kubectl"
    assert instances[0].quarantined_tests == []


def test_load_multiple_records(tmp_path: Path) -> None:
    second = {**VALID_RECORD, "instance_id": "kubectl-99999"}
    p = _write_jsonl(tmp_path, [VALID_RECORD, second])
    instances = load(p)
    assert len(instances) == 2
    assert instances[1].instance_id == "kubectl-99999"


def test_schema_error_on_missing_required_field(tmp_path: Path) -> None:
    bad = {k: v for k, v in VALID_RECORD.items() if k != "base_commit"}
    p = _write_jsonl(tmp_path, [bad])
    with pytest.raises(SchemaError, match="schema mismatch"):
        load(p)


def test_schema_error_on_unknown_field(tmp_path: Path) -> None:
    bad = {**VALID_RECORD, "mystery_column": "unexpected drift"}
    p = _write_jsonl(tmp_path, [bad])
    with pytest.raises(SchemaError, match="schema mismatch"):
        load(p)


def test_schema_error_on_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.jsonl"
    p.write_text("{not valid json}\n")
    with pytest.raises(SchemaError, match="invalid JSON"):
        load(p)


def test_empty_lines_skipped(tmp_path: Path) -> None:
    p = tmp_path / "instances.jsonl"
    p.write_text("\n" + json.dumps(VALID_RECORD) + "\n\n")
    instances = load(p)
    assert len(instances) == 1

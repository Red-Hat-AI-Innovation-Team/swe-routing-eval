"""Tests for ingest.py: JSONL loading and schema validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_routing_eval.ingest import SchemaError, load

# Canonical record using our field names (compatible with both old and new schema)
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
    "decontamination_overlap": False,  # SWE-benchify canonical field name
}

# SWE-benchify emits additional fields we must accept (not reject as drift)
SWEBENCHIFY_EXTRAS: dict[str, object] = {
    "FAIL_TO_PASS": '["TestFoo", "TestBar"]',
    "PASS_TO_PASS": '["TestBaz"]',
    "hints_text": "some hints",
    "flake_count": 0,
    "decontamination_overlap_source": "msb",
    "version": "1.0",
    "created_at": "2024-01-15T00:00:00Z",
    "environment_setup_commit": "abc123",
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


def test_extra_swebenchify_fields_are_accepted(tmp_path: Path) -> None:
    """SWE-benchify emits extra fields (FAIL_TO_PASS, hints_text, …) — must not raise."""
    record = {**VALID_RECORD, **SWEBENCHIFY_EXTRAS}
    p = _write_jsonl(tmp_path, [record])
    instances = load(p)
    assert len(instances) == 1


def test_decontamination_overlap_alias_accepted(tmp_path: Path) -> None:
    """decontamination_overlap (SWE-benchify) maps to decontam_overlap on the model."""
    record = {**VALID_RECORD}  # already uses decontamination_overlap
    p = _write_jsonl(tmp_path, [record])
    instances = load(p)
    assert instances[0].decontam_overlap is False


def test_fail_to_pass_decoded_from_json_string(tmp_path: Path) -> None:
    record = {**VALID_RECORD, "FAIL_TO_PASS": '["TestFoo", "TestBar"]'}
    p = _write_jsonl(tmp_path, [record])
    instances = load(p)
    assert instances[0].fail_to_pass == ["TestFoo", "TestBar"]


def test_compiled_defaults_to_true_when_absent(tmp_path: Path) -> None:
    record = {k: v for k, v in VALID_RECORD.items() if k != "compiled"}
    p = _write_jsonl(tmp_path, [record])
    instances = load(p)
    assert instances[0].compiled is True


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

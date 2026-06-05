"""Tests for scripts/analyze_runs.py: end-to-end pipeline."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from swe_routing_eval.ingest import SWEbenchInstance
from swe_routing_eval.store import FileStore, RunRecord

_SCRIPT = Path(__file__).parent.parent / "scripts" / "analyze_runs.py"
_spec = importlib.util.spec_from_file_location("analyze_runs", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
main = _mod.main

_OPUS_ID = "claude-opus-4-8-20251001"
_SONNET_ID = "claude-sonnet-4-6-20251001"

_PRICES = {
    _OPUS_ID: {"input_per_1k_tokens": 0.015, "output_per_1k_tokens": 0.075},
    _SONNET_ID: {"input_per_1k_tokens": 0.003, "output_per_1k_tokens": 0.015},
}


def _instance(
    instance_id: str,
    product: str = "kubectl",
    decontam_overlap: bool = False,
) -> SWEbenchInstance:
    return SWEbenchInstance(
        instance_id=instance_id,
        repo=f"kubernetes/{product}",
        base_commit="abc",
        patch="diff --git a/foo.go ...",
        test_patch="",
        problem_statement="Bug",
        repo_language="go",
        product=product,
        fix_merge_date="2024-01-01",
        provenance="github",
        link_confidence=0.9,
        n_fail_to_pass=1,
        patch_lines=5,
        files_touched=1,
        cross_file=False,
        env_spec_hash="sha256:x",
        image_name="img:abc",
        compiled=True,
        n_runs=3,
        quarantined_tests=[],
        decontam_overlap=decontam_overlap,
    )


def _record(
    instance_id: str,
    model_id: str = _OPUS_ID,
    resolved: bool = True,
    attempt_idx: int = 0,
) -> RunRecord:
    return RunRecord(
        model_id=model_id,
        instance_id=instance_id,
        attempt_idx=attempt_idx,
        seed=0,
        scaffold_version="v0.1.0",
        candidate_patch="",
        resolved=resolved,
        compiled=True,
        rejected_test_edit=False,
        f2p_results=[],
        p2p_results=[],
        tokens_in=1000,
        tokens_out=200,
        turns=3,
        tool_calls=5,
        wall_clock_s=10.0,
        cost_usd=0.03,
    )


def _setup(
    tmp_path: Path,
    instances: list[SWEbenchInstance],
    records: list[RunRecord],
) -> tuple[Path, Path, Path]:
    """Write JSONL, price table, and store; return their paths."""
    jsonl = tmp_path / "instances.jsonl"
    jsonl.write_text("\n".join(inst.model_dump_json() for inst in instances))

    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(_PRICES))

    store = FileStore(tmp_path / "runs.db")
    for r in records:
        store.save(r)

    return jsonl, prices, tmp_path / "runs.db"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_analyze_runs_produces_memo(tmp_path: Path) -> None:
    instances = [_instance(f"kubectl-{i}") for i in range(5)]
    records = [_record(f"kubectl-{i}", resolved=i % 2 == 0) for i in range(5)]
    jsonl, prices, db = _setup(tmp_path, instances, records)
    output = tmp_path / "results"

    rc = main([
        "--store", str(db),
        "--instances", str(jsonl),
        "--price-table", str(prices),
        "--output", str(output),
        "--no-plot",
    ])

    assert rc == 0
    memo_path = output / "memo.md"
    assert memo_path.exists()
    memo = memo_path.read_text()
    assert "kubectl" in memo
    assert "Testable-only" in memo          # mandatory caveat
    assert "Single fixed scaffold" in memo


def test_analyze_runs_clean_tier_in_memo(tmp_path: Path) -> None:
    instances = [
        _instance("kubectl-clean", decontam_overlap=False),
        _instance("kubectl-contam", decontam_overlap=True),
    ]
    records = [
        _record("kubectl-clean", resolved=True),
        _record("kubectl-contam", resolved=False),
    ]
    jsonl, prices, db = _setup(tmp_path, instances, records)
    output = tmp_path / "results"

    rc = main([
        "--store", str(db),
        "--instances", str(jsonl),
        "--price-table", str(prices),
        "--output", str(output),
        "--no-plot",
    ])

    assert rc == 0
    memo = (output / "memo.md").read_text()
    assert "clean" in memo
    assert "all" in memo


def test_analyze_runs_with_cascade_point(tmp_path: Path) -> None:
    instances = [_instance(f"kubectl-{i}") for i in range(4)]
    records = (
        [_record(f"kubectl-{i}", model_id=_OPUS_ID, resolved=True) for i in range(4)]
        + [_record(f"kubectl-{i}", model_id=_SONNET_ID, resolved=i % 2 == 0) for i in range(4)]
    )
    jsonl, prices, db = _setup(tmp_path, instances, records)
    output = tmp_path / "results"

    rc = main([
        "--store", str(db),
        "--instances", str(jsonl),
        "--price-table", str(prices),
        "--cascade-tiers", "sonnet", "opus",
        "--output", str(output),
        "--no-plot",
    ])

    assert rc == 0
    memo = (output / "memo.md").read_text()
    assert "cascade" in memo.lower()


def test_analyze_runs_empty_store_returns_nonzero(tmp_path: Path) -> None:
    instances = [_instance("kubectl-1")]
    jsonl, prices, db = _setup(tmp_path, instances, [])
    output = tmp_path / "results"

    rc = main([
        "--store", str(db),
        "--instances", str(jsonl),
        "--price-table", str(prices),
        "--output", str(output),
        "--no-plot",
    ])

    assert rc == 1


def test_analyze_runs_with_audit_note(tmp_path: Path) -> None:
    instances = [_instance(f"kubectl-{i}") for i in range(3)]
    records = [_record(f"kubectl-{i}", resolved=True) for i in range(3)]
    jsonl, prices, db = _setup(tmp_path, instances, records)
    output = tmp_path / "results"

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "verdicts.json").write_text(
        json.dumps({"a": "pass", "b": "fail"})
    )

    rc = main([
        "--store", str(db),
        "--instances", str(jsonl),
        "--price-table", str(prices),
        "--output", str(output),
        "--audit-dir", str(audit_dir),
        "--no-plot",
    ])

    assert rc == 0
    memo = (output / "memo.md").read_text()
    assert "audited" in memo


def test_analyze_runs_n_instances_in_memo(tmp_path: Path) -> None:
    """Sample sizes table shows actual N instances, not placeholder dashes."""
    instances = [_instance(f"kubectl-{i}") for i in range(7)]
    records = [_record(f"kubectl-{i}", resolved=True) for i in range(7)]
    jsonl, prices, db = _setup(tmp_path, instances, records)
    output = tmp_path / "results"

    rc = main([
        "--store", str(db),
        "--instances", str(jsonl),
        "--price-table", str(prices),
        "--output", str(output),
        "--no-plot",
    ])

    assert rc == 0
    memo = (output / "memo.md").read_text()
    # The number of instances (7) should appear in the sample sizes table
    assert "7" in memo


def test_analyze_runs_power_section_when_underpowered(tmp_path: Path) -> None:
    """Power sizing section appears when segment is underpowered."""
    # Only 2 instances → well below any reasonable required N
    instances = [_instance(f"kubectl-{i}") for i in range(2)]
    records = [
        _record(f"kubectl-{i}", model_id=_OPUS_ID, resolved=True) for i in range(2)
    ] + [
        _record(f"kubectl-{i}", model_id=_SONNET_ID, resolved=i % 2 == 0) for i in range(2)
    ]
    jsonl, prices, db = _setup(tmp_path, instances, records)
    output = tmp_path / "results"

    rc = main([
        "--store", str(db),
        "--instances", str(jsonl),
        "--price-table", str(prices),
        "--cascade-tiers", "sonnet", "opus",
        "--delta", "0.10",
        "--output", str(output),
        "--no-plot",
    ])

    assert rc == 0
    memo = (output / "memo.md").read_text()
    assert "Power sizing" in memo

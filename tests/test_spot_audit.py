"""Tests for scripts/spot_audit.py: sampling, bundle rendering, verdict recording."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from swe_routing_eval.ingest import SWEbenchInstance
from swe_routing_eval.store import FileStore, RunRecord

_SCRIPT = Path(__file__).parent.parent / "scripts" / "spot_audit.py"
_spec = importlib.util.spec_from_file_location("spot_audit", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

main = _mod.main
sample_bundles = _mod.sample_bundles
_render_bundle = _mod._render_bundle
_bundle_key = _mod._bundle_key
_load_verdicts = _mod._load_verdicts
_save_verdicts = _mod._save_verdicts

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_OPUS_ID = "claude-opus-4-8-20251001"


def _instance(
    instance_id: str = "kubectl-1",
    product: str = "kubectl",
) -> SWEbenchInstance:
    return SWEbenchInstance(
        instance_id=instance_id,
        repo="kubernetes/kubectl",
        base_commit="abc",
        patch="diff --git a/foo.go b/foo.go\n--- a/foo.go\n+++ b/foo.go\n+// gold fix\n",
        test_patch="",
        problem_statement="Fix nil pointer in get command",
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
        decontam_overlap=False,
    )


def _record(
    instance_id: str = "kubectl-1",
    model_id: str = _OPUS_ID,
    attempt_idx: int = 0,
    resolved: bool = True,
    candidate_patch: str = "diff --git a/foo.go ...\n+// model fix\n",
) -> RunRecord:
    return RunRecord(
        model_id=model_id,
        instance_id=instance_id,
        attempt_idx=attempt_idx,
        seed=42,
        scaffold_version="v0.1.0",
        candidate_patch=candidate_patch,
        resolved=resolved,
        compiled=True,
        rejected_test_edit=False,
        f2p_results=[{"name": "TestFoo", "passed": True}],
        p2p_results=[],
        tokens_in=5000,
        tokens_out=1000,
        turns=5,
        tool_calls=8,
        wall_clock_s=30.0,
        cost_usd=0.0,
    )


# ---------------------------------------------------------------------------
# sample_bundles
# ---------------------------------------------------------------------------


def test_sample_bundles_only_includes_resolved(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    store.save(_record(resolved=True))
    store.save(_record(instance_id="kubectl-2", resolved=False))
    instances = [_instance("kubectl-1"), _instance("kubectl-2")]
    bundles = sample_bundles(store, instances, n_per_segment=10, seed=0)
    assert all(r.resolved for r, _ in bundles)
    assert len(bundles) == 1


def test_sample_bundles_respects_n_per_segment(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    instances = []
    for i in range(10):
        iid = f"kubectl-{i}"
        store.save(_record(instance_id=iid, resolved=True))
        instances.append(_instance(iid))
    bundles = sample_bundles(store, instances, n_per_segment=3, seed=0)
    assert len(bundles) == 3


def test_sample_bundles_one_per_instance_first(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    # Two attempts per instance — should pick at most one per instance
    for attempt in range(3):
        store.save(_record(instance_id="kubectl-1", attempt_idx=attempt))
    instances = [_instance("kubectl-1")]
    bundles = sample_bundles(store, instances, n_per_segment=10, seed=0)
    instance_ids = [r.instance_id for r, _ in bundles]
    assert instance_ids.count("kubectl-1") == 1


def test_sample_bundles_seed_reproducible(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    instances = []
    for i in range(8):
        iid = f"kubectl-{i}"
        store.save(_record(instance_id=iid))
        instances.append(_instance(iid))
    b1 = sample_bundles(store, instances, n_per_segment=4, seed=7)
    b2 = sample_bundles(store, instances, n_per_segment=4, seed=7)
    assert [r.instance_id for r, _ in b1] == [r.instance_id for r, _ in b2]


def test_sample_bundles_empty_store(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    bundles = sample_bundles(store, [_instance()], n_per_segment=5, seed=0)
    assert bundles == []


# ---------------------------------------------------------------------------
# Bundle rendering
# ---------------------------------------------------------------------------


def test_render_bundle_contains_required_sections() -> None:
    record = _record()
    instance = _instance()
    text = _render_bundle(record, instance)
    assert "Problem statement" in text
    assert "Gold patch" in text
    assert "Candidate patch" in text
    assert "Gold vs candidate diff" in text
    assert "F2P test outcomes" in text
    assert "Verdict" in text


def test_render_bundle_includes_problem_statement() -> None:
    record = _record()
    instance = _instance()
    text = _render_bundle(record, instance)
    assert "Fix nil pointer in get command" in text


def test_bundle_key_format() -> None:
    record = _record(instance_id="kubectl-5", attempt_idx=2)
    key = _bundle_key(record)
    assert "kubectl-5" in key
    assert "2" in key


# ---------------------------------------------------------------------------
# Verdict recording and summary
# ---------------------------------------------------------------------------


def test_save_and_load_verdicts(tmp_path: Path) -> None:
    verdicts = {"key1": "pass", "key2": "fail"}
    _save_verdicts(tmp_path, verdicts)
    loaded = _load_verdicts(tmp_path)
    assert loaded == verdicts


def test_load_verdicts_returns_empty_when_no_file(tmp_path: Path) -> None:
    assert _load_verdicts(tmp_path) == {}


def test_record_command_stores_verdict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["record", "--audit-dir", str(tmp_path),
               "--verdict", "kubectl-1__opus__0=pass"])
    assert rc == 0
    verdicts = _load_verdicts(tmp_path)
    assert verdicts["kubectl-1__opus__0"] == "pass"


def test_record_command_rejects_invalid_verdict(tmp_path: Path) -> None:
    rc = main(["record", "--audit-dir", str(tmp_path),
               "--verdict", "key=great"])
    assert rc == 2


def test_summary_command_prints_totals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _save_verdicts(tmp_path, {"a": "pass", "b": "pass", "c": "fail"})
    rc = main(["summary", "--audit-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pass" in out
    assert "fail" in out
    assert "1/3" in out or "33%" in out

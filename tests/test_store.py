"""Tests for store.py: FileStore save/get/list round-trips."""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_routing_eval.store import FileStore, RunRecord


def _make_record(**overrides: object) -> RunRecord:
    defaults: dict[str, object] = dict(
        model_id="claude-opus-4-8",
        instance_id="kubectl-12345",
        attempt_idx=0,
        seed=42,
        scaffold_version="v0.1.0",
        candidate_patch="--- a/foo.go\n+++ b/foo.go\n+\t// fix\n",
        resolved=True,
        compiled=True,
        rejected_test_edit=False,
        f2p_results=[{"name": "TestFoo", "passed": True}],
        p2p_results=[],
        tokens_in=5_000,
        tokens_out=1_500,
        turns=8,
        tool_calls=12,
        wall_clock_s=47.3,
        cost_usd=0.0,
    )
    return RunRecord(**{**defaults, **overrides})


def test_save_and_get_round_trip(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    record = _make_record()
    store.save(record)
    fetched = store.get("claude-opus-4-8", "kubectl-12345", 0)
    assert fetched.resolved is True
    assert fetched.compiled is True
    assert fetched.tokens_in == 5_000
    assert fetched.f2p_results == [{"name": "TestFoo", "passed": True}]
    assert fetched.p2p_results == []


def test_bool_fields_survive_sqlite(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    store.save(_make_record(resolved=False, compiled=False, rejected_test_edit=True))
    fetched = store.get("claude-opus-4-8", "kubectl-12345", 0)
    assert fetched.resolved is False
    assert fetched.compiled is False
    assert fetched.rejected_test_edit is True


def test_get_missing_raises_key_error(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    with pytest.raises(KeyError):
        store.get("no-such-model", "no-such-instance", 0)


def test_list_all_returns_all_records(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    for i in range(3):
        store.save(_make_record(attempt_idx=i, seed=i))
    records = store.list_all()
    assert len(records) == 3
    attempt_indices = {r.attempt_idx for r in records}
    assert attempt_indices == {0, 1, 2}


def test_save_overwrites_on_duplicate_key(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    store.save(_make_record(resolved=True))
    store.save(_make_record(resolved=False))
    fetched = store.get("claude-opus-4-8", "kubectl-12345", 0)
    assert fetched.resolved is False


def test_store_persists_across_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.db"
    FileStore(db_path).save(_make_record())
    fetched = FileStore(db_path).get("claude-opus-4-8", "kubectl-12345", 0)
    assert fetched.instance_id == "kubectl-12345"

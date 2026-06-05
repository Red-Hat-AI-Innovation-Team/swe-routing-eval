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


def test_grader_error_round_trips(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    store.save(_make_record(grader_error="Docker timeout after 600s"))
    fetched = store.get("claude-opus-4-8", "kubectl-12345", 0)
    assert fetched.grader_error == "Docker timeout after 600s"


def test_grader_error_defaults_to_empty_string(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "runs.db")
    store.save(_make_record())
    fetched = store.get("claude-opus-4-8", "kubectl-12345", 0)
    assert fetched.grader_error == ""


def test_migration_adds_grader_error_column_to_old_db(tmp_path: Path) -> None:
    """FileStore must silently upgrade a DB that predates the grader_error column."""
    import sqlite3

    db_path = tmp_path / "old.db"

    # Build a schema and row that match the pre-migration layout (no grader_error column).
    old_schema = """
        CREATE TABLE runs (
            model_id TEXT NOT NULL, instance_id TEXT NOT NULL,
            attempt_idx INTEGER NOT NULL, seed INTEGER NOT NULL,
            scaffold_version TEXT NOT NULL, candidate_patch TEXT NOT NULL,
            resolved INTEGER NOT NULL, compiled INTEGER NOT NULL,
            rejected_test_edit INTEGER NOT NULL,
            f2p_results TEXT NOT NULL, p2p_results TEXT NOT NULL,
            tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL,
            turns INTEGER NOT NULL, tool_calls INTEGER NOT NULL,
            wall_clock_s REAL NOT NULL, cost_usd REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY (model_id, instance_id, attempt_idx)
        )
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute(old_schema)
    conn.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("m", "i", 0, 1, "v0", "", 0, 1, 0, "[]", "[]", 100, 50, 3, 5, 10.0, 0.0),
    )
    conn.commit()
    conn.close()

    # Opening with FileStore should migrate and allow reads without error.
    store = FileStore(db_path)
    record = store.get("m", "i", 0)
    assert record.grader_error == ""

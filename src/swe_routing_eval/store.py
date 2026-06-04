"""Run store: persist every attempt's full record for reproducibility (issue #11).

Keyed on (model_id, instance_id, attempt_idx). SQLite backing enables the
cross-attempt queries needed by the cost model and statistics layers.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class RunRecord:
    model_id: str
    instance_id: str
    attempt_idx: int
    seed: int
    scaffold_version: str
    candidate_patch: str
    resolved: bool
    compiled: bool
    rejected_test_edit: bool
    f2p_results: list[dict[str, object]]
    p2p_results: list[dict[str, object]]
    tokens_in: int
    tokens_out: int
    turns: int
    tool_calls: int
    wall_clock_s: float
    cost_usd: float = field(default=0.0)


_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS runs (
        model_id            TEXT    NOT NULL,
        instance_id         TEXT    NOT NULL,
        attempt_idx         INTEGER NOT NULL,
        seed                INTEGER NOT NULL,
        scaffold_version    TEXT    NOT NULL,
        candidate_patch     TEXT    NOT NULL,
        resolved            INTEGER NOT NULL,
        compiled            INTEGER NOT NULL,
        rejected_test_edit  INTEGER NOT NULL,
        f2p_results         TEXT    NOT NULL,
        p2p_results         TEXT    NOT NULL,
        tokens_in           INTEGER NOT NULL,
        tokens_out          INTEGER NOT NULL,
        turns               INTEGER NOT NULL,
        tool_calls          INTEGER NOT NULL,
        wall_clock_s        REAL    NOT NULL,
        cost_usd            REAL    NOT NULL DEFAULT 0.0,
        PRIMARY KEY (model_id, instance_id, attempt_idx)
    )
"""

_INSERT = """
    INSERT OR REPLACE INTO runs VALUES (
        :model_id, :instance_id, :attempt_idx, :seed, :scaffold_version,
        :candidate_patch, :resolved, :compiled, :rejected_test_edit,
        :f2p_results, :p2p_results, :tokens_in, :tokens_out, :turns,
        :tool_calls, :wall_clock_s, :cost_usd
    )
"""


class Store(ABC):
    @abstractmethod
    def save(self, record: RunRecord) -> None: ...

    @abstractmethod
    def get(self, model_id: str, instance_id: str, attempt_idx: int) -> RunRecord: ...

    @abstractmethod
    def list_all(self) -> list[RunRecord]: ...


class FileStore(Store):
    """SQLite-backed run store persisted at a local path."""

    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    def save(self, record: RunRecord) -> None:
        d: dict[str, object] = dict(asdict(record))
        d["f2p_results"] = json.dumps(d["f2p_results"])
        d["p2p_results"] = json.dumps(d["p2p_results"])
        d["resolved"] = int(bool(d["resolved"]))
        d["compiled"] = int(bool(d["compiled"]))
        d["rejected_test_edit"] = int(bool(d["rejected_test_edit"]))
        self._conn.execute(_INSERT, d)
        self._conn.commit()

    def get(self, model_id: str, instance_id: str, attempt_idx: int) -> RunRecord:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE model_id=? AND instance_id=? AND attempt_idx=?",
            (model_id, instance_id, attempt_idx),
        ).fetchone()
        if row is None:
            raise KeyError((model_id, instance_id, attempt_idx))
        return _row_to_record(row)

    def list_all(self) -> list[RunRecord]:
        rows = self._conn.execute("SELECT * FROM runs").fetchall()
        return [_row_to_record(r) for r in rows]


def _row_to_record(row: sqlite3.Row) -> RunRecord:
    d = dict(row)
    d["f2p_results"] = json.loads(d["f2p_results"])
    d["p2p_results"] = json.loads(d["p2p_results"])
    d["resolved"] = bool(d["resolved"])
    d["compiled"] = bool(d["compiled"])
    d["rejected_test_edit"] = bool(d["rejected_test_edit"])
    return RunRecord(**d)

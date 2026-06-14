#!/usr/bin/env python3
"""Re-grade existing runs in the store using stored candidate_patch.

Reads runs matching a SQL WHERE clause, re-runs the grading pipeline
(touches_test_files → SwebenchifyGrader → quarantine), and updates
the run record in-place.

Usage:
    # Preview which runs will be regraded
    python scripts/regrade.py --dry-run \
        --where "instance_id LIKE '%hypershift%' AND cli_scaffold = 1"

    # Regrade with concurrency
    python scripts/regrade.py --workers 4 \
        --where "compiled = 0 AND cli_scaffold = 1"
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swe_routing_eval.grading import (  # noqa: E402
    GraderError,
    SwebenchifyGrader,
    safe_grade,
)
from swe_routing_eval.ingest import SWEbenchInstance, load  # noqa: E402

logger = logging.getLogger(__name__)


def _load_instances(*paths: Path) -> dict[str, SWEbenchInstance]:
    """Load instances from multiple JSONL files; later files win on conflict."""
    by_id: dict[str, SWEbenchInstance] = {}
    for p in paths:
        if not p.exists():
            logger.warning("Instance file not found: %s", p)
            continue
        for inst in load(p):
            existing = by_id.get(inst.instance_id)
            if existing and not existing.image_name and inst.image_name:
                by_id[inst.instance_id] = inst
            elif inst.instance_id not in by_id:
                by_id[inst.instance_id] = inst
    return by_id


def _regrade_one(
    row: dict,
    instance: SWEbenchInstance,
    grader: SwebenchifyGrader,
) -> dict:
    """Re-grade a single run. Returns a dict of updated fields."""
    try:
        grade = safe_grade(instance, row["candidate_patch"], grader)
        return {
            "compiled": int(grade.compiled),
            "resolved": int(grade.resolved),
            "rejected_test_edit": int(grade.rejected_test_edit),
            "f2p_results": json.dumps(
                [{"name": r.name, "passed": r.passed} for r in grade.f2p_results]
            ),
            "p2p_results": json.dumps(
                [{"name": r.name, "passed": r.passed} for r in grade.p2p_results]
            ),
            "grader_error": "",
        }
    except GraderError as exc:
        return {"grader_error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        description="Re-grade runs matching a SQL WHERE clause.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--where", required=True,
        help="SQL WHERE clause to select runs (e.g. \"compiled = 0 AND cli_scaffold = 1\")",
    )
    parser.add_argument(
        "--db", type=Path, default=ROOT / "runs.db",
        help="SQLite run store path (default: runs.db)",
    )
    parser.add_argument(
        "--instances", nargs="+", type=Path,
        default=[ROOT / "instances.jsonl", ROOT / "instances-go.jsonl"],
        help="Instance JSONL files (default: instances.jsonl + instances-go.jsonl)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview which runs would be regraded without calling the grader",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Max concurrent grading threads (default: 1)",
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Skip database backup before regrading",
    )

    args = parser.parse_args(argv)

    instances = _load_instances(*args.instances)
    logger.info("Loaded %d instances from %d files", len(instances), len(args.instances))

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    query = f"SELECT * FROM runs WHERE {args.where}"  # noqa: S608
    try:
        rows = conn.execute(query).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"error: invalid WHERE clause: {exc}", file=sys.stderr)
        return 2

    rows = [dict(r) for r in rows]
    logger.info("Matched %d runs", len(rows))

    if not rows:
        print("No runs matched the WHERE clause.")
        return 0

    missing = [r for r in rows if r["instance_id"] not in instances]
    if missing:
        missing_ids = sorted({r["instance_id"] for r in missing})
        print(
            f"warning: {len(missing)} runs reference {len(missing_ids)} instance(s) "
            f"not found in instance files: {missing_ids[:5]}",
            file=sys.stderr,
        )
        rows = [r for r in rows if r["instance_id"] in instances]

    by_model: dict[str, int] = {}
    by_instance: dict[str, int] = {}
    for r in rows:
        by_model[r["model_id"]] = by_model.get(r["model_id"], 0) + 1
        by_instance[r["instance_id"]] = by_instance.get(r["instance_id"], 0) + 1

    print(f"Runs to regrade: {len(rows)}")
    print(f"  Models:    {dict(sorted(by_model.items()))}")
    print(f"  Instances: {len(by_instance)} distinct")
    print(f"  Current:   compiled={sum(r['compiled'] for r in rows)}/{len(rows)}, "
          f"resolved={sum(r['resolved'] for r in rows)}/{len(rows)}")

    if args.dry_run:
        print("\n--dry-run: no changes made.")
        return 0

    if not args.no_backup:
        backup_path = args.db.with_suffix(".db.bak-regrade")
        shutil.copy2(args.db, backup_path)
        logger.info("Backed up database to %s", backup_path)

    grader = SwebenchifyGrader()
    updated = 0
    errors = 0

    def do_one(row: dict) -> tuple[dict, dict | None]:
        inst = instances[row["instance_id"]]
        result = _regrade_one(row, inst, grader)
        return row, result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(do_one, r): r for r in rows}
        for i, future in enumerate(as_completed(futures), 1):
            row, result = future.result()
            key = (row["model_id"], row["instance_id"], row["attempt_idx"])

            if "grader_error" in result and result.get("compiled") is None:
                errors += 1
                conn.execute(
                    "UPDATE runs SET grader_error = ? "
                    "WHERE model_id = ? AND instance_id = ? AND attempt_idx = ?",
                    (result["grader_error"], *key),
                )
                logger.warning(
                    "[%d/%d] %s → grader error: %s",
                    i, len(rows), key, result["grader_error"],
                )
            else:
                updated += 1
                conn.execute(
                    "UPDATE runs SET compiled = ?, resolved = ?, rejected_test_edit = ?, "
                    "f2p_results = ?, p2p_results = ?, grader_error = ? "
                    "WHERE model_id = ? AND instance_id = ? AND attempt_idx = ?",
                    (
                        result["compiled"], result["resolved"], result["rejected_test_edit"],
                        result["f2p_results"], result["p2p_results"], result["grader_error"],
                        *key,
                    ),
                )
                compiled = result["compiled"]
                if result["resolved"]:
                    status = "resolved"
                elif compiled:
                    status = "compiled"
                else:
                    status = "failed"
                logger.info("[%d/%d] %s → %s", i, len(rows), key, status)

            if i % 10 == 0:
                conn.commit()

    conn.commit()
    conn.close()

    print(f"\nDone: {updated} updated, {errors} grader errors out of {len(rows)} runs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Live progress monitor for eval_sweep.py.

Polls runs.db every N seconds and prints a refreshing status table.

Usage:
    python3 scripts/watch_sweep.py                    # 36-attempt default
    python3 scripts/watch_sweep.py --total 108        # custom total
    python3 scripts/watch_sweep.py --db path/to.db --interval 5
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from swe_routing_eval.store import FileStore, RunRecord


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _render(records: list[RunRecord], total: int, elapsed_s: float) -> str:
    lines: list[str] = []

    done = len(records)
    resolved = sum(r.resolved for r in records)
    spent = sum(r.cost_usd for r in records)

    # --- header ---
    pct = done / total * 100 if total else 0
    bar_width = 40
    filled = int(bar_width * done / total) if total else 0
    bar = "#" * filled + "-" * (bar_width - filled)
    lines.append(f"[{bar}] {done}/{total} ({pct:.0f}%)")
    lines.append(f"resolved: {resolved}  spent: ${spent:.4f}  elapsed: {elapsed_s/60:.1f}m")

    if done > 0 and done < total:
        rate = done / elapsed_s  # attempts per second
        remaining_s = (total - done) / rate
        lines.append(f"eta: ~{remaining_s/60:.0f}m")

    lines.append("")

    # --- per-tier table ---
    by_tier: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        by_tier[r.model_id].append(r)

    if by_tier:
        lines.append(f"{'model':<40}  {'done':>4}  {'resolved':>8}  {'cost':>8}  {'tok_in':>8}  {'tok_out':>8}")
        lines.append("-" * 82)
        for model_id in sorted(by_tier):
            rs = by_tier[model_id]
            n_res = sum(r.resolved for r in rs)
            cost = sum(r.cost_usd for r in rs)
            tok_in = sum(r.tokens_in for r in rs)
            tok_out = sum(r.tokens_out for r in rs)
            lines.append(
                f"{model_id:<40}  {len(rs):>4}  {n_res:>8}  ${cost:>7.4f}  {tok_in:>8,}  {tok_out:>8,}"
            )
        lines.append("")

    # --- last 10 completions ---
    if records:
        lines.append("recent:")
        recent = sorted(records, key=lambda r: (r.model_id, r.instance_id, r.attempt_idx))[-10:]
        for r in reversed(recent):
            status = "OK" if r.resolved else "  "
            lines.append(
                f"  [{status}] {r.model_id.split('/')[-1]:<20}  {r.instance_id:<30}  "
                f"attempt={r.attempt_idx}  ${r.cost_usd:.4f}  {r.wall_clock_s:.0f}s"
            )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch eval_sweep progress")
    parser.add_argument("--db", default="runs.db", help="Path to the SQLite store (default: runs.db)")
    parser.add_argument("--total", type=int, default=36, help="Expected total attempts (default: 36)")
    parser.add_argument("--interval", type=float, default=10.0, help="Refresh interval in seconds (default: 10)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Store not found: {db_path}  (sweep may not have started yet, will retry)")

    t0 = time.monotonic()
    try:
        while True:
            records: list[RunRecord] = []
            if db_path.exists():
                try:
                    records = FileStore(db_path).list_all()
                except Exception as exc:
                    print(f"Could not read store: {exc}")

            _clear()
            print(_render(records, args.total, time.monotonic() - t0))
            print(f"\n(refreshing every {args.interval:.0f}s — Ctrl-C to quit)")

            if records and len(records) >= args.total:
                print("\nSweep complete.")
                break

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""M0 coverage gate: validate gold patches resolve through the grading engine (issue #1).

Usage::

    python scripts/m0_coverage_check.py instances.jsonl [--grade-binary swe-grade]

For each instance, grades the gold patch (instance.patch) and checks:
  1. The patch resolves (100% expected).
  2. The F2P count matches instance.n_fail_to_pass.

Exits 0 on full pass, 1 on any failure.

Note: requires the SWE-benchify grade binary on PATH (issue #2).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swe_routing_eval.grading import SubprocessGrader, safe_grade
from swe_routing_eval.ingest import filter_by_year, load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M0 coverage gate: grade gold patches and verify they all resolve.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("instances_jsonl", type=Path, help="SWE-benchify JSONL file")
    parser.add_argument(
        "--grade-binary",
        default="swe-grade",
        metavar="PATH",
        help="Path to the SWE-benchify grade binary (default: swe-grade)",
    )
    parser.add_argument(
        "--year",
        type=int,
        nargs="+",
        default=None,
        metavar="YEAR",
        help="Only include instances whose fix_merge_date falls in these year(s) "
             "(e.g. --year 2024 or --year 2024 2025)",
    )
    args = parser.parse_args(argv)

    print(f"Loading instances from {args.instances_jsonl} …")
    instances = load(args.instances_jsonl)
    print(f"Loaded {len(instances)} instance(s).")
    if args.year:
        instances = filter_by_year(instances, args.year)
        print(f"After --year {args.year} filter: {len(instances)} instance(s).")
    print("Grading gold patches …\n")

    grader = SubprocessGrader(binary=args.grade_binary)
    failures: list[str] = []

    for inst in instances:
        result = safe_grade(inst, inst.patch, grader)

        f2p_passed = sum(1 for r in result.f2p_results if r.passed)

        if not result.resolved:
            reason = "did not resolve"
            if not result.compiled:
                reason = "did not compile"
            failures.append(f"  FAIL  {inst.instance_id}: gold patch {reason}")
            print(f"FAIL  {inst.instance_id}: {reason}")
            continue

        if f2p_passed != inst.n_fail_to_pass:
            msg = (
                f"F2P count mismatch: expected {inst.n_fail_to_pass}, "
                f"got {f2p_passed}"
            )
            failures.append(f"  FAIL  {inst.instance_id}: {msg}")
            print(f"FAIL  {inst.instance_id}: {msg}")
            continue

        print(f"PASS  {inst.instance_id}: resolved, F2P={f2p_passed}/{inst.n_fail_to_pass}")

    print()
    if failures:
        print(f"M0 FAILED — {len(failures)}/{len(instances)} instance(s) failed:")
        for line in failures:
            print(line)
        return 1

    print(f"M0 PASSED — all {len(instances)} instance(s) resolve with their gold patch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

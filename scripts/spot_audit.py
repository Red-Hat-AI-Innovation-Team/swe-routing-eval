#!/usr/bin/env python3
"""Human spot-audit harness: sample resolved attempts per segment (issue #17).

Generates review bundles for a human to judge whether passing patches are
genuinely correct (not just test-passing). Each bundle contains:
  - the original problem statement
  - the gold (author) patch
  - the model's candidate patch
  - a unified diff between gold and candidate
  - the grading outcome (which F2P tests passed)

Usage — generate bundles::

    python scripts/spot_audit.py \\
        --store runs.db \\
        --instances instances.jsonl \\
        --n 5 \\
        --output audit/

Usage — record verdicts::

    python scripts/spot_audit.py record \\
        --audit-dir audit/ \\
        --verdict kubectl-1__opus__0=pass \\
        --verdict kubectl-2__opus__1=warn

    # or interactively:
    python scripts/spot_audit.py record --audit-dir audit/ --interactive

Usage — summarise::

    python scripts/spot_audit.py summary --audit-dir audit/
"""

from __future__ import annotations

import argparse
import difflib
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

from swe_routing_eval.ingest import SWEbenchInstance, load
from swe_routing_eval.store import FileStore, RunRecord

Verdict = str  # "pass" | "warn" | "fail"
_VALID_VERDICTS = {"pass", "warn", "fail"}


# ---------------------------------------------------------------------------
# Bundle generation
# ---------------------------------------------------------------------------


def _bundle_key(record: RunRecord) -> str:
    return f"{record.instance_id}__{record.model_id.split('-')[1]}__{record.attempt_idx}"


def _gold_candidate_diff(gold: str, candidate: str) -> str:
    gold_lines = gold.splitlines(keepends=True)
    candidate_lines = candidate.splitlines(keepends=True)
    diff = difflib.unified_diff(gold_lines, candidate_lines,
                                fromfile="gold.patch", tofile="candidate.patch")
    return "".join(diff) or "(no difference)\n"


def _render_bundle(
    record: RunRecord,
    instance: SWEbenchInstance,
) -> str:
    f2p_summary = ", ".join(
        f"{'✓' if r['passed'] else '✗'} {r['name']}"
        for r in record.f2p_results
    ) or "(none)"

    diff = _gold_candidate_diff(instance.patch, record.candidate_patch)

    return "\n".join([
        f"# Audit bundle: {_bundle_key(record)}",
        "",
        f"**Instance:** {instance.instance_id}  ",
        f"**Repo:** {instance.repo}  ",
        f"**Model:** {record.model_id}  ",
        f"**Attempt:** {record.attempt_idx}  ",
        f"**Resolved:** {record.resolved}  ",
        "",
        "## Problem statement",
        "",
        instance.problem_statement,
        "",
        "## F2P test outcomes",
        "",
        f2p_summary,
        "",
        "## Gold patch (author's fix)",
        "",
        "```diff",
        instance.patch,
        "```",
        "",
        "## Candidate patch (model's fix)",
        "",
        "```diff",
        record.candidate_patch,
        "```",
        "",
        "## Gold vs candidate diff",
        "",
        "```diff",
        diff,
        "```",
        "",
        "---",
        "",
        "**Verdict:** [ ] pass  [ ] warn  [ ] fail",
        "",
        "**Notes:** ",
        "",
    ])


def sample_bundles(
    store: FileStore,
    instances: list[SWEbenchInstance],
    n_per_segment: int,
    seed: int,
) -> list[tuple[RunRecord, SWEbenchInstance]]:
    """Sample resolved attempts, stratified by instance within each segment.

    Returns at most `n_per_segment` bundles per (product, model_id) combination.
    Stratification ensures we don't over-sample any single instance.
    """
    by_instance = {inst.instance_id: inst for inst in instances}
    resolved_records = [
        r for r in store.list_all()
        if r.resolved and r.instance_id in by_instance
    ]

    rng = random.Random(seed)

    # Group by (product, model_id), then stratify by instance
    groups: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for r in resolved_records:
        inst = by_instance[r.instance_id]
        groups[(inst.product, r.model_id)].append(r)

    selected: list[tuple[RunRecord, SWEbenchInstance]] = []
    for (product, model_id), records in sorted(groups.items()):
        # One attempt per instance first; then backfill if n_per_segment still not met
        by_inst: dict[str, list[RunRecord]] = defaultdict(list)
        for r in records:
            by_inst[r.instance_id].append(r)

        pool: list[RunRecord] = []
        for inst_records in by_inst.values():
            pool.append(rng.choice(inst_records))

        rng.shuffle(pool)
        for r in pool[:n_per_segment]:
            selected.append((r, by_instance[r.instance_id]))

    return selected


def cmd_generate(args: argparse.Namespace) -> int:
    instances = load(args.instances)
    store = FileStore(args.store)
    bundles = sample_bundles(store, instances, n_per_segment=args.n, seed=args.seed)

    if not bundles:
        print("No resolved attempts found in the run store.", file=sys.stderr)
        return 1

    args.output.mkdir(parents=True, exist_ok=True)
    for record, instance in bundles:
        key = _bundle_key(record)
        text = _render_bundle(record, instance)
        (args.output / f"{key}.md").write_text(text)

    print(f"Generated {len(bundles)} bundle(s) in {args.output}/")
    print("Open each .md file, fill in the Verdict line, then run:")
    print(f"  python {Path(__file__).name} record --audit-dir {args.output}/ --interactive")
    return 0


# ---------------------------------------------------------------------------
# Verdict recording
# ---------------------------------------------------------------------------


def _load_verdicts(audit_dir: Path) -> dict[str, Verdict]:
    vf = audit_dir / "verdicts.json"
    if vf.exists():
        return dict(json.loads(vf.read_text()))
    return {}


def _save_verdicts(audit_dir: Path, verdicts: dict[str, Verdict]) -> None:
    vf = audit_dir / "verdicts.json"
    vf.write_text(json.dumps(verdicts, indent=2))


def cmd_record(args: argparse.Namespace) -> int:
    audit_dir: Path = args.audit_dir
    verdicts = _load_verdicts(audit_dir)

    # --verdict KEY=VALUE flags
    for entry in args.verdict or []:
        key, _, value = entry.partition("=")
        if value not in _VALID_VERDICTS:
            print(f"error: {value!r} is not a valid verdict. Use: {_VALID_VERDICTS}",
                  file=sys.stderr)
            return 2
        verdicts[key.strip()] = value.strip()

    # Interactive mode
    if args.interactive:
        bundles = sorted(audit_dir.glob("*.md"))
        for bundle_path in bundles:
            key = bundle_path.stem
            if key in verdicts:
                print(f"  {key}: already recorded ({verdicts[key]}), skipping")
                continue
            print(f"\n{'='*60}")
            print(bundle_path.read_text()[:2000])
            while True:
                verdict = input(f"Verdict for {key} [pass/warn/fail/skip]: ").strip().lower()
                if verdict == "skip":
                    break
                if verdict in _VALID_VERDICTS:
                    verdicts[key] = verdict
                    break
                print("  Enter: pass, warn, fail, or skip")

    _save_verdicts(audit_dir, verdicts)
    print(f"Saved {len(verdicts)} verdict(s) to {audit_dir / 'verdicts.json'}")
    return 0


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def cmd_summary(args: argparse.Namespace) -> int:
    verdicts = _load_verdicts(args.audit_dir)
    if not verdicts:
        print("No verdicts recorded yet.")
        return 0

    counts: dict[Verdict, int] = defaultdict(int)
    for v in verdicts.values():
        counts[v] += 1

    total = len(verdicts)
    print(f"\nSpot-audit summary ({total} bundle(s) reviewed)")
    print("-" * 40)
    for verdict in ["pass", "warn", "fail"]:
        n = counts.get(verdict, 0)
        pct = n / total * 100
        print(f"  {verdict:4s}: {n:3d}  ({pct:.0f}%)")
    print()

    passing_but_wrong = counts.get("fail", 0)
    if passing_but_wrong > 0:
        pct = passing_but_wrong / total * 100
        print(f"⚠  {passing_but_wrong}/{total} ({pct:.0f}%) resolved patches judged wrong.")
        print("   Include this finding in the v1 memo before reporting results.")
    else:
        print("✓  No passing-but-wrong patches found in this sample.")

    return 0


# ---------------------------------------------------------------------------
# Memo note formatter (issue #40)
# ---------------------------------------------------------------------------


def format_memo_note(audit_dir: Path) -> str:
    """Format the spot-audit verdict summary for insertion into render_memo().

    Returns a single ready-to-use sentence for the spot_audit_note parameter
    of render_memo(). The analysis script calls this automatically when an
    audit directory exists.

    Args:
        audit_dir: directory containing verdicts.json produced by `record`.

    Returns:
        A concise summary string, e.g.:
        "10 resolved attempts audited; 1/10 (10%) judged passing-but-wrong;
         2/10 flagged for review."
        If no verdicts have been recorded, returns a placeholder string.
    """
    verdicts = _load_verdicts(audit_dir)
    if not verdicts:
        return "No spot-audit verdicts recorded yet."

    total = len(verdicts)
    n_fail = sum(1 for v in verdicts.values() if v == "fail")
    n_warn = sum(1 for v in verdicts.values() if v == "warn")
    pct_wrong = n_fail / total * 100
    return (
        f"{total} resolved attempt(s) audited; "
        f"{n_fail}/{total} ({pct_wrong:.0f}%) judged passing-but-wrong; "
        f"{n_warn}/{total} flagged for review."
    )


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Human spot-audit harness for resolved eval attempts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    # generate
    gen = sub.add_parser("generate", help="Sample and write audit bundles",
                         aliases=["gen"])
    gen.add_argument("--store", type=Path, default=Path("runs.db"))
    gen.add_argument("--instances", type=Path, required=True,
                     metavar="JSONL", help="SWE-benchify JSONL file")
    gen.add_argument("--n", type=int, default=5,
                     help="Bundles per (segment, model) (default: 5)")
    gen.add_argument("--output", type=Path, default=Path("audit"),
                     metavar="DIR", help="Output directory (default: audit/)")
    gen.add_argument("--seed", type=int, default=42)

    # record
    rec = sub.add_parser("record", help="Record verdicts for bundles")
    rec.add_argument("--audit-dir", type=Path, default=Path("audit"))
    rec.add_argument("--verdict", action="append", metavar="KEY=VERDICT",
                     help="e.g. --verdict kubectl-1__opus__0=pass (repeatable)")
    rec.add_argument("--interactive", action="store_true",
                     help="Walk through each unreviewed bundle interactively")

    # summary
    summ = sub.add_parser("summary", help="Summarise verdicts")
    summ.add_argument("--audit-dir", type=Path, default=Path("audit"))

    args = parser.parse_args(argv)
    if args.command in ("generate", "gen"):
        return cmd_generate(args)
    if args.command == "record":
        return cmd_record(args)
    if args.command == "summary":
        return cmd_summary(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

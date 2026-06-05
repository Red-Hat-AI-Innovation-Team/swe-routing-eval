#!/usr/bin/env python3
"""Eval sweep CLI: run or dry-run the model×instance×attempt matrix (issue #28).

Usage — dry-run (no inference, just cost projection)::

    python scripts/eval_sweep.py instances.jsonl \\
        --tiers opus sonnet \\
        --k 3 \\
        --price-table config/prices.json \\
        --max-spend-usd 50.00 \\
        --dry-run

Usage — full sweep::

    python scripts/eval_sweep.py instances.jsonl \\
        --tiers opus sonnet \\
        --k 3 \\
        --price-table config/prices.json \\
        --max-spend-usd 50.00 \\
        --store runs.db \\
        --workspace-root /tmp/workspaces

Price table JSON format::

    {
        "<pinned-model-id>": {
            "input_per_1k_tokens": 0.015,
            "output_per_1k_tokens": 0.075
        }
    }
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from swe_routing_eval.budget import BudgetConfig
from swe_routing_eval.cost import PriceTable, TierPricing
from swe_routing_eval.grading import SubprocessGrader, SwebenchifyGrader
from swe_routing_eval.ingest import load
from swe_routing_eval.orchestrator import BudgetExceeded, Orchestrator, SweepConfig
from swe_routing_eval.scaffold import Scaffold
from swe_routing_eval.store import FileStore
from swe_routing_eval.vertex import ConfigError, Tier, VertexConfig

_VALID_TIERS: set[Tier] = {"opus", "sonnet", "haiku"}


def _load_price_table(path: Path) -> PriceTable:
    raw = json.loads(path.read_text())
    tiers = {
        model_id: TierPricing(
            input_per_1k_tokens=float(entry["input_per_1k_tokens"]),
            output_per_1k_tokens=float(entry["output_per_1k_tokens"]),
        )
        for model_id, entry in raw.items()
    }
    return PriceTable(tiers=tiers)


def _workspace_factory(workspace_root: Path) -> Callable[[object, int], Path]:

    def factory(instance: object, attempt_idx: int) -> Path:
        from swe_routing_eval.ingest import SWEbenchInstance

        assert isinstance(instance, SWEbenchInstance)
        ws = workspace_root / f"{instance.instance_id}_attempt{attempt_idx}"
        ws.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", f"https://github.com/{instance.repo}.git", str(ws)],
            check=True,
        )
        subprocess.run(
            ["git", "checkout", "--quiet", instance.base_commit],
            cwd=str(ws),
            check=True,
        )
        return ws

    return factory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run or dry-run the eval sweep.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("instances_jsonl", type=Path, help="SWE-benchify JSONL file")
    parser.add_argument(
        "--tiers",
        nargs="+",
        default=["sonnet"],
        metavar="TIER",
        help="Model tiers to evaluate: opus, sonnet, haiku (default: sonnet)",
    )
    parser.add_argument(
        "--k", type=int, default=3, help="Attempts per (model, instance) (default: 3)"
    )
    parser.add_argument(
        "--price-table",
        type=Path,
        required=True,
        metavar="FILE",
        help="JSON file mapping pinned model IDs to Vertex pricing (RH rates)",
    )
    parser.add_argument(
        "--max-spend-usd",
        type=float,
        default=100.0,
        help="Hard spend cap in USD (default: 100.00)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print cost projection and exit — no model inference runs",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=Path("runs.db"),
        metavar="FILE",
        help="SQLite run store path (default: runs.db)",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        metavar="DIR",
        help="Directory for per-attempt repo checkouts (default: system temp dir)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Max concurrent attempts (default: 4)",
    )
    parser.add_argument(
        "--grade-binary",
        default=None,
        metavar="PATH",
        help="Path to swe-grade binary (default: use importable swebenchify.grader.grade)",
    )

    args = parser.parse_args(argv)

    # Validate tiers
    invalid = [t for t in args.tiers if t not in _VALID_TIERS]
    if invalid:
        print(f"error: unknown tiers: {invalid}. Valid: {sorted(_VALID_TIERS)}", file=sys.stderr)
        return 2

    # Load inputs
    print(f"Loading instances from {args.instances_jsonl} …")
    instances = load(args.instances_jsonl)
    print(f"Loaded {len(instances)} instance(s).")

    price_table = _load_price_table(args.price_table)

    # Vertex config (required even for dry-run to resolve model IDs)
    try:
        vertex_config = VertexConfig.from_env()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sweep_config = SweepConfig(
        model_tiers=list(args.tiers),  # type: ignore[arg-type]
        k_attempts=args.k,
        max_workers=args.workers,
    )
    budget = BudgetConfig(max_spend_usd=args.max_spend_usd)

    store = FileStore(args.store)
    scaffold = Scaffold(vertex_config)
    if args.grade_binary:
        grader = SubprocessGrader(binary=args.grade_binary)
    else:
        grader = SwebenchifyGrader()

    orchestrator = Orchestrator(
        store=store,
        scaffold=scaffold,
        grader=grader,
        vertex_config=vertex_config,
        price_table=price_table,
    )

    if args.dry_run:
        try:
            orchestrator.dry_run(sweep_config, instances, budget)
            print("Within budget.")
        except BudgetExceeded as exc:
            print(f"Over budget: {exc}", file=sys.stderr)
            return 1
        return 0

    # Full sweep
    workspace_root = args.workspace_root or Path(tempfile.mkdtemp(prefix="swe-routing-eval-"))
    try:
        orchestrator.run(
            sweep_config,
            instances,
            _workspace_factory(workspace_root),
            budget,
        )
    except BudgetExceeded as exc:
        print(f"Aborted: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

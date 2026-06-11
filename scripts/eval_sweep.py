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
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from swe_routing_eval.budget import BudgetConfig
from swe_routing_eval.cost import PriceTable, TierPricing
from swe_routing_eval.grading import SubprocessGrader, SwebenchifyGrader
from swe_routing_eval.ingest import filter_by_year, load
from swe_routing_eval.orchestrator import (
    BudgetExceeded,
    GraderCircuitBreaker,
    Orchestrator,
    SweepConfig,
)
from swe_routing_eval.scaffold import CLIScaffold, Scaffold
from swe_routing_eval.store import FileStore
from swe_routing_eval.vertex import ConfigError, Tier, VertexConfig

_ANTHROPIC_TIERS: set[Tier] = {"opus", "sonnet", "haiku"}


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
    """Clone each repo once into a shared cache; derive per-attempt workspaces via git worktree.

    Cloning kubernetes or etcd once (~seconds) and then creating lightweight
    worktrees per attempt is orders of magnitude faster than a full clone per attempt.
    """
    import threading

    cache_root = workspace_root / "_cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    _clone_locks: dict[str, threading.Lock] = {}
    _registry_lock = threading.Lock()

    def _get_lock(repo: str) -> threading.Lock:
        with _registry_lock:
            if repo not in _clone_locks:
                _clone_locks[repo] = threading.Lock()
            return _clone_locks[repo]

    def factory(instance: object, attempt_idx: int, model_id: str = "") -> Path:
        from swe_routing_eval.ingest import SWEbenchInstance

        assert isinstance(instance, SWEbenchInstance)
        repo = instance.repo
        cache_name = repo.replace("/", "__")
        cache_path = cache_root / cache_name

        # Clone once per repo; concurrent attempts for the same repo wait on the lock.
        with _get_lock(repo):
            if not cache_path.exists():
                print(f"Cloning {repo} (once) …", flush=True)
                subprocess.run(
                    ["git", "clone", "--quiet",
                     f"https://github.com/{repo}.git", str(cache_path)],
                    check=True,
                )
            # Ensure the required commit is present (in case of shallow clones)
            subprocess.run(
                ["git", "fetch", "--quiet", "origin", instance.base_commit],
                cwd=str(cache_path),
                capture_output=True,  # don't spam if already present
            )

        # Each attempt gets its own worktree at base_commit — lightweight and instant.
        import shutil
        tier_suffix = f"_{model_id.split('-')[1]}" if model_id else ""
        wt_name = f"{instance.instance_id}_attempt{attempt_idx}{tier_suffix}"
        wt_path = workspace_root / wt_name
        if wt_path.exists():
            # Try registered worktree removal first; fall back to plain rmtree
            # (old full-clone directories are not registered worktrees)
            r = subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=str(cache_path),
                capture_output=True,
            )
            if r.returncode != 0 and wt_path.exists():
                shutil.rmtree(wt_path)
        subprocess.run(
            ["git", "worktree", "add", "--detach", "--quiet",
             str(wt_path), instance.base_commit],
            cwd=str(cache_path),
            check=True,
        )
        return wt_path

    return factory


def _workspace_cleanup(workspace_dir: Path) -> None:
    git_file = workspace_dir / ".git"
    if git_file.is_file():
        try:
            content = git_file.read_text().strip()
            if content.startswith("gitdir: "):
                gitdir = Path(content.split("gitdir: ", 1)[1])
                repo_root = gitdir.parent.parent.parent
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(workspace_dir)],
                    cwd=str(repo_root),
                    capture_output=True,
                )
        except Exception:
            pass
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

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

    # Validate tiers
    invalid = [t for t in args.tiers if t not in _ANTHROPIC_TIERS and not t.startswith("gpt-")]
    if invalid:
        print(f"error: unknown tiers: {invalid}. Anthropic: {sorted(_ANTHROPIC_TIERS)}, or any gpt-* tier", file=sys.stderr)
        return 2

    # Load inputs
    print(f"Loading instances from {args.instances_jsonl} …")
    instances = load(args.instances_jsonl)
    print(f"Loaded {len(instances)} instance(s).")
    if args.year:
        instances = filter_by_year(instances, args.year)
        print(f"After --year {args.year} filter: {len(instances)} instance(s).")

    price_table = _load_price_table(args.price_table)

    # Vertex config is only needed when Anthropic tiers are requested.
    needs_vertex = bool(set(args.tiers) & _ANTHROPIC_TIERS)

    vertex_config: VertexConfig | None = None
    if needs_vertex:
        try:
            vertex_config = VertexConfig.from_env()
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if vertex_config is None:
        vertex_config = VertexConfig(
            project_id="", region="",
            opus_model_id="", sonnet_model_id="", haiku_model_id="",
        )

    sweep_config = SweepConfig(
        model_tiers=list(args.tiers),  # type: ignore[arg-type]
        k_attempts=args.k,
        max_workers=args.workers,
    )
    budget = BudgetConfig(max_spend_usd=args.max_spend_usd)

    store = FileStore(args.store)
    scaffold = Scaffold(vertex_config)
    cli_scaffold = CLIScaffold()
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
        cli_scaffold=cli_scaffold,
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
            workspace_cleanup=_workspace_cleanup,
        )
    except BudgetExceeded as exc:
        print(f"Aborted: {exc}", file=sys.stderr)
        return 1
    except GraderCircuitBreaker as exc:
        print(f"Aborted: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Export dashboard data from runs.db + instance JSONL files to a static JSON file.

Usage:
    python scripts/export_dashboard_data.py
    python scripts/export_dashboard_data.py --db runs.db --output docs/data.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from swe_routing_eval.ingest import load as load_instances  # noqa: E402
from swe_routing_eval.store import FileStore  # noqa: E402


def parse_instance_id(instance_id: str) -> tuple[str, str, str]:
    """Parse 'owner__repo-NUM' into (owner, repo_name, issue_num)."""
    owner, rest = instance_id.split("__", 1)
    last_dash = rest.rfind("-")
    repo_name = rest[:last_dash]
    issue_num = rest[last_dash + 1 :]
    return owner, repo_name, issue_num


def issue_url(instance_id: str) -> str:
    owner, repo_name, issue_num = parse_instance_id(instance_id)
    return f"https://github.com/{owner}/{repo_name}/issues/{issue_num}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export dashboard JSON")
    parser.add_argument("--db", default=str(ROOT / "runs.db"))
    parser.add_argument(
        "--instances",
        nargs="+",
        default=[
            str(ROOT / "instances.jsonl"),
            str(ROOT / "instances-go.jsonl"),
            str(ROOT / "instances-python.jsonl"),
            str(ROOT / "instances-rust.jsonl"),
        ],
    )
    parser.add_argument("--output", default=str(ROOT / "docs" / "data.json"))
    args = parser.parse_args()

    EXCLUDED_MODELS = {"claude-haiku-4-5@20251001"}

    store = FileStore(args.db)
    records = [r for r in store.list_all() if r.model_id not in EXCLUDED_MODELS]

    inst_by_id = {}
    for path in args.instances:
        p = Path(path)
        if not p.exists():
            print(f"warning: {p} not found, skipping", file=sys.stderr)
            continue
        for inst in load_instances(p):
            inst_by_id[inst.instance_id] = inst

    run_instance_ids = {r.instance_id for r in records}
    MODEL_ORDER = ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5@20251001"]
    seen_models = {r.model_id for r in records}
    models = [m for m in MODEL_ORDER if m in seen_models] + sorted(
        seen_models - set(MODEL_ORDER)
    )

    instances_out = {}
    for iid in sorted(run_instance_ids):
        inst = inst_by_id.get(iid)
        if inst:
            instances_out[iid] = {
                "repo": inst.repo,
                "language": inst.repo_language,
                "patch_lines": inst.patch_lines,
                "files_touched": inst.files_touched,
                "cross_file": inst.cross_file,
                "n_fail_to_pass": inst.n_fail_to_pass,
                "issue_url": issue_url(iid),
                "fix_merge_date": inst.fix_merge_date,
                "human_patch": inst.patch or "",
            }
        else:
            owner, repo_name, _ = parse_instance_id(iid)
            instances_out[iid] = {
                "repo": f"{owner}/{repo_name}",
                "language": "go",
                "patch_lines": None,
                "files_touched": None,
                "cross_file": None,
                "n_fail_to_pass": None,
                "issue_url": issue_url(iid),
                "fix_merge_date": None,
            }

    runs_out: dict[str, dict[str, list]] = {}
    for r in records:
        model_runs = runs_out.setdefault(r.model_id, {})
        inst_runs = model_runs.setdefault(r.instance_id, [])
        inst_runs.append(
            {
                "a": r.attempt_idx,
                "r": r.resolved,
                "c": r.compiled,
                "f2p": [{"n": t["name"], "p": t["passed"]} for t in r.f2p_results],
                "p2p": [{"n": t["name"], "p": t["passed"]} for t in r.p2p_results],
                "patch": r.candidate_patch,
                "ti": r.tokens_in,
                "to": r.tokens_out,
                "turns": r.turns,
                "tc": r.tool_calls,
                "wall": round(r.wall_clock_s, 1),
                "cost": round(r.cost_usd, 4),
                "cli": r.cli_scaffold,
            }
        )

    for model_runs in runs_out.values():
        for inst_runs in model_runs.values():
            inst_runs.sort(key=lambda x: x["a"])

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": models,
        "instances": instances_out,
        "runs": runs_out,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(output, f, separators=(",", ":"))

    size_kb = out_path.stat().st_size / 1024
    print(
        f"Exported {len(records)} runs across {len(run_instance_ids)} instances "
        f"and {len(models)} models to {out_path} ({size_kb:.1f} KB)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

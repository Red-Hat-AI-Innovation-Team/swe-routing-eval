#!/usr/bin/env python3
"""End-to-end analysis pipeline: run store → frontier + memo + plot (issue #41).

Loads all run-store records, joins with instance metadata, computes per-segment
statistics (clean tier and all instances), cost metrics, cascade points, and
power flags; then writes memo.md and frontier.png to the output directory.

Usage::

    python scripts/analyze_runs.py \\
        --store runs.db \\
        --instances instances.jsonl \\
        --price-table config/prices.json \\
        --tiers opus sonnet haiku \\
        --cascade-tiers haiku sonnet opus \\
        --output results/ \\
        [--audit-dir audit/] \\
        [--delta 0.10] \\
        [--power 0.80]

Outputs::

    results/memo.md       — one-page markdown memo with tables and caveats
    results/frontier.png  — Pareto frontier chart (requires matplotlib)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

from swe_routing_eval.cost import PriceTable, TierPricing, cascade_point
from swe_routing_eval.frontier import FrontierPoint, build_frontier, render_memo
from swe_routing_eval.ingest import SWEbenchInstance, load
from swe_routing_eval.stats import power_flag, segment_stats
from swe_routing_eval.store import FileStore, RunRecord


def _load_price_table(path: Path) -> PriceTable:
    raw = json.loads(path.read_text())
    return PriceTable(tiers={
        model_id: TierPricing(
            input_per_1k_tokens=float(entry["input_per_1k_tokens"]),
            output_per_1k_tokens=float(entry["output_per_1k_tokens"]),
        )
        for model_id, entry in raw.items()
    })


def _spot_audit_note(audit_dir: Path | None) -> str:
    if audit_dir is None or not (audit_dir / "verdicts.json").exists():
        return ""
    _spec = importlib.util.spec_from_file_location(
        "spot_audit",
        Path(__file__).parent / "spot_audit.py",
    )
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    return str(_mod.format_memo_note(audit_dir))  # type: ignore[attr-defined]


def _build_frontier_points(
    instances: list[SWEbenchInstance],
    records: list[RunRecord],
    price_table: PriceTable,
    tiers: list[str],
    cascade_tiers: list[str],
    delta: float,
    target_power: float,
) -> list[FrontierPoint]:
    """Compute all FrontierPoints for each (segment, model) pair."""
    # Map instance_id → instance
    inst_map = {i.instance_id: i for i in instances}

    # Group records by (product/segment, model_id)
    groups: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for r in records:
        inst = inst_map.get(r.instance_id)
        if inst is None:
            continue
        groups[(inst.product, r.model_id)].append(r)

    # Clean instance IDs per segment and contamination presence flag.
    clean_ids_by_seg: dict[str, set[str]] = defaultdict(set)
    seg_has_contaminated: dict[str, bool] = defaultdict(bool)
    for inst in instances:
        if inst.decontam_overlap:
            seg_has_contaminated[inst.product] = True
        else:
            clean_ids_by_seg[inst.product].add(inst.instance_id)

    # Estimate p_discordant per segment using the cheapest and most expensive cascade tiers.
    p_discordant_by_seg: dict[str, float] = {}
    if len(cascade_tiers) >= 2:
        segments_seen = {seg for seg, _ in groups}
        for seg in segments_seen:
            cheap_recs = groups.get((seg, _resolve_model_id(cascade_tiers[0], groups, seg)), [])
            front_recs = groups.get((seg, _resolve_model_id(cascade_tiers[-1], groups, seg)), [])
            p_discordant_by_seg[seg] = _estimate_p_discordant(cheap_recs, front_recs)

    points: list[FrontierPoint] = []

    def _contam_tiers(seg: str) -> list[tuple[str, set[str] | None]]:
        """Return the contamination tiers to compute for a segment.

        Omits the redundant "all" tier when no instance in the segment is
        contaminated (decontam_overlap=True), since clean == all in that case.
        """
        tiers: list[tuple[str, set[str] | None]] = [("clean", clean_ids_by_seg[seg])]
        if seg_has_contaminated[seg]:
            tiers.append(("all", None))
        return tiers

    for (seg, model_id), recs in groups.items():
        for contam_tier, filt in _contam_tiers(seg):
            try:
                stats = segment_stats(
                    seg, model_id, recs, seed=42,
                    instance_filter=filt if contam_tier == "clean" else None,
                )
            except ValueError:
                continue

            cost_per_res = price_table.cost_per_resolved(recs)
            p_discordant = p_discordant_by_seg.get(seg, 0.3)
            try:
                underpowered, required_n = power_flag(
                    stats.n_instances, p_discordant, delta, target_power=target_power
                )
            except ValueError:
                underpowered, required_n = True, 0  # formula undefined → underpowered, N unknown

            points.append(FrontierPoint(
                segment=seg,
                model_id=model_id,
                is_cascade=False,
                cost_per_resolved=cost_per_res,
                resolution_rate=stats.pass_at_1,
                ci_lower=stats.ci_lower,
                ci_upper=stats.ci_upper,
                underpowered=underpowered,
                contamination_tier=contam_tier,
                n_instances=stats.n_instances,
                required_n=required_n,
            ))

    # Cascade points: all adjacent pairs + full chain (if > 2 tiers).
    if len(cascade_tiers) >= 2:
        segments = {p.segment for p in points}
        # Build the list of tier sequences to evaluate.
        tier_sequences: list[list[str]] = []
        for i in range(len(cascade_tiers) - 1):
            tier_sequences.append(cascade_tiers[i : i + 2])  # adjacent pair
        if len(cascade_tiers) > 2:
            tier_sequences.append(cascade_tiers)  # full chain

        for seg in segments:
            for seq in tier_sequences:
                mids = [_resolve_model_id(t, groups, seg) for t in seq]
                recs_by_mid = [groups.get((seg, mid), []) for mid in mids]
                if any(not r for r in recs_by_mid):
                    continue
                for contam_tier, filt in _contam_tiers(seg):
                    filtered = [
                        [r for r in recs if filt is None or r.instance_id in filt]
                        for recs in recs_by_mid
                    ]
                    if any(not f for f in filtered):
                        continue
                    tier_stats = [
                        (sum(r.resolved for r in f) / len(f), price_table.expected_cost(f))
                        for f in filtered
                    ]
                    p_casc, e_casc = cascade_point(tier_stats)
                    cost_per_res = e_casc / p_casc if p_casc > 0 else float("inf")
                    points.append(FrontierPoint(
                        segment=seg,
                        model_id="→".join(mids),
                        is_cascade=True,
                        cost_per_resolved=cost_per_res,
                        resolution_rate=p_casc,
                        ci_lower=p_casc,
                        ci_upper=p_casc,
                        underpowered=False,
                        contamination_tier=contam_tier,
                    ))

    return points


def _resolve_model_id(tier: str, groups: dict[tuple[str, str], list[RunRecord]], seg: str) -> str:
    """Find the model_id in groups for a given segment that matches the tier label."""
    for (s, mid), _ in groups.items():
        if s == seg and tier.lower() in mid.lower():
            return mid
    return tier  # fall back to the tier string itself


def _estimate_p_discordant(
    cheap_records: list[RunRecord],
    frontier_records: list[RunRecord],
) -> float:
    """Fraction of instances where cheap and frontier tiers disagree."""
    cheap_by_inst = {r.instance_id: r.resolved for r in cheap_records}
    front_by_inst = {r.instance_id: r.resolved for r in frontier_records}
    shared = set(cheap_by_inst) & set(front_by_inst)
    if not shared:
        return 0.30  # fallback if no shared instances
    discordant = sum(
        1 for iid in shared if cheap_by_inst[iid] != front_by_inst[iid]
    )
    return discordant / len(shared)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze eval runs and produce frontier + memo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--store", type=Path, default=Path("runs.db"))
    parser.add_argument(
        "--instances", type=Path, required=True, metavar="JSONL",
        help="SWE-benchify JSONL with instance metadata"
    )
    parser.add_argument(
        "--price-table", type=Path, required=True, metavar="FILE",
        help="JSON pricing file (see config/prices.example.json)"
    )
    parser.add_argument(
        "--tiers", nargs="+", default=None, metavar="TIER",
        help="Restrict to these model tier labels (substring match against model_id)"
    )
    parser.add_argument(
        "--cascade-tiers", nargs="+", default=[], metavar="TIER",
        help="Ordered tier labels for cascade analysis, cheapest first "
             "(e.g. --cascade-tiers haiku sonnet opus). Emits cascade points "
             "for every adjacent pair and the full chain."
    )
    parser.add_argument(
        "--output", type=Path, default=Path("results"), metavar="DIR",
        help="Output directory for memo.md and frontier.png (default: results/)"
    )
    parser.add_argument(
        "--audit-dir", type=Path, default=None, metavar="DIR",
        help="Directory with spot-audit verdicts.json (optional)"
    )
    parser.add_argument(
        "--delta", type=float, default=0.10,
        help="Minimum detectable effect size for power sizing (default: 0.10)"
    )
    parser.add_argument(
        "--power", type=float, default=0.80,
        help="Target power for Connor power sizing (default: 0.80)"
    )
    parser.add_argument(
        "--no-plot", action="store_true",
        help="Skip frontier.png (useful if matplotlib is not installed)"
    )

    args = parser.parse_args(argv)

    print(f"Loading instances from {args.instances} …")
    instances = load(args.instances)
    print(f"Loaded {len(instances)} instance(s).")

    print(f"Loading run store from {args.store} …")
    store = FileStore(args.store)
    all_records = store.list_all()
    print(f"Loaded {len(all_records)} run record(s).")

    if not all_records:
        print("No records in store — nothing to analyse.", file=sys.stderr)
        return 1

    price_table = _load_price_table(args.price_table)

    print("Computing frontier points …")
    frontier_points = _build_frontier_points(
        instances=instances,
        records=all_records,
        price_table=price_table,
        tiers=args.tiers or [],
        cascade_tiers=args.cascade_tiers,
        delta=args.delta,
        target_power=args.power,
    )

    if not frontier_points:
        print("No frontier points computed — check that records match instances.", file=sys.stderr)
        return 1

    args.output.mkdir(parents=True, exist_ok=True)

    audit_note = _spot_audit_note(args.audit_dir)
    memo = render_memo(frontier_points, spot_audit_note=audit_note)
    memo_path = args.output / "memo.md"
    memo_path.write_text(memo)
    print(f"Wrote {memo_path}")

    if not args.no_plot:
        try:
            from swe_routing_eval.frontier import plot_frontier
            plot_path = args.output / "frontier.png"
            plot_frontier(build_frontier(frontier_points), plot_path)
            print(f"Wrote {plot_path}")
        except ImportError:
            print("matplotlib not available — skipping frontier.png (use --no-plot to suppress)")

    pareto = build_frontier(frontier_points)
    underpowered = [p for p in frontier_points if p.underpowered and not p.is_cascade]
    print(
        f"\nDone. {len(pareto)} Pareto-optimal point(s); "
        f"{len(underpowered)} underpowered segment(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

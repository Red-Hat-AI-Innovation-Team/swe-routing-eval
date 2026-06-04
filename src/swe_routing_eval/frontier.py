"""Pareto frontier and v1 deliverable (issue #16).

One point per tier + cascade point per segment, with bootstrap CIs.
Axes: x = expected cost per resolved bug, y = resolution rate.

For matplotlib plots: pip install -e ".[analysis]"
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class FrontierPoint:
    segment: str
    model_id: str
    is_cascade: bool
    cost_per_resolved: float
    resolution_rate: float
    ci_lower: float
    ci_upper: float
    underpowered: bool = False
    contamination_tier: str = "all"  # "clean" (decontam_overlap=False only) or "all"
    n_instances: int = 0      # instances evaluated; 0 for cascade points
    required_n: int = 0       # Connor required N; 0 when adequately powered or cascade

    @property
    def label(self) -> str:
        if self.is_cascade:
            return f"{self.segment} cascade"
        short_id = self.model_id.split("-")[1] if "-" in self.model_id else self.model_id
        return f"{self.segment} {short_id}"


def build_frontier(points: list[FrontierPoint]) -> list[FrontierPoint]:
    """Return the Pareto-optimal subset sorted by ascending cost.

    A point is on the frontier if no *other* point strictly dominates it on
    at least one axis while being at least as good on the other:
        dominated(p) ⟺ ∃q≠p: q.cost ≤ p.cost AND q.rate ≥ p.rate
                            AND (q.cost < p.cost OR q.rate > p.rate)
    """
    frontier = []
    for p in points:
        dominated = any(
            (
                o.cost_per_resolved <= p.cost_per_resolved
                and o.resolution_rate >= p.resolution_rate
                and (
                    o.cost_per_resolved < p.cost_per_resolved
                    or o.resolution_rate > p.resolution_rate
                )
            )
            for o in points
            if o is not p
        )
        if not dominated:
            frontier.append(p)
    return sorted(frontier, key=lambda p: p.cost_per_resolved)


_CAVEATS = """\
**Standing caveats (v1)**

1. **Testable-only.** Results cover only instances with runnable Docker test
   environments. Instances that could not be validated are excluded.
2. **Single fixed scaffold.** All models were evaluated with the same
   SWE-agent-style harness (`SCAFFOLD_VERSION`). Scaffold choice is a
   documented confound; a second-scaffold robustness check is deferred.
3. **Contamination tier.** Results are stratified by decontamination-overlap
   flag. The cleanest tier is reported first; contaminated instances are
   shown separately and should be interpreted with caution.
4. **Synthetic excluded.** All instances come from real merged pull requests.
   Synthetic or AI-generated instances are not included.\
"""


def render_memo(
    frontier_points: list[FrontierPoint],
    contamination_note: str = "",
    spot_audit_note: str = "",
) -> str:
    """Generate the v1 one-page memo as a markdown string.

    Args:
        frontier_points: all frontier points (all segments, all tiers + cascade).
            Underpowered points must already have `underpowered=True`.
        contamination_note: optional finding about contamination stratification.
        spot_audit_note: optional finding from the human spot-audit (#17).

    Returns:
        Markdown string suitable for saving as a .md file or rendering to PDF.
    """
    segments = sorted({p.segment for p in frontier_points})

    lines: list[str] = ["# v1 Routing Eval — Result Memo\n"]

    # --- per-segment result ---
    # Within each segment, clean tier (decontam_overlap=False) leads the table.
    lines.append("## Results\n")
    for seg in segments:
        seg_points = [p for p in frontier_points if p.segment == seg]
        pareto = build_frontier(seg_points)
        lines.append(f"### {seg}\n")
        lines.append(
            "| Label | Tier | Resolution rate | 95% CI | Cost/resolved bug | Underpowered |"
        )
        lines.append("|---|---|---|---|---|---|")
        # Sort: clean-first, then by cost within each contamination tier
        def _sort_key(x: FrontierPoint) -> tuple[bool, float]:
            return (x.contamination_tier != "clean", x.cost_per_resolved)

        for p in sorted(seg_points, key=_sort_key):
            on_frontier = "★" if p in pareto else ""
            power_flag_str = "⚠ YES" if p.underpowered else "no"
            lines.append(
                f"| {on_frontier}{p.label} "
                f"| {p.contamination_tier} "
                f"| {p.resolution_rate:.1%} "
                f"| ({p.ci_lower:.1%}, {p.ci_upper:.1%}) "
                f"| ${p.cost_per_resolved:.2f} "
                f"| {power_flag_str} |"
            )
        lines.append("")

    # --- do the segments separate? ---
    lines.append("## Do the segments separate?\n")
    if len(segments) >= 2:
        lines.append(
            "Inspect the frontier points above. If one segment's Pareto frontier "
            "lies strictly above and to the left of another's across all tiers, "
            "that segment benefits more from the frontier tier. "
            "A negative result (overlapping frontiers) is equally informative.\n"
        )
    else:
        lines.append("Only one segment evaluated.\n")

    # --- sample sizes (auto-populated from FrontierPoint.n_instances) ---
    lines.append("## Sample sizes\n")
    lines.append("| Segment | Tier | Model | N instances |")
    lines.append("|---|---|---|---|")
    def _sample_sort(x: FrontierPoint) -> tuple[str, bool, str]:
        return (x.segment, x.contamination_tier != "clean", x.model_id)

    for p in sorted(frontier_points, key=_sample_sort):
        if p.is_cascade:
            continue
        n_str = str(p.n_instances) if p.n_instances > 0 else "—"
        lines.append(f"| {p.segment} | {p.contamination_tier} | {p.model_id} | {n_str} |")
    lines.append("")

    # --- power sizing (shown when any segment is underpowered) ---
    underpowered_points = [p for p in frontier_points if p.underpowered and not p.is_cascade]
    if underpowered_points:
        lines.append("## Power sizing\n")
        lines.append(
            "One or more segments are underpowered. "
            "Point estimates from underpowered segments are inconclusive — "
            "do not interpret as evidence of no difference.\n"
        )
        lines.append("| Segment | Tier | Current N | Required N | Gap | Delta | Power |")
        lines.append("|---|---|---|---|---|---|---|")
        def _power_sort(x: FrontierPoint) -> tuple[str, bool]:
            return (x.segment, x.contamination_tier != "clean")

        for p in sorted(underpowered_points, key=_power_sort):
            gap: int | str = p.required_n - p.n_instances if p.required_n > 0 else "?"
            req = str(p.required_n) if p.required_n > 0 else "?"
            cur = str(p.n_instances) if p.n_instances > 0 else "?"
            tier = p.contamination_tier
            lines.append(f"| {p.segment} | {tier} | {cur} | {req} | {gap} | — | — |")
        lines.append("")
        lines.append(
            "*(Delta and power level used are set by `--delta` and `--power` in `analyze_runs.py`. "
            "Fill in if different from defaults 0.10 / 0.80.)*\n"
        )

    # --- optional notes ---
    if contamination_note:
        lines.append("## Contamination stratification\n")
        lines.append(contamination_note + "\n")

    if spot_audit_note:
        lines.append("## Human spot-audit\n")
        lines.append(spot_audit_note + "\n")

    # --- caveats ---
    lines.append("---\n")
    lines.append(_CAVEATS)

    return "\n".join(lines) + "\n"


def plot_frontier(
    points: list[FrontierPoint],
    output_path: Path,
    *,
    title: str = "Cost / Quality Routing Frontier",
) -> None:
    """Save the Pareto frontier chart as a PNG.

    Requires matplotlib (pip install -e ".[analysis]").

    Args:
        points: all frontier points to plot (filtered or full set).
        output_path: where to write the PNG.
        title: chart title.
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
        from matplotlib.ticker import FuncFormatter
    except ImportError as exc:
        raise ImportError(
            "matplotlib is required for plot_frontier. "
            "Install with: pip install -e '.[analysis]'"
        ) from exc

    fig, ax = plt.subplots(figsize=(8, 5))
    segments = sorted({p.segment for p in points})
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    seg_color = {seg: colors[i % len(colors)] for i, seg in enumerate(segments)}

    pareto = build_frontier(points)

    for p in points:
        color = seg_color[p.segment]
        marker = "D" if p.is_cascade else ("o" if p in pareto else "x")
        alpha = 1.0 if p in pareto else 0.4
        ax.scatter(p.cost_per_resolved, p.resolution_rate,
                   color=color, marker=marker, s=80, alpha=alpha, zorder=3)
        ax.errorbar(p.cost_per_resolved, p.resolution_rate,
                    yerr=[[p.resolution_rate - p.ci_lower],
                          [p.ci_upper - p.resolution_rate]],
                    fmt="none", color=color, alpha=alpha * 0.6, capsize=4)
        if p in pareto or p.is_cascade:
            ax.annotate(
                p.label,
                (p.cost_per_resolved, p.resolution_rate),
                textcoords="offset points",
                xytext=(6, 4),
                fontsize=8,
                color=color,
            )
        if p.underpowered:
            ax.annotate(
                "⚠",
                (p.cost_per_resolved, p.resolution_rate),
                textcoords="offset points",
                xytext=(0, 8),
                fontsize=10,
                ha="center",
            )

    ax.set_xlabel("Expected cost per resolved bug (USD)")
    ax.set_ylabel("Resolution rate")
    ax.set_title(title)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:.0%}"))

    handles = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor=seg_color[s],
               markersize=8, label=s)
        for s in segments
    ]
    handles += [
        Line2D([0], [0], marker="D", color="grey", markersize=8, label="cascade"),
        Line2D([0], [0], marker="x", color="grey", markersize=8,
               alpha=0.4, label="off-frontier"),
    ]
    ax.legend(handles=handles, loc="lower right", fontsize=8)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)

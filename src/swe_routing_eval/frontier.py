"""Pareto frontier and v1 deliverable (issue #16).

One point per tier + cascade point per segment, with bootstrap CIs.
Axes: x = expected cost per resolved bug, y = resolution rate.
"""

from __future__ import annotations

from dataclasses import dataclass


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


def build_frontier(points: list[FrontierPoint]) -> list[FrontierPoint]:
    """Return the Pareto-optimal subset (lowest cost OR highest resolution rate).

    A point is on the frontier if no other point dominates it on both axes
    (lower cost AND higher resolution rate).
    """
    raise NotImplementedError

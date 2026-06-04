"""Statistics: per-segment resolution rates, CIs, paired tests, power sizing (issues #14–#15).

Requires: numpy, scipy (install with `pip install -e ".[analysis]"`)

Bootstrap CI is over instances (not attempts — attempts are nested within instances).
Paired comparison uses McNemar with continuity correction — never unpaired.
Power sizing uses Connor (1987) for paired proportions.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from scipy.stats import chi2, norm

from swe_routing_eval.store import RunRecord


@dataclass
class SegmentResult:
    segment: str
    model_id: str
    n_instances: int
    n_attempts: int
    pass_at_1: float
    ci_lower: float
    ci_upper: float


def bootstrap_ci(
    resolved: list[bool],
    n_boot: int = 10_000,
    alpha: float = 0.05,
    seed: int | None = None,
) -> tuple[float, float]:
    """Bootstrap CI over instance-level resolution.

    Resamples at the *instance* level so the CI reflects uncertainty over the
    instance population, not the attempt distribution.

    Args:
        resolved: one bool per instance (True if any attempt resolved)
        n_boot: number of bootstrap resamples
        alpha: significance level; returns (alpha/2, 1-alpha/2) percentile CI
        seed: RNG seed for reproducibility

    Returns:
        (lower, upper) confidence interval bounds.
    """
    if not resolved:
        raise ValueError("resolved list is empty")
    arr = np.array(resolved, dtype=float)
    rng = np.random.default_rng(seed)
    boot_means = rng.choice(arr, size=(n_boot, len(arr)), replace=True).mean(axis=1)
    lower = float(np.percentile(boot_means, 100.0 * alpha / 2.0))
    upper = float(np.percentile(boot_means, 100.0 * (1.0 - alpha / 2.0)))
    return lower, upper


def mcnemar_test(
    resolved_a: list[bool],
    resolved_b: list[bool],
) -> tuple[float, float]:
    """Paired McNemar test with continuity correction.

    Args:
        resolved_a: per-instance resolution for tier A
        resolved_b: per-instance resolution for tier B (same instances, same order)

    Returns:
        (statistic, p_value). statistic=0, p_value=1 when no discordant pairs.
    """
    if len(resolved_a) != len(resolved_b):
        raise ValueError(
            f"Length mismatch: resolved_a has {len(resolved_a)} items, "
            f"resolved_b has {len(resolved_b)}"
        )
    b = sum(1 for a, bb in zip(resolved_a, resolved_b) if a and not bb)
    c = sum(1 for a, bb in zip(resolved_a, resolved_b) if not a and bb)
    if b + c == 0:
        return 0.0, 1.0
    statistic = float((abs(b - c) - 1) ** 2 / (b + c))
    p_value = float(chi2.sf(statistic, df=1))
    return statistic, p_value


def connor_power(
    p_discordant: float,
    delta: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """Required instances per segment for paired proportions (Connor 1987).

    Args:
        p_discordant: estimated fraction of instances where tiers disagree;
            obtain from the M1 pilot via mcnemar_test discordant pair counts
        delta: minimum detectable difference in resolution rate
        alpha: type-I error rate
        power: desired power (1 - type-II error rate)

    Returns:
        Required number of instances per segment (ceiling).

    Raises:
        ValueError if p_discordant ≤ delta² (formula undefined).
    """
    if not (0.0 < p_discordant <= 1.0):
        raise ValueError(f"p_discordant must be in (0, 1], got {p_discordant}")
    if not (0.0 < delta < 1.0):
        raise ValueError(f"delta must be in (0, 1), got {delta}")
    discriminant = p_discordant - delta ** 2
    if discriminant <= 0:
        raise ValueError(
            f"p_discordant ({p_discordant}) must exceed delta² ({delta**2:.4f}); "
            "try a smaller delta or collect a pilot with higher discordance"
        )
    z_alpha = float(norm.ppf(1.0 - alpha / 2.0))
    z_beta = float(norm.ppf(power))
    numerator = (z_alpha * np.sqrt(p_discordant) + z_beta * np.sqrt(discriminant)) ** 2
    return int(np.ceil(float(numerator) / delta ** 2))


def segment_stats(
    segment: str,
    model_id: str,
    records: list[RunRecord],
    seed: int | None = None,
) -> SegmentResult:
    """Compute per-segment resolution rate with bootstrap CI.

    Groups attempts by instance, computes per-instance any-resolved, then
    bootstraps over instances (not attempts) for the CI.

    Args:
        segment: segment label (e.g. "kubectl", "etcd")
        model_id: pinned model ID for these records
        records: all run-store records for this (segment, model) pair
        seed: RNG seed for reproducibility

    Returns:
        SegmentResult with n_instances, n_attempts, pass_at_1, and bootstrap CI.
    """
    if not records:
        raise ValueError(f"No records for segment={segment!r} model_id={model_id!r}")

    by_instance: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        by_instance[r.instance_id].append(r.resolved)

    instance_resolved = [any(v) for v in by_instance.values()]
    n_instances = len(instance_resolved)
    n_attempts = len(records)
    pass_at_1 = sum(r.resolved for r in records) / n_attempts
    ci_lower, ci_upper = bootstrap_ci(instance_resolved, seed=seed)

    return SegmentResult(
        segment=segment,
        model_id=model_id,
        n_instances=n_instances,
        n_attempts=n_attempts,
        pass_at_1=pass_at_1,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
    )

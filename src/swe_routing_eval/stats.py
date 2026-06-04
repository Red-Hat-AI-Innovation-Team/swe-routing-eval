"""Statistics: per-segment resolution rates, CIs, paired tests, power sizing (issues #14–#15).

Bootstrap CI is over instances (not attempts — attempts are nested within instances).
Paired comparison uses McNemar / paired bootstrap — never unpaired.
Power sizing uses Connor (1987) for paired proportions.
"""

from __future__ import annotations

from dataclasses import dataclass


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
) -> tuple[float, float]:
    """Bootstrap CI over instance-level resolution.

    Args:
        resolved: one bool per instance (True if any attempt resolved)
        n_boot: number of bootstrap resamples
        alpha: significance level; returns (alpha/2, 1-alpha/2) percentile CI

    Returns:
        (lower, upper) confidence interval bounds.
    """
    raise NotImplementedError


def mcnemar_test(
    resolved_a: list[bool],
    resolved_b: list[bool],
) -> tuple[float, float]:
    """Paired McNemar test comparing two tiers over the same instance set.

    Args:
        resolved_a: per-instance resolution for tier A
        resolved_b: per-instance resolution for tier B (same instances, same order)

    Returns:
        (statistic, p_value)
    """
    raise NotImplementedError


def connor_power(
    p_discordant: float,
    delta: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """Required sample size for paired proportions (Connor 1987).

    Args:
        p_discordant: estimated fraction of instances where tiers disagree (from M1 pilot)
        delta: minimum detectable difference in resolution rate
        alpha: type-I error rate
        power: desired power (1 - type-II error rate)

    Returns:
        Required number of instances per segment.
    """
    raise NotImplementedError

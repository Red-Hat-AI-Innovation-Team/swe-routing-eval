"""Tests for stats.py: bootstrap_ci, mcnemar_test, connor_power, segment_stats."""

from __future__ import annotations

import pytest

from swe_routing_eval.stats import (
    SegmentResult,
    bootstrap_ci,
    connor_power,
    mcnemar_test,
    segment_stats,
)
from swe_routing_eval.store import RunRecord

# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------


def test_bootstrap_ci_all_resolved() -> None:
    # All resolved → rate = 1.0, CI should be tight around 1.0
    ci = bootstrap_ci([True] * 50, seed=0)
    assert ci[0] == pytest.approx(1.0)
    assert ci[1] == pytest.approx(1.0)


def test_bootstrap_ci_none_resolved() -> None:
    # None resolved → rate = 0.0, CI should be tight around 0.0
    ci = bootstrap_ci([False] * 50, seed=0)
    assert ci[0] == pytest.approx(0.0)
    assert ci[1] == pytest.approx(0.0)


def test_bootstrap_ci_ordered_bounds() -> None:
    resolved = [True, False] * 25
    lo, hi = bootstrap_ci(resolved, seed=42)
    assert lo <= hi
    assert 0.0 <= lo <= 1.0
    assert 0.0 <= hi <= 1.0


def test_bootstrap_ci_contains_true_rate() -> None:
    # 60% resolved → 95% CI should contain 0.6 for reasonable n
    resolved = [True] * 60 + [False] * 40
    lo, hi = bootstrap_ci(resolved, n_boot=20_000, seed=7)
    assert lo < 0.6 < hi


def test_bootstrap_ci_seed_reproducible() -> None:
    resolved = [True, False] * 20
    assert bootstrap_ci(resolved, seed=1) == bootstrap_ci(resolved, seed=1)


def test_bootstrap_ci_raises_on_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        bootstrap_ci([])


# ---------------------------------------------------------------------------
# mcnemar_test
# ---------------------------------------------------------------------------


def test_mcnemar_no_discordant_pairs() -> None:
    # Both tiers agree on every instance
    stat, p = mcnemar_test([True, False, True], [True, False, True])
    assert stat == 0.0
    assert p == 1.0


def test_mcnemar_all_discordant_one_direction() -> None:
    # A always resolves, B never does → strong evidence of difference
    a = [True] * 20
    b = [False] * 20
    stat, p = mcnemar_test(a, b)
    assert p < 0.05


def test_mcnemar_symmetric_discordance_no_difference() -> None:
    # Equal discordance in both directions → no significant difference
    a = [True, True, False, False] * 5
    b = [True, False, True, False] * 5
    _, p = mcnemar_test(a, b)
    assert p > 0.05


def test_mcnemar_statistic_nonnegative() -> None:
    a = [True, False, True, True]
    b = [False, True, True, False]
    stat, p = mcnemar_test(a, b)
    assert stat >= 0
    assert 0.0 <= p <= 1.0


def test_mcnemar_raises_on_length_mismatch() -> None:
    with pytest.raises(ValueError, match="Length mismatch"):
        mcnemar_test([True, False], [True])


# ---------------------------------------------------------------------------
# connor_power
# ---------------------------------------------------------------------------


def test_connor_power_known_example() -> None:
    # p_d=0.30, delta=0.10, alpha=0.05, power=0.80 → ~234 (standard reference)
    n = connor_power(p_discordant=0.30, delta=0.10)
    assert 220 <= n <= 250  # allow some tolerance on the reference value


def test_connor_power_larger_delta_needs_fewer() -> None:
    n_small_delta = connor_power(p_discordant=0.30, delta=0.05)
    n_large_delta = connor_power(p_discordant=0.30, delta=0.15)
    assert n_small_delta > n_large_delta


def test_connor_power_higher_power_needs_more() -> None:
    n_80 = connor_power(p_discordant=0.30, delta=0.10, power=0.80)
    n_90 = connor_power(p_discordant=0.30, delta=0.10, power=0.90)
    assert n_90 > n_80


def test_connor_power_returns_positive_int() -> None:
    n = connor_power(p_discordant=0.20, delta=0.08)
    assert isinstance(n, int)
    assert n > 0


def test_connor_power_raises_on_invalid_p_discordant() -> None:
    with pytest.raises(ValueError, match="p_discordant"):
        connor_power(p_discordant=0.0, delta=0.1)


def test_connor_power_raises_when_delta_too_large() -> None:
    # delta^2 > p_discordant → formula undefined
    with pytest.raises(ValueError, match="must exceed"):
        connor_power(p_discordant=0.05, delta=0.30)


# ---------------------------------------------------------------------------
# segment_stats
# ---------------------------------------------------------------------------


def _make_record(
    instance_id: str,
    resolved: bool,
    model_id: str = "claude-opus-4-8-20251001",
    attempt_idx: int = 0,
) -> RunRecord:
    return RunRecord(
        model_id=model_id,
        instance_id=instance_id,
        attempt_idx=attempt_idx,
        seed=0,
        scaffold_version="v0.1.0",
        candidate_patch="",
        resolved=resolved,
        compiled=True,
        rejected_test_edit=False,
        f2p_results=[],
        p2p_results=[],
        tokens_in=1000,
        tokens_out=200,
        turns=3,
        tool_calls=5,
        wall_clock_s=10.0,
        cost_usd=0.0,
    )


def test_segment_stats_basic() -> None:
    records = [
        _make_record("inst-1", resolved=True),
        _make_record("inst-2", resolved=False),
        _make_record("inst-3", resolved=True),
    ]
    result = segment_stats("kubectl", "claude-opus-4-8-20251001", records, seed=42)
    assert isinstance(result, SegmentResult)
    assert result.segment == "kubectl"
    assert result.n_instances == 3
    assert result.n_attempts == 3
    assert result.pass_at_1 == pytest.approx(2 / 3)
    assert result.ci_lower <= result.pass_at_1 <= result.ci_upper


def test_segment_stats_any_attempt_counts() -> None:
    # Instance with two attempts: first fails, second resolves → instance resolved
    records = [
        _make_record("inst-1", resolved=False, attempt_idx=0),
        _make_record("inst-1", resolved=True, attempt_idx=1),
        _make_record("inst-2", resolved=False, attempt_idx=0),
        _make_record("inst-2", resolved=False, attempt_idx=1),
    ]
    result = segment_stats("etcd", "claude-opus-4-8-20251001", records, seed=0)
    assert result.n_instances == 2
    assert result.n_attempts == 4
    # inst-1 resolved (any attempt), inst-2 not → 1/2 instances
    assert result.ci_lower <= 0.5 <= result.ci_upper


def test_segment_stats_raises_on_empty() -> None:
    with pytest.raises(ValueError, match="No records"):
        segment_stats("kubectl", "some-model", [])

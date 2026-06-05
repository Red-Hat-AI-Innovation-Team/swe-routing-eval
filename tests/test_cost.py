"""Tests for cost.py: expected_cost, cost_per_resolved, cascade_point."""

from __future__ import annotations

import pytest

from swe_routing_eval.cost import PriceTable, TierPricing, cascade_point
from swe_routing_eval.store import RunRecord

OPUS_ID = "claude-opus-4-8-20251001"
SONNET_ID = "claude-sonnet-4-6-20251001"

TABLE = PriceTable(tiers={
    OPUS_ID: TierPricing(input_per_1k_tokens=0.015, output_per_1k_tokens=0.075),
    SONNET_ID: TierPricing(input_per_1k_tokens=0.003, output_per_1k_tokens=0.015),
})


def _record(
    model_id: str = OPUS_ID,
    resolved: bool = True,
    tokens_in: int = 10_000,
    tokens_out: int = 2_000,
) -> RunRecord:
    return RunRecord(
        model_id=model_id,
        instance_id="kubectl-1",
        attempt_idx=0,
        seed=0,
        scaffold_version="v0.1.0",
        candidate_patch="",
        resolved=resolved,
        compiled=True,
        rejected_test_edit=False,
        f2p_results=[],
        p2p_results=[],
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        turns=5,
        tool_calls=8,
        wall_clock_s=30.0,
        cost_usd=0.0,
    )


# ---------------------------------------------------------------------------
# compute_cost
# ---------------------------------------------------------------------------

def test_compute_cost_opus() -> None:
    r = _record(model_id=OPUS_ID, tokens_in=10_000, tokens_out=2_000)
    # 10 * 0.015 + 2 * 0.075 = 0.15 + 0.15 = 0.30
    assert TABLE.compute_cost(r) == pytest.approx(0.30)


def test_compute_cost_sonnet() -> None:
    r = _record(model_id=SONNET_ID, tokens_in=10_000, tokens_out=2_000)
    # 10 * 0.003 + 2 * 0.015 = 0.03 + 0.03 = 0.06
    assert TABLE.compute_cost(r) == pytest.approx(0.06)


def test_compute_cost_raises_on_unknown_model() -> None:
    r = _record(model_id="unknown-model")
    with pytest.raises(KeyError, match="unknown-model"):
        TABLE.compute_cost(r)


# ---------------------------------------------------------------------------
# expected_cost
# ---------------------------------------------------------------------------

def test_expected_cost_mean_of_records() -> None:
    records = [
        _record(tokens_in=10_000, tokens_out=2_000),   # 0.30
        _record(tokens_in=20_000, tokens_out=4_000),   # 0.60
    ]
    assert TABLE.expected_cost(records) == pytest.approx(0.45)


def test_expected_cost_raises_on_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        TABLE.expected_cost([])


# ---------------------------------------------------------------------------
# cost_per_resolved
# ---------------------------------------------------------------------------

def test_cost_per_resolved_all_resolved() -> None:
    records = [_record(resolved=True), _record(resolved=True)]
    # E[cost] = 0.30, p_hat = 1.0 -> cost_per_resolved = 0.30
    assert TABLE.cost_per_resolved(records) == pytest.approx(0.30)


def test_cost_per_resolved_half_resolved() -> None:
    records = [_record(resolved=True), _record(resolved=False)]
    # E[cost] = 0.30, p_hat = 0.5 -> cost_per_resolved = 0.60
    assert TABLE.cost_per_resolved(records) == pytest.approx(0.60)


def test_cost_per_resolved_none_resolved_returns_inf() -> None:
    records = [_record(resolved=False), _record(resolved=False)]
    assert TABLE.cost_per_resolved(records) == float("inf")


def test_cost_per_resolved_raises_on_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        TABLE.cost_per_resolved([])


# ---------------------------------------------------------------------------
# cascade_point
# ---------------------------------------------------------------------------

def test_cascade_fully_cheap() -> None:
    """If cheap resolves everything, cascade = cheap."""
    p, c = cascade_point([(1.0, 0.10), (0.9, 0.50)])
    assert p == pytest.approx(1.0)
    assert c == pytest.approx(0.10)


def test_cascade_cheap_zero_resolution() -> None:
    """If cheap never resolves, cascade = frontier cost + cheap cost."""
    p, c = cascade_point([(0.0, 0.05), (0.8, 0.40)])
    assert p == pytest.approx(0.8)
    assert c == pytest.approx(0.45)


def test_cascade_mixed() -> None:
    p, c = cascade_point([(0.5, 0.10), (0.8, 0.50)])
    # p = 0.5 + 0.5 * 0.8 = 0.90
    # c = 0.10 + 0.5 * 0.50 = 0.35
    assert p == pytest.approx(0.90)
    assert c == pytest.approx(0.35)


def test_cascade_three_tiers() -> None:
    # haiku: p=0.2, c=0.05; sonnet: p=0.5, c=0.20; opus: p=0.8, c=1.00
    # p = 0.2 + 0.8*0.5 + 0.8*0.5*0.8 = 0.2 + 0.4 + 0.32 = 0.92
    # c = 0.05 + 0.8*0.20 + 0.8*0.5*1.00 = 0.05 + 0.16 + 0.40 = 0.61
    p, c = cascade_point([(0.2, 0.05), (0.5, 0.20), (0.8, 1.00)])
    assert p == pytest.approx(0.92)
    assert c == pytest.approx(0.61)


def test_cascade_raises_on_fewer_than_two_tiers() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        cascade_point([(0.5, 0.10)])


def test_cascade_raises_on_out_of_range() -> None:
    with pytest.raises(ValueError, match=r"tiers\[0\]"):
        cascade_point([(1.5, 0.1), (0.5, 0.5)])
    with pytest.raises(ValueError, match=r"tiers\[1\]"):
        cascade_point([(0.5, 0.1), (-0.1, 0.5)])

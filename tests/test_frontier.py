"""Tests for frontier.py: build_frontier, render_memo, FrontierPoint.label."""

from __future__ import annotations

from swe_routing_eval.frontier import FrontierPoint, build_frontier, render_memo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_OPUS_ID = "claude-opus-4-8-20251001"
_SONNET_ID = "claude-sonnet-4-6-20251001"
_HAIKU_ID = "claude-haiku-4-5-20251001"


def _pt(
    segment: str = "kubectl",
    model_id: str = _SONNET_ID,
    cost: float = 1.0,
    rate: float = 0.5,
    is_cascade: bool = False,
    underpowered: bool = False,
) -> FrontierPoint:
    return FrontierPoint(
        segment=segment,
        model_id=model_id,
        is_cascade=is_cascade,
        cost_per_resolved=cost,
        resolution_rate=rate,
        ci_lower=rate - 0.05,
        ci_upper=rate + 0.05,
        underpowered=underpowered,
    )


# ---------------------------------------------------------------------------
# build_frontier
# ---------------------------------------------------------------------------


def test_frontier_single_point_always_pareto() -> None:
    pt = _pt()
    assert build_frontier([pt]) == [pt]


def test_frontier_dominated_point_excluded() -> None:
    # cheap: low cost, low rate — dominated by sonnet
    cheap = _pt(cost=0.5, rate=0.3)
    # sonnet: low cost AND high rate — dominates cheap
    sonnet = _pt(cost=0.5, rate=0.6)
    frontier = build_frontier([cheap, sonnet])
    assert sonnet in frontier
    assert cheap not in frontier


def test_frontier_neither_dominates_both_included() -> None:
    # lower cost + lower rate vs higher cost + higher rate → neither dominates
    haiku = _pt(model_id=_HAIKU_ID, cost=0.5, rate=0.3)
    opus = _pt(model_id=_OPUS_ID, cost=2.0, rate=0.7)
    frontier = build_frontier([haiku, opus])
    assert haiku in frontier
    assert opus in frontier


def test_frontier_sorted_by_ascending_cost() -> None:
    pts = [_pt(cost=3.0), _pt(cost=1.0), _pt(cost=2.0)]
    frontier = build_frontier(pts)
    costs = [p.cost_per_resolved for p in frontier]
    assert costs == sorted(costs)


def test_frontier_empty_input() -> None:
    assert build_frontier([]) == []


def test_frontier_all_same_cost_keeps_highest_rate() -> None:
    # Same cost → only the one with highest rate survives
    low = _pt(model_id=_HAIKU_ID, cost=1.0, rate=0.2)
    high = _pt(model_id=_OPUS_ID, cost=1.0, rate=0.7)
    frontier = build_frontier([low, high])
    assert high in frontier
    assert low not in frontier


def test_frontier_cascade_point_included() -> None:
    cascade = _pt(is_cascade=True, cost=0.8, rate=0.65)
    tier = _pt(cost=2.0, rate=0.7)
    frontier = build_frontier([cascade, tier])
    assert cascade in frontier


# ---------------------------------------------------------------------------
# FrontierPoint.label
# ---------------------------------------------------------------------------


def test_label_non_cascade() -> None:
    pt = _pt(segment="kubectl", model_id="claude-sonnet-4-6-20251001")
    assert "kubectl" in pt.label
    assert "sonnet" in pt.label


def test_label_cascade() -> None:
    pt = _pt(segment="etcd", is_cascade=True)
    assert "etcd" in pt.label
    assert "cascade" in pt.label


# ---------------------------------------------------------------------------
# render_memo
# ---------------------------------------------------------------------------


def test_render_memo_contains_four_caveats() -> None:
    pts = [_pt("kubectl"), _pt("etcd")]
    memo = render_memo(pts)
    assert "Testable-only" in memo
    assert "Single fixed scaffold" in memo
    assert "Contamination tier" in memo
    assert "Synthetic excluded" in memo


def test_render_memo_flags_underpowered() -> None:
    pts = [_pt("kubectl", underpowered=True)]
    memo = render_memo(pts)
    assert "⚠" in memo or "YES" in memo


def test_render_memo_includes_all_segments() -> None:
    pts = [_pt("kubectl"), _pt("etcd")]
    memo = render_memo(pts)
    assert "kubectl" in memo
    assert "etcd" in memo


def test_render_memo_includes_optional_notes() -> None:
    pts = [_pt()]
    memo = render_memo(pts,
                       contamination_note="Clean tier only.",
                       spot_audit_note="5/5 sampled patches were correct.")
    assert "Clean tier only." in memo
    assert "5/5 sampled" in memo


def test_render_memo_returns_string() -> None:
    pts = [_pt("kubectl"), _pt("kubectl", model_id=_OPUS_ID, cost=2.0, rate=0.7)]
    result = render_memo(pts)
    assert isinstance(result, str)
    assert len(result) > 200


# ---------------------------------------------------------------------------
# contamination_tier field and clean-first ordering — issue #38
# ---------------------------------------------------------------------------


def test_frontier_point_default_contamination_tier() -> None:
    pt = _pt()
    assert pt.contamination_tier == "all"


def test_render_memo_clean_tier_appears_before_contaminated() -> None:
    clean = _pt("kubectl", cost=1.0, rate=0.5)
    clean.contamination_tier = "clean"
    contam = _pt("kubectl", model_id=_OPUS_ID, cost=0.5, rate=0.4)
    contam.contamination_tier = "all"
    memo = render_memo([clean, contam])
    clean_pos = memo.index("clean")
    all_pos = memo.index("all")
    assert clean_pos < all_pos


def test_render_memo_shows_tier_column() -> None:
    pt = _pt()
    pt.contamination_tier = "clean"
    memo = render_memo([pt])
    assert "Tier" in memo
    assert "clean" in memo

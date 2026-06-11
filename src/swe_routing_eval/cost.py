"""Cost model: Vertex pricing and per-attempt cost computation (issue #13).

Pricing is parameterised via a config file — never hard-code Anthropic list prices.
Cost axis uses Vertex pricing at RH committed-use rates.

Key metrics:
  expected_cost(records)  — mean cost per attempt
  cost_per_resolved(records) — E[cost] / p_hat
  cascade_point(...)      — n-tier cascade resolution rate and expected cost
"""

from __future__ import annotations

from dataclasses import dataclass

from swe_routing_eval.store import RunRecord


@dataclass
class TierPricing:
    input_per_1k_tokens: float   # USD
    output_per_1k_tokens: float  # USD


@dataclass
class PriceTable:
    """Vertex pricing keyed by pinned Model Garden model ID."""

    tiers: dict[str, TierPricing]

    def _lookup(self, model_id: str) -> TierPricing:
        pricing = self.tiers.get(model_id)
        if pricing is None and model_id.startswith("gpt-"):
            pricing = self.tiers.get("gpt-*")
        if pricing is None:
            raise KeyError(f"No pricing entry for model_id {model_id!r}")
        return pricing

    def compute_cost(self, record: RunRecord) -> float:
        """Return the USD cost of a single attempt from its telemetry."""
        pricing = self._lookup(record.model_id)
        return (
            record.tokens_in / 1000 * pricing.input_per_1k_tokens
            + record.tokens_out / 1000 * pricing.output_per_1k_tokens
        )

    def expected_cost(self, records: list[RunRecord]) -> float:
        """Mean cost per attempt over the given records."""
        if not records:
            raise ValueError("empty record list")
        return sum(self.compute_cost(r) for r in records) / len(records)

    def cost_per_resolved(self, records: list[RunRecord]) -> float:
        """Expected cost per resolved bug: E[cost] / p_hat.

        Returns inf when no attempt resolved (tier with 0% resolution rate).
        """
        if not records:
            raise ValueError("empty record list")
        e_cost = self.expected_cost(records)
        p_hat = sum(1 for r in records if r.resolved) / len(records)
        if p_hat == 0.0:
            return float("inf")
        return e_cost / p_hat


def cascade_point(tiers: list[tuple[float, float]]) -> tuple[float, float]:
    """N-tier cascade: run each tier in order, escalating only if unresolved.

    Computes cascade statistics analytically — no additional model runs needed.

    Args:
        tiers: list of (p_resolve, e_cost) pairs ordered cheapest-first.
               Must have at least 2 entries.

    Returns:
        (cascade_resolution_rate, cascade_expected_cost)

        For tiers [(p0,c0), (p1,c1), ..., (pn,cn)]:
          p_cascade = p0 + (1−p0)·p1 + (1−p0)·(1−p1)·p2 + …
          e_cost    = c0 + (1−p0)·c1 + (1−p0)·(1−p1)·c2 + …
    """
    if len(tiers) < 2:
        raise ValueError(f"cascade requires at least 2 tiers, got {len(tiers)}")
    for i, (p, _) in enumerate(tiers):
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"tiers[{i}] p={p} must be in [0, 1]")

    p_cascade = 0.0
    e_cost_cascade = 0.0
    p_fail = 1.0  # probability all previous tiers failed
    for p, e_cost in tiers:
        p_cascade += p_fail * p
        e_cost_cascade += p_fail * e_cost
        p_fail *= (1.0 - p)
    return p_cascade, e_cost_cascade

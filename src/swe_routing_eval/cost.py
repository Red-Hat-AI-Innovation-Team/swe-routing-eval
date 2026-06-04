"""Cost model: Vertex pricing and per-attempt cost computation (issue #13).

Pricing is parameterised via a config file — never hard-code Anthropic list prices.
Cost axis uses Vertex pricing at RH committed-use rates.

Key metrics:
  expected_cost(records)  — mean cost per attempt
  cost_per_resolved(records) — E[cost] / p_hat
  cascade_point(...)      — two-tier cascade resolution rate and expected cost
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

    def compute_cost(self, record: RunRecord) -> float:
        """Return the USD cost of a single attempt from its telemetry."""
        pricing = self.tiers.get(record.model_id)
        if pricing is None:
            raise KeyError(f"No pricing entry for model_id {record.model_id!r}")
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


def cascade_point(
    p_cheap: float,
    e_cost_cheap: float,
    p_frontier: float,
    e_cost_frontier: float,
) -> tuple[float, float]:
    """Two-tier cascade: run cheap first, escalate to frontier only if unresolved.

    Computes cascade statistics analytically — no additional model runs needed.

    Args:
        p_cheap: per-attempt resolution rate for the cheap tier.
        e_cost_cheap: expected cost per attempt for the cheap tier.
        p_frontier: per-attempt resolution rate for the frontier tier.
        e_cost_frontier: expected cost per attempt for the frontier tier.

    Returns:
        (cascade_resolution_rate, cascade_expected_cost)

        cascade_resolution_rate = p_cheap + (1 − p_cheap) × p_frontier
        cascade_expected_cost   = E[c_cheap] + (1 − p_cheap) × E[c_frontier]
    """
    if not (0.0 <= p_cheap <= 1.0):
        raise ValueError(f"p_cheap must be in [0, 1], got {p_cheap}")
    if not (0.0 <= p_frontier <= 1.0):
        raise ValueError(f"p_frontier must be in [0, 1], got {p_frontier}")

    p_cascade = p_cheap + (1.0 - p_cheap) * p_frontier
    e_cost_cascade = e_cost_cheap + (1.0 - p_cheap) * e_cost_frontier
    return p_cascade, e_cost_cascade

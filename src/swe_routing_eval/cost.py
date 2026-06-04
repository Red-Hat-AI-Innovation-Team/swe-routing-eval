"""Cost model: Vertex pricing and per-attempt cost computation (issue #13).

Pricing is parameterised via a config file — never hard-code Anthropic list prices.
Cost axis uses Vertex pricing at RH committed-use rates.
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

    def cost_per_resolved(self, records: list[RunRecord]) -> float:
        """Expected cost per resolved bug: E[cost] / p_hat."""
        if not records:
            raise ValueError("empty record list")
        total_cost = sum(self.compute_cost(r) for r in records)
        n_resolved = sum(1 for r in records if r.resolved)
        if n_resolved == 0:
            return float("inf")
        return total_cost / n_resolved

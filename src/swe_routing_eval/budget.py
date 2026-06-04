"""Eval-run budgeting: dry-run projection and spend cap (issue #12).

Frontier-tier runs dominate spend; cheap-tier sweeps are nearly free.
The budget gate exists to prevent accidental overrun on Vertex.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetConfig:
    max_spend_usd: float
    warn_at_fraction: float = 0.8


def dry_run_estimate(
    n_instances: int,
    k_attempts: int,
    avg_tokens_in: int,
    avg_tokens_out: int,
    price_in_per_1k: float,
    price_out_per_1k: float,
) -> float:
    """Estimate total sweep cost without running any model inference.

    Args:
        n_instances: number of instances in the sweep
        k_attempts: attempts per (model, instance)
        avg_tokens_in: expected input tokens per attempt
        avg_tokens_out: expected output tokens per attempt
        price_in_per_1k: Vertex input price per 1k tokens (RH committed-use rate)
        price_out_per_1k: Vertex output price per 1k tokens (RH committed-use rate)

    Returns:
        Projected total cost in USD.
    """
    cost_per_attempt = (
        avg_tokens_in / 1000 * price_in_per_1k
        + avg_tokens_out / 1000 * price_out_per_1k
    )
    return cost_per_attempt * n_instances * k_attempts

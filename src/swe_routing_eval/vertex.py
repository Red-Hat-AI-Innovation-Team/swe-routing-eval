"""Vertex AI provider configuration (issue #7).

No ANTHROPIC_API_KEY — all inference goes through Vertex AI at RH committed-use rates.
Tier model IDs are pinned to RH Model Garden IDs, never aliases, so the exact model
running is always recorded in telemetry and reproducible.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

Tier = Literal["opus", "sonnet", "haiku"]

VERTEX_TIERS: set[str] = {"opus", "sonnet", "haiku"}

_REQUIRED_ENV = {
    "project_id": "ANTHROPIC_VERTEX_PROJECT_ID",
    "region": "CLOUD_ML_REGION",
    "opus_model_id": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet_model_id": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku_model_id": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}


class ConfigError(Exception):
    """Raised when required Vertex environment variables are missing."""


@dataclass(frozen=True)
class VertexConfig:
    """Immutable Vertex AI configuration for a single eval run.

    Always constructed via from_env() in production; construct directly in tests.
    """

    project_id: str
    region: str
    opus_model_id: str
    sonnet_model_id: str
    haiku_model_id: str

    def model_id(self, tier: str) -> str:
        """Return the pinned Model Garden model ID for Vertex tiers, or
        the tier string itself for CLI scaffold tiers."""
        match tier:
            case "opus":
                return self.opus_model_id
            case "sonnet":
                return self.sonnet_model_id
            case "haiku":
                return self.haiku_model_id
            case t if t not in VERTEX_TIERS:
                return t
            case _:
                raise ValueError(f"Unknown tier: {tier!r}")

    @classmethod
    def from_env(cls) -> "VertexConfig":
        """Load configuration from environment variables.

        Required variables:
            ANTHROPIC_VERTEX_PROJECT_ID   — GCP project ID
            CLOUD_ML_REGION               — Vertex region (e.g. us-east5)
            ANTHROPIC_DEFAULT_OPUS_MODEL  — pinned RH Model Garden Opus ID
            ANTHROPIC_DEFAULT_SONNET_MODEL
            ANTHROPIC_DEFAULT_HAIKU_MODEL

        Raises ConfigError listing all missing variables.
        """
        values: dict[str, str] = {}
        missing: list[str] = []
        for field, env_key in _REQUIRED_ENV.items():
            val = os.environ.get(env_key, "").strip()
            if not val:
                missing.append(env_key)
            else:
                values[field] = val
        if missing:
            raise ConfigError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Set these to the RH Model Garden IDs, not aliases. "
                "See docs/PLAN.md §WS-2."
            )
        return cls(**values)

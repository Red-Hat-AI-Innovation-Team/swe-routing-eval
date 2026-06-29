"""OpenAI-compatible provider configuration.

Supports any OpenAI-compatible endpoint (DeepSeek, vLLM, etc.) via base_url.
Tiers: flash, pro.  Model IDs are hardcoded per-provider defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

OPENAI_TIERS: set[str] = {"flash", "pro"}

# DeepSeek defaults — override via constructor for other providers.
_DEFAULT_MODELS: dict[str, str] = {
    "flash": "deepseek-v4-flash",
    "pro": "deepseek-v4-pro",
}


class ConfigError(Exception):
    """Raised when required OpenAI configuration is missing."""


@dataclass(frozen=True)
class OpenAIConfig:
    """Immutable OpenAI-compatible provider configuration.

    Construct directly (tests, scripts) or via from_env() for production.
    """

    api_key: str
    base_url: str
    flash_model_id: str = _DEFAULT_MODELS["flash"]
    pro_model_id: str = _DEFAULT_MODELS["pro"]

    def model_id(self, tier: str) -> str:
        """Return the model ID for a given tier."""
        match tier:
            case "flash":
                return self.flash_model_id
            case "pro":
                return self.pro_model_id
            case _:
                raise ValueError(f"Unknown OpenAI tier: {tier!r}")

    @classmethod
    def from_env(cls) -> "OpenAIConfig":
        """Load config from OPENAI_API_KEY and OPENAI_BASE_URL env vars.

        Raises ConfigError if either is missing.
        """
        missing: list[str] = []
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            missing.append("OPENAI_API_KEY")
        base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
        if not base_url:
            missing.append("OPENAI_BASE_URL")
        if missing:
            raise ConfigError(
                f"Missing required environment variable(s): {', '.join(missing)}"
            )
        return cls(api_key=api_key, base_url=base_url)

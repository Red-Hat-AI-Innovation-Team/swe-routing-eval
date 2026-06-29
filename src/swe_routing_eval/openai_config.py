"""OpenAI Chat Completions - compatible provider configuration.

Supports any OpenAI Chat Completions - compatible endpoint via base_url.
DeepSeekConfig inherits from OpenAIChatCompletionsConfig with DeepSeek-specific defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

OPENAI_TIERS: set[str] = {"mini", "pro"}


class ConfigError(Exception):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class OpenAIChatCompletionsConfig:
    """Immutable OpenAI-compatible provider configuration.

    Construct directly (tests, scripts) or via from_env() for production.
    """

    api_key: str
    base_url: str
    mini_model_id: str = "gpt-4.1-mini"
    pro_model_id: str = "gpt-4.1"

    def model_id(self, tier: str) -> str:
        """Return the model ID for a given tier."""
        match tier:
            case "mini":
                return self.mini_model_id
            case "pro":
                return self.pro_model_id
            case _:
                raise ValueError(f"Unknown tier: {tier!r}")

    @classmethod
    def from_env(cls) -> OpenAIChatCompletionsConfig:
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


@dataclass(frozen=True)
class DeepSeekConfig(OpenAIChatCompletionsConfig):
    """DeepSeek-specific configuration.

    Defaults to DeepSeek model IDs and reads DEEPSEEK_* env vars.
    """

    mini_model_id: str = "deepseek-v4-flash"
    pro_model_id: str = "deepseek-v4-pro"

    @classmethod
    def from_env(cls) -> DeepSeekConfig:
        """Load config from DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL env vars.

        Raises ConfigError if either is missing.
        """
        missing: list[str] = []
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            missing.append("DEEPSEEK_API_KEY")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "").strip()
        if not base_url:
            missing.append("DEEPSEEK_BASE_URL")
        if missing:
            raise ConfigError(
                f"Missing required environment variable(s): {', '.join(missing)}"
            )
        return cls(api_key=api_key, base_url=base_url)

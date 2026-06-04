"""Tests for vertex.py: VertexConfig loading and model ID resolution."""

from __future__ import annotations

import pytest

from swe_routing_eval.vertex import ConfigError, VertexConfig

_ALL_VARS = {
    "ANTHROPIC_VERTEX_PROJECT_ID": "my-project",
    "CLOUD_ML_REGION": "us-east5",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8-20251001",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-6-20251001",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5-20251001",
}


def test_from_env_loads_all_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _ALL_VARS.items():
        monkeypatch.setenv(k, v)
    cfg = VertexConfig.from_env()
    assert cfg.project_id == "my-project"
    assert cfg.region == "us-east5"
    assert cfg.opus_model_id == "claude-opus-4-8-20251001"
    assert cfg.sonnet_model_id == "claude-sonnet-4-6-20251001"
    assert cfg.haiku_model_id == "claude-haiku-4-5-20251001"


def test_from_env_raises_when_all_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in _ALL_VARS:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ConfigError, match="Missing required"):
        VertexConfig.from_env()


def test_from_env_error_message_lists_missing_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in _ALL_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "p")
    monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
    with pytest.raises(ConfigError) as exc_info:
        VertexConfig.from_env()
    msg = str(exc_info.value)
    assert "ANTHROPIC_DEFAULT_OPUS_MODEL" in msg
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" in msg
    assert "ANTHROPIC_DEFAULT_HAIKU_MODEL" in msg


def test_from_env_rejects_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _ALL_VARS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "")
    with pytest.raises(ConfigError, match="ANTHROPIC_DEFAULT_OPUS_MODEL"):
        VertexConfig.from_env()


def test_model_id_returns_correct_tier() -> None:
    cfg = VertexConfig(
        project_id="p",
        region="r",
        opus_model_id="opus-pinned-id",
        sonnet_model_id="sonnet-pinned-id",
        haiku_model_id="haiku-pinned-id",
    )
    assert cfg.model_id("opus") == "opus-pinned-id"
    assert cfg.model_id("sonnet") == "sonnet-pinned-id"
    assert cfg.model_id("haiku") == "haiku-pinned-id"


def test_config_is_immutable() -> None:
    cfg = VertexConfig(
        project_id="p", region="r",
        opus_model_id="o", sonnet_model_id="s", haiku_model_id="h",
    )
    with pytest.raises((AttributeError, TypeError)):
        cfg.project_id = "changed"  # type: ignore[misc]

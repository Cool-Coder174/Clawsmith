from __future__ import annotations

from pathlib import Path

import pytest

from config.config_loader import (
    ConfigurationError,
    get_config,
    load_config,
)


def test_loads_default_settings_yaml():
    config = load_config()
    assert config.models.local_router.model_name == "ollama/mistral"
    assert config.mcp_server.port == 8765


def test_env_override_merges_correctly(monkeypatch, sample_config_yaml):
    monkeypatch.setenv("CLAWSMITH_MCP_SERVER__PORT", "9999")
    config = load_config(sample_config_yaml)
    assert config.mcp_server.port == 9999


def test_missing_file_raises_configuration_error():
    with pytest.raises(ConfigurationError):
        load_config(Path("nonexistent.yaml"))


def test_invalid_yaml_raises_configuration_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text('not: valid: yaml: [["', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(bad)


def test_get_config_returns_singleton():
    result1 = get_config()
    result2 = get_config()
    assert result1 is result2

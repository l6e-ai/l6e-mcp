"""Tests for l6e_mcp.config — env var + config.toml resolution."""
from __future__ import annotations

import textwrap

import pytest

from l6e_mcp import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("L6E_API_KEY", raising=False)
    monkeypatch.delenv("L6E_CLOUD_SYNC", raising=False)
    monkeypatch.delenv("L6E_CLOUD_ENDPOINT", raising=False)
    monkeypatch.delenv("L6E_CONFIG_PATH", raising=False)


def test_get_api_key_from_env(monkeypatch):
    monkeypatch.setenv("L6E_API_KEY", "sk-l6e-from-env")
    assert config.get_api_key() == "sk-l6e-from-env"


def test_get_api_key_from_toml(monkeypatch, tmp_path):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('api_key = "sk-l6e-from-toml"\n')
    monkeypatch.setenv("L6E_CONFIG_PATH", str(toml_file))
    assert config.get_api_key() == "sk-l6e-from-toml"


def test_env_takes_precedence_over_toml(monkeypatch, tmp_path):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('api_key = "sk-l6e-toml"\n')
    monkeypatch.setenv("L6E_CONFIG_PATH", str(toml_file))
    monkeypatch.setenv("L6E_API_KEY", "sk-l6e-env")
    assert config.get_api_key() == "sk-l6e-env"


def test_get_api_key_returns_none_when_missing():
    assert config.get_api_key() is None


def test_cloud_sync_disabled_by_default():
    assert config.is_cloud_sync_enabled() is False


def test_cloud_sync_enabled_via_env(monkeypatch):
    monkeypatch.setenv("L6E_CLOUD_SYNC", "1")
    assert config.is_cloud_sync_enabled() is True


def test_cloud_sync_enabled_via_toml(monkeypatch, tmp_path):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("cloud_sync = true\n")
    monkeypatch.setenv("L6E_CONFIG_PATH", str(toml_file))
    assert config.is_cloud_sync_enabled() is True


def test_cloud_sync_false_via_env(monkeypatch):
    monkeypatch.setenv("L6E_CLOUD_SYNC", "0")
    assert config.is_cloud_sync_enabled() is False


def test_cloud_endpoint_default():
    assert config.get_cloud_endpoint() == "https://api.l6e.ai"


def test_cloud_endpoint_from_env(monkeypatch):
    monkeypatch.setenv("L6E_CLOUD_ENDPOINT", "https://custom.example.com/")
    assert config.get_cloud_endpoint() == "https://custom.example.com"


def test_cloud_endpoint_from_toml(monkeypatch, tmp_path):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('cloud_endpoint = "https://toml.example.com"\n')
    monkeypatch.setenv("L6E_CONFIG_PATH", str(toml_file))
    assert config.get_cloud_endpoint() == "https://toml.example.com"


def test_missing_toml_returns_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("L6E_CONFIG_PATH", str(tmp_path / "nonexistent.toml"))
    assert config.get_api_key() is None
    assert config.is_cloud_sync_enabled() is False
    assert config.get_cloud_endpoint() == "https://api.l6e.ai"


def test_full_config_toml(monkeypatch, tmp_path):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(textwrap.dedent("""\
        api_key = "sk-l6e-full"
        cloud_sync = true
        cloud_endpoint = "https://staging.l6e.ai"
    """))
    monkeypatch.setenv("L6E_CONFIG_PATH", str(toml_file))
    assert config.get_api_key() == "sk-l6e-full"
    assert config.is_cloud_sync_enabled() is True
    assert config.get_cloud_endpoint() == "https://staging.l6e.ai"

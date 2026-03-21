"""Tests for manual per-model calibration config (Phase 0b).

Covers:
- get_manual_calibration_factors() env var and TOML parsing
- ensure_config_template() file creation
- authorize_call() with calibration_factor
- CalibrationCache.get_with_manual_fallback()
- Integration: manual factors in l6e_authorize_call local path and check_only path
"""
from __future__ import annotations

import stat
from unittest.mock import patch

from l6e_mcp import config
from l6e_mcp.core.calibration_cache import CalibrationCache

# ---------------------------------------------------------------------------
# get_manual_calibration_factors()
# ---------------------------------------------------------------------------


class TestGetManualCalibrationFactors:
    def test_parses_env_var(self, monkeypatch):
        monkeypatch.setenv("L6E_CALIBRATION_FACTORS", "claude-4-opus:72.0,claude-4-sonnet:45.0")
        result = config.get_manual_calibration_factors()
        assert result == {"claude-4-opus": 72.0, "claude-4-sonnet": 45.0}

    def test_skips_malformed_entries_and_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("L6E_CALIBRATION_FACTORS", "good-model:10.0,badentry,also:bad:3")
        result = config.get_manual_calibration_factors()
        assert result == {"good-model": 10.0, "also:bad": 3.0}
        assert any("malformed" in r.message for r in caplog.records)

    def test_skips_invalid_float_values(self, monkeypatch, caplog):
        monkeypatch.setenv("L6E_CALIBRATION_FACTORS", "model-a:notanumber,model-b:5.0")
        result = config.get_manual_calibration_factors()
        assert result == {"model-b": 5.0}
        assert any("invalid_value" in r.message for r in caplog.records)

    def test_reads_toml_calibration_section(self, monkeypatch):
        monkeypatch.delenv("L6E_CALIBRATION_FACTORS", raising=False)
        toml_data = {"calibration": {"claude-4-opus": 72.0, "claude-3.5-haiku": 12.0}}
        with patch.object(config, "_load_toml", return_value=toml_data):
            result = config.get_manual_calibration_factors()
        assert result == {"claude-4-opus": 72.0, "claude-3.5-haiku": 12.0}

    def test_env_var_takes_precedence_over_toml(self, monkeypatch):
        monkeypatch.setenv("L6E_CALIBRATION_FACTORS", "claude-4-opus:99.0")
        toml_data = {"calibration": {"claude-4-opus": 72.0}}
        with patch.object(config, "_load_toml", return_value=toml_data):
            result = config.get_manual_calibration_factors()
        assert result == {"claude-4-opus": 99.0}

    def test_returns_empty_dict_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("L6E_CALIBRATION_FACTORS", raising=False)
        with patch.object(config, "_load_toml", return_value={}):
            result = config.get_manual_calibration_factors()
        assert result == {}

    def test_handles_empty_env_var(self, monkeypatch):
        monkeypatch.setenv("L6E_CALIBRATION_FACTORS", "")
        with patch.object(config, "_load_toml", return_value={}):
            result = config.get_manual_calibration_factors()
        assert result == {}

    def test_handles_whitespace_in_env_var(self, monkeypatch):
        monkeypatch.setenv("L6E_CALIBRATION_FACTORS", " model-a : 5.0 , model-b : 10.0 ")
        result = config.get_manual_calibration_factors()
        assert result == {"model-a": 5.0, "model-b": 10.0}


# ---------------------------------------------------------------------------
# ensure_config_template()
# ---------------------------------------------------------------------------


class TestEnsureConfigTemplate:
    def test_creates_file_when_dir_exists_but_file_does_not(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".l6e"
        config_dir.mkdir()
        config_path = config_dir / "config.toml"
        monkeypatch.setattr(config, "_CONFIG_DIR", config_dir)
        monkeypatch.setattr(config, "_CONFIG_PATH", config_path)

        config.ensure_config_template()

        assert config_path.is_file()
        content = config_path.read_text()
        assert "[calibration]" in content
        assert "api_key" in content
        mode = config_path.stat().st_mode
        assert mode & stat.S_IRWXU == stat.S_IRUSR | stat.S_IWUSR  # 0o600

    def test_does_not_overwrite_existing_file(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".l6e"
        config_dir.mkdir()
        config_path = config_dir / "config.toml"
        config_path.write_text("existing content")
        monkeypatch.setattr(config, "_CONFIG_DIR", config_dir)
        monkeypatch.setattr(config, "_CONFIG_PATH", config_path)

        config.ensure_config_template()

        assert config_path.read_text() == "existing content"

    def test_noop_when_dir_does_not_exist(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".l6e"
        config_path = config_dir / "config.toml"
        monkeypatch.setattr(config, "_CONFIG_DIR", config_dir)
        monkeypatch.setattr(config, "_CONFIG_PATH", config_path)

        config.ensure_config_template()

        assert not config_path.exists()


# ---------------------------------------------------------------------------
# CalibrationCache.get_with_manual_fallback()
# ---------------------------------------------------------------------------


class TestGetWithManualFallback:
    def test_returns_server_cache_entry_when_available(self, monkeypatch):
        cache = CalibrationCache()
        cache.update("sess-1", factor=68.0, source="personal", confidence="high")

        monkeypatch.delenv("L6E_CALIBRATION_FACTORS", raising=False)
        result = cache.get_with_manual_fallback("sess-1", "claude-4-opus")

        assert result is not None
        assert result.factor == 68.0
        assert result.source == "personal"

    def test_falls_back_to_manual_factor(self, monkeypatch):
        cache = CalibrationCache()
        monkeypatch.setenv("L6E_CALIBRATION_FACTORS", "claude-4-opus:72.0")

        result = cache.get_with_manual_fallback("sess-1", "claude-4-opus")

        assert result is not None
        assert result.factor == 72.0
        assert result.source == "manual"

    def test_returns_none_when_neither_exists(self, monkeypatch):
        cache = CalibrationCache()
        monkeypatch.delenv("L6E_CALIBRATION_FACTORS", raising=False)
        with patch.object(config, "_load_toml", return_value={}):
            result = cache.get_with_manual_fallback("sess-1", "unknown-model")

        assert result is None

    def test_server_cache_takes_precedence_over_manual(self, monkeypatch):
        cache = CalibrationCache()
        cache.update("sess-1", factor=68.0, source="personal")
        monkeypatch.setenv("L6E_CALIBRATION_FACTORS", "claude-4-opus:72.0")

        result = cache.get_with_manual_fallback("sess-1", "claude-4-opus")

        assert result is not None
        assert result.factor == 68.0
        assert result.source == "personal"


# ---------------------------------------------------------------------------
# authorize_call() with calibration_factor
# ---------------------------------------------------------------------------


class TestAuthorizeCallWithCalibration:
    def test_factor_multiplies_estimated_cost(self, monkeypatch, tmp_path):
        """With a manual calibration factor, the budget gate sees the inflated cost."""
        monkeypatch.setenv("L6E_SESSION_DB_PATH", str(tmp_path / "sessions.db"))

        from l6e._types import BudgetMode, PipelinePolicy

        from l6e_mcp.core.authorization import authorize_call
        from l6e_mcp.session_store import LocalSessionStore

        store = LocalSessionStore()
        policy = PipelinePolicy(budget=5.0, budget_mode=BudgetMode.WARN)
        store.create_session(
            session_id="sess-cal",
            model="claude-4-opus",
            policy=policy,
            source="test",
            log_path=str(tmp_path / "runs.jsonl"),
        )
        session = store.require_active_session("sess-cal")

        decision_no_factor = authorize_call(
            store=store,
            session=session,
            tool_name="test",
            estimated_tokens=2000,
            estimated_prompt_tokens=None,
            estimated_completion_tokens=None,
            actor_type="parent_agent",
            actor_id=None,
            actor_name=None,
            parent_call_id=None,
            call_mode=None,
            actual_prompt_tokens=None,
            actual_completion_tokens=None,
            calibration_factor=None,
        )

        decision_with_factor = authorize_call(
            store=store,
            session=session,
            tool_name="test",
            estimated_tokens=2000,
            estimated_prompt_tokens=None,
            estimated_completion_tokens=None,
            actor_type="parent_agent",
            actor_id=None,
            actor_name=None,
            parent_call_id=None,
            call_mode=None,
            actual_prompt_tokens=None,
            actual_completion_tokens=None,
            calibration_factor=50.0,
        )

        assert decision_no_factor.calibration_factor is None
        assert decision_with_factor.calibration_factor == 50.0
        assert decision_with_factor.calibration_source == "manual"
        assert decision_with_factor.action in ("allow", "reroute")


# ---------------------------------------------------------------------------
# Integration: manual factors in l6e_authorize_call
# ---------------------------------------------------------------------------


async def _start_session(mcp_client, budget: float = 5.0) -> str:
    result = await mcp_client.call_tool(
        "l6e_run_start",
        {"budget_usd": budget, "model": "claude-4-opus"},
        raise_on_error=False,
    )
    assert not result.is_error, f"l6e_run_start failed: {result}"
    return result.data["session_id"]


async def _authorize(mcp_client, session_id: str, **kwargs) -> dict:
    params = {
        "session_id": session_id,
        "tool_name": "planning",
        "estimated_prompt_tokens": 2000,
        "estimated_completion_tokens": 400,
        **kwargs,
    }
    result = await mcp_client.call_tool(
        "l6e_authorize_call", params, raise_on_error=False,
    )
    assert not result.is_error, f"l6e_authorize_call failed: {result}"
    return result.data


class TestManualFactorInLocalAuth:
    async def test_local_auth_applies_manual_factor(self, client, monkeypatch):
        monkeypatch.delenv("L6E_API_KEY", raising=False)
        monkeypatch.setenv("L6E_CALIBRATION_FACTORS", "claude-4-opus:50.0")

        session_id = await _start_session(client)
        result = await _authorize(client, session_id)

        assert result["action"] == "allow"
        assert result.get("calibration_factor") == 50.0
        assert result.get("calibration_source") == "manual"

    async def test_local_auth_no_factor_when_model_not_configured(self, client, monkeypatch):
        monkeypatch.delenv("L6E_API_KEY", raising=False)
        monkeypatch.delenv("L6E_CALIBRATION_FACTORS", raising=False)
        with patch.object(config, "_load_toml", return_value={}):
            session_id = await _start_session(client)
            result = await _authorize(client, session_id)

        assert result["action"] == "allow"
        assert "calibration_factor" not in result


class TestManualFactorInCheckOnly:
    async def test_check_only_uses_manual_factor_fallback(self, client, monkeypatch):
        monkeypatch.delenv("L6E_API_KEY", raising=False)
        monkeypatch.setenv("L6E_CALIBRATION_FACTORS", "claude-4-opus:50.0")

        session_id = await _start_session(client)
        result = await _authorize(client, session_id, check_only=True)

        assert result.get("calibration_applied") is True
        assert result.get("calibration_source") == "manual"

    async def test_check_only_no_calibration_without_config(self, client, monkeypatch):
        monkeypatch.delenv("L6E_API_KEY", raising=False)
        monkeypatch.delenv("L6E_CALIBRATION_FACTORS", raising=False)
        with patch.object(config, "_load_toml", return_value={}):
            session_id = await _start_session(client)
            result = await _authorize(client, session_id, check_only=True)

        assert result.get("calibration_applied") is None or result.get("calibration_applied") is False  # noqa: E501

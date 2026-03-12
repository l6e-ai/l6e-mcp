"""Unit tests for calibration config loading and estimate resolution."""
from __future__ import annotations

import json

import pytest

from l6e_mcp.calibration.config import (
    CalibrationConfig,
    load_calibration_config,
    resolve_estimated_tokens,
)

# --- CalibrationConfig.combined_multiplier ---


def test_combined_multiplier_uses_stage_model_key_when_present():
    cfg = CalibrationConfig(
        stage_model_multiplier={"planning:gpt-4o": 3.0},
        stage_multiplier={"planning": 2.0},
        model_multiplier={"gpt-4o": 1.5},
    )
    result = cfg.combined_multiplier(stage="planning", model="gpt-4o")
    # stage_model_multiplier takes precedence; stage * model fallback is NOT applied
    assert result == pytest.approx(3.0)


def test_combined_multiplier_falls_back_to_stage_times_model():
    cfg = CalibrationConfig(
        stage_multiplier={"planning": 2.0},
        model_multiplier={"gpt-4o": 1.5},
    )
    result = cfg.combined_multiplier(stage="planning", model="gpt-4o")
    assert result == pytest.approx(3.0)


def test_combined_multiplier_clamps_to_max():
    cfg = CalibrationConfig(
        stage_multiplier={"planning": 100.0},
        max_multiplier=10.0,
    )
    assert cfg.combined_multiplier(stage="planning", model="unknown") == pytest.approx(10.0)


def test_combined_multiplier_clamps_to_min():
    cfg = CalibrationConfig(
        stage_multiplier={"planning": 0.0},
        min_multiplier=0.1,
    )
    assert cfg.combined_multiplier(stage="planning", model="unknown") == pytest.approx(0.1)


def test_combined_multiplier_defaults_to_one_when_no_keys():
    cfg = CalibrationConfig()
    assert cfg.combined_multiplier(stage="planning", model="gpt-4o") == pytest.approx(1.0)


# --- CalibrationConfig.reasoning_overhead_ratio ---


def test_reasoning_overhead_ratio_sums_stage_and_model():
    cfg = CalibrationConfig(
        stage_reasoning_overhead_ratio={"agent": 0.3},
        model_reasoning_overhead_ratio={"claude-3": 0.2},
    )
    assert cfg.reasoning_overhead_ratio(stage="agent", model="claude-3") == pytest.approx(0.5)


def test_reasoning_overhead_ratio_defaults_to_zero():
    cfg = CalibrationConfig()
    assert cfg.reasoning_overhead_ratio(stage="planning", model="gpt-4o") == pytest.approx(0.0)


# --- CalibrationConfig.internal_turns_multiplier ---


def test_internal_turns_multiplier_returns_configured_value():
    cfg = CalibrationConfig(stage_internal_turns_multiplier={"agent": 2.5})
    assert cfg.internal_turns_multiplier(stage="agent") == pytest.approx(2.5)


def test_internal_turns_multiplier_clamps_below_one():
    cfg = CalibrationConfig(stage_internal_turns_multiplier={"planning": 0.5})
    assert cfg.internal_turns_multiplier(stage="planning") == pytest.approx(1.0)


# --- load_calibration_config ---


def test_load_calibration_config_no_env_var(monkeypatch):
    monkeypatch.delenv("L6E_CALIBRATION_PATH", raising=False)
    cfg = load_calibration_config()
    assert cfg.stage_multiplier == {}
    assert cfg.model_multiplier == {}


def test_load_calibration_config_env_set_but_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("L6E_CALIBRATION_PATH", str(tmp_path / "nonexistent.json"))
    cfg = load_calibration_config()
    assert cfg.stage_multiplier == {}


def test_load_calibration_config_invalid_json(monkeypatch, tmp_path):
    bad_file = tmp_path / "calibration.json"
    bad_file.write_text("{ not valid json }", encoding="utf-8")
    monkeypatch.setenv("L6E_CALIBRATION_PATH", str(bad_file))
    cfg = load_calibration_config()
    assert cfg.stage_multiplier == {}


def test_load_calibration_config_valid_json(monkeypatch, tmp_path):
    payload = {
        "stage_multiplier": {"planning": 1.5, "agent": 2.0},
        "model_multiplier": {"gpt-4o": 1.2},
        "stage_model_multiplier": {"planning:gpt-4o": 1.8},
        "min_multiplier": 0.1,
        "max_multiplier": 20.0,
    }
    config_file = tmp_path / "calibration.json"
    config_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("L6E_CALIBRATION_PATH", str(config_file))
    cfg = load_calibration_config()
    assert cfg.stage_multiplier == {"planning": 1.5, "agent": 2.0}
    assert cfg.model_multiplier == {"gpt-4o": 1.2}
    assert cfg.stage_model_multiplier == {"planning:gpt-4o": 1.8}
    assert cfg.min_multiplier == pytest.approx(0.1)
    assert cfg.max_multiplier == pytest.approx(20.0)


def test_to_float_map_skips_non_string_keys():
    """_to_float_map is not exported, but its behavior is exercised here via CalibrationConfig
    directly — using a raw dict with integer keys to trigger the isinstance(key, str) guard."""
    from l6e_mcp.calibration.config import _to_float_map

    result = _to_float_map({1: 2.0, "planning": 1.5})
    assert "planning" in result
    assert 1 not in result


def test_load_calibration_config_skips_non_numeric_values(monkeypatch, tmp_path):
    payload = {"stage_multiplier": {"planning": "not-a-number", "agent": 2.0}}
    config_file = tmp_path / "calibration.json"
    config_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("L6E_CALIBRATION_PATH", str(config_file))
    cfg = load_calibration_config()
    assert "planning" not in cfg.stage_multiplier
    assert cfg.stage_multiplier == {"agent": 2.0}


# --- resolve_estimated_tokens ---


def test_resolve_uses_explicit_prompt_and_completion():
    cfg = CalibrationConfig()
    result = resolve_estimated_tokens(
        stage="planning",
        model="gpt-4o",
        estimated_tokens=None,
        estimated_prompt_tokens=1000,
        estimated_completion_tokens=200,
        calibration=cfg,
    )
    assert result.source == "explicit_prompt_completion"
    assert result.base_prompt_tokens == 1000
    assert result.base_completion_tokens == 200


def test_resolve_uses_prompt_with_ratio_when_only_prompt_given():
    cfg = CalibrationConfig(stage_output_input_ratio={"planning": 0.25})
    result = resolve_estimated_tokens(
        stage="planning",
        model="gpt-4o",
        estimated_tokens=None,
        estimated_prompt_tokens=1000,
        estimated_completion_tokens=None,
        calibration=cfg,
    )
    assert result.source == "explicit_prompt_with_ratio"
    assert result.base_prompt_tokens == 1000
    assert result.base_completion_tokens == 250


def test_resolve_uses_legacy_total_with_ratio():
    cfg = CalibrationConfig(stage_output_input_ratio={"planning": 0.25})
    result = resolve_estimated_tokens(
        stage="planning",
        model="gpt-4o",
        estimated_tokens=1000,
        estimated_prompt_tokens=None,
        estimated_completion_tokens=None,
        calibration=cfg,
    )
    assert result.source == "legacy_total_with_ratio"
    # With ratio 0.25: prompt = 1000 / 1.25 = 800, completion = 200
    assert result.base_prompt_tokens == 800
    assert result.base_completion_tokens == 200


def test_resolve_applies_reasoning_overhead():
    cfg = CalibrationConfig(
        stage_reasoning_overhead_ratio={"agent": 0.5},
    )
    result = resolve_estimated_tokens(
        stage="agent",
        model="gpt-4o",
        estimated_tokens=None,
        estimated_prompt_tokens=1000,
        estimated_completion_tokens=200,
        calibration=cfg,
    )
    # overhead_ratio=0.5, so reasoning_tokens = (1000+200)*0.5 = 600
    assert result.reasoning_tokens == 600
    assert result.effective_multiplier == pytest.approx(1.0 * 1.5 * 1.0)


def test_resolve_applies_internal_turns_multiplier():
    cfg = CalibrationConfig(
        stage_internal_turns_multiplier={"agent": 2.0},
    )
    result = resolve_estimated_tokens(
        stage="agent",
        model="gpt-4o",
        estimated_tokens=None,
        estimated_prompt_tokens=100,
        estimated_completion_tokens=50,
        calibration=cfg,
    )
    assert result.internal_turns_multiplier == pytest.approx(2.0)
    # prompt = (100 + 0 reasoning) * 2 = 200, completion = 50 * 2 = 100
    assert result.prompt_tokens == 200
    assert result.completion_tokens == 100


def test_resolve_none_estimated_tokens_defaults_to_zero():
    cfg = CalibrationConfig()
    result = resolve_estimated_tokens(
        stage="planning",
        model="gpt-4o",
        estimated_tokens=None,
        estimated_prompt_tokens=None,
        estimated_completion_tokens=None,
        calibration=cfg,
    )
    assert result.source == "legacy_total_with_ratio"
    assert result.prompt_tokens >= 0
    assert result.completion_tokens >= 0

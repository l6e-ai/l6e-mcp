from __future__ import annotations

import json
from pathlib import Path

from l6e_mcp.calibration.config import CalibrationConfig, resolve_estimated_tokens
from l6e_mcp.tools.calibration_generator import generate_calibration


def test_resolve_estimated_tokens_prefers_explicit_prompt_and_completion() -> None:
    config = CalibrationConfig(stage_output_input_ratio={"read_files": 0.5})
    resolved = resolve_estimated_tokens(
        stage="read_files",
        model="gpt-4o-mini",
        estimated_tokens=1000,
        estimated_prompt_tokens=700,
        estimated_completion_tokens=300,
        calibration=config,
    )
    assert resolved.prompt_tokens == 700
    assert resolved.completion_tokens == 300
    assert resolved.source == "explicit_prompt_completion"


def test_resolve_estimated_tokens_uses_prompt_plus_ratio() -> None:
    config = CalibrationConfig(stage_output_input_ratio={"read_files": 0.5})
    resolved = resolve_estimated_tokens(
        stage="read_files",
        model="gpt-4o-mini",
        estimated_tokens=None,
        estimated_prompt_tokens=400,
        estimated_completion_tokens=None,
        calibration=config,
    )
    assert resolved.prompt_tokens == 400
    assert resolved.completion_tokens == 200
    assert resolved.source == "explicit_prompt_with_ratio"


def test_resolve_estimated_tokens_uses_legacy_total_split() -> None:
    config = CalibrationConfig(stage_output_input_ratio={"read_files": 0.25})
    resolved = resolve_estimated_tokens(
        stage="read_files",
        model="gpt-4o-mini",
        estimated_tokens=1000,
        estimated_prompt_tokens=None,
        estimated_completion_tokens=None,
        calibration=config,
    )
    assert resolved.prompt_tokens == 800
    assert resolved.completion_tokens == 200
    assert resolved.source == "legacy_total_with_ratio"


def test_resolve_estimated_tokens_applies_multiplier_bounds() -> None:
    config = CalibrationConfig(
        stage_multiplier={"edit_file": 20.0},
        min_multiplier=0.5,
        max_multiplier=3.0,
    )
    resolved = resolve_estimated_tokens(
        stage="edit_file",
        model="unknown-model",
        estimated_tokens=1000,
        estimated_prompt_tokens=None,
        estimated_completion_tokens=None,
        calibration=config,
    )
    assert resolved.multiplier_applied == 3.0
    assert resolved.prompt_tokens > 0


def test_resolve_estimated_tokens_applies_reasoning_and_internal_turns() -> None:
    config = CalibrationConfig(
        stage_reasoning_overhead_ratio={"read_files": 1.0},
        stage_internal_turns_multiplier={"read_files": 2.0},
    )
    resolved = resolve_estimated_tokens(
        stage="read_files",
        model="gpt-4o-mini",
        estimated_tokens=1000,
        estimated_prompt_tokens=400,
        estimated_completion_tokens=100,
        calibration=config,
    )
    assert resolved.base_prompt_tokens == 400
    assert resolved.base_completion_tokens == 100
    assert resolved.reasoning_tokens == 500
    assert resolved.internal_turns_multiplier == 2.0
    assert resolved.prompt_tokens == 1800
    assert resolved.completion_tokens == 200
    assert resolved.effective_multiplier == 4.0


def test_calibration_generator_is_deterministic(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    rows = [
        {
            "run_id": "session_a",
            "records": [
                {
                    "stage": "read_files",
                    "model_used": "gpt-4o-mini",
                    "prompt_tokens": 1000,
                    "completion_tokens": 0,
                    "cost_usd": 0.01,
                },
                {
                    "stage": "read_files",
                    "model_used": "gpt-4o-mini",
                    "prompt_tokens": 1100,
                    "completion_tokens": 0,
                    "cost_usd": 0.011,
                },
                {
                    "stage": "edit_file",
                    "model_used": "gpt-4o",
                    "prompt_tokens": 2000,
                    "completion_tokens": 0,
                    "cost_usd": 0.03,
                },
                {
                    "stage": "edit_file",
                    "model_used": "gpt-4o",
                    "prompt_tokens": 2100,
                    "completion_tokens": 0,
                    "cost_usd": 0.031,
                },
                {
                    "stage": "edit_file",
                    "model_used": "gpt-4o",
                    "prompt_tokens": 2200,
                    "completion_tokens": 0,
                    "cost_usd": 0.032,
                },
            ],
        }
    ]
    runs.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    first = generate_calibration(
        runs_path=runs,
        usage_csv_path=None,
        min_samples=2,
        max_step_change=0.25,
    )
    second = generate_calibration(
        runs_path=runs,
        usage_csv_path=None,
        min_samples=2,
        max_step_change=0.25,
    )
    assert first == second

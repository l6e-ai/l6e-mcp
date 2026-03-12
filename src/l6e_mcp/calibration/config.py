"""Calibration config and estimate resolution helpers."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CalibrationConfig:
    """Token-estimation calibration controls loaded from JSON."""

    stage_output_input_ratio: dict[str, float] = field(default_factory=dict)
    stage_multiplier: dict[str, float] = field(default_factory=dict)
    model_multiplier: dict[str, float] = field(default_factory=dict)
    stage_model_multiplier: dict[str, float] = field(default_factory=dict)
    stage_reasoning_overhead_ratio: dict[str, float] = field(default_factory=dict)
    model_reasoning_overhead_ratio: dict[str, float] = field(default_factory=dict)
    stage_internal_turns_multiplier: dict[str, float] = field(default_factory=dict)
    min_multiplier: float = 0.05
    max_multiplier: float = 32.0

    def output_input_ratio_for_stage(self, stage: str) -> float:
        ratio = self.stage_output_input_ratio.get(stage, 0.20)
        return max(0.0, min(ratio, 2.0))

    def combined_multiplier(self, *, stage: str, model: str) -> float:
        key = f"{stage}:{model}"
        factor = 1.0
        if key in self.stage_model_multiplier:
            factor *= self.stage_model_multiplier[key]
        else:
            factor *= self.stage_multiplier.get(stage, 1.0)
            factor *= self.model_multiplier.get(model, 1.0)
        return max(self.min_multiplier, min(self.max_multiplier, factor))

    def reasoning_overhead_ratio(self, *, stage: str, model: str) -> float:
        stage_ratio = self.stage_reasoning_overhead_ratio.get(stage, 0.0)
        model_ratio = self.model_reasoning_overhead_ratio.get(model, 0.0)
        return max(0.0, min(64.0, stage_ratio + model_ratio))

    def internal_turns_multiplier(self, *, stage: str) -> float:
        value = self.stage_internal_turns_multiplier.get(stage, 1.0)
        return max(1.0, min(16.0, value))


@dataclass(frozen=True)
class ResolvedEstimates:
    prompt_tokens: int
    completion_tokens: int
    source: str
    multiplier_applied: float
    output_input_ratio: float
    base_prompt_tokens: int
    base_completion_tokens: int
    visible_prompt_tokens: int
    visible_completion_tokens: int
    reasoning_tokens: int
    internal_turns_multiplier: float
    effective_multiplier: float


def _default_config() -> CalibrationConfig:
    return CalibrationConfig()


def _config_path() -> Path | None:
    raw = os.environ.get("L6E_CALIBRATION_PATH", "").strip()
    if not raw:
        return None
    return Path(raw)


def load_calibration_config() -> CalibrationConfig:
    """Load config from L6E_CALIBRATION_PATH when available."""
    path = _config_path()
    if path is None or not path.exists():
        return _default_config()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_config()

    return CalibrationConfig(
        stage_output_input_ratio=_to_float_map(payload.get("stage_output_input_ratio")),
        stage_multiplier=_to_float_map(payload.get("stage_multiplier")),
        model_multiplier=_to_float_map(payload.get("model_multiplier")),
        stage_model_multiplier=_to_float_map(payload.get("stage_model_multiplier")),
        stage_reasoning_overhead_ratio=_to_float_map(payload.get("stage_reasoning_overhead_ratio")),
        model_reasoning_overhead_ratio=_to_float_map(payload.get("model_reasoning_overhead_ratio")),
        stage_internal_turns_multiplier=_to_float_map(payload.get("stage_internal_turns_multiplier")),
        min_multiplier=float(payload.get("min_multiplier", 0.05)),
        max_multiplier=float(payload.get("max_multiplier", 32.0)),
    )


def resolve_estimated_tokens(
    *,
    stage: str,
    model: str,
    estimated_tokens: int | None,
    estimated_prompt_tokens: int | None,
    estimated_completion_tokens: int | None,
    calibration: CalibrationConfig,
) -> ResolvedEstimates:
    """Resolve prompt/completion estimates with canonical precedence."""
    ratio = calibration.output_input_ratio_for_stage(stage)

    if estimated_prompt_tokens is not None and estimated_completion_tokens is not None:
        base_prompt = max(0, int(estimated_prompt_tokens))
        base_completion = max(0, int(estimated_completion_tokens))
        source = "explicit_prompt_completion"
    elif estimated_prompt_tokens is not None:
        base_prompt = max(0, int(estimated_prompt_tokens))
        base_completion = int(round(base_prompt * ratio))
        source = "explicit_prompt_with_ratio"
    else:
        total = max(0, int(estimated_tokens or 0))
        divisor = 1.0 + ratio
        base_prompt = int(round(total / divisor))
        base_completion = max(0, total - base_prompt)
        source = "legacy_total_with_ratio"

    visible_multiplier = calibration.combined_multiplier(stage=stage, model=model)
    visible_prompt = max(0, int(round(base_prompt * visible_multiplier)))
    visible_completion = max(0, int(round(base_completion * visible_multiplier)))
    overhead_ratio = calibration.reasoning_overhead_ratio(stage=stage, model=model)
    reasoning_tokens = max(
        0,
        int(round((visible_prompt + visible_completion) * overhead_ratio)),
    )
    turns_multiplier = calibration.internal_turns_multiplier(stage=stage)
    prompt = max(
        0,
        int(round((visible_prompt + reasoning_tokens) * turns_multiplier)),
    )
    completion = max(0, int(round(visible_completion * turns_multiplier)))
    effective_multiplier = visible_multiplier * (1.0 + overhead_ratio) * turns_multiplier
    return ResolvedEstimates(
        prompt_tokens=prompt,
        completion_tokens=completion,
        source=source,
        multiplier_applied=visible_multiplier,
        output_input_ratio=ratio,
        base_prompt_tokens=base_prompt,
        base_completion_tokens=base_completion,
        visible_prompt_tokens=visible_prompt,
        visible_completion_tokens=visible_completion,
        reasoning_tokens=reasoning_tokens,
        internal_turns_multiplier=turns_multiplier,
        effective_multiplier=effective_multiplier,
    )


def _to_float_map(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            continue
    return out

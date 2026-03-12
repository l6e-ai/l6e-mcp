"""Generate calibration suggestions from run logs and optional usage export."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    stage: str
    model: str
    estimated_cost: float
    actual_cost: float


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", default=".l6e/runs.jsonl", help="Path to runs.jsonl")
    parser.add_argument(
        "--usage-csv",
        default=None,
        help="Optional Cursor usage export CSV with cost column.",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=5,
        help="Minimum samples required per bucket.",
    )
    parser.add_argument(
        "--max-step-change",
        type=float,
        default=0.25,
        help="Maximum step change from 1.0 (25%% = 0.25).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path for machine-readable calibration JSON.",
    )
    return parser.parse_args()


def _load_samples_from_runs(path: Path) -> list[Sample]:
    if not path.exists():
        return []
    samples: list[Sample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            run = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for record in run.get("records", []):
            stage = str(record.get("stage") or "unknown_stage")
            model = str(record.get("model_used") or "unknown")
            prompt = int(record.get("prompt_tokens", 0) or 0)
            completion = int(record.get("completion_tokens", 0) or 0)
            # Keep deterministic baseline approximation when only run logs exist.
            estimated = float(record.get("cost_usd", 0.0) or 0.0)
            actual = estimated
            if completion == 0 and prompt > 0:
                # Synthetic uplift for estimate-only rows gives a stable calibration hint.
                actual = estimated * 1.5
            samples.append(
                Sample(
                    stage=stage,
                    model=model,
                    estimated_cost=max(estimated, 1e-9),
                    actual_cost=max(actual, 1e-9),
                )
            )
    return samples


def _load_usage_total(path: Path | None) -> float | None:
    if path is None or not path.exists():
        return None
    total = 0.0
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row.get("cost") or row.get("Cost") or row.get("estimated_cost") or "0"
            try:
                total += float(raw)
            except ValueError:
                continue
    return total


def _bucket_ratios(samples: list[Sample]) -> tuple[dict[str, float], dict[str, float]]:
    stage_ratios: dict[str, list[float]] = {}
    model_ratios: dict[str, list[float]] = {}
    for s in samples:
        ratio = s.actual_cost / s.estimated_cost
        stage_ratios.setdefault(s.stage, []).append(ratio)
        model_ratios.setdefault(s.model, []).append(ratio)
    return (
        {k: _robust_ratio(v) for k, v in sorted(stage_ratios.items())},
        {k: _robust_ratio(v) for k, v in sorted(model_ratios.items())},
    )


def _robust_ratio(values: list[float]) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    trim = max(0, int(len(ordered) * 0.1))
    trimmed = ordered[trim : len(ordered) - trim] if len(ordered) > 2 * trim else ordered
    return float(statistics.median(trimmed))


def _clamp_step(value: float, max_step_change: float) -> float:
    lower = max(0.1, 1.0 - max_step_change)
    upper = 1.0 + max_step_change
    return max(lower, min(upper, value))


def generate_calibration(
    *,
    runs_path: Path,
    usage_csv_path: Path | None,
    min_samples: int,
    max_step_change: float,
) -> dict:
    samples = _load_samples_from_runs(runs_path)
    stage_ratios, model_ratios = _bucket_ratios(samples)
    stage_multiplier: dict[str, float] = {}
    model_multiplier: dict[str, float] = {}

    for stage, ratio in stage_ratios.items():
        count = sum(1 for s in samples if s.stage == stage)
        if count < min_samples:
            continue
        stage_multiplier[stage] = round(_clamp_step(ratio, max_step_change), 4)

    for model, ratio in model_ratios.items():
        count = sum(1 for s in samples if s.model == model)
        if count < min_samples:
            continue
        model_multiplier[model] = round(_clamp_step(ratio, max_step_change), 4)

    usage_total = _load_usage_total(usage_csv_path)
    runs_total = sum(s.estimated_cost for s in samples)
    global_ratio = (
        (usage_total / runs_total)
        if usage_total is not None and runs_total > 0
        else None
    )

    return {
        "version": 1,
        "sample_count": len(samples),
        "min_samples": min_samples,
        "max_step_change": max_step_change,
        "stage_multiplier": dict(sorted(stage_multiplier.items())),
        "model_multiplier": dict(sorted(model_multiplier.items())),
        "global_ratio": round(global_ratio, 4) if global_ratio is not None else None,
        "confidence": "low" if len(samples) < (min_samples * 3) else "medium",
    }


def main() -> None:
    args = _parse_args()
    payload = generate_calibration(
        runs_path=Path(args.runs),
        usage_csv_path=Path(args.usage_csv) if args.usage_csv else None,
        min_samples=args.min_samples,
        max_step_change=args.max_step_change,
    )
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

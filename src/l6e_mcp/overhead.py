"""Estimate l6e MCP overhead cost from tool-call token budgets."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from l6e.costs import LiteLLMCostEstimator

OVERHEAD_TOKEN_ESTIMATES: dict[str, dict[str, int]] = {
    "l6e_run_start": {"prompt": 450, "completion": 120},
    "l6e_authorize_call": {"prompt": 380, "completion": 280},
    "l6e_run_status": {"prompt": 220, "completion": 350},
    "l6e_record_usage": {"prompt": 310, "completion": 120},
    "l6e_run_end": {"prompt": 220, "completion": 100},
}


def estimate_overhead(
    *,
    model: str,
    estimator: LiteLLMCostEstimator,
    tool_call_counts: Mapping[str, int],
) -> tuple[Decimal, int]:
    """Return (overhead_usd, overhead_calls) for tracked MCP tool calls."""
    total_cost = Decimal("0")
    total_calls = 0
    for tool_name, count in tool_call_counts.items():
        if count <= 0:
            continue
        token_estimate = OVERHEAD_TOKEN_ESTIMATES.get(tool_name)
        if token_estimate is None:
            continue
        per_call_cost = estimator.estimate(
            model=model,
            prompt_tokens=token_estimate["prompt"],
            completion_tokens=token_estimate["completion"],
        )
        total_calls += count
        total_cost += per_call_cost * count
    return total_cost, total_calls

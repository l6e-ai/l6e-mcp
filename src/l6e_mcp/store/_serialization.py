"""Serialization helpers: policy JSON and row-to-dataclass conversions."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from typing import TYPE_CHECKING

from l6e._types import (
    BudgetMode,
    OnBudgetExceeded,
    PipelinePolicy,
    PromptComplexity,
    StageRoutingHint,
    UnknownModelPricingMode,
)

from l6e_mcp.contracts.mode_coverage import ModeCoverage
from l6e_mcp.core.exactness import normalize_call_exactness_state
from l6e_mcp.store import schema as store_schema

if TYPE_CHECKING:
    from l6e_mcp.store.calls import CallState
    from l6e_mcp.store.sessions import SessionState


def _policy_to_json(policy: PipelinePolicy) -> str:
    return json.dumps(
        {
            "budget": policy.budget,
            "budget_mode": policy.budget_mode.value,
            "on_budget_exceeded": policy.on_budget_exceeded.value,
            "fallback_result": policy.fallback_result,
            "latency_sla": policy.latency_sla,
            "reroute_threshold": policy.reroute_threshold,
            "unknown_model_cost_per_1k_tokens": policy.unknown_model_cost_per_1k_tokens,
            "unknown_model_pricing_mode": policy.unknown_model_pricing_mode.value,
            "stage_routing": {k: v.value for k, v in policy.stage_routing.items()},
            "stage_overrides": {k: v.value for k, v in policy.stage_overrides.items()},
        },
        default=str,
    )


def _policy_from_json(raw: str) -> PipelinePolicy:
    data = json.loads(raw)
    return PipelinePolicy(
        budget=float(data["budget"]),
        budget_mode=BudgetMode(data.get("budget_mode", BudgetMode.HALT)),
        on_budget_exceeded=OnBudgetExceeded(
            data.get("on_budget_exceeded", OnBudgetExceeded.RAISE)
        ),
        fallback_result=data.get("fallback_result"),
        latency_sla=data.get("latency_sla"),
        reroute_threshold=float(data.get("reroute_threshold", 0.8)),
        unknown_model_cost_per_1k_tokens=float(
            data.get("unknown_model_cost_per_1k_tokens", 0.01)
        ),
        unknown_model_pricing_mode=UnknownModelPricingMode(
            data.get("unknown_model_pricing_mode", "warn_only")
        ),
        stage_routing={
            k: StageRoutingHint(v) for k, v in data.get("stage_routing", {}).items()
        },
        stage_overrides={
            k: BudgetMode(v) for k, v in data.get("stage_overrides", {}).items()
        },
    )


def _default_mode_coverage(*, usage_channel: str, accounting_mode: str) -> ModeCoverage:
    if accounting_mode == store_schema.ACCOUNTING_MODE_ESTIMATE_ONLY:
        return ModeCoverage(False, False, False)
    if usage_channel == store_schema.USAGE_CHANNEL_HOSTED_EDGE:
        return ModeCoverage(True, True, True)
    if usage_channel == store_schema.USAGE_CHANNEL_SELF_HOSTED_RELAY:
        return ModeCoverage(True, True, False)
    if usage_channel == store_schema.USAGE_CHANNEL_MANUAL_IMPORT:
        return ModeCoverage(False, False, False)
    return ModeCoverage(False, False, False)


def _session_from_row(row: sqlite3.Row) -> SessionState:
    from l6e_mcp.store.sessions import SessionState

    return SessionState(
        session_id=str(row["session_id"]),
        model=str(row["model"]),
        policy=_policy_from_json(str(row["policy_json"])),
        source=str(row["source"]),
        log_path=str(row["log_path"]) if row["log_path"] is not None else None,
        accounting_mode=(
            str(row["accounting_mode"])
            if row["accounting_mode"] is not None
            else store_schema.ACCOUNTING_MODE_ESTIMATE_ONLY
        ),
        usage_channel=(
            str(row["usage_channel"])
            if row["usage_channel"] is not None
            else store_schema.USAGE_CHANNEL_NONE
        ),
        state=str(row["state"]),
        next_call_index=int(row["next_call_index"]),
        checkpoint_calls=(
            int(row["checkpoint_calls"]) if "checkpoint_calls" in row.keys() else 0  # noqa: SIM118
        ),
        status_calls=(
            int(row["status_calls"]) if "status_calls" in row.keys() else 0  # noqa: SIM118
        ),
        created_at=float(row["created_at"]),
        ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
        finalized_at=(
            float(row["finalized_at"]) if row["finalized_at"] is not None else None
        ),
        ask_mode_exact_capable=bool(row["ask_mode_exact_capable"]),
        plan_mode_exact_capable=bool(row["plan_mode_exact_capable"]),
        agent_mode_exact_capable=bool(row["agent_mode_exact_capable"]),
    )


def _call_from_row(row: sqlite3.Row) -> CallState:
    from l6e_mcp.store.calls import CallState

    prompt_complexity = (
        PromptComplexity(str(row["prompt_complexity"]))
        if row["prompt_complexity"] is not None
        else None
    )
    accounting_mode = (
        str(row["session_accounting_mode"])
        if "session_accounting_mode" in row.keys()  # noqa: SIM118
        and row["session_accounting_mode"] is not None
        else store_schema.ACCOUNTING_MODE_ESTIMATE_ONLY
    )
    mode_exact_capable = (
        bool(row["mode_exact_capable"]) if row["mode_exact_capable"] is not None else None
    )
    normalized_exactness = normalize_call_exactness_state(
        str(row["exactness_state"]) if row["exactness_state"] is not None else None,
        status=str(row["status"]),
        accounting_mode=accounting_mode,
        mode_exact_capable=mode_exact_capable,
    )
    return CallState(
        call_id=str(row["call_id"]),
        session_id=str(row["session_id"]),
        call_index=int(row["call_index"]),
        tool_name=str(row["tool_name"]),
        model_requested=str(row["model_requested"]),
        model_used=str(row["model_used"]),
        estimated_prompt_tokens=int(row["estimated_prompt_tokens"]),
        estimated_completion_tokens=int(row["estimated_completion_tokens"]),
        estimated_cost_usd=Decimal(str(row["estimated_cost_usd"])),
        actual_prompt_tokens=(
            int(row["actual_prompt_tokens"])
            if row["actual_prompt_tokens"] is not None
            else None
        ),
        actual_completion_tokens=(
            int(row["actual_completion_tokens"])
            if row["actual_completion_tokens"] is not None
            else None
        ),
        actual_cost_usd=(
            Decimal(str(row["actual_cost_usd"])) if row["actual_cost_usd"] is not None else None
        ),
        rerouted=bool(row["rerouted"]),
        elapsed_ms=float(row["elapsed_ms"]),
        prompt_complexity=prompt_complexity,
        is_multi_turn=bool(row["is_multi_turn"]),
        status=str(row["status"]),
        created_at=float(row["created_at"]),
        reconciled_at=(
            float(row["reconciled_at"]) if row["reconciled_at"] is not None else None
        ),
        correlation_key=(
            str(row["correlation_key"]) if row["correlation_key"] is not None else None
        ),
        correlation_source=(
            str(row["correlation_source"]) if row["correlation_source"] is not None else None
        ),
        callback_request_id=(
            str(row["callback_request_id"])
            if row["callback_request_id"] is not None
            else None
        ),
        callback_trace_id=(
            str(row["callback_trace_id"]) if row["callback_trace_id"] is not None else None
        ),
        exactness_state=normalized_exactness.value,
        hosted_ledger_id=(
            str(row["hosted_ledger_id"]) if row["hosted_ledger_id"] is not None else None
        ),
        actor_type=(
            str(row["actor_type"]) if row["actor_type"] is not None else "parent_agent"
        ),
        actor_id=str(row["actor_id"]) if row["actor_id"] is not None else None,
        actor_name=str(row["actor_name"]) if row["actor_name"] is not None else None,
        parent_call_id=(
            str(row["parent_call_id"]) if row["parent_call_id"] is not None else None
        ),
        call_mode=str(row["call_mode"]) if row["call_mode"] is not None else None,
        mode_exact_capable=mode_exact_capable,
    )

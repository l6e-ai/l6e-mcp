"""Run summary computation: session_run_summary and helpers."""
from __future__ import annotations

from decimal import Decimal

from l6e._types import CallRecord, PipelinePolicy, RunSummary, SubagentSpend

from l6e_mcp.contracts.exactness import ExactnessState, RunExactnessState
from l6e_mcp.core.exactness import run_exactness_state
from l6e_mcp.overhead import estimate_overhead
from l6e_mcp.store.calls import CallState
from l6e_mcp.store.sessions import SessionState


def session_run_summary(session: SessionState, calls: list[CallState]) -> RunSummary:
    estimator = _estimator_for_policy(session.policy)
    total_cost = Decimal("0")
    counterfactual_cost = Decimal("0")
    records: list[CallRecord] = []
    reroutes = 0
    subagent_calls = 0
    subagent_spend_usd = Decimal("0")
    subagent_rollups: dict[str, SubagentSpend] = {}
    for call in calls:
        record = call.effective_record()
        records.append(record)
        total_cost += record.cost_usd
        if record.actor_type == "subagent":
            subagent_calls += 1
            subagent_spend_usd += record.cost_usd
            actor_id = record.actor_id or f"call:{record.call_index}"
            existing = subagent_rollups.get(actor_id)
            if existing is None:
                subagent_rollups[actor_id] = SubagentSpend(
                    actor_id=actor_id,
                    actor_name=record.actor_name,
                    calls_made=1,
                    total_cost_usd=record.cost_usd,
                )
            else:
                subagent_rollups[actor_id] = SubagentSpend(
                    actor_id=existing.actor_id,
                    actor_name=existing.actor_name or record.actor_name,
                    calls_made=existing.calls_made + 1,
                    total_cost_usd=existing.total_cost_usd + record.cost_usd,
                )
        if record.rerouted and record.model_requested != record.model_used:
            counterfactual = estimator.estimate(
                model=record.model_requested,
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
            )
            counterfactual_cost += max(counterfactual, record.cost_usd)
            reroutes += 1
        else:
            counterfactual_cost += record.cost_usd
    savings_usd = max(Decimal("0"), counterfactual_cost - total_cost)
    overhead_usd, overhead_calls = estimate_overhead(
        model=session.model,
        estimator=estimator,
        tool_call_counts={
            "l6e_run_start": 1,
            "l6e_authorize_call": session.checkpoint_calls,
            "l6e_run_status": session.status_calls,
            "l6e_run_end": 1,
        },
    )
    call_exactness_states = [ExactnessState(c.exactness_state) for c in calls]
    run_exactness = run_exactness_state(call_exactness_states)
    savings_confidence = _savings_confidence_from_run_exactness(run_exactness)
    return RunSummary(
        run_id=session.session_id,
        policy=session.policy,
        total_cost=total_cost,
        calls_made=len(records),
        reroutes=reroutes,
        savings_usd=savings_usd,
        records=tuple(records),
        source=session.source,
        subagent_calls=subagent_calls,
        subagent_spend_usd=subagent_spend_usd,
        subagents=tuple(subagent_rollups.values()),
        overhead_usd=overhead_usd,
        overhead_calls=overhead_calls,
        net_savings_usd=savings_usd - overhead_usd,
        savings_confidence=savings_confidence,
    )


def build_session_report(
    session: SessionState,
    summary: RunSummary,
    calls: list[CallState],
) -> dict:
    """Serialize a completed session into the POST /v1/session-reports payload."""
    from l6e_mcp import config as _config

    has_raw_costs = any(c.raw_estimated_cost_usd is not None for c in calls)
    raw_total = sum(
        (c.raw_estimated_cost_usd or c.estimated_cost_usd) for c in calls
    ) if has_raw_costs else None

    report: dict = {
        "session_id": session.session_id,
        "model": session.model,
        "source": session.source,
        "total_cost_usd": float(round(summary.total_cost, 8)),
        "calls_made": summary.calls_made,
        "reroutes": summary.reroutes,
        "savings_confidence": summary.savings_confidence,
        "accounting_mode": session.accounting_mode,
    }
    if raw_total is not None:
        report["raw_total_cost_usd"] = float(round(float(raw_total), 8))
    if _config.send_task_summaries():
        if session.start_summary is not None:
            report["start_summary"] = session.start_summary
        if session.end_summary is not None:
            report["end_summary"] = session.end_summary
    call_dicts = []
    for c in calls:
        entry: dict = {
            "call_id": c.call_id,
            "tool_name": c.tool_name,
            "model_requested": c.model_requested,
            "model_used": c.model_used,
            "estimated_prompt_tokens": c.estimated_prompt_tokens,
            "estimated_completion_tokens": c.estimated_completion_tokens,
            "estimated_cost_usd": float(round(c.estimated_cost_usd, 8)),
            "actual_prompt_tokens": c.actual_prompt_tokens,
            "actual_completion_tokens": c.actual_completion_tokens,
            "actual_cost_usd": (
                float(round(c.actual_cost_usd, 8)) if c.actual_cost_usd is not None else None
            ),
            "action": "reroute" if c.rerouted else "allow",
            "actor_type": getattr(c, "actor_type", "parent_agent"),
            "created_at": c.created_at,
            "finished_at": (
                c.created_at + c.elapsed_ms / 1000.0
                if c.elapsed_ms > 0 else None
            ),
        }
        if c.raw_estimated_cost_usd is not None:
            entry["raw_estimated_cost_usd"] = float(round(c.raw_estimated_cost_usd, 8))
        call_dicts.append(entry)
    report["calls"] = call_dicts
    return report


def _savings_confidence_from_run_exactness(state: RunExactnessState) -> str:
    if state == RunExactnessState.FULLY_EXACT_FOR_SUPPORTED_CALLS:
        return "exact"
    if state == RunExactnessState.PARTIAL_EXACT:
        return "partial_exact"
    return "estimate_only"


def _estimator_for_policy(policy: PipelinePolicy):
    """Return a LiteLLMCostEstimator configured with the policy's unknown-model fallback cost."""
    from l6e.costs import LiteLLMCostEstimator

    return LiteLLMCostEstimator(
        fallback_cost_per_1k_tokens=policy.unknown_model_cost_per_1k_tokens
    )

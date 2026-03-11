"""Reconciliation and unmatched-usage services."""
from __future__ import annotations

import json

from l6e.costs import LiteLLMCostEstimator

from l6e_mcp.contracts.usage_report import UsageReport
from l6e_mcp.session_store import CallState, LocalSessionStore, SessionState


def reconcile_usage_report(
    *,
    store: LocalSessionStore,
    session: SessionState,
    existing_call: CallState,
    report: UsageReport,
) -> CallState:
    """Attach exact usage to an existing call intent."""
    estimator = LiteLLMCostEstimator(
        fallback_cost_per_1k_tokens=session.policy.unknown_model_cost_per_1k_tokens
    )
    actual_cost = estimator.estimate(
        report.model_used,
        report.prompt_tokens,
        report.completion_tokens,
    )
    idempotency_key = report.idempotency_key or f"call:{report.call_id}"
    store.record_reconciliation_attempt(
        session_id=session.session_id,
        call_id=report.call_id,
        usage_source=report.usage_source,
        result="matched",
        idempotency_key=idempotency_key,
        error_code=None,
        details_json=json.dumps(
            {
                "provider_request_id": report.provider_request_id,
                "provider_trace_id": report.provider_trace_id,
                "hosted_ledger_id": report.hosted_ledger_id,
            }
        ),
    )
    return store.reconcile_call(
        call_id=existing_call.call_id,
        actual_prompt_tokens=report.prompt_tokens,
        actual_completion_tokens=report.completion_tokens,
        actual_cost_usd=actual_cost,
        model_used=report.model_used,
        callback_request_id=report.provider_request_id,
        callback_trace_id=report.provider_trace_id,
        correlation_key=report.call_id,
        correlation_source=report.usage_source,
        hosted_ledger_id=report.hosted_ledger_id,
    )


def record_unmatched_usage(
    *,
    store: LocalSessionStore,
    session_id: str | None,
    usage_source: str,
    reason: str,
    payload: dict,
    call_id: str | None,
    request_id: str | None,
    trace_id: str | None,
) -> None:
    """Persist unmatched usage diagnostics for later inspection."""
    store.record_orphan_callback(
        session_id=session_id,
        reason=reason,
        payload_json=json.dumps(payload),
        correlation_key=call_id,
        correlation_source=usage_source,
        callback_request_id=request_id,
        callback_trace_id=trace_id,
    )
    store.record_unmatched_usage_event(
        session_id=session_id,
        call_id=call_id,
        usage_source=usage_source,
        classification=reason,
        provider_request_id=request_id,
        provider_trace_id=trace_id,
        payload_ref_or_json=json.dumps(payload),
    )

"""Helpers for persisting unmatched usage diagnostics via core services."""
from __future__ import annotations

from l6e_mcp.core.reconciliation import record_unmatched_usage
from l6e_mcp.session_store import LocalSessionStore


def persist_unmatched_usage(
    *,
    session_id: str | None,
    usage_source: str,
    reason: str,
    payload: dict,
    call_id: str | None,
    request_id: str | None,
    trace_id: str | None,
) -> None:
    """Store unmatched usage and reconciliation diagnostics."""
    actionable_reason = reason or "missing_call_id_metadata"
    record_unmatched_usage(
        store=LocalSessionStore(),
        session_id=session_id,
        usage_source=usage_source,
        reason=actionable_reason,
        payload=payload,
        call_id=call_id,
        request_id=request_id,
        trace_id=trace_id,
    )

"""Exactness projection helpers for l6e MCP spend snapshots."""
from __future__ import annotations

from l6e_mcp.contracts.exactness import ExactnessState, RunExactnessState


def normalize_call_exactness_state(
    raw_state: str | None,
    *,
    status: str,
    accounting_mode: str,
    mode_exact_capable: bool | None = None,
) -> ExactnessState:
    """Normalize persisted exactness state with migration-safe fallbacks."""
    if raw_state is not None:
        try:
            return ExactnessState(raw_state)
        except ValueError:
            pass
    if status == "reconciled":
        return ExactnessState.EXACT_RECORDED
    if mode_exact_capable is False and accounting_mode != "estimate_only":
        return ExactnessState.EXACT_UNAVAILABLE
    if accounting_mode == "estimate_only":
        return ExactnessState.ESTIMATE_ONLY
    return ExactnessState.EXACT_PENDING


def run_exactness_state(exactness_states: list[ExactnessState]) -> RunExactnessState:
    """Aggregate call-level exactness into a run-level status."""
    if not exactness_states:
        return RunExactnessState.ALL_ESTIMATE_ONLY
    if any(state == ExactnessState.EXACT_UNAVAILABLE for state in exactness_states):
        return RunExactnessState.EXACTNESS_DEGRADED
    has_exact = any(state == ExactnessState.EXACT_RECORDED for state in exactness_states)
    has_pending = any(state == ExactnessState.EXACT_PENDING for state in exactness_states)
    has_estimate_only = any(state == ExactnessState.ESTIMATE_ONLY for state in exactness_states)
    has_non_exact = has_pending or has_estimate_only
    if has_exact and has_non_exact:
        return RunExactnessState.PARTIAL_EXACT
    if has_exact and not has_non_exact:
        return RunExactnessState.FULLY_EXACT_FOR_SUPPORTED_CALLS
    return RunExactnessState.ALL_ESTIMATE_ONLY

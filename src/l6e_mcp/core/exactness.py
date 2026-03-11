"""Exactness projection helpers for l6e MCP spend snapshots."""
from __future__ import annotations

from l6e_mcp.contracts.exactness import ExactnessState, RunExactnessState


def call_exactness_state(status: str, accounting_mode: str) -> ExactnessState:
    """Project call exactness from persisted call status and session mode."""
    if status == "reconciled":
        return ExactnessState.EXACT_RECORDED
    if accounting_mode == "estimate_only":
        return ExactnessState.ESTIMATE_ONLY
    return ExactnessState.EXACT_PENDING


def run_exactness_state(exactness_states: list[ExactnessState]) -> RunExactnessState:
    """Aggregate call-level exactness into a run-level status."""
    if not exactness_states:
        return RunExactnessState.ALL_ESTIMATE_ONLY
    has_exact = any(state == ExactnessState.EXACT_RECORDED for state in exactness_states)
    has_non_exact = any(state != ExactnessState.EXACT_RECORDED for state in exactness_states)
    if has_exact and has_non_exact:
        return RunExactnessState.PARTIAL_EXACT
    if has_exact and not has_non_exact:
        return RunExactnessState.FULLY_EXACT_FOR_SUPPORTED_CALLS
    return RunExactnessState.ALL_ESTIMATE_ONLY

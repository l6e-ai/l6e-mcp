"""Exactness state machine behavior tests."""
from __future__ import annotations

from l6e_mcp.contracts.exactness import ExactnessState, RunExactnessState
from l6e_mcp.core.exactness import normalize_call_exactness_state, run_exactness_state


def test_normalize_call_exactness_state_from_status_and_mode():
    assert (
        normalize_call_exactness_state(
            None,
            status="pending",
            accounting_mode="estimate_only",
            mode_exact_capable=None,
        )
        == ExactnessState.ESTIMATE_ONLY
    )
    assert (
        normalize_call_exactness_state(
            None,
            status="pending",
            accounting_mode="exact_optional",
            mode_exact_capable=True,
        )
        == ExactnessState.EXACT_PENDING
    )
    assert (
        normalize_call_exactness_state(
            None,
            status="pending",
            accounting_mode="exact_optional",
            mode_exact_capable=False,
        )
        == ExactnessState.EXACT_UNAVAILABLE
    )
    assert (
        normalize_call_exactness_state(
            None,
            status="reconciled",
            accounting_mode="exact_optional",
            mode_exact_capable=False,
        )
        == ExactnessState.EXACT_RECORDED
    )


def test_run_exactness_state_projection_matrix():
    assert run_exactness_state([]) == RunExactnessState.ALL_ESTIMATE_ONLY
    assert run_exactness_state([ExactnessState.ESTIMATE_ONLY]) == (
        RunExactnessState.ALL_ESTIMATE_ONLY
    )
    assert run_exactness_state([ExactnessState.EXACT_PENDING]) == (
        RunExactnessState.ALL_ESTIMATE_ONLY
    )
    assert run_exactness_state([ExactnessState.EXACT_RECORDED]) == (
        RunExactnessState.FULLY_EXACT_FOR_SUPPORTED_CALLS
    )
    assert run_exactness_state(
        [ExactnessState.EXACT_RECORDED, ExactnessState.EXACT_PENDING]
    ) == RunExactnessState.PARTIAL_EXACT
    assert run_exactness_state(
        [ExactnessState.EXACT_RECORDED, ExactnessState.EXACT_UNAVAILABLE]
    ) == RunExactnessState.EXACTNESS_DEGRADED

"""Facade: backward-compatible LocalSessionStore composing the store sub-modules.

All public method signatures are preserved so server.py and tests require zero changes.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from l6e._types import PipelinePolicy, PromptComplexity

from l6e_mcp.store._connection import _db_path

# Re-export the public types so existing ``from l6e_mcp.session_store import X`` imports work.
from l6e_mcp.store.calls import CallRepository, CallState, ReconcileRequest
from l6e_mcp.store.diagnostics import DiagnosticsRepository
from l6e_mcp.store.sessions import SessionRepository, SessionState, StaleSessionInfo
from l6e_mcp.store.summary import (  # noqa: F401 (re-export)
    build_session_report,
    session_run_summary,
)

__all__ = [
    "LocalSessionStore",
    "SessionState",
    "StaleSessionInfo",
    "CallState",
    "ReconcileRequest",
    "build_session_report",
    "session_run_summary",
]


class LocalSessionStore:
    """Backward-compatible facade over SessionRepository, CallRepository, and DiagnosticsRepository.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        path = db_path or _db_path()
        self._sessions = SessionRepository(path)
        self._calls = CallRepository(path)
        self._diagnostics = DiagnosticsRepository(path)

    # ------------------------------------------------------------------
    # Session methods
    # ------------------------------------------------------------------

    def create_session(
        self,
        *,
        session_id: str,
        model: str,
        policy: PipelinePolicy,
        source: str,
        log_path: str | None,
        accounting_mode: str | None = None,
        usage_channel: str | None = None,
        ask_mode_exact_capable: bool | None = None,
        plan_mode_exact_capable: bool | None = None,
        agent_mode_exact_capable: bool | None = None,
        start_summary: str | None = None,
    ) -> SessionState:
        return self._sessions.create(
            session_id=session_id,
            model=model,
            policy=policy,
            source=source,
            log_path=log_path,
            accounting_mode=accounting_mode,
            usage_channel=usage_channel,
            ask_mode_exact_capable=ask_mode_exact_capable,
            plan_mode_exact_capable=plan_mode_exact_capable,
            agent_mode_exact_capable=agent_mode_exact_capable,
            start_summary=start_summary,
        )

    def get_session(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def require_active_session(self, session_id: str) -> SessionState:
        return self._sessions.require_active(session_id)

    def finalize_session(self, session_id: str, *, end_summary: str | None = None) -> SessionState:
        return self._sessions.finalize(session_id, end_summary=end_summary)

    def increment_checkpoint_calls(self, session_id: str, increment_by: int = 1) -> None:
        self._sessions.increment_checkpoint_calls(session_id, increment_by=increment_by)

    def increment_status_calls(self, session_id: str, increment_by: int = 1) -> None:
        self._sessions.increment_status_calls(session_id, increment_by=increment_by)

    def list_stale_active(self, max_idle_seconds: float = 3600) -> list[StaleSessionInfo]:
        return self._sessions.list_stale_active(max_idle_seconds)

    # ------------------------------------------------------------------
    # Call methods
    # ------------------------------------------------------------------

    def create_call(
        self,
        *,
        session_id: str,
        tool_name: str,
        model_requested: str,
        model_used: str,
        estimated_prompt_tokens: int,
        estimated_completion_tokens: int,
        estimated_cost_usd: Decimal,
        rerouted: bool,
        elapsed_ms: float = 0.0,
        prompt_complexity: PromptComplexity | None = None,
        is_multi_turn: bool = False,
        actual_prompt_tokens: int | None = None,
        actual_completion_tokens: int | None = None,
        actual_cost_usd: Decimal | None = None,
        status: str = "pending",
        correlation_key: str | None = None,
        correlation_source: str | None = None,
        actor_type: str = "parent_agent",
        actor_id: str | None = None,
        actor_name: str | None = None,
        parent_call_id: str | None = None,
        call_mode: str | None = None,
        exactness_state: str | None = None,
        hosted_ledger_id: str | None = None,
    ) -> CallState:
        return self._calls.create(
            session_id=session_id,
            tool_name=tool_name,
            model_requested=model_requested,
            model_used=model_used,
            estimated_prompt_tokens=estimated_prompt_tokens,
            estimated_completion_tokens=estimated_completion_tokens,
            estimated_cost_usd=estimated_cost_usd,
            rerouted=rerouted,
            elapsed_ms=elapsed_ms,
            prompt_complexity=prompt_complexity,
            is_multi_turn=is_multi_turn,
            actual_prompt_tokens=actual_prompt_tokens,
            actual_completion_tokens=actual_completion_tokens,
            actual_cost_usd=actual_cost_usd,
            status=status,
            correlation_key=correlation_key,
            correlation_source=correlation_source,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            parent_call_id=parent_call_id,
            call_mode=call_mode,
            exactness_state=exactness_state,
            hosted_ledger_id=hosted_ledger_id,
        )

    def get_call(self, call_id: str) -> CallState | None:
        return self._calls.get(call_id)

    def list_calls_for_session(self, session_id: str) -> list[CallState]:
        return self._calls.list_for_session(session_id)

    def latest_pending_call(self, session_id: str) -> CallState | None:
        return self._calls.latest_pending(session_id)

    def find_pending_call_by_correlation_key(self, correlation_key: str) -> CallState | None:
        return self._calls.find_pending_by_correlation_key(correlation_key)

    def reconcile_call(self, request: ReconcileRequest) -> CallState:
        return self._calls.reconcile(request)

    # ------------------------------------------------------------------
    # Diagnostics methods
    # ------------------------------------------------------------------

    def record_orphan_callback(
        self,
        *,
        session_id: str | None,
        reason: str,
        payload_json: str,
        correlation_key: str | None = None,
        correlation_source: str | None = None,
        callback_request_id: str | None = None,
        callback_trace_id: str | None = None,
    ) -> None:
        self._diagnostics.record_orphan_callback(
            session_id=session_id,
            reason=reason,
            payload_json=payload_json,
            correlation_key=correlation_key,
            correlation_source=correlation_source,
            callback_request_id=callback_request_id,
            callback_trace_id=callback_trace_id,
        )

    def record_reconciliation_attempt(
        self,
        *,
        session_id: str | None,
        call_id: str | None,
        usage_source: str,
        result: str,
        idempotency_key: str | None,
        error_code: str | None,
        details_json: str | None,
    ) -> None:
        self._diagnostics.record_reconciliation_attempt(
            session_id=session_id,
            call_id=call_id,
            usage_source=usage_source,
            result=result,
            idempotency_key=idempotency_key,
            error_code=error_code,
            details_json=details_json,
        )

    def record_unmatched_usage_event(
        self,
        *,
        session_id: str | None,
        call_id: str | None,
        usage_source: str,
        provider_request_id: str | None,
        provider_trace_id: str | None,
        classification: str,
        payload_ref_or_json: str,
    ) -> None:
        self._diagnostics.record_unmatched_usage_event(
            session_id=session_id,
            call_id=call_id,
            usage_source=usage_source,
            provider_request_id=provider_request_id,
            provider_trace_id=provider_trace_id,
            classification=classification,
            payload_ref_or_json=payload_ref_or_json,
        )

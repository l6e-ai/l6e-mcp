"""Call persistence: CallState dataclass and CallRepository."""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from l6e._types import CallRecord, PromptComplexity

from l6e_mcp.contracts.exactness import ExactnessState
from l6e_mcp.contracts.mode_coverage import ModeCoverage, mode_exact_capable_for_call_mode
from l6e_mcp.core.exactness import normalize_call_exactness_state
from l6e_mcp.store._connection import _db_path, make_connection
from l6e_mcp.store._serialization import _call_from_row


@dataclass(frozen=True)
class CallState:
    call_id: str
    session_id: str
    call_index: int
    tool_name: str
    model_requested: str
    model_used: str
    estimated_prompt_tokens: int
    estimated_completion_tokens: int
    estimated_cost_usd: float
    actual_prompt_tokens: int | None
    actual_completion_tokens: int | None
    actual_cost_usd: float | None
    rerouted: bool
    elapsed_ms: float
    prompt_complexity: PromptComplexity | None
    is_multi_turn: bool
    status: str
    created_at: float
    reconciled_at: float | None
    correlation_key: str | None
    correlation_source: str | None
    callback_request_id: str | None
    callback_trace_id: str | None
    exactness_state: str
    hosted_ledger_id: str | None
    actor_type: str
    actor_id: str | None
    actor_name: str | None
    parent_call_id: str | None
    call_mode: str | None
    mode_exact_capable: bool | None

    def effective_record(self) -> CallRecord:
        prompt_tokens = (
            self.actual_prompt_tokens
            if self.actual_prompt_tokens is not None
            else self.estimated_prompt_tokens
        )
        completion_tokens = (
            self.actual_completion_tokens
            if self.actual_completion_tokens is not None
            else self.estimated_completion_tokens
        )
        cost_usd = (
            self.actual_cost_usd
            if self.actual_cost_usd is not None
            else self.estimated_cost_usd
        )
        return CallRecord(
            call_index=self.call_index,
            model_requested=self.model_requested,
            model_used=self.model_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            rerouted=self.rerouted,
            elapsed_ms=self.elapsed_ms,
            stage=self.tool_name,
            prompt_complexity=self.prompt_complexity,
            is_multi_turn=self.is_multi_turn,
            actor_type=self.actor_type,
            actor_id=self.actor_id,
            actor_name=self.actor_name,
            parent_call_id=self.parent_call_id,
        )


class CallRepository:
    """CRUD and reconciliation for the calls table."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _db_path()

    def create(
        self,
        *,
        session_id: str,
        tool_name: str,
        model_requested: str,
        model_used: str,
        estimated_prompt_tokens: int,
        estimated_completion_tokens: int,
        estimated_cost_usd: float,
        rerouted: bool,
        elapsed_ms: float = 0.0,
        prompt_complexity: PromptComplexity | None = None,
        is_multi_turn: bool = False,
        actual_prompt_tokens: int | None = None,
        actual_completion_tokens: int | None = None,
        actual_cost_usd: float | None = None,
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
        created_at = time.time()
        call_id = f"call_{secrets.token_hex(8)}"
        effective_correlation_key = correlation_key or call_id
        effective_correlation_source = correlation_source or "checkpoint_call_id"
        with make_connection(self._path) as conn:
            row = conn.execute(
                """
                SELECT state, next_call_index, accounting_mode,
                       ask_mode_exact_capable, plan_mode_exact_capable, agent_mode_exact_capable
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown session '{session_id}'. Call l6e_run_start first.")
            if row["state"] == "finalized":
                raise KeyError(
                    f"Unknown session '{session_id}'. Already ended or never started."
                )
            call_index = int(row["next_call_index"])
            coverage = ModeCoverage(
                ask_mode_exact_capable=bool(row["ask_mode_exact_capable"]),
                plan_mode_exact_capable=bool(row["plan_mode_exact_capable"]),
                agent_mode_exact_capable=bool(row["agent_mode_exact_capable"]),
            )
            mode_exact_capable = mode_exact_capable_for_call_mode(coverage, call_mode)
            resolved_exactness_state = normalize_call_exactness_state(
                exactness_state,
                status=status,
                accounting_mode=str(row["accounting_mode"]),
                mode_exact_capable=mode_exact_capable,
            ).value
            conn.execute(
                "UPDATE sessions SET next_call_index = ? WHERE session_id = ?",
                (call_index + 1, session_id),
            )
            conn.execute(
                """
                INSERT INTO calls (
                    call_id, session_id, call_index, tool_name, model_requested, model_used,
                    estimated_prompt_tokens, estimated_completion_tokens, estimated_cost_usd,
                    actual_prompt_tokens, actual_completion_tokens, actual_cost_usd,
                    rerouted, elapsed_ms, prompt_complexity, is_multi_turn, status,
                    created_at, reconciled_at, correlation_key, correlation_source,
                    callback_request_id, callback_trace_id, actor_type, actor_id,
                    actor_name, parent_call_id, call_mode, mode_exact_capable,
                    exactness_state, hosted_ledger_id
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    call_id,
                    session_id,
                    call_index,
                    tool_name,
                    model_requested,
                    model_used,
                    estimated_prompt_tokens,
                    estimated_completion_tokens,
                    estimated_cost_usd,
                    actual_prompt_tokens,
                    actual_completion_tokens,
                    actual_cost_usd,
                    int(rerouted),
                    elapsed_ms,
                    prompt_complexity.value if prompt_complexity is not None else None,
                    int(is_multi_turn),
                    status,
                    created_at,
                    created_at if status == "reconciled" else None,
                    effective_correlation_key,
                    effective_correlation_source,
                    None,
                    None,
                    actor_type,
                    actor_id,
                    actor_name,
                    parent_call_id,
                    call_mode.strip().lower() if call_mode is not None else None,
                    int(mode_exact_capable) if mode_exact_capable is not None else None,
                    resolved_exactness_state,
                    hosted_ledger_id,
                ),
            )
        call = self.get(call_id)
        if call is None:
            raise RuntimeError(f"Failed to create call '{call_id}'")
        return call

    def get(self, call_id: str) -> CallState | None:
        with make_connection(self._path) as conn:
            row = conn.execute(
                """
                SELECT c.*, s.accounting_mode AS session_accounting_mode
                FROM calls c
                JOIN sessions s ON s.session_id = c.session_id
                WHERE c.call_id = ?
                """,
                (call_id,),
            ).fetchone()
        return _call_from_row(row) if row is not None else None

    def list_for_session(self, session_id: str) -> list[CallState]:
        with make_connection(self._path) as conn:
            rows = conn.execute(
                """
                SELECT c.*, s.accounting_mode AS session_accounting_mode
                FROM calls c
                JOIN sessions s ON s.session_id = c.session_id
                WHERE c.session_id = ?
                ORDER BY call_index ASC
                """,
                (session_id,),
            ).fetchall()
        return [_call_from_row(row) for row in rows]

    def latest_pending(self, session_id: str) -> CallState | None:
        with make_connection(self._path) as conn:
            row = conn.execute(
                """
                SELECT c.*, s.accounting_mode AS session_accounting_mode
                FROM calls c
                JOIN sessions s ON s.session_id = c.session_id
                WHERE c.session_id = ? AND c.status = 'pending'
                ORDER BY call_index DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return _call_from_row(row) if row is not None else None

    def find_pending_by_correlation_key(self, correlation_key: str) -> CallState | None:
        with make_connection(self._path) as conn:
            row = conn.execute(
                """
                SELECT c.*, s.accounting_mode AS session_accounting_mode
                FROM calls c
                JOIN sessions s ON s.session_id = c.session_id
                WHERE c.correlation_key = ? AND c.status = 'pending'
                ORDER BY call_index DESC
                LIMIT 1
                """,
                (correlation_key,),
            ).fetchone()
        return _call_from_row(row) if row is not None else None

    def reconcile(
        self,
        *,
        call_id: str,
        actual_prompt_tokens: int,
        actual_completion_tokens: int,
        actual_cost_usd: float,
        model_used: str | None = None,
        callback_request_id: str | None = None,
        callback_trace_id: str | None = None,
        correlation_key: str | None = None,
        correlation_source: str | None = None,
        hosted_ledger_id: str | None = None,
    ) -> CallState:
        reconciled_at = time.time()
        with make_connection(self._path) as conn:
            row = conn.execute(
                """
                SELECT c.status, c.actual_prompt_tokens, c.actual_completion_tokens,
                       c.actual_cost_usd, s.state
                FROM calls c
                JOIN sessions s ON s.session_id = c.session_id
                WHERE c.call_id = ?
                """,
                (call_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown call '{call_id}'.")
            if row["state"] == "finalized":
                raise KeyError(f"Call '{call_id}' belongs to a finalized session.")
            if row["status"] == "reconciled":
                same_values = (
                    row["actual_prompt_tokens"] == actual_prompt_tokens
                    and row["actual_completion_tokens"] == actual_completion_tokens
                    and float(row["actual_cost_usd"]) == actual_cost_usd
                )
                if same_values:
                    call = self.get(call_id)
                    if call is None:
                        raise RuntimeError(f"Failed to reload reconciled call '{call_id}'")
                    return call
            if model_used is None:
                conn.execute(
                    """
                    UPDATE calls
                    SET actual_prompt_tokens = ?, actual_completion_tokens = ?,
                        actual_cost_usd = ?, status = 'reconciled', reconciled_at = ?,
                        callback_request_id = COALESCE(?, callback_request_id),
                        callback_trace_id = COALESCE(?, callback_trace_id),
                        correlation_key = COALESCE(?, correlation_key),
                        correlation_source = COALESCE(?, correlation_source),
                        exactness_state = ?,
                        hosted_ledger_id = COALESCE(?, hosted_ledger_id)
                    WHERE call_id = ?
                    """,
                    (
                        actual_prompt_tokens,
                        actual_completion_tokens,
                        actual_cost_usd,
                        reconciled_at,
                        callback_request_id,
                        callback_trace_id,
                        correlation_key,
                        correlation_source,
                        ExactnessState.EXACT_RECORDED.value,
                        hosted_ledger_id,
                        call_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE calls
                    SET actual_prompt_tokens = ?, actual_completion_tokens = ?,
                        actual_cost_usd = ?, model_used = ?, status = 'reconciled',
                        reconciled_at = ?,
                        callback_request_id = COALESCE(?, callback_request_id),
                        callback_trace_id = COALESCE(?, callback_trace_id),
                        correlation_key = COALESCE(?, correlation_key),
                        correlation_source = COALESCE(?, correlation_source),
                        exactness_state = ?,
                        hosted_ledger_id = COALESCE(?, hosted_ledger_id)
                    WHERE call_id = ?
                    """,
                    (
                        actual_prompt_tokens,
                        actual_completion_tokens,
                        actual_cost_usd,
                        model_used,
                        reconciled_at,
                        callback_request_id,
                        callback_trace_id,
                        correlation_key,
                        correlation_source,
                        ExactnessState.EXACT_RECORDED.value,
                        hosted_ledger_id,
                        call_id,
                    ),
                )
        call = self.get(call_id)
        if call is None:
            raise RuntimeError(f"Failed to reconcile call '{call_id}'")
        return call

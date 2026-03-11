"""Local SQLite-backed session and call persistence for l6e-mcp."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from l6e._types import (
    BudgetMode,
    CallRecord,
    OnBudgetExceeded,
    PipelinePolicy,
    PromptComplexity,
    RunSummary,
    StageRoutingHint,
    SubagentSpend,
)

from l6e_mcp.contracts.exactness import ExactnessState
from l6e_mcp.contracts.mode_coverage import ModeCoverage, mode_exact_capable_for_call_mode
from l6e_mcp.core.exactness import normalize_call_exactness_state
from l6e_mcp.store import schema as store_schema

_DEFAULT_DB_PATH = Path.home() / ".l6e" / "sessions.db"


def _db_path() -> Path:
    raw = os.environ.get("L6E_SESSION_DB_PATH")
    return Path(raw) if raw else _DEFAULT_DB_PATH


@dataclass(frozen=True)
class SessionState:
    session_id: str
    model: str
    policy: PipelinePolicy
    source: str
    log_path: str | None
    accounting_mode: str
    usage_channel: str
    state: str
    next_call_index: int
    created_at: float
    ended_at: float | None
    finalized_at: float | None
    advanced_fallback_enabled: bool
    ask_mode_exact_capable: bool
    plan_mode_exact_capable: bool
    agent_mode_exact_capable: bool

    @property
    def proxy_mode(self) -> bool:
        """Backward-compatible projection for legacy proxy-mode behavior."""
        return self.usage_channel == store_schema.USAGE_CHANNEL_SELF_HOSTED_RELAY


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


class LocalSessionStore:
    """Authoritative local persistence for MCP sessions and reconciled calls."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    log_path TEXT,
                    proxy_mode INTEGER NOT NULL,
                    accounting_mode TEXT NOT NULL DEFAULT 'estimate_only',
                    usage_channel TEXT NOT NULL DEFAULT 'none',
                    advanced_fallback_enabled INTEGER NOT NULL DEFAULT 0,
                    ask_mode_exact_capable INTEGER NOT NULL DEFAULT 0,
                    plan_mode_exact_capable INTEGER NOT NULL DEFAULT 0,
                    agent_mode_exact_capable INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    next_call_index INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    ended_at REAL,
                    finalized_at REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    call_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    call_index INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    model_requested TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    estimated_prompt_tokens INTEGER NOT NULL,
                    estimated_completion_tokens INTEGER NOT NULL,
                    estimated_cost_usd REAL NOT NULL,
                    actual_prompt_tokens INTEGER,
                    actual_completion_tokens INTEGER,
                    actual_cost_usd REAL,
                    rerouted INTEGER NOT NULL,
                    elapsed_ms REAL NOT NULL,
                    prompt_complexity TEXT,
                    is_multi_turn INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    reconciled_at REAL,
                    correlation_key TEXT,
                    correlation_source TEXT,
                    callback_request_id TEXT,
                    callback_trace_id TEXT,
                    exactness_state TEXT NOT NULL DEFAULT 'estimate_only',
                    hosted_ledger_id TEXT,
                    actor_type TEXT NOT NULL DEFAULT 'parent_agent',
                    actor_id TEXT,
                    actor_name TEXT,
                    parent_call_id TEXT,
                    call_mode TEXT,
                    mode_exact_capable INTEGER,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_calls_session_id ON calls(session_id, call_index)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orphan_callbacks (
                    orphan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    reason TEXT NOT NULL,
                    correlation_key TEXT,
                    correlation_source TEXT,
                    callback_request_id TEXT,
                    callback_trace_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            _ensure_column(conn, "calls", "correlation_key", "TEXT")
            _ensure_column(conn, "calls", "correlation_source", "TEXT")
            _ensure_column(conn, "calls", "callback_request_id", "TEXT")
            _ensure_column(conn, "calls", "callback_trace_id", "TEXT")
            _ensure_column(conn, "calls", "actor_type", "TEXT NOT NULL DEFAULT 'parent_agent'")
            _ensure_column(conn, "calls", "actor_id", "TEXT")
            _ensure_column(conn, "calls", "actor_name", "TEXT")
            _ensure_column(conn, "calls", "parent_call_id", "TEXT")
            _ensure_column(
                conn,
                "sessions",
                "accounting_mode",
                "TEXT NOT NULL DEFAULT 'estimate_only'",
            )
            _ensure_column(conn, "sessions", "usage_channel", "TEXT NOT NULL DEFAULT 'none'")
            _ensure_column(
                conn,
                "sessions",
                "advanced_fallback_enabled",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                conn,
                "sessions",
                "ask_mode_exact_capable",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                conn,
                "sessions",
                "plan_mode_exact_capable",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                conn,
                "sessions",
                "agent_mode_exact_capable",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                conn,
                "calls",
                "exactness_state",
                "TEXT NOT NULL DEFAULT 'estimate_only'",
            )
            _ensure_column(conn, "calls", "hosted_ledger_id", "TEXT")
            _ensure_column(conn, "calls", "call_mode", "TEXT")
            _ensure_column(conn, "calls", "mode_exact_capable", "INTEGER")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reconciliation_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    call_id TEXT,
                    usage_source TEXT NOT NULL,
                    result TEXT NOT NULL,
                    idempotency_key TEXT,
                    error_code TEXT,
                    details_json TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS unmatched_usage_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    call_id TEXT,
                    usage_source TEXT NOT NULL,
                    provider_request_id TEXT,
                    provider_trace_id TEXT,
                    classification TEXT NOT NULL,
                    payload_ref_or_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )

    def create_session(
        self,
        *,
        session_id: str,
        model: str,
        policy: PipelinePolicy,
        source: str,
        log_path: str | None,
        proxy_mode: bool = False,
        accounting_mode: str | None = None,
        usage_channel: str | None = None,
        advanced_fallback_enabled: bool = False,
        ask_mode_exact_capable: bool | None = None,
        plan_mode_exact_capable: bool | None = None,
        agent_mode_exact_capable: bool | None = None,
    ) -> SessionState:
        created_at = time.time()
        effective_accounting_mode = accounting_mode or (
            store_schema.ACCOUNTING_MODE_EXACT_OPTIONAL
            if proxy_mode
            else store_schema.ACCOUNTING_MODE_ESTIMATE_ONLY
        )
        effective_usage_channel = usage_channel or (
            store_schema.USAGE_CHANNEL_SELF_HOSTED_RELAY
            if proxy_mode
            else store_schema.USAGE_CHANNEL_NONE
        )
        default_coverage = _default_mode_coverage(
            usage_channel=effective_usage_channel,
            accounting_mode=effective_accounting_mode,
        )
        resolved_ask = (
            default_coverage.ask_mode_exact_capable
            if ask_mode_exact_capable is None
            else ask_mode_exact_capable
        )
        resolved_plan = (
            default_coverage.plan_mode_exact_capable
            if plan_mode_exact_capable is None
            else plan_mode_exact_capable
        )
        resolved_agent = (
            default_coverage.agent_mode_exact_capable
            if agent_mode_exact_capable is None
            else agent_mode_exact_capable
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, model, policy_json, source, log_path, proxy_mode,
                    accounting_mode, usage_channel, advanced_fallback_enabled,
                    ask_mode_exact_capable,
                    plan_mode_exact_capable, agent_mode_exact_capable, state, next_call_index,
                    created_at, ended_at, finalized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, NULL, NULL)
                """,
                (
                    session_id,
                    model,
                    _policy_to_json(policy),
                    source,
                    log_path,
                    int(proxy_mode),
                    effective_accounting_mode,
                    effective_usage_channel,
                    int(advanced_fallback_enabled),
                    int(resolved_ask),
                    int(resolved_plan),
                    int(resolved_agent),
                    created_at,
                ),
            )
        session = self.get_session(session_id)
        if session is None:
            raise RuntimeError(f"Failed to create session '{session_id}'")
        return session

    def get_session(self, session_id: str) -> SessionState | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return _session_from_row(row) if row is not None else None

    def require_active_session(self, session_id: str) -> SessionState:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"Unknown session '{session_id}'. Call l6e_run_start first.")
        if session.state == "finalized":
            raise KeyError(f"Unknown session '{session_id}'. Already ended or never started.")
        return session

    def create_call(
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
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT state, next_call_index
                       , accounting_mode, ask_mode_exact_capable, plan_mode_exact_capable,
                         agent_mode_exact_capable
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown session '{session_id}'. Call l6e_run_start first.")
            if row["state"] == "finalized":
                raise KeyError(f"Unknown session '{session_id}'. Already ended or never started.")
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
                    actor_name, parent_call_id, call_mode, mode_exact_capable, exactness_state,
                    hosted_ledger_id
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
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
        call = self.get_call(call_id)
        if call is None:
            raise RuntimeError(f"Failed to create call '{call_id}'")
        return call

    def get_call(self, call_id: str) -> CallState | None:
        with self._connect() as conn:
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

    def list_calls_for_session(self, session_id: str) -> list[CallState]:
        with self._connect() as conn:
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

    def latest_pending_call(self, session_id: str) -> CallState | None:
        with self._connect() as conn:
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

    def find_pending_call_by_correlation_key(self, correlation_key: str) -> CallState | None:
        with self._connect() as conn:
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

    def reconcile_call(
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
        with self._connect() as conn:
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
                    call = self.get_call(call_id)
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
        call = self.get_call(call_id)
        if call is None:
            raise RuntimeError(f"Failed to reconcile call '{call_id}'")
        return call

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
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO orphan_callbacks (
                    session_id, reason, correlation_key, correlation_source,
                    callback_request_id, callback_trace_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    reason,
                    correlation_key,
                    correlation_source,
                    callback_request_id,
                    callback_trace_id,
                    payload_json,
                    time.time(),
                ),
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
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reconciliation_attempts (
                    session_id, call_id, usage_source, result, idempotency_key,
                    error_code, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    call_id,
                    usage_source,
                    result,
                    idempotency_key,
                    error_code,
                    details_json,
                    time.time(),
                ),
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
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO unmatched_usage_events (
                    session_id, call_id, usage_source, provider_request_id, provider_trace_id,
                    classification, payload_ref_or_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    call_id,
                    usage_source,
                    provider_request_id,
                    provider_trace_id,
                    classification,
                    payload_ref_or_json,
                    time.time(),
                ),
            )

    def finalize_session(self, session_id: str) -> SessionState:
        finalized_at = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown session '{session_id}'. Already ended or never started.")
            if row["state"] == "finalized":
                raise KeyError(f"Unknown session '{session_id}'. Already ended or never started.")
            conn.execute(
                """
                UPDATE sessions
                SET state = 'finalized', ended_at = ?, finalized_at = ?
                WHERE session_id = ?
                """,
                (finalized_at, finalized_at, session_id),
            )
        session = self.get_session(session_id)
        if session is None:
            raise RuntimeError(f"Failed to finalize session '{session_id}'")
        return session


def session_run_summary(session: SessionState, calls: list[CallState]) -> RunSummary:
    estimator = _estimator_for_policy(session.policy)
    total_cost = 0.0
    counterfactual_cost = 0.0
    records: list[CallRecord] = []
    reroutes = 0
    subagent_calls = 0
    subagent_spend_usd = 0.0
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
    return RunSummary(
        run_id=session.session_id,
        policy=session.policy,
        total_cost=total_cost,
        calls_made=len(records),
        reroutes=reroutes,
        savings_usd=max(0.0, counterfactual_cost - total_cost),
        records=tuple(records),
        source=session.source,
        subagent_calls=subagent_calls,
        subagent_spend_usd=subagent_spend_usd,
        subagents=tuple(subagent_rollups.values()),
    )


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
        stage_routing={
            k: StageRoutingHint(v) for k, v in data.get("stage_routing", {}).items()
        },
        stage_overrides={
            k: BudgetMode(v) for k, v in data.get("stage_overrides", {}).items()
        },
    )


def _session_from_row(row: sqlite3.Row) -> SessionState:
    return SessionState(
        session_id=str(row["session_id"]),
        model=str(row["model"]),
        policy=_policy_from_json(str(row["policy_json"])),
        source=str(row["source"]),
        log_path=str(row["log_path"]) if row["log_path"] is not None else None,
        accounting_mode=(
            str(row["accounting_mode"])
            if row["accounting_mode"] is not None
            else (
                store_schema.ACCOUNTING_MODE_EXACT_OPTIONAL
                if bool(row["proxy_mode"])
                else store_schema.ACCOUNTING_MODE_ESTIMATE_ONLY
            )
        ),
        usage_channel=(
            str(row["usage_channel"])
            if row["usage_channel"] is not None
            else (
                store_schema.USAGE_CHANNEL_SELF_HOSTED_RELAY
                if bool(row["proxy_mode"])
                else store_schema.USAGE_CHANNEL_NONE
            )
        ),
        state=str(row["state"]),
        next_call_index=int(row["next_call_index"]),
        created_at=float(row["created_at"]),
        ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
        finalized_at=(
            float(row["finalized_at"]) if row["finalized_at"] is not None else None
        ),
        advanced_fallback_enabled=bool(row["advanced_fallback_enabled"]),
        ask_mode_exact_capable=bool(row["ask_mode_exact_capable"]),
        plan_mode_exact_capable=bool(row["plan_mode_exact_capable"]),
        agent_mode_exact_capable=bool(row["agent_mode_exact_capable"]),
    )


def _call_from_row(row: sqlite3.Row) -> CallState:
    prompt_complexity = (
        PromptComplexity(str(row["prompt_complexity"]))
        if row["prompt_complexity"] is not None
        else None
    )
    accounting_mode = (
        str(row["session_accounting_mode"])
        if "session_accounting_mode" in row and row["session_accounting_mode"] is not None
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
        estimated_cost_usd=float(row["estimated_cost_usd"]),
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
            float(row["actual_cost_usd"]) if row["actual_cost_usd"] is not None else None
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
        correlation_key=str(row["correlation_key"]) if row["correlation_key"] is not None else None,
        correlation_source=(
            str(row["correlation_source"]) if row["correlation_source"] is not None else None
        ),
        callback_request_id=(
            str(row["callback_request_id"]) if row["callback_request_id"] is not None else None
        ),
        callback_trace_id=(
            str(row["callback_trace_id"]) if row["callback_trace_id"] is not None else None
        ),
        exactness_state=normalized_exactness.value,
        hosted_ledger_id=(
            str(row["hosted_ledger_id"]) if row["hosted_ledger_id"] is not None else None
        ),
        actor_type=str(row["actor_type"]) if row["actor_type"] is not None else "parent_agent",
        actor_id=str(row["actor_id"]) if row["actor_id"] is not None else None,
        actor_name=str(row["actor_name"]) if row["actor_name"] is not None else None,
        parent_call_id=(
            str(row["parent_call_id"]) if row["parent_call_id"] is not None else None
        ),
        call_mode=str(row["call_mode"]) if row["call_mode"] is not None else None,
        mode_exact_capable=mode_exact_capable,
    )


def _estimator_for_policy(policy: PipelinePolicy):
    from l6e.costs import LiteLLMCostEstimator

    return LiteLLMCostEstimator(
        fallback_cost_per_1k_tokens=policy.unknown_model_cost_per_1k_tokens
    )


def _default_mode_coverage(*, usage_channel: str, accounting_mode: str) -> ModeCoverage:
    if accounting_mode == store_schema.ACCOUNTING_MODE_ESTIMATE_ONLY:
        return ModeCoverage(False, False, False)
    if usage_channel == store_schema.USAGE_CHANNEL_HOSTED_EDGE:
        return ModeCoverage(True, True, True)
    if usage_channel == store_schema.USAGE_CHANNEL_SELF_HOSTED_RELAY:
        # Cursor Ask/Plan can route via proxy today; Agent mode often cannot.
        return ModeCoverage(True, True, False)
    if usage_channel == store_schema.USAGE_CHANNEL_MANUAL_IMPORT:
        return ModeCoverage(False, False, False)
    return ModeCoverage(False, False, False)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    existing = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

"""Session persistence: SessionState dataclass and SessionRepository."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from l6e._types import PipelinePolicy

from l6e_mcp.store import schema as store_schema
from l6e_mcp.store._connection import _db_path, make_connection
from l6e_mcp.store._migrations import init_schema
from l6e_mcp.store._serialization import (
    _default_mode_coverage,
    _policy_to_json,
    _session_from_row,
)


@dataclass(frozen=True)
class StaleSessionInfo:
    """Lightweight descriptor for a stale active session."""

    session_id: str
    call_count: int
    session_created_at: float


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
    checkpoint_calls: int
    status_calls: int
    ask_mode_exact_capable: bool
    plan_mode_exact_capable: bool
    agent_mode_exact_capable: bool


class SessionRepository:
    """CRUD for sessions table."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with make_connection(self._path) as conn:
            init_schema(conn)

    def create(
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
    ) -> SessionState:
        created_at = time.time()
        effective_usage_channel = usage_channel or store_schema.USAGE_CHANNEL_NONE
        effective_accounting_mode = accounting_mode or (
            store_schema.ACCOUNTING_MODE_EXACT_OPTIONAL
            if effective_usage_channel == store_schema.USAGE_CHANNEL_SELF_HOSTED_RELAY
            else store_schema.ACCOUNTING_MODE_ESTIMATE_ONLY
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
        with make_connection(self._path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, model, policy_json, source, log_path,
                    accounting_mode, usage_channel,
                    ask_mode_exact_capable, plan_mode_exact_capable, agent_mode_exact_capable,
                    state, next_call_index, checkpoint_calls, status_calls,
                    created_at, ended_at, finalized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, 0, ?, NULL, NULL)
                """,
                (
                    session_id,
                    model,
                    _policy_to_json(policy),
                    source,
                    log_path,
                    effective_accounting_mode,
                    effective_usage_channel,
                    int(resolved_ask),
                    int(resolved_plan),
                    int(resolved_agent),
                    created_at,
                ),
            )
        session = self.get(session_id)
        if session is None:
            raise RuntimeError(f"Failed to create session '{session_id}'")
        return session

    def get(self, session_id: str) -> SessionState | None:
        with make_connection(self._path) as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return _session_from_row(row) if row is not None else None

    def require_active(self, session_id: str) -> SessionState:
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"Unknown session '{session_id}'. Call l6e_run_start first.")
        if session.state == "finalized":
            raise KeyError(f"Unknown session '{session_id}'. Already ended or never started.")
        return session

    def finalize(self, session_id: str) -> SessionState:
        finalized_at = time.time()
        with make_connection(self._path) as conn:
            row = conn.execute(
                "SELECT state FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(
                    f"Unknown session '{session_id}'. Already ended or never started."
                )
            if row["state"] == "finalized":
                raise KeyError(
                    f"Unknown session '{session_id}'. Already ended or never started."
                )
            conn.execute(
                """
                UPDATE sessions
                SET state = 'finalized', ended_at = ?, finalized_at = ?
                WHERE session_id = ?
                """,
                (finalized_at, finalized_at, session_id),
            )
        session = self.get(session_id)
        if session is None:
            raise RuntimeError(f"Failed to finalize session '{session_id}'")
        return session

    def increment_checkpoint_calls(self, session_id: str, increment_by: int = 1) -> None:
        if increment_by <= 0:
            return
        with make_connection(self._path) as conn:
            updated = conn.execute(
                """
                UPDATE sessions
                SET checkpoint_calls = checkpoint_calls + ?
                WHERE session_id = ? AND state != 'finalized'
                """,
                (increment_by, session_id),
            )
            if updated.rowcount == 0:
                raise KeyError(
                    f"Unknown session '{session_id}'. Already ended or never started."
                )

    def increment_status_calls(self, session_id: str, increment_by: int = 1) -> None:
        if increment_by <= 0:
            return
        with make_connection(self._path) as conn:
            updated = conn.execute(
                """
                UPDATE sessions
                SET status_calls = status_calls + ?
                WHERE session_id = ? AND state != 'finalized'
                """,
                (increment_by, session_id),
            )
            if updated.rowcount == 0:
                raise KeyError(
                    f"Unknown session '{session_id}'. Already ended or never started."
                )

    def list_stale_active(self, max_idle_seconds: float = 3600) -> list[StaleSessionInfo]:
        """Return active sessions whose last activity is older than the threshold."""
        cutoff = time.time() - max_idle_seconds
        with make_connection(self._path) as conn:
            rows = conn.execute(
                """
                SELECT s.session_id,
                       COALESCE(c.call_count, 0) AS call_count,
                       s.created_at AS session_created_at
                FROM sessions s
                LEFT JOIN (
                    SELECT session_id,
                           MAX(created_at) AS last_call_at,
                           COUNT(*) AS call_count
                    FROM calls
                    GROUP BY session_id
                ) c ON c.session_id = s.session_id
                WHERE s.state = 'active'
                  AND COALESCE(c.last_call_at, s.created_at) < ?
                """,
                (cutoff,),
            ).fetchall()
        return [
            StaleSessionInfo(
                session_id=row["session_id"],
                call_count=int(row["call_count"]),
                session_created_at=float(row["session_created_at"]),
            )
            for row in rows
        ]

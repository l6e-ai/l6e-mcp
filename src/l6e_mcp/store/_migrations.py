"""SQLite schema initialization and incremental migrations."""
from __future__ import annotations

import sqlite3


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            source TEXT NOT NULL,
            log_path TEXT,
            accounting_mode TEXT NOT NULL DEFAULT 'estimate_only',
            usage_channel TEXT NOT NULL DEFAULT 'none',
            ask_mode_exact_capable INTEGER NOT NULL DEFAULT 0,
            plan_mode_exact_capable INTEGER NOT NULL DEFAULT 0,
            agent_mode_exact_capable INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL,
            next_call_index INTEGER NOT NULL,
            checkpoint_calls INTEGER NOT NULL DEFAULT 0,
            status_calls INTEGER NOT NULL DEFAULT 0,
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
    # Incremental column migrations for existing databases
    _ensure_column(conn, "calls", "correlation_key", "TEXT")
    _ensure_column(conn, "calls", "correlation_source", "TEXT")
    _ensure_column(conn, "calls", "callback_request_id", "TEXT")
    _ensure_column(conn, "calls", "callback_trace_id", "TEXT")
    _ensure_column(conn, "calls", "actor_type", "TEXT NOT NULL DEFAULT 'parent_agent'")
    _ensure_column(conn, "calls", "actor_id", "TEXT")
    _ensure_column(conn, "calls", "actor_name", "TEXT")
    _ensure_column(conn, "calls", "parent_call_id", "TEXT")
    _ensure_column(conn, "calls", "exactness_state", "TEXT NOT NULL DEFAULT 'estimate_only'")
    _ensure_column(conn, "calls", "estimated_prompt_tokens", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "calls", "estimated_completion_tokens", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "calls", "hosted_ledger_id", "TEXT")
    _ensure_column(conn, "calls", "call_mode", "TEXT")
    _ensure_column(conn, "calls", "mode_exact_capable", "INTEGER")
    _ensure_column(conn, "sessions", "accounting_mode", "TEXT NOT NULL DEFAULT 'estimate_only'")
    _ensure_column(conn, "sessions", "usage_channel", "TEXT NOT NULL DEFAULT 'none'")
    _ensure_column(conn, "sessions", "ask_mode_exact_capable", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "sessions", "plan_mode_exact_capable", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "sessions", "agent_mode_exact_capable", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "sessions", "checkpoint_calls", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "sessions", "status_calls", "INTEGER NOT NULL DEFAULT 0")


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, column_type: str
) -> None:
    existing = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

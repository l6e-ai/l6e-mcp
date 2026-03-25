"""Diagnostics persistence: orphan callbacks, reconciliation attempts, unmatched events."""
from __future__ import annotations

import time
from pathlib import Path

from l6e_mcp.store._connection import _db_path, get_connection


class DiagnosticsRepository:
    """Write-only repository for diagnostic/audit tables."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _db_path()

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
        conn = get_connection(self._path)
        with conn:
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
        conn = get_connection(self._path)
        with conn:
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
        conn = get_connection(self._path)
        with conn:
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

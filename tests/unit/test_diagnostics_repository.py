"""Unit tests for DiagnosticsRepository."""
from __future__ import annotations

import sqlite3

from l6e_mcp.store.diagnostics import DiagnosticsRepository
from l6e_mcp.store.sessions import SessionRepository


def _setup(tmp_path):
    db = tmp_path / "sessions.db"
    SessionRepository(db)  # initializes schema
    return DiagnosticsRepository(db), db


def test_record_orphan_callback(tmp_path):
    diag, db = _setup(tmp_path)
    diag.record_orphan_callback(
        session_id="session_test",
        reason="no_match",
        payload_json='{"x": 1}',
        correlation_key="key_1",
        correlation_source="spend_logs",
        callback_request_id="req_1",
        callback_trace_id="trace_1",
    )
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM orphan_callbacks").fetchone()[0]
    assert count == 1


def test_record_orphan_callback_with_nulls(tmp_path):
    diag, db = _setup(tmp_path)
    diag.record_orphan_callback(
        session_id=None,
        reason="no_session",
        payload_json="{}",
    )
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT * FROM orphan_callbacks").fetchone()
    assert row is not None
    assert row[1] is None  # session_id


def test_record_reconciliation_attempt(tmp_path):
    diag, db = _setup(tmp_path)
    diag.record_reconciliation_attempt(
        session_id="session_test",
        call_id="call_abc",
        usage_source="hosted_edge",
        result="matched",
        idempotency_key="idem_1",
        error_code=None,
        details_json='{"ok": true}',
    )
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM reconciliation_attempts").fetchone()[0]
    assert count == 1


def test_record_unmatched_usage_event(tmp_path):
    diag, db = _setup(tmp_path)
    diag.record_unmatched_usage_event(
        session_id="session_test",
        call_id=None,
        usage_source="hosted_edge",
        provider_request_id="req_1",
        provider_trace_id="trace_1",
        classification="missing_call",
        payload_ref_or_json='{"call_id": "missing"}',
    )
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM unmatched_usage_events").fetchone()[0]
    assert count == 1


def test_multiple_records_accumulate(tmp_path):
    diag, db = _setup(tmp_path)
    for i in range(3):
        diag.record_reconciliation_attempt(
            session_id=f"session_{i}",
            call_id=f"call_{i}",
            usage_source="hosted_edge",
            result="matched",
            idempotency_key=None,
            error_code=None,
            details_json=None,
        )
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM reconciliation_attempts").fetchone()[0]
    assert count == 3

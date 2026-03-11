"""Tests for the local SQLite session store."""
from __future__ import annotations

import pytest
from l6e._types import BudgetMode, PipelinePolicy

from l6e_mcp.session_store import LocalSessionStore, session_run_summary


def test_session_store_reconciles_existing_call(tmp_path):
    store = LocalSessionStore(tmp_path / "sessions.db")
    policy = PipelinePolicy(budget=2.0, budget_mode=BudgetMode.WARN)
    session = store.create_session(
        session_id="session_cursor_2026-03-11_deadbeef",
        model="gpt-4o",
        policy=policy,
        source="mcp",
        log_path=str(tmp_path / "runs.jsonl"),
        proxy_mode=True,
    )

    call = store.create_call(
        session_id=session.session_id,
        tool_name="read_files",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=500,
        estimated_completion_tokens=0,
        estimated_cost_usd=0.1,
        rerouted=False,
    )
    reconciled = store.reconcile_call(
        call_id=call.call_id,
        actual_prompt_tokens=123,
        actual_completion_tokens=45,
        actual_cost_usd=0.25,
        model_used="gpt-4o-mini",
    )

    assert reconciled.status == "reconciled"
    assert reconciled.actual_prompt_tokens == 123
    assert reconciled.actual_completion_tokens == 45
    assert reconciled.model_used == "gpt-4o-mini"

    summary = session_run_summary(session, store.list_calls_for_session(session.session_id))
    assert summary.calls_made == 1
    assert summary.records[0].completion_tokens == 45
    assert summary.records[0].model_used == "gpt-4o-mini"


def test_session_store_shares_state_across_instances(tmp_path):
    db_path = tmp_path / "sessions.db"
    policy = PipelinePolicy(budget=2.0, budget_mode=BudgetMode.WARN)

    writer = LocalSessionStore(db_path)
    reader = LocalSessionStore(db_path)
    session = writer.create_session(
        session_id="session_cursor_2026-03-11_feedface",
        model="gpt-4o",
        policy=policy,
        source="mcp",
        log_path=str(tmp_path / "runs.jsonl"),
        proxy_mode=True,
    )
    call = writer.create_call(
        session_id=session.session_id,
        tool_name="read_files",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=500,
        estimated_completion_tokens=0,
        estimated_cost_usd=0.1,
        rerouted=False,
    )

    reconciled = reader.reconcile_call(
        call_id=call.call_id,
        actual_prompt_tokens=200,
        actual_completion_tokens=50,
        actual_cost_usd=0.2,
        model_used="gpt-4o-mini",
    )
    summary = session_run_summary(session, writer.list_calls_for_session(session.session_id))

    assert reconciled.call_id == call.call_id
    assert summary.calls_made == 1
    assert summary.records[0].completion_tokens == 50


def test_session_store_persists_callback_correlation_fields(tmp_path):
    store = LocalSessionStore(tmp_path / "sessions.db")
    policy = PipelinePolicy(budget=2.0, budget_mode=BudgetMode.WARN)
    session = store.create_session(
        session_id="session_cursor_2026-03-11_c0ffee00",
        model="gpt-4o",
        policy=policy,
        source="mcp",
        log_path=str(tmp_path / "runs.jsonl"),
        proxy_mode=True,
    )
    call = store.create_call(
        session_id=session.session_id,
        tool_name="read_files",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=500,
        estimated_completion_tokens=0,
        estimated_cost_usd=0.1,
        rerouted=False,
    )
    reconciled = store.reconcile_call(
        call_id=call.call_id,
        actual_prompt_tokens=123,
        actual_completion_tokens=45,
        actual_cost_usd=0.25,
        model_used="gpt-4o-mini",
        callback_request_id="req_123",
        callback_trace_id="trace_123",
        correlation_key=call.call_id,
        correlation_source="spend_logs_metadata",
    )
    assert reconciled.callback_request_id == "req_123"
    assert reconciled.callback_trace_id == "trace_123"
    assert reconciled.correlation_key == call.call_id
    assert reconciled.correlation_source == "spend_logs_metadata"


def test_session_store_persists_subagent_metadata_and_rolls_up_summary(tmp_path):
    store = LocalSessionStore(tmp_path / "sessions.db")
    policy = PipelinePolicy(budget=2.0, budget_mode=BudgetMode.WARN)
    session = store.create_session(
        session_id="session_cursor_2026-03-11_facefeed",
        model="gpt-4o",
        policy=policy,
        source="mcp",
        log_path=str(tmp_path / "runs.jsonl"),
        proxy_mode=True,
    )
    store.create_call(
        session_id=session.session_id,
        tool_name="subagent_run",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=800,
        estimated_completion_tokens=0,
        estimated_cost_usd=0.2,
        rerouted=False,
        actor_type="subagent",
        actor_id="subagent_search_1",
        actor_name="Search agent",
        parent_call_id="call_parent_123",
    )
    store.create_call(
        session_id=session.session_id,
        tool_name="subagent_run",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=400,
        estimated_completion_tokens=0,
        estimated_cost_usd=0.1,
        rerouted=False,
        actor_type="subagent",
        actor_id="subagent_search_1",
        actor_name="Search agent",
        parent_call_id="call_parent_123",
    )

    calls = store.list_calls_for_session(session.session_id)
    assert calls[0].actor_type == "subagent"
    assert calls[0].actor_id == "subagent_search_1"
    assert calls[0].actor_name == "Search agent"
    assert calls[0].parent_call_id == "call_parent_123"

    summary = session_run_summary(session, calls)
    assert summary.subagent_calls == 2
    assert summary.subagent_spend_usd == pytest.approx(0.3)
    assert len(summary.subagents) == 1
    assert summary.subagents[0].actor_id == "subagent_search_1"
    assert summary.subagents[0].calls_made == 2
    assert summary.records[0].actor_type == "subagent"
    assert summary.records[0].parent_call_id == "call_parent_123"


def test_session_store_records_reconciliation_attempt_and_unmatched_usage(tmp_path):
    store = LocalSessionStore(tmp_path / "sessions.db")
    store.record_reconciliation_attempt(
        session_id="session_1",
        call_id="call_1",
        usage_source="hosted_edge",
        result="matched",
        idempotency_key="idem_1",
        error_code=None,
        details_json='{"ok": true}',
    )
    store.record_unmatched_usage_event(
        session_id="session_1",
        call_id=None,
        usage_source="hosted_edge",
        provider_request_id="req_1",
        provider_trace_id="trace_1",
        classification="missing_call",
        payload_ref_or_json='{"call_id": "missing"}',
    )

    import sqlite3

    with sqlite3.connect(tmp_path / "sessions.db") as conn:
        attempts = conn.execute("SELECT COUNT(*) FROM reconciliation_attempts").fetchone()[0]
        unmatched = conn.execute("SELECT COUNT(*) FROM unmatched_usage_events").fetchone()[0]
    assert attempts == 1
    assert unmatched == 1


def test_session_store_normalizes_unknown_exactness_state(tmp_path):
    store = LocalSessionStore(tmp_path / "sessions.db")
    policy = PipelinePolicy(budget=2.0, budget_mode=BudgetMode.WARN)
    session = store.create_session(
        session_id="session_cursor_2026-03-11_badstate",
        model="gpt-4o",
        policy=policy,
        source="mcp",
        log_path=str(tmp_path / "runs.jsonl"),
        proxy_mode=True,
    )
    call = store.create_call(
        session_id=session.session_id,
        tool_name="read_files",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=500,
        estimated_completion_tokens=0,
        estimated_cost_usd=0.1,
        rerouted=False,
        exactness_state="legacy_state_unknown",
    )
    assert call.exactness_state == "exact_pending"

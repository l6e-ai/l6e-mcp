"""Unit tests for CallRepository."""
from __future__ import annotations

import pytest
from l6e._types import BudgetMode, PipelinePolicy

from l6e_mcp.store.calls import CallRepository
from l6e_mcp.store.sessions import SessionRepository


def _policy() -> PipelinePolicy:
    return PipelinePolicy(budget=1.5, budget_mode=BudgetMode.HALT)


def _setup(tmp_path):
    db = tmp_path / "sessions.db"
    sessions = SessionRepository(db)
    sessions.create(
        session_id="session_cursor_2026-03-12_calltest1",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
        proxy_mode=True,
    )
    calls = CallRepository(db)
    return sessions, calls


def test_create_and_get_call(tmp_path):
    _, calls = _setup(tmp_path)
    call = calls.create(
        session_id="session_cursor_2026-03-12_calltest1",
        tool_name="planning",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=500,
        estimated_completion_tokens=200,
        estimated_cost_usd=0.01,
        rerouted=False,
    )
    assert call.tool_name == "planning"
    assert call.status == "pending"
    assert call.call_index == 0

    fetched = calls.get(call.call_id)
    assert fetched is not None
    assert fetched.call_id == call.call_id


def test_create_call_increments_index(tmp_path):
    _, calls = _setup(tmp_path)
    sid = "session_cursor_2026-03-12_calltest1"
    c1 = calls.create(
        session_id=sid, tool_name="a", model_requested="gpt-4o", model_used="gpt-4o",
        estimated_prompt_tokens=100, estimated_completion_tokens=50,
        estimated_cost_usd=0.001, rerouted=False,
    )
    c2 = calls.create(
        session_id=sid, tool_name="b", model_requested="gpt-4o", model_used="gpt-4o",
        estimated_prompt_tokens=100, estimated_completion_tokens=50,
        estimated_cost_usd=0.001, rerouted=False,
    )
    assert c1.call_index == 0
    assert c2.call_index == 1


def test_list_calls_for_session(tmp_path):
    _, calls = _setup(tmp_path)
    sid = "session_cursor_2026-03-12_calltest1"
    calls.create(
        session_id=sid, tool_name="a", model_requested="gpt-4o", model_used="gpt-4o",
        estimated_prompt_tokens=100, estimated_completion_tokens=50,
        estimated_cost_usd=0.001, rerouted=False,
    )
    calls.create(
        session_id=sid, tool_name="b", model_requested="gpt-4o", model_used="gpt-4o",
        estimated_prompt_tokens=100, estimated_completion_tokens=50,
        estimated_cost_usd=0.001, rerouted=False,
    )
    listed = calls.list_for_session(sid)
    assert len(listed) == 2
    assert listed[0].call_index < listed[1].call_index


def test_reconcile_call(tmp_path):
    _, calls = _setup(tmp_path)
    call = calls.create(
        session_id="session_cursor_2026-03-12_calltest1",
        tool_name="planning",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=500,
        estimated_completion_tokens=200,
        estimated_cost_usd=0.01,
        rerouted=False,
    )
    reconciled = calls.reconcile(
        call_id=call.call_id,
        actual_prompt_tokens=400,
        actual_completion_tokens=150,
        actual_cost_usd=0.008,
    )
    assert reconciled.status == "reconciled"
    assert reconciled.actual_prompt_tokens == 400
    assert reconciled.actual_completion_tokens == 150
    assert reconciled.actual_cost_usd == pytest.approx(0.008)


def test_reconcile_call_with_model_used(tmp_path):
    _, calls = _setup(tmp_path)
    call = calls.create(
        session_id="session_cursor_2026-03-12_calltest1",
        tool_name="planning",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=500,
        estimated_completion_tokens=200,
        estimated_cost_usd=0.01,
        rerouted=False,
    )
    reconciled = calls.reconcile(
        call_id=call.call_id,
        actual_prompt_tokens=400,
        actual_completion_tokens=150,
        actual_cost_usd=0.008,
        model_used="gpt-4o-mini",
    )
    assert reconciled.model_used == "gpt-4o-mini"


def test_reconcile_idempotent_same_values(tmp_path):
    _, calls = _setup(tmp_path)
    call = calls.create(
        session_id="session_cursor_2026-03-12_calltest1",
        tool_name="planning",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=500,
        estimated_completion_tokens=200,
        estimated_cost_usd=0.01,
        rerouted=False,
    )
    calls.reconcile(
        call_id=call.call_id,
        actual_prompt_tokens=400,
        actual_completion_tokens=150,
        actual_cost_usd=0.008,
    )
    r2 = calls.reconcile(
        call_id=call.call_id,
        actual_prompt_tokens=400,
        actual_completion_tokens=150,
        actual_cost_usd=0.008,
    )
    assert r2.status == "reconciled"


def test_latest_pending_call(tmp_path):
    _, calls = _setup(tmp_path)
    sid = "session_cursor_2026-03-12_calltest1"
    calls.create(
        session_id=sid, tool_name="a", model_requested="gpt-4o", model_used="gpt-4o",
        estimated_prompt_tokens=100, estimated_completion_tokens=50,
        estimated_cost_usd=0.001, rerouted=False,
    )
    c2 = calls.create(
        session_id=sid, tool_name="b", model_requested="gpt-4o", model_used="gpt-4o",
        estimated_prompt_tokens=100, estimated_completion_tokens=50,
        estimated_cost_usd=0.001, rerouted=False,
    )
    latest = calls.latest_pending(sid)
    assert latest is not None
    assert latest.call_id == c2.call_id


def test_find_pending_by_correlation_key(tmp_path):
    _, calls = _setup(tmp_path)
    call = calls.create(
        session_id="session_cursor_2026-03-12_calltest1",
        tool_name="planning",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=500,
        estimated_completion_tokens=200,
        estimated_cost_usd=0.01,
        rerouted=False,
        correlation_key="my-custom-key",
    )
    found = calls.find_pending_by_correlation_key("my-custom-key")
    assert found is not None
    assert found.call_id == call.call_id


def test_get_missing_call_returns_none(tmp_path):
    _, calls = _setup(tmp_path)
    assert calls.get("nonexistent_call") is None


def test_create_call_unknown_session_raises(tmp_path):
    db = tmp_path / "sessions.db"
    SessionRepository(db)  # init schema only
    calls = CallRepository(db)
    with pytest.raises(KeyError):
        calls.create(
            session_id="no_such_session",
            tool_name="planning",
            model_requested="gpt-4o",
            model_used="gpt-4o",
            estimated_prompt_tokens=100,
            estimated_completion_tokens=50,
            estimated_cost_usd=0.001,
            rerouted=False,
        )

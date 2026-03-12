"""Unit tests for store serialization helpers (pure, no I/O)."""
from __future__ import annotations

import json
import sqlite3

import pytest
from l6e._types import BudgetMode, OnBudgetExceeded, PipelinePolicy, PromptComplexity

from l6e_mcp.store._serialization import (
    _call_from_row,
    _default_mode_coverage,
    _policy_from_json,
    _policy_to_json,
    _session_from_row,
)
from l6e_mcp.store import schema as store_schema


# ---------------------------------------------------------------------------
# Policy round-trip
# ---------------------------------------------------------------------------

def _basic_policy(**kwargs) -> PipelinePolicy:
    defaults = dict(budget=1.5, budget_mode=BudgetMode.HALT)
    defaults.update(kwargs)
    return PipelinePolicy(**defaults)


def test_policy_roundtrip_basic():
    policy = _basic_policy()
    assert _policy_from_json(_policy_to_json(policy)).budget == pytest.approx(1.5)
    assert _policy_from_json(_policy_to_json(policy)).budget_mode == BudgetMode.HALT


def test_policy_roundtrip_preserves_reroute_threshold():
    policy = _basic_policy(reroute_threshold=0.65)
    assert _policy_from_json(_policy_to_json(policy)).reroute_threshold == pytest.approx(0.65)


def test_policy_roundtrip_preserves_unknown_model_pricing_mode():
    from l6e._types import UnknownModelPricingMode
    policy = _basic_policy(unknown_model_pricing_mode=UnknownModelPricingMode.WARN_ONLY)
    result = _policy_from_json(_policy_to_json(policy))
    assert result.unknown_model_pricing_mode == UnknownModelPricingMode.WARN_ONLY


def test_policy_roundtrip_stage_routing():
    from l6e._types import StageRoutingHint
    policy = _basic_policy(stage_routing={"planning": StageRoutingHint.LOCAL})
    result = _policy_from_json(_policy_to_json(policy))
    assert result.stage_routing["planning"] == StageRoutingHint.LOCAL


def test_policy_to_json_is_valid_json():
    raw = _policy_to_json(_basic_policy())
    parsed = json.loads(raw)
    assert "budget" in parsed


# ---------------------------------------------------------------------------
# _default_mode_coverage
# ---------------------------------------------------------------------------

def test_default_mode_coverage_estimate_only_returns_all_false():
    cov = _default_mode_coverage(
        usage_channel=store_schema.USAGE_CHANNEL_NONE,
        accounting_mode=store_schema.ACCOUNTING_MODE_ESTIMATE_ONLY,
    )
    assert not cov.ask_mode_exact_capable
    assert not cov.plan_mode_exact_capable
    assert not cov.agent_mode_exact_capable


def test_default_mode_coverage_hosted_edge_returns_all_true():
    cov = _default_mode_coverage(
        usage_channel=store_schema.USAGE_CHANNEL_HOSTED_EDGE,
        accounting_mode=store_schema.ACCOUNTING_MODE_EXACT_OPTIONAL,
    )
    assert cov.ask_mode_exact_capable
    assert cov.plan_mode_exact_capable
    assert cov.agent_mode_exact_capable


def test_default_mode_coverage_self_hosted_relay_agent_false():
    cov = _default_mode_coverage(
        usage_channel=store_schema.USAGE_CHANNEL_SELF_HOSTED_RELAY,
        accounting_mode=store_schema.ACCOUNTING_MODE_EXACT_OPTIONAL,
    )
    assert cov.ask_mode_exact_capable
    assert cov.plan_mode_exact_capable
    assert not cov.agent_mode_exact_capable


def test_default_mode_coverage_manual_import_returns_all_false():
    cov = _default_mode_coverage(
        usage_channel=store_schema.USAGE_CHANNEL_MANUAL_IMPORT,
        accounting_mode=store_schema.ACCOUNTING_MODE_EXACT_OPTIONAL,
    )
    assert not cov.ask_mode_exact_capable


# ---------------------------------------------------------------------------
# _session_from_row / _call_from_row — use in-memory SQLite to produce rows
# ---------------------------------------------------------------------------

def _make_session_row(overrides: dict | None = None) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE sessions (
            session_id TEXT, model TEXT, policy_json TEXT, source TEXT, log_path TEXT,
            proxy_mode INTEGER, accounting_mode TEXT, usage_channel TEXT,
            advanced_fallback_enabled INTEGER, ask_mode_exact_capable INTEGER,
            plan_mode_exact_capable INTEGER, agent_mode_exact_capable INTEGER,
            state TEXT, next_call_index INTEGER, checkpoint_calls INTEGER,
            status_calls INTEGER, created_at REAL, ended_at REAL, finalized_at REAL
        )
        """
    )
    defaults = dict(
        session_id="session_test_2026-03-12_abcdef01",
        model="gpt-4o",
        policy_json=_policy_to_json(_basic_policy()),
        source="mcp",
        log_path=None,
        proxy_mode=0,
        accounting_mode=store_schema.ACCOUNTING_MODE_ESTIMATE_ONLY,
        usage_channel=store_schema.USAGE_CHANNEL_NONE,
        advanced_fallback_enabled=0,
        ask_mode_exact_capable=0,
        plan_mode_exact_capable=0,
        agent_mode_exact_capable=0,
        state="active",
        next_call_index=0,
        checkpoint_calls=0,
        status_calls=0,
        created_at=1_000_000.0,
        ended_at=None,
        finalized_at=None,
    )
    if overrides:
        defaults.update(overrides)
    conn.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        list(defaults.values()),
    )
    return conn.execute("SELECT * FROM sessions").fetchone()


def test_session_from_row_basic():
    row = _make_session_row()
    state = _session_from_row(row)
    assert state.session_id == "session_test_2026-03-12_abcdef01"
    assert state.model == "gpt-4o"
    assert state.state == "active"
    assert state.next_call_index == 0
    assert state.checkpoint_calls == 0
    assert state.status_calls == 0
    assert state.ended_at is None
    assert state.finalized_at is None


def test_session_from_row_proxy_mode_derived_from_usage_channel():
    row = _make_session_row({"usage_channel": store_schema.USAGE_CHANNEL_SELF_HOSTED_RELAY, "proxy_mode": 0})
    state = _session_from_row(row)
    assert state.proxy_mode is True


def test_session_from_row_advanced_fallback():
    row = _make_session_row({"advanced_fallback_enabled": 1})
    state = _session_from_row(row)
    assert state.advanced_fallback_enabled is True


def _make_call_row(overrides: dict | None = None) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE calls (
            call_id TEXT, session_id TEXT, call_index INTEGER, tool_name TEXT,
            model_requested TEXT, model_used TEXT,
            estimated_prompt_tokens INTEGER, estimated_completion_tokens INTEGER,
            estimated_cost_usd REAL,
            actual_prompt_tokens INTEGER, actual_completion_tokens INTEGER,
            actual_cost_usd REAL,
            rerouted INTEGER, elapsed_ms REAL, prompt_complexity TEXT,
            is_multi_turn INTEGER, status TEXT, created_at REAL, reconciled_at REAL,
            correlation_key TEXT, correlation_source TEXT,
            callback_request_id TEXT, callback_trace_id TEXT,
            exactness_state TEXT, hosted_ledger_id TEXT,
            actor_type TEXT, actor_id TEXT, actor_name TEXT, parent_call_id TEXT,
            call_mode TEXT, mode_exact_capable INTEGER,
            session_accounting_mode TEXT
        )
        """
    )
    defaults = dict(
        call_id="call_abc123",
        session_id="session_test_2026-03-12_abcdef01",
        call_index=0,
        tool_name="planning",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=500,
        estimated_completion_tokens=200,
        estimated_cost_usd=0.01,
        actual_prompt_tokens=None,
        actual_completion_tokens=None,
        actual_cost_usd=None,
        rerouted=0,
        elapsed_ms=123.0,
        prompt_complexity=None,
        is_multi_turn=0,
        status="pending",
        created_at=1_000_000.0,
        reconciled_at=None,
        correlation_key="call_abc123",
        correlation_source="checkpoint_call_id",
        callback_request_id=None,
        callback_trace_id=None,
        exactness_state="estimate_only",
        hosted_ledger_id=None,
        actor_type="parent_agent",
        actor_id=None,
        actor_name=None,
        parent_call_id=None,
        call_mode=None,
        mode_exact_capable=None,
        session_accounting_mode=store_schema.ACCOUNTING_MODE_ESTIMATE_ONLY,
    )
    if overrides:
        defaults.update(overrides)
    conn.execute(
        f"INSERT INTO calls VALUES ({','.join('?' * len(defaults))})",
        list(defaults.values()),
    )
    return conn.execute("SELECT * FROM calls").fetchone()


def test_call_from_row_basic():
    row = _make_call_row()
    call = _call_from_row(row)
    assert call.call_id == "call_abc123"
    assert call.tool_name == "planning"
    assert call.actual_prompt_tokens is None
    assert call.rerouted is False
    assert call.actor_type == "parent_agent"


def test_call_from_row_with_actual_tokens():
    row = _make_call_row({
        "actual_prompt_tokens": 300,
        "actual_completion_tokens": 100,
        "actual_cost_usd": 0.005,
        "status": "reconciled",
        "exactness_state": "exact_recorded",
    })
    call = _call_from_row(row)
    assert call.actual_prompt_tokens == 300
    assert call.actual_completion_tokens == 100
    assert call.actual_cost_usd == pytest.approx(0.005)


def test_call_from_row_prompt_complexity():
    row = _make_call_row({"prompt_complexity": PromptComplexity.HIGH.value})
    call = _call_from_row(row)
    assert call.prompt_complexity == PromptComplexity.HIGH


def test_call_from_row_subagent_fields():
    row = _make_call_row({
        "actor_type": "subagent",
        "actor_id": "search_agent_1",
        "actor_name": "Search",
        "parent_call_id": "call_parent_xyz",
    })
    call = _call_from_row(row)
    assert call.actor_type == "subagent"
    assert call.actor_id == "search_agent_1"
    assert call.parent_call_id == "call_parent_xyz"

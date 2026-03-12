"""Tests for the l6e-mcp server."""
from __future__ import annotations

import json
import re

import pytest

from l6e_mcp import server as srv
from l6e_mcp.session_store import LocalSessionStore

SESSION_ID_RE = re.compile(r"^session_.+_\d{4}-\d{2}-\d{2}_[0-9a-f]{8}$")


async def start_session(mcp_client, budget_usd: float = 5.0, **kwargs) -> dict:
    if "model" not in kwargs:
        kwargs["model"] = "unknown"
    result = await mcp_client.call_tool(
        "l6e_run_start",
        {"budget_usd": budget_usd, **kwargs},
        raise_on_error=False,
    )
    assert not result.is_error, f"l6e_run_start failed: {result}"
    return result.data


async def test_session_start_returns_session_id(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": 2.0, "model": "unknown"},
        raise_on_error=False,
    )
    assert not result.is_error
    assert SESSION_ID_RE.match(result.data["session_id"])
    assert result.data["accounting_mode"] == "estimate_only"
    assert result.data["usage_channel"] == "none"


async def test_tool_discovery_exposes_canonical_names_only(client):
    tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert names == {
        "l6e_run_start",
        "l6e_authorize_call",
        "l6e_record_usage",
        "l6e_run_status",
        "l6e_run_end",
    }


async def test_session_start_requires_model(client):
    result = await client.call_tool("l6e_run_start", {"budget_usd": 2.0}, raise_on_error=False)
    assert result.is_error is True


async def test_session_start_normalizes_blank_model_to_unknown(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": 2.0, "model": "   "},
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["model"] == "unknown"
    session = LocalSessionStore().get_session(result.data["session_id"])
    assert session is not None
    assert session.model == "unknown"


async def test_session_start_persists_session(client):
    result = await start_session(client, budget_usd=2.0, model="gpt-4o")
    session = LocalSessionStore().get_session(result["session_id"])
    assert session is not None
    assert session.model == "gpt-4o"
    assert session.policy.budget == pytest.approx(2.0)


async def test_session_start_default_proxy_mode_does_not_write_active_files(
    client,
    tmp_path,
    monkeypatch,
):
    active_session = tmp_path / "active_session"
    active_call = tmp_path / "active_call"
    monkeypatch.setattr(srv, "_ACTIVE_SESSION_FILE", active_session)
    monkeypatch.setattr(srv, "_ACTIVE_CALL_FILE", active_call)

    result = await start_session(client, budget_usd=2.0, proxy_mode=True)
    assert not active_session.exists()
    assert not active_call.exists()
    assert result["advanced_fallback_enabled"] is False
    assert result["fallback_correlation_capability"] == "metadata_only"


async def test_session_start_advanced_fallback_writes_active_files(client, tmp_path, monkeypatch):
    active_session = tmp_path / "active_session"
    active_call = tmp_path / "active_call"
    monkeypatch.setattr(srv, "_ACTIVE_SESSION_FILE", active_session)
    monkeypatch.setattr(srv, "_ACTIVE_CALL_FILE", active_call)

    result = await start_session(
        client,
        budget_usd=2.0,
        proxy_mode=True,
        advanced_fallback=True,
    )
    assert active_session.exists()
    assert active_session.read_text().strip() == result["session_id"]
    assert result["active_session_file"] == str(active_session)
    assert result["active_call_file"] == str(active_call)
    assert result["accounting_mode"] == "exact_optional"
    assert result["usage_channel"] == "self_hosted_relay"
    assert result["advanced_fallback_enabled"] is True
    assert result["fallback_correlation_capability"] == "active_file"


async def test_checkpoint_returns_call_id_and_updates_spend(client):
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 1000},
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["action"] in ("allow", "reroute", "halt")
    if result.data["action"] != "halt":
        assert "call_id" in result.data
        assert "correlation" in result.data
        assert "proxy_correlation" not in result.data
        assert result.data["correlation"]["call_id"] == result.data["call_id"]
        assert result.data["spend_so_far_usd"] > 0
        assert result.data["exactness_state"] in {
            "all_estimate_only",
            "partial_exact",
            "fully_exact_for_supported_calls",
        }


async def test_checkpoint_increments_calls_made(client):
    session = await start_session(client, budget_usd=10.0)
    await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "tool_a", "estimated_tokens": 100},
        raise_on_error=False,
    )
    await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "tool_b", "estimated_tokens": 100},
        raise_on_error=False,
    )
    spend = await client.call_tool(
        "l6e_run_status",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not spend.is_error
    assert spend.data["calls_made"] == 2


async def test_checkpoint_direct_actual_tokens_creates_reconciled_call(client):
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "sub_agent_explore",
            "estimated_tokens": 100,
            "actual_prompt_tokens": 50_000,
            "actual_completion_tokens": 2_000,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["spend_so_far_usd"] > 0
    call = LocalSessionStore().get_call(result.data["call_id"])
    assert call is not None
    assert call.status == "reconciled"
    assert call.actual_completion_tokens == 2_000


async def test_checkpoint_accepts_subagent_metadata_and_updates_spend_breakdown(client):
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "subagent_run",
            "estimated_tokens": 2_000,
            "actor_type": "subagent",
            "actor_id": "subagent_search_1",
            "actor_name": "Search agent",
            "parent_call_id": "call_parent_123",
        },
        raise_on_error=False,
    )
    assert not result.is_error
    call = LocalSessionStore().get_call(result.data["call_id"])
    assert call is not None
    assert call.actor_type == "subagent"
    assert call.actor_id == "subagent_search_1"
    assert call.actor_name == "Search agent"
    assert call.parent_call_id == "call_parent_123"
    assert (
        result.data["correlation"]["metadata"]["spend_logs_metadata"]["l6e_actor_type"]
        == "subagent"
    )
    assert (
        result.data["correlation"]["metadata"]["spend_logs_metadata"]["l6e_actor_id"]
        == "subagent_search_1"
    )
    assert "l6e_parent_call_id:call_parent_123" in result.data["correlation"]["request_tags"]

    spend = await client.call_tool(
        "l6e_run_status",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not spend.is_error
    assert spend.data["subagent_calls"] == 1
    assert spend.data["subagent_spend_usd"] > 0
    assert spend.data["subagents"][0]["actor_id"] == "subagent_search_1"


async def test_reconcile_call_updates_existing_pending_call_without_duplication(client):
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    checkpoint = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    assert not checkpoint.is_error
    call_id = checkpoint.data["call_id"]

    reconcile = await client.call_tool(
        "l6e_record_usage",
        {
            "call_id": call_id,
            "actual_prompt_tokens": 1200,
            "actual_completion_tokens": 300,
            "model_used": "gpt-4o-mini",
            "hosted_ledger_id": "ledger_123",
            "idempotency_key": "idem_123",
        },
        raise_on_error=False,
    )
    assert not reconcile.is_error
    assert reconcile.data["call_id"] == call_id
    spend = await client.call_tool(
        "l6e_run_status",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert spend.data["calls_made"] == 1
    call = LocalSessionStore().get_call(call_id)
    assert call is not None
    assert call.status == "reconciled"
    assert call.actual_completion_tokens == 300
    assert call.model_used == "gpt-4o-mini"
    assert call.hosted_ledger_id == "ledger_123"
    assert reconcile.data["exactness_state"] == "exact_recorded"


async def test_reconcile_call_is_idempotent_for_same_values(client):
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    checkpoint = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    call_id = checkpoint.data["call_id"]

    args = {
        "call_id": call_id,
        "actual_prompt_tokens": 1200,
        "actual_completion_tokens": 300,
        "model_used": "gpt-4o-mini",
    }
    r1 = await client.call_tool("l6e_record_usage", args, raise_on_error=False)
    r2 = await client.call_tool("l6e_record_usage", args, raise_on_error=False)
    assert not r1.is_error
    assert not r2.is_error
    spend = await client.call_tool(
        "l6e_run_status",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert spend.data["calls_made"] == 1


async def test_reconcile_calls_remain_correct_when_completed_out_of_order(client):
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    first = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "tool_a", "estimated_tokens": 500},
        raise_on_error=False,
    )
    second = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "tool_b", "estimated_tokens": 500},
        raise_on_error=False,
    )
    first_call_id = first.data["call_id"]
    second_call_id = second.data["call_id"]

    second_reconcile = await client.call_tool(
        "l6e_record_usage",
        {
            "call_id": second_call_id,
            "actual_prompt_tokens": 2200,
            "actual_completion_tokens": 400,
            "model_used": "gpt-4o-mini",
        },
        raise_on_error=False,
    )
    first_reconcile = await client.call_tool(
        "l6e_record_usage",
        {
            "call_id": first_call_id,
            "actual_prompt_tokens": 1200,
            "actual_completion_tokens": 200,
            "model_used": "gpt-4o-mini",
        },
        raise_on_error=False,
    )

    assert not second_reconcile.is_error
    assert not first_reconcile.is_error
    first_call = LocalSessionStore().get_call(first_call_id)
    second_call = LocalSessionStore().get_call(second_call_id)
    assert first_call is not None
    assert second_call is not None
    assert first_call.actual_completion_tokens == 200
    assert second_call.actual_completion_tokens == 400


async def test_reconcile_unknown_call_is_tool_error(client):
    result = await client.call_tool(
        "l6e_record_usage",
        {
            "call_id": "call_missing",
            "actual_prompt_tokens": 1,
            "actual_completion_tokens": 1,
        },
        raise_on_error=False,
    )
    assert result.is_error is True


async def test_spend_is_readonly(client):
    session = await start_session(client, budget_usd=5.0)
    r1 = await client.call_tool(
        "l6e_run_status",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    r2 = await client.call_tool(
        "l6e_run_status",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not r1.is_error
    assert not r2.is_error
    assert r1.data == r2.data


async def test_run_status_exposes_mode_coverage_and_lag_indicators(client):
    session = await start_session(client, budget_usd=5.0, proxy_mode=True, model="gpt-4o")
    pending = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "read_files",
            "estimated_tokens": 300,
            "call_mode": "agent",
        },
        raise_on_error=False,
    )
    assert not pending.is_error

    status = await client.call_tool(
        "l6e_run_status",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not status.is_error
    assert status.data["exactness_state"] == "exactness_degraded"
    assert status.data["pending_exact_calls"] == 0
    assert status.data["unavailable_exact_calls"] == 1
    assert status.data["mode_coverage"]["agent_mode_exact_capable"] is False


async def test_session_end_writes_jsonl_with_reconciled_record(client, tmp_path):
    session = await start_session(client, budget_usd=10.0, proxy_mode=True, model="gpt-4o")
    checkpoint = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    call_id = checkpoint.data["call_id"]
    await client.call_tool(
        "l6e_record_usage",
        {
            "call_id": call_id,
            "actual_prompt_tokens": 123,
            "actual_completion_tokens": 45,
            "model_used": "gpt-4o-mini",
        },
        raise_on_error=False,
    )

    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    log = tmp_path / "runs.jsonl"
    assert log.exists()
    entry = json.loads(log.read_text().strip())
    assert entry["run_id"] == session["session_id"]
    assert entry["records"][0]["completion_tokens"] == 45
    assert entry["records"][0]["model_used"] == "gpt-4o-mini"


async def test_session_end_writes_subagent_metadata_to_jsonl(client, tmp_path):
    session = await start_session(client, budget_usd=10.0, proxy_mode=True, model="gpt-4o")
    await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "subagent_run",
            "estimated_tokens": 1_500,
            "actor_type": "subagent",
            "actor_id": "subagent_search_1",
            "actor_name": "Search agent",
            "parent_call_id": "call_parent_123",
        },
        raise_on_error=False,
    )

    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    log = tmp_path / "runs.jsonl"
    entry = json.loads(log.read_text().strip())
    assert entry["subagent_calls"] == 1
    assert entry["subagent_spend_usd"] > 0
    assert entry["subagents"][0]["actor_id"] == "subagent_search_1"
    assert entry["records"][0]["actor_type"] == "subagent"
    assert entry["records"][0]["parent_call_id"] == "call_parent_123"


async def test_session_end_clears_active_files(client, tmp_path, monkeypatch):
    active_session = tmp_path / "active_session"
    active_call = tmp_path / "active_call"
    monkeypatch.setattr(srv, "_ACTIVE_SESSION_FILE", active_session)
    monkeypatch.setattr(srv, "_ACTIVE_CALL_FILE", active_call)

    session = await start_session(client, budget_usd=2.0, proxy_mode=True, advanced_fallback=True)
    await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    assert active_session.exists()
    assert active_call.exists()

    await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not active_session.exists()
    assert not active_call.exists()


async def test_session_end_twice_is_tool_error(client):
    session = await start_session(client, budget_usd=5.0)
    await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    result = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert result.is_error is True


async def test_checkpoint_accepts_dual_token_estimates(client):
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "read_files",
            "estimated_tokens": 10,
            "estimated_prompt_tokens": 1200,
            "estimated_completion_tokens": 600,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["estimate_prompt_tokens"] == 1200
    assert result.data["estimate_completion_tokens"] == 600
    call = LocalSessionStore().get_call(result.data["call_id"])
    assert call is not None
    assert call.estimated_prompt_tokens == 1200
    assert call.estimated_completion_tokens == 600


async def test_unknown_model_pricing_warns_by_default(client):
    session = await start_session(client, budget_usd=10.0, model="unknown-model-123")
    result = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["model_pricing_known"] is False
    assert result.data["pricing_confidence"] == "low"
    assert "pricing_warning" in result.data


async def test_unknown_model_pricing_halts_when_policy_requires(client):
    session = await start_session(
        client,
        budget_usd=10.0,
        model="unknown-model-123",
        unknown_model_pricing_mode="halt_on_unknown_pricing",
    )
    result = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["action"] == "halt"
    assert result.data["reason"] == "unknown_model_pricing:halt"


async def test_unknown_model_pricing_reroutes_when_local_model_priced(client, monkeypatch):
    monkeypatch.setattr(
        "l6e_mcp.core.authorization.LocalRouter.best_local_model",
        lambda self: "gpt-4o-mini",
    )
    session = await start_session(
        client,
        budget_usd=10.0,
        model="unknown-model-123",
        unknown_model_pricing_mode="reroute_required",
    )
    result = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["action"] == "reroute"
    assert result.data["target_model"] == "gpt-4o-mini"


async def test_run_status_returns_pricing_warnings(client):
    session = await start_session(client, budget_usd=10.0, model="unknown-model-123")
    await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    status = await client.call_tool(
        "l6e_run_status",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not status.is_error
    assert isinstance(status.data["pricing_warnings"], list)
    assert len(status.data["pricing_warnings"]) == 1

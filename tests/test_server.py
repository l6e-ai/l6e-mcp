"""Tests for the l6e-mcp server."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

from l6e_mcp.server import l6e_debug_pricing_state
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


async def test_tool_discovery_exposes_canonical_names_only(client):
    tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert names == {
        "l6e_run_start",
        "l6e_authorize_call",
        "l6e_record_usage",
        "l6e_run_end",
        "l6e_list_billing_batches",
        "l6e_delete_billing_batch",
        "l6e_sync_anthropic_usage",
    }


def test_debug_pricing_tool_registration_is_env_gated():
    script = """
import asyncio
import json
from fastmcp.client import Client
from l6e_mcp.server import mcp

async def main():
    async with Client(transport=mcp) as client:
        tools = await client.list_tools()
        print(json.dumps(sorted(tool.name for tool in tools)))

asyncio.run(main())
"""
    base_env = {**os.environ, "L6E_DEBUG_TOOLS": "0"}
    enabled_env = {**os.environ, "L6E_DEBUG_TOOLS": "1"}

    hidden = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        env=base_env,
        text=True,
    )
    visible = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        env=enabled_env,
        text=True,
    )

    assert "l6e_debug_pricing_state" not in json.loads(hidden.stdout)
    assert "l6e_debug_pricing_state" in json.loads(visible.stdout)


async def test_debug_pricing_state_returns_process_pricing_and_probe_state():
    result = await l6e_debug_pricing_state(probe_models=["gpt-4o", "not-a-real-l6e-model"])

    assert result["process"]["pid"] == os.getpid()
    assert result["process"]["python_executable"] == sys.executable
    assert result["process"]["started_at_unix"] > 0
    assert result["process"]["uptime_seconds"] >= 0
    assert result["process"]["litellm_path"]
    assert result["process"]["l6e_costs_path"]
    assert result["process"]["litellm_version"]
    assert result["process"]["l6e_version"]

    assert isinstance(result["litellm_cost_map_source_info"], dict)
    assert result["litellm_model_cost_state"]["total_models"] > 0
    assert isinstance(result["litellm_model_cost_state"]["claude_opus_4_7_present"], bool)
    assert isinstance(result["litellm_model_cost_state"]["claude_opus_4_6_present"], bool)

    before = result["l6e_bare_keys_cache_before_probes"]
    after = result["l6e_bare_keys_cache_after_probes"]
    assert set(before) >= {"populated", "size"}
    assert set(after) >= {"populated", "size"}
    if after["populated"]:
        assert set(after) >= {
            "opus_keys_total",
            "opus_4_7_keys",
            "opus_4_6_keys",
            "opus_keys_sample",
        }

    probes = {probe["model_id"]: probe for probe in result["probes"]}
    assert set(probes) == {"gpt-4o", "not-a-real-l6e-model"}
    for probe in probes.values():
        assert set(probe) == {
            "model_id",
            "direct_cost_per_token",
            "resolve_model_id_result",
            "estimate_with_metadata",
        }
        assert "ok" in probe["direct_cost_per_token"]
        assert probe["resolve_model_id_result"] is None or isinstance(
            probe["resolve_model_id_result"], str
        )
        assert isinstance(probe["estimate_with_metadata"], dict)


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
    session = LocalSessionStore().get_session(result.data["session_id"])
    assert session is not None
    assert session.model == "unknown"


async def test_session_start_persists_session(client):
    result = await start_session(client, budget_usd=2.0, model="gpt-4o")
    session = LocalSessionStore().get_session(result["session_id"])
    assert session is not None
    assert session.model == "gpt-4o"
    assert session.policy.budget == pytest.approx(2.0)


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
        assert "remaining_usd" in result.data
        assert result.data["remaining_usd"] < 10.0


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
    calls = LocalSessionStore().list_calls_for_session(session["session_id"])
    assert len(calls) == 2


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
    assert result.data["remaining_usd"] < 10.0
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

    calls = LocalSessionStore().list_calls_for_session(session["session_id"])
    assert len(calls) == 1

    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    assert end.data["calls_made"] == 1
    assert end.data["total_cost_usd"] > 0
    stored_call = LocalSessionStore().get_call(result.data["call_id"])
    assert stored_call is not None
    assert stored_call.actor_type == "subagent"


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
        },
        raise_on_error=False,
    )
    assert not reconcile.is_error
    assert reconcile.data["call_id"] == call_id
    calls = LocalSessionStore().list_calls_for_session(session["session_id"])
    assert len(calls) == 1
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
    calls = LocalSessionStore().list_calls_for_session(session["session_id"])
    assert len(calls) == 1


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


async def test_check_only_accumulates_spend(client):
    """check_only=True records a real call, so successive calls decrease remaining."""
    session = await start_session(client, budget_usd=5.0)
    args = {
        "session_id": session["session_id"],
        "tool_name": "status",
        "check_only": True,
    }
    r1 = await client.call_tool("l6e_authorize_call", args, raise_on_error=False)
    r2 = await client.call_tool("l6e_authorize_call", args, raise_on_error=False)
    assert not r1.is_error
    assert not r2.is_error
    assert r2.data["remaining_usd"] < r1.data["remaining_usd"]
    assert r2.data["pct_used"] > r1.data["pct_used"]


async def test_run_status_exposes_mode_coverage_and_lag_indicators(client):
    """Mode coverage and exactness are surfaced in l6e_run_end response."""
    session = await start_session(
        client,
        budget_usd=5.0,
        model="gpt-4o",
        usage_channel="self_hosted_relay",
    )
    await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "read_files",
            "estimated_tokens": 300,
            "call_mode": "agent",
        },
        raise_on_error=False,
    )

    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    assert "exactness_state" in end.data
    assert "pending_exact_calls" in end.data
    assert "mode_coverage" in end.data


async def test_session_end_writes_jsonl_with_reconciled_record(client, tmp_path):
    session = await start_session(
        client,
        budget_usd=10.0,
        model="gpt-4o",
        usage_channel="self_hosted_relay",
    )
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
    assert entry["overhead_calls"] >= 2
    assert float(str(entry["overhead_usd"])) > 0
    assert float(str(entry["net_savings_usd"])) == pytest.approx(
        float(str(entry["savings_usd"])) - float(str(entry["overhead_usd"]))
    )


async def test_session_end_writes_subagent_metadata_to_jsonl(client, tmp_path):
    session = await start_session(
        client,
        budget_usd=10.0,
        model="gpt-4o",
        usage_channel="self_hosted_relay",
    )
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
    assert float(str(entry["subagent_spend_usd"])) > 0
    assert entry["subagents"][0]["actor_id"] == "subagent_search_1"
    assert entry["records"][0]["actor_type"] == "subagent"
    assert entry["records"][0]["parent_call_id"] == "call_parent_123"


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


async def test_estimate_only_mode_omits_net_savings_and_includes_note(client, tmp_path):
    """In estimate-only mode, check_only omits overhead/savings fields.
    l6e_run_end reports savings_confidence, and the JSONL log contains the full summary."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )

    status = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "status", "check_only": True},
        raise_on_error=False,
    )
    assert not status.is_error
    assert "overhead_usd" not in status.data
    assert "savings_confidence" not in status.data
    assert "net_savings_usd" not in status.data
    assert "net_savings_unavailable" not in status.data

    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    assert end.data["savings_confidence"] == "estimate_only"

    log = tmp_path / "runs.jsonl"
    entry = json.loads(log.read_text().strip())
    assert entry["savings_confidence"] == "estimate_only"
    assert float(str(entry["net_savings_usd"])) == pytest.approx(
        float(str(entry["savings_usd"])) - float(str(entry["overhead_usd"]))
    )


async def test_exact_mode_includes_net_savings_usd(client, tmp_path):
    """In exact mode (all calls reconciled) the JSONL log includes net_savings_usd."""
    session = await start_session(
        client,
        budget_usd=10.0,
        model="gpt-4o",
        usage_channel="self_hosted_relay",
    )
    checkpoint = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    await client.call_tool(
        "l6e_record_usage",
        {
            "call_id": checkpoint.data["call_id"],
            "actual_prompt_tokens": 500,
            "actual_completion_tokens": 100,
            "model_used": "gpt-4o",
        },
        raise_on_error=False,
    )

    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    assert end.data["savings_confidence"] == "exact"

    log = tmp_path / "runs.jsonl"
    entry = json.loads(log.read_text().strip())
    assert entry["savings_confidence"] == "exact"
    assert "net_savings_usd" in entry


# --- Error path tests ---


async def test_run_start_rejects_zero_budget(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": 0, "model": "gpt-4o"},
        raise_on_error=False,
    )
    assert result.is_error
    assert "positive" in str(result).lower()


async def test_run_start_rejects_negative_budget(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": -5.0, "model": "gpt-4o"},
        raise_on_error=False,
    )
    assert result.is_error


async def test_run_start_rejects_infinite_budget(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": float("inf"), "model": "gpt-4o"},
        raise_on_error=False,
    )
    assert result.is_error


async def test_run_start_invalid_unknown_model_pricing_mode(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": 1.0, "model": "gpt-4o", "unknown_model_pricing_mode": "not_valid"},
        raise_on_error=False,
    )
    assert result.is_error
    error_text = str(result)
    assert "warn_only" in error_text


async def test_run_start_rejects_invalid_accounting_mode(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": 1.0, "model": "gpt-4o", "accounting_mode": "exact"},
        raise_on_error=False,
    )
    assert result.is_error
    assert "accounting_mode" in str(result)
    assert "estimate_only" in str(result)


async def test_run_start_rejects_invalid_usage_channel(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": 1.0, "model": "gpt-4o", "usage_channel": "cloud"},
        raise_on_error=False,
    )
    assert result.is_error
    assert "usage_channel" in str(result)
    assert "hosted_edge" in str(result)


async def test_run_start_normalizes_accounting_mode_case(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": 1.0, "model": "gpt-4o", "accounting_mode": "ESTIMATE_ONLY"},
        raise_on_error=False,
    )
    assert not result.is_error
    session = LocalSessionStore().get_session(result.data["session_id"])
    assert session is not None
    assert session.accounting_mode == "estimate_only"


async def test_run_start_normalizes_usage_channel_case(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": 1.0, "model": "gpt-4o", "usage_channel": "Hosted_Edge"},
        raise_on_error=False,
    )
    assert not result.is_error
    session = LocalSessionStore().get_session(result.data["session_id"])
    assert session is not None
    assert session.usage_channel == "hosted_edge"


async def test_authorize_rejects_invalid_actor_type(client):
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "test",
            "actor_type": "debugger",
        },
        raise_on_error=False,
    )
    assert result.is_error
    assert "actor_type" in str(result)
    assert "parent_agent" in str(result)


async def test_authorize_normalizes_actor_type_case(client):
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "test",
            "actor_type": "SubAgent",
            "actor_id": "sa_1",
        },
        raise_on_error=False,
    )
    assert not result.is_error
    call = LocalSessionStore().get_call(result.data["call_id"])
    assert call is not None
    assert call.actor_type == "subagent"


async def test_authorize_rejects_invalid_call_mode(client):
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "test",
            "call_mode": "debug",
        },
        raise_on_error=False,
    )
    assert result.is_error
    assert "call_mode" in str(result)
    assert "agent" in str(result)


async def test_authorize_normalizes_call_mode_case(client):
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "test",
            "call_mode": "AGENT",
        },
        raise_on_error=False,
    )
    assert not result.is_error


async def test_authorize_check_only_unknown_session_returns_error(client):
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": "session_unknown_2026-03-12_deadbeef",
            "tool_name": "status",
            "check_only": True,
        },
        raise_on_error=False,
    )
    assert result.is_error
    assert "session_unknown_2026-03-12_deadbeef" in str(result)


async def test_record_usage_unknown_call_id_returns_error(client):
    result = await client.call_tool(
        "l6e_record_usage",
        {
            "call_id": "call_does_not_exist",
            "actual_prompt_tokens": 100,
            "actual_completion_tokens": 50,
        },
        raise_on_error=False,
    )
    assert result.is_error
    assert "call_does_not_exist" in str(result)


async def test_run_end_on_already_finalized_session_returns_error(client):
    session = await start_session(client, budget_usd=1.0)
    session_id = session["session_id"]
    end1 = await client.call_tool(
        "l6e_run_end",
        {"session_id": session_id},
        raise_on_error=False,
    )
    assert not end1.is_error
    end2 = await client.call_tool(
        "l6e_run_end",
        {"session_id": session_id},
        raise_on_error=False,
    )
    assert end2.is_error


async def test_record_usage_with_different_values_on_reconciled_call_returns_error(client):
    """Calling l6e_record_usage a second time with different token values on an
    already-reconciled call must return is_error=True and must NOT overwrite
    the stored actual token counts."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    checkpoint = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    call_id = checkpoint.data["call_id"]

    r1 = await client.call_tool(
        "l6e_record_usage",
        {"call_id": call_id, "actual_prompt_tokens": 1000, "actual_completion_tokens": 200},
        raise_on_error=False,
    )
    assert not r1.is_error

    r2 = await client.call_tool(
        "l6e_record_usage",
        {"call_id": call_id, "actual_prompt_tokens": 9999, "actual_completion_tokens": 9999},
        raise_on_error=False,
    )
    assert r2.is_error

    stored = LocalSessionStore().get_call(call_id)
    assert stored is not None
    assert stored.actual_prompt_tokens == 1000
    assert stored.actual_completion_tokens == 200


# ---------------------------------------------------------------------------
# Slim response shape — l6e_authorize_call
# ---------------------------------------------------------------------------


async def test_authorize_call_response_contains_only_agent_essential_fields(client):
    """l6e_authorize_call must return only the fields the agent needs to make
    a decision. Internal bookkeeping fields must NOT appear in the response."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "planning",
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    data = result.data

    assert "action" in data
    assert "remaining_usd" in data
    assert "budget_pressure" in data
    assert "call_id" in data
    assert "reason" in data

    noise_fields = [
        "spend_so_far_usd",
        "pricing_confidence",
        "pricing_source",
        "model_pricing_known",
        "estimate_source",
        "estimate_prompt_tokens",
        "estimate_completion_tokens",
        "calibration_multiplier",
        "effective_multiplier",
        "estimate_reasoning_tokens",
        "internal_turns_multiplier",
        "pricing_warnings",
        "correlation",
    ]
    for field in noise_fields:
        assert field not in data, f"Noise field '{field}' should not appear in response"


async def test_authorize_call_reroute_includes_target_model(client):
    """When action is reroute, target_model must be in the response."""
    session = await start_session(client, budget_usd=1.0, model="gpt-4o")
    session_id = session["session_id"]

    await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session_id,
            "tool_name": "preload",
            "estimated_tokens": 100,
            "actual_prompt_tokens": 320_000,
            "actual_completion_tokens": 5_000,
        },
        raise_on_error=False,
    )
    result = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session_id, "tool_name": "search", "estimated_tokens": 100},
        raise_on_error=False,
    )
    assert not result.is_error
    if result.data["action"] == "reroute":
        assert "target_model" in result.data


async def test_authorize_call_no_correlation_block_in_oss_mode(client):
    """The correlation envelope was proxy-only and has been removed; sessions must never emit it."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "implement", "estimated_tokens": 1000},
        raise_on_error=False,
    )
    assert not result.is_error
    assert "correlation" not in result.data


# ---------------------------------------------------------------------------
# check_only mode — l6e_authorize_call
# ---------------------------------------------------------------------------


async def test_authorize_check_only_response_is_slim(client):
    """check_only=True must return a minimal snapshot without gate fields."""
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 1000,
            "estimated_completion_tokens": 200,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    data = result.data

    assert "budget_pressure" in data
    assert "remaining_usd" in data
    assert "pct_used" in data

    for field in ("action", "call_id", "reason", "calls_made", "reroutes", "spent_usd"):
        assert field not in data, f"Field '{field}' should not appear in check_only response"


async def test_authorize_check_only_accepts_estimates(client):
    """check_only=True must accept estimated token params without error."""
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 3000,
            "estimated_completion_tokens": 600,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["budget_pressure"] in ("low", "moderate", "high", "critical")


async def test_authorize_check_only_projects_cost(client):
    """check_only=True records real calls, so remaining decreases monotonically.
    A larger estimate produces a bigger drop than a smaller one."""
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    sid = session["session_id"]

    small = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 100,
            "estimated_completion_tokens": 50,
        },
        raise_on_error=False,
    )
    assert not small.is_error

    large = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 5000,
            "estimated_completion_tokens": 1000,
        },
        raise_on_error=False,
    )
    assert not large.is_error
    assert large.data["remaining_usd"] < small.data["remaining_usd"]
    assert large.data["pct_used"] > small.data["pct_used"]

    repeat_small = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 100,
            "estimated_completion_tokens": 50,
        },
        raise_on_error=False,
    )
    assert not repeat_small.is_error
    assert repeat_small.data["remaining_usd"] < large.data["remaining_usd"], (
        "Each check_only call accumulates spend — remaining must keep decreasing"
    )


async def test_authorize_check_only_creates_call_record(client):
    """check_only=True records a real call so spend accumulates."""
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    sid = session["session_id"]

    await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    calls = LocalSessionStore().list_calls_for_session(sid)
    assert len(calls) == 1, "check_only must create a call record"
    assert calls[0].estimated_prompt_tokens == 2000
    assert calls[0].estimated_completion_tokens == 400


async def test_authorize_check_only_uses_cached_calibration(client):
    """When calibration cache is populated, check_only must apply the factor."""
    from l6e_mcp.server import _get_calibration_cache

    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    sid = session["session_id"]

    uncalibrated = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    assert not uncalibrated.is_error
    assert "calibration_applied" not in uncalibrated.data

    _get_calibration_cache().update(
        sid, factor=5.0, source="test", confidence="high",
    )

    calibrated = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    assert not calibrated.is_error
    assert calibrated.data.get("calibration_applied") is True
    assert calibrated.data["remaining_usd"] < uncalibrated.data["remaining_usd"]


async def test_authorize_check_only_falls_back_when_cache_expired(client):
    """Expired cache entry must not be used — fallback to uncalibrated."""
    from l6e_mcp.server import _get_calibration_cache

    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    sid = session["session_id"]

    _get_calibration_cache().update(
        sid, factor=5.0, source="test", confidence="high",
    )
    _get_calibration_cache()._entries[sid] = _get_calibration_cache()._entries[sid].__class__(
        factor=5.0,
        source="test",
        confidence="high",
        factor_range=None,
        fetched_at=0.0,  # epoch — long expired
    )

    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    assert "calibration_applied" not in result.data


async def test_authorize_check_only_increments_checkpoint_calls(client):
    """check_only=True creates a real call and increments checkpoint_calls."""
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    sid = session["session_id"]
    store = LocalSessionStore()

    before = store.get_session(sid)
    assert before is not None
    initial_checkpoint = before.checkpoint_calls

    await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "check_only": True,
        },
        raise_on_error=False,
    )

    after = store.get_session(sid)
    assert after is not None
    assert after.checkpoint_calls == initial_checkpoint + 1


async def test_check_only_spend_visible_in_full_gate_remaining(client):
    """Spend from check_only calls must be reflected in subsequent full-gate remaining."""
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    sid = session["session_id"]

    check = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    assert not check.is_error
    remaining_after_check = check.data["remaining_usd"]

    gate = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "implement",
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    assert not gate.is_error
    assert gate.data["remaining_usd"] < remaining_after_check, (
        "Full-gate remaining must reflect the check_only call's spend"
    )


# ---------------------------------------------------------------------------
# Slim response shape — l6e_run_start
# ---------------------------------------------------------------------------


async def test_run_start_response_is_slim(client):
    """l6e_run_start must return only session_id.
    Echoed inputs and internal config fields must not be in the response."""
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": 2.0, "model": "gpt-4o"},
        raise_on_error=False,
    )
    assert not result.is_error
    data = result.data

    assert "session_id" in data

    noise_fields = [
        "budget_usd",
        "model",
        "accounting_mode",
        "usage_channel",
        "advanced_fallback_enabled",
        "fallback_correlation_capability",
        "unknown_model_pricing_mode",
        "savings_note",
    ]
    for field in noise_fields:
        assert field not in data, (
            f"Noise field '{field}' should not appear in OSS run_start response"
        )


# ---------------------------------------------------------------------------
# Slim response shape — l6e_run_end
# ---------------------------------------------------------------------------


async def test_run_end_response_is_slim(client):
    """l6e_run_end must return only the minimal session summary.
    Nested objects and dashboard-only stats must not appear in the response."""
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "planning", "estimated_tokens": 500},
        raise_on_error=False,
    )
    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    data = end.data

    assert "session_id" in data
    assert "total_cost_usd" in data
    assert "calls_made" in data
    assert "savings_confidence" in data

    noise_fields = [
        "subagents",
        "exactness_breakdown",
        "overhead_calls",
        "overhead_usd",
        "savings_usd",
        "subagent_calls",
        "subagent_spend_usd",
        "unavailable_exact_calls",
        "exact_calls",
        "reroutes",
        "pricing_warnings",
        "source",
        "net_savings_unavailable",
    ]
    for field in noise_fields:
        assert field not in data, (
            f"Noise field '{field}' should not appear in slim run_end response"
        )


async def test_run_end_does_not_write_log_if_finalize_fails(client, tmp_path, monkeypatch):
    """If finalize_session raises (simulated DB error), the run log must NOT be
    written so that a subsequent retry of l6e_run_end produces exactly one log entry."""
    from unittest.mock import patch

    session = await start_session(client, budget_usd=1.0, model="gpt-4o")
    session_id = session["session_id"]

    log_path = tmp_path / "runs.jsonl"

    original_finalize = LocalSessionStore.finalize_session
    call_count = {"n": 0}

    def failing_first_finalize(self, sid):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise KeyError("simulated DB error")
        return original_finalize(self, sid)

    with patch.object(LocalSessionStore, "finalize_session", failing_first_finalize):
        end1 = await client.call_tool(
            "l6e_run_end", {"session_id": session_id}, raise_on_error=False
        )
    assert end1.is_error

    if log_path.exists() and log_path.stat().st_size > 0:
        entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
        assert len(entries) == 0, "Log must not be written when finalize fails"

    end2 = await client.call_tool(
        "l6e_run_end", {"session_id": session_id}, raise_on_error=False
    )
    assert not end2.is_error
    entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(entries) == 1, f"Expected 1 log entry, got {len(entries)}"


# ---------------------------------------------------------------------------
# l6e_sync_anthropic_usage admin_key resolution
#
# These tests pin the env-fallback contract that landed
# pre-flight on 2026-04-26: callers can omit ``admin_key`` from the tool
# call and the server reads ANTHROPIC_ADMIN_KEY from its environment
# instead. This keeps the admin key out of MCP tool-call payloads and
# chat transcripts, which is the security property the design relies on.
# ---------------------------------------------------------------------------


def _fake_sync_and_upload_factory(captured: dict):
    """Return a fake ``sync_and_upload`` that records its kwargs and returns a stub.

    Used to assert what admin_key the tool actually passed downstream
    without any network I/O. The returned ``SyncResult`` is a real
    dataclass instance so the tool's response-building path stays valid
    even when new fields are added to ``SyncResult``.
    """
    from decimal import Decimal

    from l6e_mcp.anthropic_sync import SyncResult

    def fake(*, admin_key, date_start, date_end, api_key_id=None, include_claude_code=True):
        captured["admin_key"] = admin_key
        captured["date_start"] = date_start
        captured["date_end"] = date_end
        captured["api_key_id"] = api_key_id
        captured["include_claude_code"] = include_claude_code
        return SyncResult(
            buckets_fetched=0,
            rows_sent=0,
            total_cost_usd=Decimal("0"),
            server_response={},
            warnings=[],
            source="cost_report",
        )

    return fake


async def test_sync_anthropic_usage_reads_admin_key_from_env_when_arg_empty(client, monkeypatch):
    sentinel = "sk-ant-admin01-test-env-fallback-only"
    captured: dict = {}

    monkeypatch.setattr(
        "l6e_mcp.anthropic_sync.sync_and_upload",
        _fake_sync_and_upload_factory(captured),
    )
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", sentinel)

    result = await client.call_tool(
        "l6e_sync_anthropic_usage",
        {"date_start": "2026-04-25", "date_end": "2026-04-26"},
        raise_on_error=False,
    )
    assert not result.is_error, f"tool call failed: {result}"
    assert captured["admin_key"] == sentinel


async def test_sync_anthropic_usage_arg_takes_precedence_over_env(client, monkeypatch):
    env_sentinel = "sk-ant-admin01-from-env"
    arg_sentinel = "sk-ant-admin01-from-arg"
    captured: dict = {}

    monkeypatch.setattr(
        "l6e_mcp.anthropic_sync.sync_and_upload",
        _fake_sync_and_upload_factory(captured),
    )
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", env_sentinel)

    result = await client.call_tool(
        "l6e_sync_anthropic_usage",
        {
            "date_start": "2026-04-25",
            "date_end": "2026-04-26",
            "admin_key": arg_sentinel,
        },
        raise_on_error=False,
    )
    assert not result.is_error, f"tool call failed: {result}"
    assert captured["admin_key"] == arg_sentinel


async def test_sync_anthropic_usage_errors_when_arg_empty_and_env_unset(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_ADMIN_KEY", raising=False)

    result = await client.call_tool(
        "l6e_sync_anthropic_usage",
        {"date_start": "2026-04-25", "date_end": "2026-04-26"},
        raise_on_error=False,
    )
    assert result.is_error
    msg = str(result.content[0].text if result.content else "")
    assert "ANTHROPIC_ADMIN_KEY" in msg


async def test_sync_anthropic_usage_rejects_non_admin_key_prefix(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", "sk-ant-api03-not-an-admin-key")

    result = await client.call_tool(
        "l6e_sync_anthropic_usage",
        {"date_start": "2026-04-25", "date_end": "2026-04-26"},
        raise_on_error=False,
    )
    assert result.is_error
    msg = str(result.content[0].text if result.content else "")
    assert "sk-ant-admin" in msg

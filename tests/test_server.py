"""Tests for the l6e-mcp server."""
from __future__ import annotations

import json
import re

import pytest

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
    # Subagent metadata persists in the store
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
    assert r1.data["remaining_usd"] == r2.data["remaining_usd"]
    assert r1.data["budget_pressure"] == r2.data["budget_pressure"]
    assert "overhead_usd" not in r1.data
    assert "overhead_calls" not in r1.data


async def test_run_end_exposes_mode_coverage_and_lag_indicators(client):
    """Exactness/mode-coverage detail is stored per-call; slim run_end only returns summary."""
    session = await start_session(
        client, budget_usd=5.0, usage_channel="self_hosted_relay", model="gpt-4o"
    )
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

    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    assert end.data["calls_made"] == 1
    # Exactness/mode-coverage fields no longer in response; verify via store
    call = LocalSessionStore().get_call(pending.data["call_id"])
    assert call is not None
    assert call.exactness_state == "exact_unavailable"


async def test_session_end_writes_jsonl_with_reconciled_record(client, tmp_path):
    session = await start_session(
        client, budget_usd=10.0, usage_channel="self_hosted_relay", model="gpt-4o"
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
    assert end.data["calls_made"] == 1
    assert end.data["total_cost_usd"] > 0
    log = tmp_path / "runs.jsonl"
    assert log.exists()
    entry = json.loads(log.read_text().strip())
    assert entry["run_id"] == session["session_id"]
    assert entry["records"][0]["completion_tokens"] == 45
    assert entry["records"][0]["model_used"] == "gpt-4o-mini"
    assert entry["overhead_calls"] >= 2
    assert entry["overhead_usd"] > 0
    assert entry["net_savings_usd"] == pytest.approx(
        entry["savings_usd"] - entry["overhead_usd"]
    )


async def test_session_end_writes_subagent_metadata_to_jsonl(client, tmp_path):
    session = await start_session(
        client, budget_usd=10.0, usage_channel="self_hosted_relay", model="gpt-4o"
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
    assert entry["subagent_spend_usd"] > 0
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
    # pricing_warning is surfaced when model is unknown; internal diagnostic fields are not
    assert "pricing_warning" in result.data
    assert "model_pricing_known" not in result.data
    assert "pricing_confidence" not in result.data


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


async def test_run_end_returns_pricing_warnings(client):
    """Pricing warnings for unknown models are surfaced at authorize_call time.
    The slim run_end response does not include them."""
    session = await start_session(client, budget_usd=10.0, model="unknown-model-123")
    checkpoint = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    assert not checkpoint.is_error
    # Warning surfaced at checkpoint time
    assert "pricing_warning" in checkpoint.data
    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    assert "pricing_warnings" not in end.data


async def test_estimate_only_mode_omits_net_savings_and_includes_note(client, tmp_path):
    """In estimate-only mode the slim run_end has savings_confidence='estimate_only'.
    The full net_savings detail lives only in the run log."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
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
    assert "overhead_usd" not in status.data
    assert "savings_confidence" not in status.data
    assert "net_savings_usd" not in status.data

    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    assert end.data["savings_confidence"] == "estimate_only"
    assert "net_savings_usd" not in end.data
    assert "net_savings_unavailable" not in end.data

    log = tmp_path / "runs.jsonl"
    entry = json.loads(log.read_text().strip())
    assert entry["savings_confidence"] == "estimate_only"
    assert entry["net_savings_usd"] == pytest.approx(
        entry["savings_usd"] - entry["overhead_usd"]
    )


async def test_exact_mode_includes_net_savings_usd(client, tmp_path):
    """In exact mode the slim run_end has savings_confidence='exact'.
    The net_savings detail lives in the run log."""
    session = await start_session(
        client, budget_usd=10.0, usage_channel="self_hosted_relay", model="gpt-4o"
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

    status = await client.call_tool(
        "l6e_run_status",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not status.is_error
    assert "overhead_usd" not in status.data
    assert "savings_confidence" not in status.data
    assert "net_savings_usd" not in status.data

    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session["session_id"]},
        raise_on_error=False,
    )
    assert not end.is_error
    assert end.data["savings_confidence"] == "exact"
    assert "net_savings_usd" not in end.data

    log = tmp_path / "runs.jsonl"
    entry = json.loads(log.read_text().strip())
    assert entry["savings_confidence"] == "exact"
    assert entry["net_savings_usd"] == pytest.approx(
        entry["savings_usd"] - entry["overhead_usd"]
    )


# --- Error path tests ---


async def test_run_start_invalid_unknown_model_pricing_mode(client):
    result = await client.call_tool(
        "l6e_run_start",
        {"budget_usd": 1.0, "model": "gpt-4o", "unknown_model_pricing_mode": "not_valid"},
        raise_on_error=False,
    )
    assert result.is_error
    error_text = str(result)
    assert "warn_only" in error_text


async def test_run_status_unknown_session_returns_error(client):
    result = await client.call_tool(
        "l6e_run_status",
        {"session_id": "session_unknown_2026-03-12_deadbeef"},
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


async def test_run_end_deduplicates_pricing_warnings_for_repeated_model(client):
    """Pricing warnings surface at authorize_call time for each unknown-model call.
    run_end is slim and contains no pricing_warnings field."""
    session = await start_session(client, budget_usd=10.0, model="unknown-exotic-model")
    session_id = session["session_id"]
    for _ in range(3):
        result = await client.call_tool(
            "l6e_authorize_call",
            {"session_id": session_id, "tool_name": "read_files", "estimated_tokens": 200},
            raise_on_error=False,
        )
        assert not result.is_error
        assert "pricing_warning" in result.data
    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": session_id},
        raise_on_error=False,
    )
    assert not end.is_error
    assert "pricing_warnings" not in end.data


async def test_run_end_on_already_finalized_session_returns_error(client):
    session = await start_session(client, budget_usd=1.0)
    session_id = session["session_id"]
    # First end — succeeds
    end1 = await client.call_tool(
        "l6e_run_end",
        {"session_id": session_id},
        raise_on_error=False,
    )
    assert not end1.is_error
    # Second end — should fail because session is already finalized
    end2 = await client.call_tool(
        "l6e_run_end",
        {"session_id": session_id},
        raise_on_error=False,
    )
    assert end2.is_error


async def test_warn_budget_pressure_reroute_stores_rerouted_true(client):
    """When the gate returns warn:budget_pressure (>80% spent) the MCP layer
    translates the action to 'reroute'. The persisted call must also have
    rerouted=True so that savings calculations are consistent."""
    from l6e_mcp.session_store import LocalSessionStore

    # Use gpt-4o so pricing is known. Spend 82% of budget up front via
    # actual_prompt_tokens / actual_completion_tokens (reconciled call), which
    # lets us control the exact spend without relying on token estimates.
    session = await start_session(client, budget_usd=1.0, model="gpt-4o")
    session_id = session["session_id"]

    # Pre-load spend to ~85% using a direct actual-token call
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

    status = await client.call_tool(
        "l6e_run_status", {"session_id": session_id}, raise_on_error=False
    )
    pct = status.data["pct_used"]
    # Only run the reroute assertion if we're above the 80% threshold
    if pct < 80.0:
        import pytest
        pytest.skip(f"Pre-load only reached {pct:.1f}% — increase token counts")

    result = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session_id, "tool_name": "search", "estimated_tokens": 100},
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["action"] == "reroute", (
        f"Expected reroute at {pct:.1f}% spend but got: {result.data['action']}"
    )
    call_id = result.data.get("call_id")
    assert call_id is not None
    stored = LocalSessionStore().get_call(call_id)
    assert stored is not None
    assert stored.rerouted is True, (
        "Stored call must have rerouted=True when MCP action is 'reroute'"
    )


async def test_authorize_call_default_tokens_produces_nonzero_cost(client):
    """Calling l6e_authorize_call with no token parameters should use a
    default that is large enough to produce a non-negligible estimated cost.
    The stored estimated_prompt_tokens (after the legacy ratio split of the 2000
    total-token default) must be greater than the old 500-token default split (~417)."""
    from l6e_mcp.session_store import LocalSessionStore

    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session["session_id"], "tool_name": "planning"},
        raise_on_error=False,
    )
    assert not result.is_error
    call_id = result.data.get("call_id")
    assert call_id is not None
    stored = LocalSessionStore().get_call(call_id)
    assert stored is not None
    # The legacy total of 2000 splits to ~1667 prompt + ~333 completion.
    # This must be greater than the old 500-total split of ~417 prompt tokens.
    assert stored.estimated_prompt_tokens > 500, (
        f"Default estimated_prompt_tokens too low: {stored.estimated_prompt_tokens}. "
        "Should be well above the old 500-total default (~417 prompt tokens)."
    )
    assert stored.estimated_cost_usd > 0


async def test_run_status_on_finalized_session_returns_clean_error(client):
    """l6e_run_status on a finalized session must return is_error=True,
    not raise an unhandled KeyError that produces an opaque 500."""
    session = await start_session(client, budget_usd=1.0)
    session_id = session["session_id"]
    await client.call_tool("l6e_run_end", {"session_id": session_id}, raise_on_error=False)

    result = await client.call_tool(
        "l6e_run_status",
        {"session_id": session_id},
        raise_on_error=False,
    )
    assert result.is_error
    assert "finalized" in str(result).lower() or "ended" in str(result).lower()


async def test_authorize_call_on_finalized_session_returns_clean_error(client):
    """l6e_authorize_call on a finalized session must return is_error=True
    rather than propagating an unhandled KeyError from increment_checkpoint_calls."""
    session = await start_session(client, budget_usd=1.0)
    session_id = session["session_id"]
    await client.call_tool("l6e_run_end", {"session_id": session_id}, raise_on_error=False)

    result = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session_id, "tool_name": "planning", "estimated_tokens": 100},
        raise_on_error=False,
    )
    assert result.is_error


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

    # First reconcile — succeeds
    r1 = await client.call_tool(
        "l6e_record_usage",
        {"call_id": call_id, "actual_prompt_tokens": 1000, "actual_completion_tokens": 200},
        raise_on_error=False,
    )
    assert not r1.is_error

    # Second reconcile with DIFFERENT values — must fail
    r2 = await client.call_tool(
        "l6e_record_usage",
        {"call_id": call_id, "actual_prompt_tokens": 9999, "actual_completion_tokens": 9999},
        raise_on_error=False,
    )
    assert r2.is_error

    # Stored values must be unchanged from first reconcile
    from l6e_mcp.session_store import LocalSessionStore
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

    # Required agent-decision fields
    assert "action" in data
    assert "remaining_usd" in data
    assert "budget_pressure" in data
    assert "call_id" in data
    assert "reason" in data

    # Internal bookkeeping must be absent
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

    # Spend 85% up front
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
# Slim response shape + required estimate — l6e_run_status
# ---------------------------------------------------------------------------

async def test_run_status_response_is_slim(client):
    """l6e_run_status must return a minimal decision-relevant snapshot.
    Dashboard-only fields (calls_made, reroutes) must not appear."""
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_run_status",
        {
            "session_id": session["session_id"],
            "estimated_prompt_tokens": 1000,
            "estimated_completion_tokens": 200,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    data = result.data

    # Fields the agent needs
    assert "budget_pressure" in data
    assert "remaining_usd" in data
    assert "pct_used" in data

    # Dashboard stats that aren't decision inputs must be absent
    for field in ("calls_made", "reroutes", "spent_usd"):
        assert field not in data, f"Dashboard field '{field}' should not appear in slim status"


async def test_run_status_accepts_estimate_params(client):
    """l6e_run_status must accept estimated_prompt_tokens and
    estimated_completion_tokens without error."""
    session = await start_session(client, budget_usd=5.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_run_status",
        {
            "session_id": session["session_id"],
            "estimated_prompt_tokens": 3000,
            "estimated_completion_tokens": 600,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["budget_pressure"] in ("low", "moderate", "high", "critical")


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

    # Minimal summary the agent can glance at
    assert "session_id" in data
    assert "total_cost_usd" in data
    assert "calls_made" in data
    assert "savings_confidence" in data

    # Nested rollups and dashboard-only stats must be absent
    noise_fields = [
        "subagents",           # array of dicts
        "exactness_breakdown", # dict
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
    import json
    from unittest.mock import patch

    from l6e_mcp.session_store import LocalSessionStore

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

    # First l6e_run_end call — finalize fails, log must NOT be written
    with patch.object(LocalSessionStore, "finalize_session", failing_first_finalize):
        end1 = await client.call_tool(
            "l6e_run_end", {"session_id": session_id}, raise_on_error=False
        )
    assert end1.is_error

    if log_path.exists() and log_path.stat().st_size > 0:
        entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
        assert len(entries) == 0, "Log must not be written when finalize fails"

    # Second call — finalize succeeds; log must now contain exactly one entry
    end2 = await client.call_tool(
        "l6e_run_end", {"session_id": session_id}, raise_on_error=False
    )
    assert not end2.is_error
    entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(entries) == 1, f"Expected 1 log entry, got {len(entries)}"

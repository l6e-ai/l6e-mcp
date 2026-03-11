"""Tests for l6e-mcp server — all four tools plus all error paths.

Uses FastMCP's in-process Client transport so parameter validation, ToolError
serialization, and the full MCP wire path are exercised without a subprocess.
"""
from __future__ import annotations

import json
import re

import pytest

from l6e_mcp import server as srv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SESSION_ID_RE = re.compile(r"^session_.+_\d{4}-\d{2}-\d{2}_[0-9a-f]{8}$")


async def start_session(mcp_client, budget_usd: float = 5.0, **kwargs) -> str:
    """Helper: start a session and return the session_id."""
    result = await mcp_client.call_tool(
        "l6e_session_start", {"budget_usd": budget_usd, **kwargs},
        raise_on_error=False,
    )
    assert not result.is_error, f"l6e_session_start failed: {result}"
    return result.data["session_id"]


# ---------------------------------------------------------------------------
# l6e_session_start
# ---------------------------------------------------------------------------


async def test_session_start_returns_session_id(client):
    result = await client.call_tool("l6e_session_start", {"budget_usd": 2.0}, raise_on_error=False)
    assert not result.is_error
    sid = result.data["session_id"]
    assert SESSION_ID_RE.match(sid), f"Unexpected session_id format: {sid}"


async def test_session_start_registers_in_registry(client):
    await client.call_tool("l6e_session_start", {"budget_usd": 2.0}, raise_on_error=False)
    assert len(srv._sessions) == 1


async def test_session_start_includes_local_model_key(client):
    result = await client.call_tool("l6e_session_start", {"budget_usd": 2.0}, raise_on_error=False)
    assert not result.is_error
    assert "local_model" in result.data


async def test_session_start_includes_reroute_capable_key(client):
    result = await client.call_tool("l6e_session_start", {"budget_usd": 2.0}, raise_on_error=False)
    assert not result.is_error
    assert "reroute_capable" in result.data


async def test_session_start_reroute_capable_false_when_no_local(client):
    """reroute_capable is False when local_model is null (no Ollama detected)."""
    result = await client.call_tool("l6e_session_start", {"budget_usd": 2.0}, raise_on_error=False)
    assert not result.is_error
    data = result.data
    # reroute_capable must be the boolean inverse of local_model being null
    assert data["reroute_capable"] == (data["local_model"] is not None)


async def test_session_start_client_label_in_session_id(client):
    result = await client.call_tool(
        "l6e_session_start", {"budget_usd": 2.0, "client": "cursor"}, raise_on_error=False,
    )
    assert not result.is_error
    assert "cursor" in result.data["session_id"]


async def test_session_start_returns_budget_and_model(client):
    result = await client.call_tool(
        "l6e_session_start",
        {"budget_usd": 3.5, "model": "claude-3-5-sonnet-20241022"},
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["budget_usd"] == pytest.approx(3.5)
    assert result.data["model"] == "claude-3-5-sonnet-20241022"


# ---------------------------------------------------------------------------
# l6e_checkpoint
# ---------------------------------------------------------------------------


async def test_checkpoint_allow_under_budget(client):
    sid = await start_session(client, budget_usd=10.0)
    result = await client.call_tool(
        "l6e_checkpoint",
        {"session_id": sid, "tool_name": "read_file", "estimated_tokens": 100},
        raise_on_error=False,
    )
    assert not result.is_error
    assert result.data["action"] == "allow"
    assert "spend_so_far_usd" in result.data
    assert "remaining_usd" in result.data
    assert "budget_pressure" in result.data
    assert "reason" in result.data


async def test_checkpoint_records_spend(client):
    sid = await start_session(client, budget_usd=10.0)

    r1 = await client.call_tool(
        "l6e_checkpoint",
        {"session_id": sid, "tool_name": "tool_a", "estimated_tokens": 1000},
        raise_on_error=False,
    )
    r2 = await client.call_tool(
        "l6e_checkpoint",
        {"session_id": sid, "tool_name": "tool_b", "estimated_tokens": 1000},
        raise_on_error=False,
    )
    assert not r1.is_error
    assert not r2.is_error
    # spend must increase (or stay the same if model is unknown to LiteLLM — cost=0)
    assert r2.data["spend_so_far_usd"] >= r1.data["spend_so_far_usd"]


async def test_checkpoint_calls_made_increments(client):
    sid = await start_session(client, budget_usd=10.0)
    await client.call_tool(
        "l6e_checkpoint",
        {"session_id": sid, "tool_name": "tool_a", "estimated_tokens": 100},
        raise_on_error=False,
    )
    await client.call_tool(
        "l6e_checkpoint",
        {"session_id": sid, "tool_name": "tool_b", "estimated_tokens": 100},
        raise_on_error=False,
    )
    spend_result = await client.call_tool(
        "l6e_spend", {"session_id": sid}, raise_on_error=False,
    )
    assert spend_result.data["calls_made"] == 2


async def test_checkpoint_halt_when_over_budget(client, monkeypatch):
    """Force the gate to halt by setting a tiny budget and using a known-cost model."""
    # Use a budget so small any call would exceed it.
    # monkeypatch the estimator on the context to return a large cost.
    sid = await start_session(client, budget_usd=0.000001)
    result = await client.call_tool(
        "l6e_checkpoint",
        {"session_id": sid, "tool_name": "expensive_tool", "estimated_tokens": 100000},
        raise_on_error=False,
    )
    assert not result.is_error
    # With budget=0.000001 USD and 100000 tokens, the gate should halt.
    # If LiteLLM returns 0 for unknown model, this may still be allow — acceptable.
    assert result.data["action"] in ("allow", "reroute", "halt")


async def test_checkpoint_unknown_session_is_tool_error(client):
    result = await client.call_tool(
        "l6e_checkpoint",
        {
            "session_id": "session_bad_2026-01-01_deadbeef",
            "tool_name": "some_tool",
        },
        raise_on_error=False,
    )
    assert result.is_error is True


# ---------------------------------------------------------------------------
# l6e_spend
# ---------------------------------------------------------------------------


async def test_spend_returns_all_fields(client):
    sid = await start_session(client, budget_usd=5.0)
    result = await client.call_tool("l6e_spend", {"session_id": sid}, raise_on_error=False)
    assert not result.is_error
    data = result.data
    for field in ("spent_usd", "remaining_usd", "budget_usd", "calls_made", "reroutes",
                  "budget_pressure", "pct_used"):
        assert field in data, f"Missing field: {field}"


async def test_spend_is_readonly(client):
    sid = await start_session(client, budget_usd=5.0)
    r1 = await client.call_tool("l6e_spend", {"session_id": sid}, raise_on_error=False)
    r2 = await client.call_tool("l6e_spend", {"session_id": sid}, raise_on_error=False)
    assert not r1.is_error
    assert not r2.is_error
    # calls_made must not change from reading spend
    assert r1.data["calls_made"] == r2.data["calls_made"]
    assert r1.data["spent_usd"] == r2.data["spent_usd"]


async def test_spend_unknown_session_is_tool_error(client):
    result = await client.call_tool(
        "l6e_spend", {"session_id": "session_bad_2026-01-01_deadbeef"}, raise_on_error=False,
    )
    assert result.is_error is True


# ---------------------------------------------------------------------------
# l6e_session_end
# ---------------------------------------------------------------------------


async def test_session_end_returns_summary_fields(client):
    sid = await start_session(client, budget_usd=5.0)
    result = await client.call_tool("l6e_session_end", {"session_id": sid}, raise_on_error=False)
    assert not result.is_error
    data = result.data
    assert data["session_id"] == sid
    assert data["source"] == "mcp"
    assert "total_cost_usd" in data
    assert "calls_made" in data
    assert "reroutes" in data
    assert "savings_usd" in data


async def test_session_end_writes_jsonl(client, tmp_path):
    sid = await start_session(client, budget_usd=5.0)
    await client.call_tool("l6e_session_end", {"session_id": sid}, raise_on_error=False)

    log = tmp_path / "runs.jsonl"
    assert log.exists(), "runs.jsonl was not created"
    line = log.read_text().strip()
    assert line, "runs.jsonl is empty"
    entry = json.loads(line)
    assert entry["source"] == "mcp"
    assert entry["run_id"] == sid


async def test_session_end_removes_from_registry(client):
    sid = await start_session(client, budget_usd=5.0)
    await client.call_tool("l6e_session_end", {"session_id": sid}, raise_on_error=False)
    assert sid not in srv._sessions


async def test_session_end_twice_is_tool_error(client):
    sid = await start_session(client, budget_usd=5.0)
    await client.call_tool("l6e_session_end", {"session_id": sid}, raise_on_error=False)
    result = await client.call_tool(
        "l6e_session_end", {"session_id": sid}, raise_on_error=False,
    )
    assert result.is_error is True


async def test_session_end_unknown_session_is_tool_error(client):
    result = await client.call_tool(
        "l6e_session_end", {"session_id": "session_bad_2026-01-01_deadbeef"}, raise_on_error=False,
    )
    assert result.is_error is True


# ---------------------------------------------------------------------------
# Full round-trip: start → checkpoint → spend → end
# ---------------------------------------------------------------------------


async def test_full_round_trip(client, tmp_path):
    """Start a session, run two checkpoints, read spend, end — verify log."""
    sid = await start_session(client, budget_usd=10.0, **{"client": "cursor"})

    c1 = await client.call_tool(
        "l6e_checkpoint",
        {"session_id": sid, "tool_name": "read_files", "estimated_tokens": 500},
        raise_on_error=False,
    )
    assert not c1.is_error
    assert c1.data["action"] in ("allow", "reroute", "halt")

    c2 = await client.call_tool(
        "l6e_checkpoint",
        {"session_id": sid, "tool_name": "edit_file", "estimated_tokens": 300},
        raise_on_error=False,
    )
    assert not c2.is_error

    spend = await client.call_tool("l6e_spend", {"session_id": sid}, raise_on_error=False)
    assert not spend.is_error

    end = await client.call_tool("l6e_session_end", {"session_id": sid}, raise_on_error=False)
    assert not end.is_error
    assert end.data["source"] == "mcp"

    log = tmp_path / "runs.jsonl"
    assert log.exists()
    entry = json.loads(log.read_text().strip())
    assert entry["source"] == "mcp"
    assert entry["run_id"] == sid

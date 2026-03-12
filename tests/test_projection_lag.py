"""Projection lag indicators are surfaced in run_end snapshots."""
from __future__ import annotations

from fastmcp.client import Client

from l6e_mcp.server import mcp


async def test_run_end_includes_lag_and_coverage_fields():
    async with Client(transport=mcp) as client:
        start = await client.call_tool(
            "l6e_run_start",
            {"budget_usd": 5.0, "model": "gpt-4o", "proxy_mode": True},
            raise_on_error=False,
        )
        session_id = start.data["session_id"]
        first = await client.call_tool(
            "l6e_authorize_call",
            {
                "session_id": session_id,
                "tool_name": "read_files",
                "estimated_tokens": 500,
                "call_mode": "ask",
            },
            raise_on_error=False,
        )
        second = await client.call_tool(
            "l6e_authorize_call",
            {
                "session_id": session_id,
                "tool_name": "read_files",
                "estimated_tokens": 500,
                "call_mode": "ask",
                "actual_prompt_tokens": 600,
                "actual_completion_tokens": 120,
            },
            raise_on_error=False,
        )
        assert not first.is_error
        assert not second.is_error

        end = await client.call_tool(
            "l6e_run_end",
            {"session_id": session_id},
            raise_on_error=False,
        )
        assert not end.is_error
        data = end.data
        assert data["pending_exact_calls"] == 1
        assert data["exactness_state"] == "partial_exact"
        assert data["last_reconciled_at"] is not None
        assert data["mode_coverage"]["ask_mode_exact_capable"] is True
        assert "agent" in data["mode_coverage_gaps"]

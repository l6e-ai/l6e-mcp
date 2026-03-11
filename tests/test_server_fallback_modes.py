"""Server lifecycle behavior for advanced fallback modes."""
from __future__ import annotations

from fastmcp.client import Client

from l6e_mcp import server as srv
from l6e_mcp.server import mcp


async def test_default_session_lifecycle_has_no_active_file_side_effects(tmp_path, monkeypatch):
    active_session = tmp_path / "active_session"
    active_call = tmp_path / "active_call"
    monkeypatch.setattr(srv, "_ACTIVE_SESSION_FILE", active_session)
    monkeypatch.setattr(srv, "_ACTIVE_CALL_FILE", active_call)

    async with Client(transport=mcp) as client:
        start = await client.call_tool(
            "l6e_run_start",
            {"budget_usd": 2.0, "model": "gpt-4o", "proxy_mode": True},
            raise_on_error=False,
        )
        session_id = start.data["session_id"]
        await client.call_tool(
            "l6e_authorize_call",
            {"session_id": session_id, "tool_name": "read_files", "estimated_tokens": 100},
            raise_on_error=False,
        )
        assert not active_session.exists()
        assert not active_call.exists()
        await client.call_tool(
            "l6e_run_end",
            {"session_id": session_id},
            raise_on_error=False,
        )
        assert not active_session.exists()
        assert not active_call.exists()


async def test_advanced_fallback_session_writes_active_files(tmp_path, monkeypatch):
    active_session = tmp_path / "active_session"
    active_call = tmp_path / "active_call"
    monkeypatch.setattr(srv, "_ACTIVE_SESSION_FILE", active_session)
    monkeypatch.setattr(srv, "_ACTIVE_CALL_FILE", active_call)

    async with Client(transport=mcp) as client:
        start = await client.call_tool(
            "l6e_run_start",
            {
                "budget_usd": 2.0,
                "model": "gpt-4o",
                "proxy_mode": True,
                "advanced_fallback": True,
            },
            raise_on_error=False,
        )
        session_id = start.data["session_id"]
        assert active_session.exists()

        authorize = await client.call_tool(
            "l6e_authorize_call",
            {"session_id": session_id, "tool_name": "read_files", "estimated_tokens": 100},
            raise_on_error=False,
        )
        assert not authorize.is_error
        assert active_call.exists()

        await client.call_tool(
            "l6e_run_end",
            {"session_id": session_id},
            raise_on_error=False,
        )
        assert not active_session.exists()
        assert not active_call.exists()

"""Shared fixtures for l6e-mcp tests."""
from __future__ import annotations

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport

from l6e_mcp import server as srv
from l6e_mcp.server import mcp


@pytest.fixture(autouse=True)
def reset_sessions(tmp_path, monkeypatch):
    """Clear session registry and redirect log writes to tmp_path before every test.

    The autouse=True ensures every test starts with a clean registry and a
    writable log path — critical because l6e_session_start reads L6E_LOG_PATH
    at call time and the default relative path fails under Windsurf's cwd=/ issue.
    """
    srv._sessions.clear()
    monkeypatch.setenv("L6E_LOG_PATH", str(tmp_path / "runs.jsonl"))
    yield
    srv._sessions.clear()


@pytest.fixture
async def client():
    """In-process FastMCP client — tests the full MCP wire path without a subprocess."""
    async with Client(transport=mcp) as c:
        yield c

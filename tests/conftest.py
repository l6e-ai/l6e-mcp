"""Shared fixtures for l6e-mcp tests."""
from __future__ import annotations

import pytest
from fastmcp.client import Client

from l6e_mcp.server import mcp


@pytest.fixture(autouse=True)
def reset_sessions(tmp_path, monkeypatch):
    """Redirect all local persistence to tmp_path before every test.

    The autouse=True ensures every test starts with isolated SQLite state
    and a writable log path.
    """
    monkeypatch.setenv("L6E_LOG_PATH", str(tmp_path / "runs.jsonl"))
    monkeypatch.setenv("L6E_SESSION_DB_PATH", str(tmp_path / "sessions.db"))
    yield


@pytest.fixture
async def client():
    """In-process FastMCP client — tests the full MCP wire path without a subprocess."""
    async with Client(transport=mcp) as c:
        yield c

"""TDD tests for MCP tool hanging fixes.

Tests are written BEFORE the implementation. They cover:
- Fix 1: Session store singleton + cached init_schema
- Fix 2: Single _get_session_store() call per tool handler
- Fix 4: Tool-level timeouts on all @mcp.tool decorators
- Fix 5: Background sync deadline + store reuse + timeout normalization
- Fix 6: l6e_run_end cloud sync moved to background thread
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from l6e_mcp.session_store import LocalSessionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ===========================================================================
# Fix 1: Session store singleton
# ===========================================================================


def test_get_session_store_returns_singleton():
    from l6e_mcp.server import _get_session_store, _reset_session_store

    _reset_session_store()
    a = _get_session_store()
    b = _get_session_store()
    assert a is b


def test_get_session_store_resets_after_clear():
    from l6e_mcp.server import _get_session_store, _reset_session_store

    _reset_session_store()
    a = _get_session_store()
    _reset_session_store()
    b = _get_session_store()
    assert a is not b


def test_init_schema_runs_once_per_singleton():
    from l6e_mcp.server import _get_session_store, _reset_session_store

    _reset_session_store()
    with patch("l6e_mcp.store.sessions.init_schema") as mock_init:
        _get_session_store()
        _get_session_store()
        _get_session_store()
        assert mock_init.call_count == 1


# ===========================================================================
# Fix 2: Single store access per tool call
# ===========================================================================


async def test_authorize_call_single_store_access(client, monkeypatch):
    session = await start_session(client)
    session_id = session["session_id"]

    real_store = None
    call_count = 0
    original_fn = None

    def counting_get_store():
        nonlocal call_count, real_store, original_fn
        call_count += 1
        if real_store is None:
            # Import here to avoid circular; use original on first call
            if original_fn is None:
                original_fn = LocalSessionStore
            real_store = original_fn()
        return real_store

    call_count = 0
    monkeypatch.setattr("l6e_mcp.server._get_session_store", counting_get_store)

    result = await client.call_tool(
        "l6e_authorize_call",
        {"session_id": session_id, "tool_name": "implement"},
        raise_on_error=False,
    )
    assert not result.is_error
    assert call_count == 1, f"_get_session_store called {call_count} times, expected 1"


async def test_run_status_single_store_access(client, monkeypatch):
    session = await start_session(client)
    session_id = session["session_id"]

    real_store = None
    call_count = 0

    def counting_get_store():
        nonlocal call_count, real_store
        call_count += 1
        if real_store is None:
            real_store = LocalSessionStore()
        return real_store

    call_count = 0
    monkeypatch.setattr("l6e_mcp.server._get_session_store", counting_get_store)

    result = await client.call_tool(
        "l6e_run_status",
        {"session_id": session_id},
        raise_on_error=False,
    )
    assert not result.is_error
    assert call_count == 1, f"_get_session_store called {call_count} times, expected 1"


# ===========================================================================
# Fix 4: Tool-level timeouts
# ===========================================================================


def _get_tool_map():
    """Return {name: Tool} from the FastMCP local provider."""
    from fastmcp.tools.tool import Tool

    from l6e_mcp.server import mcp

    return {
        comp.name: comp
        for comp in mcp._local_provider._components.values()
        if isinstance(comp, Tool)
    }


def test_all_tools_have_timeout_set():
    """Every registered MCP tool must have a non-None timeout."""
    tools = _get_tool_map()
    for name, tool in tools.items():
        assert tool.timeout is not None, (
            f"Tool '{name}' has no timeout set. "
            "All tools must have a timeout to prevent indefinite hangs."
        )


EXPECTED_TIMEOUTS = {
    "l6e_run_start": 10,
    "l6e_authorize_call": 10,
    "l6e_record_usage": 10,
    "l6e_run_status": 5,
    "l6e_run_end": 10,
}


@pytest.mark.parametrize("tool_name,expected", EXPECTED_TIMEOUTS.items())
def test_tool_timeout_values(tool_name, expected):
    tools = _get_tool_map()
    assert tool_name in tools, f"Tool '{tool_name}' not found"
    assert tools[tool_name].timeout == expected, (
        f"Tool '{tool_name}' timeout is {tools[tool_name].timeout}, expected {expected}"
    )


# ===========================================================================
# Fix 5: Background sync deadline + timeout normalization
# ===========================================================================


def test_drain_respects_deadline(tmp_path, monkeypatch):
    from l6e_mcp import outbox

    monkeypatch.setenv("L6E_OUTBOX_DIR", str(tmp_path / "outbox"))

    for i in range(5):
        outbox.enqueue({"session_id": f"sess_{i}", "data": "test"})

    sent_count = 0

    def slow_send(payload, api_key, endpoint, timeout=3.0):
        nonlocal sent_count
        sent_count += 1
        return True

    with patch.object(outbox, "try_send", side_effect=slow_send):
        deadline = time.time() - 0.001  # already expired
        outbox.drain("sk-test", "https://api.l6e.ai", deadline=deadline)

    assert sent_count <= 1, f"drain processed {sent_count} items despite expired deadline"


def test_recover_stale_sessions_respects_deadline(tmp_path, monkeypatch):
    from l6e._types import BudgetMode, PipelinePolicy

    from l6e_mcp import outbox
    from l6e_mcp.store._connection import make_connection
    from l6e_mcp.store._serialization import _policy_to_json
    from l6e_mcp.store.sessions import SessionRepository

    db = tmp_path / "sessions.db"
    monkeypatch.setenv("L6E_SESSION_DB_PATH", str(db))
    monkeypatch.setenv("L6E_LOG_PATH", str(tmp_path / "runs.jsonl"))

    policy = PipelinePolicy(budget=1.5, budget_mode=BudgetMode.HALT)
    SessionRepository(db)  # ensure schema
    created_at = time.time() - 7200

    for i in range(3):
        sid = f"stale_{i}"
        with make_connection(db) as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, model, policy_json, source, log_path,
                    accounting_mode, usage_channel,
                    ask_mode_exact_capable, plan_mode_exact_capable, agent_mode_exact_capable,
                    state, next_call_index, checkpoint_calls, status_calls,
                    created_at, ended_at, finalized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, 0, ?, NULL, NULL)
                """,
                (sid, "gpt-4o", _policy_to_json(policy), "mcp", None,
                 "estimate_only", "none", 0, 0, 0, created_at),
            )

    finalized = []
    original_finalize = LocalSessionStore.finalize_session

    def tracking_finalize(self, session_id):
        finalized.append(session_id)
        return original_finalize(self, session_id)

    with patch.object(LocalSessionStore, "finalize_session", tracking_finalize), \
            patch("l6e_mcp.outbox.httpx") as mock_httpx:
            mock_httpx.post.return_value.status_code = 201
            deadline = time.time() - 0.001  # already expired
            outbox.recover_stale_sessions(
                "sk-test", "https://api.l6e.ai", deadline=deadline,
            )

    assert len(finalized) <= 1, (
        f"recover_stale_sessions processed {len(finalized)} sessions despite expired deadline"
    )


def test_recover_stale_sessions_uses_passed_store(tmp_path, monkeypatch):
    """When a store is passed, recover_stale_sessions must not create its own."""
    from l6e_mcp import outbox

    monkeypatch.setenv("L6E_SESSION_DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("L6E_LOG_PATH", str(tmp_path / "runs.jsonl"))

    store = LocalSessionStore(tmp_path / "sessions.db")

    with patch("l6e_mcp.session_store.LocalSessionStore") as MockStore:
        outbox.recover_stale_sessions(
            "sk-test", "https://api.l6e.ai", store=store,
        )
        MockStore.assert_not_called()


def test_drain_uses_default_timeout(tmp_path, monkeypatch):
    """drain() should use the default try_send timeout (3.0s), not override to 5.0s."""
    from l6e_mcp import outbox

    monkeypatch.setenv("L6E_OUTBOX_DIR", str(tmp_path / "outbox"))
    outbox.enqueue({"session_id": "sess_1", "data": "test"})

    with patch.object(outbox, "try_send", return_value=True) as mock_send:
        outbox.drain("sk-test", "https://api.l6e.ai")

    if mock_send.called:
        call_kwargs = mock_send.call_args
        # Should not pass timeout=5.0; either omit it (use default) or pass 3.0
        if call_kwargs.kwargs.get("timeout") is not None:
            assert call_kwargs.kwargs["timeout"] == 3.0, (
                f"drain uses timeout={call_kwargs.kwargs['timeout']}, expected 3.0"
            )


# ===========================================================================
# Fix 6: l6e_run_end cloud sync via outbox (no blocking HTTP)
# ===========================================================================


async def test_run_end_enqueues_to_outbox(client, monkeypatch, tmp_path):
    """l6e_run_end enqueues the session report to the outbox for deferred sync."""
    monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test-key")
    monkeypatch.setenv("L6E_CLOUD_SYNC", "1")
    outbox_dir = tmp_path / "outbox"
    monkeypatch.setenv("L6E_OUTBOX_DIR", str(outbox_dir))

    session = await start_session(client, budget_usd=1.0)
    session_id = session["session_id"]

    start = time.monotonic()
    result = await client.call_tool(
        "l6e_run_end",
        {"session_id": session_id},
        raise_on_error=False,
    )
    elapsed = time.monotonic() - start

    assert not result.is_error
    assert elapsed < 2.0, (
        f"l6e_run_end took {elapsed:.1f}s — should be fast with outbox-only sync"
    )
    outbox_files = list(outbox_dir.glob("*.json"))
    assert len(outbox_files) == 1, "session report should be enqueued to outbox"

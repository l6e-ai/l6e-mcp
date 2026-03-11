"""Tests for the LiteLLM callback server."""
from __future__ import annotations

import json

from l6e_mcp.litellm_proxy import callback_server as cb


async def test_call_l6e_reconcile_call_uses_fastmcp_transport(monkeypatch):
    """Callback forwarding should target the FastMCP HTTP transport at /mcp."""

    captured: dict[str, object] = {}

    class FakeResult:
        is_error = False
        data = {"status": "reconciled", "remaining_usd": 1.0}

    class FakeClient:
        def __init__(self, transport, timeout):
            captured["transport"] = transport
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def call_tool(self, name, arguments, raise_on_error=False):
            captured["tool_name"] = name
            captured["arguments"] = arguments
            captured["raise_on_error"] = raise_on_error
            return FakeResult()

    monkeypatch.setattr(cb, "Client", FakeClient)
    monkeypatch.setattr(cb, "MCP_HTTP_URL", "http://127.0.0.1:8000/")

    result = await cb._call_l6e_reconcile_call(
        call_id="call_deadbeef",
        prompt_tokens=123,
        completion_tokens=45,
        model="gpt-4o-mini",
    )

    assert captured["transport"] == "http://127.0.0.1:8000/mcp"
    assert captured["timeout"] == 5.0
    assert captured["tool_name"] == "l6e_reconcile_call"
    assert captured["raise_on_error"] is False
    assert captured["arguments"] == {
        "call_id": "call_deadbeef",
        "actual_prompt_tokens": 123,
        "actual_completion_tokens": 45,
        "model_used": "gpt-4o-mini",
    }
    assert result == {"status": "reconciled", "remaining_usd": 1.0}


def test_read_active_call_supports_json_payload(tmp_path, monkeypatch):
    active_call = tmp_path / "active_call"
    active_call.write_text(json.dumps({"session_id": "session_x", "call_id": "call_x"}))
    monkeypatch.setattr(cb, "ACTIVE_CALL_FILE", active_call)
    assert cb._read_active_call() == "call_x"


def test_extract_call_correlation_prefers_spend_logs_metadata():
    payload = {
        "metadata": {
            "spend_logs_metadata": {
                "l6e_call_id": "call_meta",
            }
        },
        "request_tags": ["l6e_call_id:call_tag"],
    }
    assert cb._extract_call_correlation(payload) == ("call_meta", "spend_logs_metadata")


def test_extract_call_correlation_reads_request_tags():
    payload = {
        "request_tags": ["foo", "l6e_call_id:call_tag"],
    }
    assert cb._extract_call_correlation(payload) == ("call_tag", "request_tags")


def test_callback_summary_extracts_subagent_metadata():
    payload = {
        "metadata": {
            "spend_logs_metadata": {
                "l6e_call_id": "call_meta",
                "l6e_actor_type": "subagent",
                "l6e_actor_id": "subagent_search_1",
                "l6e_actor_name": "Search agent",
                "l6e_parent_call_id": "call_parent_123",
            }
        },
        "response": {
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        },
    }
    summary = cb._callback_summary(payload)
    assert summary["correlated_call_id"] == "call_meta"
    assert summary["actor_type"] == "subagent"
    assert summary["actor_id"] == "subagent_search_1"
    assert summary["actor_name"] == "Search agent"
    assert summary["parent_call_id"] == "call_parent_123"


async def test_success_callback_prefers_metadata_call_id_over_active_call(tmp_path, monkeypatch):
    active_session = tmp_path / "active_session"
    active_call = tmp_path / "active_call"
    active_session.write_text("session_123")
    active_call.write_text(json.dumps({"session_id": "session_123", "call_id": "call_fallback"}))
    monkeypatch.setattr(cb, "ACTIVE_SESSION_FILE", active_session)
    monkeypatch.setattr(cb, "ACTIVE_CALL_FILE", active_call)

    captured: dict[str, object] = {}

    async def fake_reconcile(
        call_id,
        prompt_tokens,
        completion_tokens,
        model,
        request_id=None,
        trace_id=None,
        correlation_key=None,
        correlation_source=None,
    ):
        captured["call_id"] = call_id
        captured["request_id"] = request_id
        captured["trace_id"] = trace_id
        captured["correlation_key"] = correlation_key
        captured["correlation_source"] = correlation_source
        return {"status": "reconciled"}

    monkeypatch.setattr(cb, "_call_l6e_reconcile_call", fake_reconcile)

    class FakeRequest:
        async def json(self):
            return {
                "id": "req_1",
                "trace_id": "trace_1",
                "metadata": {"spend_logs_metadata": {"l6e_call_id": "call_meta"}},
                "response": {
                    "model": "gpt-4o-mini",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            }

    response = await cb.litellm_success_callback(FakeRequest())
    body = json.loads(response.body.decode())
    assert body["status"] == "recorded"
    assert captured["call_id"] == "call_meta"
    assert captured["correlation_source"] == "spend_logs_metadata"
    assert captured["correlation_key"] == "call_meta"


async def test_success_callback_records_orphan_when_no_correlation(tmp_path, monkeypatch):
    active_session = tmp_path / "active_session"
    active_call = tmp_path / "active_call"
    active_session.write_text("session_123")
    monkeypatch.setattr(cb, "ACTIVE_SESSION_FILE", active_session)
    monkeypatch.setattr(cb, "ACTIVE_CALL_FILE", active_call)

    captured: dict[str, object] = {}

    def fake_persist_unmatched_usage(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cb, "persist_unmatched_usage", fake_persist_unmatched_usage)

    class FakeRequest:
        async def json(self):
            return {
                "id": "req_2",
                "trace_id": "trace_2",
                "response": {
                    "model": "gpt-4o-mini",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            }

    response = await cb.litellm_success_callback(FakeRequest())
    body = json.loads(response.body.decode())
    assert body["status"] == "no_active_call"
    assert captured["reason"] == "no_correlation_match"
    assert captured["request_id"] == "req_2"
    assert captured["trace_id"] == "trace_2"

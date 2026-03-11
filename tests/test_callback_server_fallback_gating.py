"""Fallback gating behavior for callback correlation."""
from __future__ import annotations

import json

from l6e_mcp.litellm_proxy import callback_server as cb


async def test_callback_missing_metadata_default_mode_does_not_use_active_file(
    tmp_path,
    monkeypatch,
):
    active_session = tmp_path / "active_session"
    active_call = tmp_path / "active_call"
    active_session.write_text("session_123")
    active_call.write_text(json.dumps({"session_id": "session_123", "call_id": "call_fallback"}))
    monkeypatch.setattr(cb, "ACTIVE_SESSION_FILE", active_session)
    monkeypatch.setattr(cb, "ACTIVE_CALL_FILE", active_call)
    monkeypatch.setattr(cb, "_is_advanced_fallback_enabled", lambda session_id: False)

    called = {"reconcile": False}

    async def fake_reconcile(*args, **kwargs):
        called["reconcile"] = True
        return {"status": "reconciled"}

    monkeypatch.setattr(cb, "_call_l6e_record_usage", fake_reconcile)

    class FakeRequest:
        async def json(self):
            return {
                "id": "req_3",
                "trace_id": "trace_3",
                "response": {
                    "model": "gpt-4o-mini",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            }

    response = await cb.litellm_success_callback(FakeRequest())
    body = json.loads(response.body.decode())
    assert body["status"] == "no_active_call"
    assert body["diagnostic"] == "missing_call_id_metadata"
    assert called["reconcile"] is False


async def test_callback_missing_metadata_advanced_mode_uses_active_file(tmp_path, monkeypatch):
    active_session = tmp_path / "active_session"
    active_call = tmp_path / "active_call"
    active_session.write_text("session_advanced")
    active_call.write_text(
        json.dumps(
            {
                "session_id": "session_advanced",
                "call_id": "call_fallback_advanced",
            }
        )
    )
    monkeypatch.setattr(cb, "ACTIVE_SESSION_FILE", active_session)
    monkeypatch.setattr(cb, "ACTIVE_CALL_FILE", active_call)
    monkeypatch.setattr(cb, "_is_advanced_fallback_enabled", lambda session_id: True)

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
        captured["correlation_source"] = correlation_source
        return {"status": "reconciled"}

    monkeypatch.setattr(cb, "_call_l6e_record_usage", fake_reconcile)

    class FakeRequest:
        async def json(self):
            return {
                "id": "req_4",
                "trace_id": "trace_4",
                "response": {
                    "model": "gpt-4o-mini",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            }

    response = await cb.litellm_success_callback(FakeRequest())
    body = json.loads(response.body.decode())
    assert body["status"] == "recorded"
    assert captured["call_id"] == "call_fallback_advanced"
    assert captured["correlation_source"] == "active_call_fallback_advanced"

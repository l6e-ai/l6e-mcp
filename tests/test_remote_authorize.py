"""Tests for server-side authorize wiring (remote_authorize + server.py integration).

Covers:
- try_remote_authorize HTTP client (mock httpx)
- Server-first branch in l6e_authorize_call
- Fallback to local auth when server is unreachable
- Calibrated spend accumulation across calls
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
import pytest

from l6e_mcp.core.remote_authorize import try_remote_authorize


# ---------------------------------------------------------------------------
# try_remote_authorize unit tests
# ---------------------------------------------------------------------------

_SERVER_ALLOW = {
    "action": "allow",
    "calibrated_cost_usd": 0.68,
    "raw_cost_usd": 0.01,
    "calibration_factor": 68.0,
    "calibration_source": "personal",
    "remaining_usd": 4.32,
    "budget_pressure": "low",
}

_SERVER_HALT = {
    "action": "halt",
    "calibrated_cost_usd": 5.10,
    "raw_cost_usd": 0.075,
    "calibration_factor": 68.0,
    "calibration_source": "personal",
    "remaining_usd": 0.0,
    "budget_pressure": "critical",
}

_CALL_KWARGS = dict(
    api_key="sk-l6e-test",
    endpoint="https://api.l6e.ai",
    session_id="session_test",
    model="claude-4.6-opus-high-thinking",
    tool_name="planning",
    estimated_cost_usd=0.01,
    budget_usd=5.0,
    spent_usd=0.0,
)


class TestTryRemoteAuthorize:
    def test_returns_response_on_allow(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _SERVER_ALLOW

        with patch("l6e_mcp.core.remote_authorize.httpx.post", return_value=mock_resp):
            result = try_remote_authorize(**_CALL_KWARGS)
        assert result is not None
        assert result["action"] == "allow"
        assert result["calibration_factor"] == 68.0

    def test_returns_response_on_halt(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _SERVER_HALT

        with patch("l6e_mcp.core.remote_authorize.httpx.post", return_value=mock_resp):
            result = try_remote_authorize(**_CALL_KWARGS)
        assert result is not None
        assert result["action"] == "halt"

    def test_returns_none_on_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("l6e_mcp.core.remote_authorize.httpx.post", return_value=mock_resp):
            result = try_remote_authorize(**_CALL_KWARGS)
        assert result is None

    def test_returns_none_on_timeout(self):
        with patch(
            "l6e_mcp.core.remote_authorize.httpx.post",
            side_effect=httpx.TimeoutException("timed out"),
        ):
            result = try_remote_authorize(**_CALL_KWARGS)
        assert result is None

    def test_returns_none_on_network_error(self):
        with patch(
            "l6e_mcp.core.remote_authorize.httpx.post",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            result = try_remote_authorize(**_CALL_KWARGS)
        assert result is None

    def test_sends_correct_payload(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _SERVER_ALLOW

        with patch("l6e_mcp.core.remote_authorize.httpx.post", return_value=mock_resp) as mock_post:
            try_remote_authorize(**_CALL_KWARGS)

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args.args[0] == "https://api.l6e.ai/v1/authorize"
        assert call_args.kwargs["json"]["model"] == "claude-4.6-opus-high-thinking"
        assert call_args.kwargs["json"]["estimated_cost_usd"] == 0.01
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer sk-l6e-test"


# ---------------------------------------------------------------------------
# Integration: server-first branch in l6e_authorize_call
# ---------------------------------------------------------------------------


async def _start_session(mcp_client, budget: float = 5.0) -> str:
    result = await mcp_client.call_tool(
        "l6e_run_start",
        {"budget_usd": budget, "model": "claude-4.6-opus-high-thinking"},
        raise_on_error=False,
    )
    assert not result.is_error, f"l6e_run_start failed: {result}"
    return result.data["session_id"]


async def _authorize(mcp_client, session_id: str, **kwargs) -> dict:
    params = {
        "session_id": session_id,
        "tool_name": "planning",
        "estimated_prompt_tokens": 2000,
        "estimated_completion_tokens": 400,
        **kwargs,
    }
    result = await mcp_client.call_tool(
        "l6e_authorize_call", params, raise_on_error=False,
    )
    assert not result.is_error, f"l6e_authorize_call failed: {result}"
    return result.data


class TestServerFirstBranch:
    async def test_uses_server_decision_when_available(self, client, monkeypatch):
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")

        session_id = await _start_session(client)

        with patch(
            "l6e_mcp.server.try_remote_authorize", return_value=_SERVER_ALLOW,
        ):
            result = await _authorize(client, session_id)

        assert result["action"] == "allow"
        assert result["reason"] == "server_calibrated"
        assert result["calibration_factor"] == 68.0
        assert result["calibration_source"] == "personal"
        assert "call_id" in result

    async def test_server_halt_returned_to_agent(self, client, monkeypatch):
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")

        session_id = await _start_session(client)

        with patch(
            "l6e_mcp.server.try_remote_authorize", return_value=_SERVER_HALT,
        ):
            result = await _authorize(client, session_id)

        assert result["action"] == "halt"
        assert result["budget_pressure"] == "critical"

    async def test_falls_back_to_local_when_server_unreachable(self, client, monkeypatch):
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")

        session_id = await _start_session(client)

        with patch(
            "l6e_mcp.server.try_remote_authorize", return_value=None,
        ):
            result = await _authorize(client, session_id)

        assert result["action"] == "allow"
        assert result["reason"] != "server_calibrated"
        assert "call_id" in result

    async def test_skips_server_when_cloud_sync_disabled(self, client, monkeypatch):
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "false")

        session_id = await _start_session(client)

        with patch(
            "l6e_mcp.server.try_remote_authorize",
        ) as mock_remote:
            result = await _authorize(client, session_id)

        mock_remote.assert_not_called()
        assert result["action"] == "allow"

    async def test_skips_server_when_no_api_key(self, client, monkeypatch):
        monkeypatch.delenv("L6E_API_KEY", raising=False)
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")

        session_id = await _start_session(client)

        with patch(
            "l6e_mcp.server.try_remote_authorize",
        ) as mock_remote:
            result = await _authorize(client, session_id)

        mock_remote.assert_not_called()
        assert result["action"] == "allow"

    async def test_skips_server_when_actual_tokens_provided(self, client, monkeypatch):
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")

        session_id = await _start_session(client)

        with patch(
            "l6e_mcp.server.try_remote_authorize",
        ) as mock_remote:
            result = await _authorize(
                client,
                session_id,
                actual_prompt_tokens=1000,
                actual_completion_tokens=200,
            )

        mock_remote.assert_not_called()

    async def test_reroute_passed_through_as_budget_signal(self, client, monkeypatch):
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")

        reroute_resp = {
            **_SERVER_ALLOW,
            "action": "reroute",
            "budget_pressure": "high",
        }
        session_id = await _start_session(client)

        with patch(
            "l6e_mcp.server.try_remote_authorize", return_value=reroute_resp,
        ):
            result = await _authorize(client, session_id)

        assert result["action"] == "reroute"
        assert result["budget_pressure"] == "high"


class TestCalibratedSpendAccumulation:
    async def test_calibrated_cost_accumulates_in_local_spend(self, client, monkeypatch):
        """After a server-calibrated call, local spend tracking reflects
        the calibrated cost, not the raw estimate."""
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")

        session_id = await _start_session(client, budget=5.0)

        with patch(
            "l6e_mcp.server.try_remote_authorize", return_value=_SERVER_ALLOW,
        ):
            await _authorize(client, session_id)

        status = await client.call_tool(
            "l6e_run_status", {"session_id": session_id}, raise_on_error=False,
        )
        assert not status.is_error
        spent = status.data["pct_used"]
        assert spent > 1.0, "Spend should reflect calibrated cost (0.68), not raw (0.01)"

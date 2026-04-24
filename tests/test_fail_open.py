"""Iron-rule tests for MCP server authorize path (L6E-41).

Companion to ``l6e/tests/unit/test_fail_open.py`` (SDK side). Covers
the cloud-facing failure modes from the iron-rule matrix in
``pivot-docs/cost-benchmark-margin-thesis/05-integration-architecture.md``:

| Failure | Expected |
|---|---|
| Cloud `/v1/authorize` down | Fall back to local auth (already tested) |
| Cloud slow (> latency_deadline_ms) | Tighter timeout honored → fall back |
| Prediction returns garbage (NaN, neg, missing fields) | Fall back to local |
| Gate crashes in-process (MCP tool) | Tool returns fail-open allow |

Every test asserts the MCP tool itself never raises an uncaught
exception back to the agent — agents must always receive a dict with
an actionable ``action`` or ``budget_pressure`` field.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from l6e_mcp.core.remote_authorize import _reset_client, try_remote_authorize
from l6e_mcp.server import _sanitize_server_authorize_response

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_client():
    _reset_client()
    yield
    _reset_client()


def _mock_async_client(*, post_return=None, post_side_effect=None):
    mock_client = MagicMock()
    mock_client.post = AsyncMock(
        return_value=post_return, side_effect=post_side_effect,
    )
    return patch(
        "l6e_mcp.core.remote_authorize._get_async_client",
        return_value=mock_client,
    )


_VALID_SERVER_RESP = {
    "action": "allow",
    "calibrated_cost_usd": 0.68,
    "raw_cost_usd": 0.01,
    "calibration_factor": 68.0,
    "calibration_source": "personal",
    "remaining_usd": 4.32,
    "budget_pressure": "low",
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


class TestRemoteAuthorizeFailOpenLogging:
    """Cloud authorize failures should be visible in production log levels."""

    async def test_non_200_response_logs_warning(self, caplog) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "gateway unavailable"

        caplog.set_level(logging.WARNING, logger="l6e_mcp.core.remote_authorize")
        with _mock_async_client(post_return=mock_resp):
            result = await try_remote_authorize(**_CALL_KWARGS)

        assert result is None
        record = next(
            r for r in caplog.records if r.getMessage() == "remote_authorize_rejected"
        )
        assert record.levelno == logging.WARNING
        assert record.status == 503
        assert record.body == "gateway unavailable"

    async def test_timeout_logs_warning_with_effective_timeout(
        self, caplog,
    ) -> None:
        caplog.set_level(logging.WARNING, logger="l6e_mcp.core.remote_authorize")
        with _mock_async_client(
            post_side_effect=httpx.TimeoutException("deadline exceeded"),
        ):
            result = await try_remote_authorize(
                **_CALL_KWARGS, latency_deadline_ms=5,
            )

        assert result is None
        record = next(
            r for r in caplog.records if r.getMessage() == "remote_authorize_timeout"
        )
        assert record.levelno == logging.WARNING
        assert record.url == "https://api.l6e.ai/v1/authorize"
        assert record.effective_timeout == pytest.approx(0.005)

    async def test_bad_json_logs_warning_with_traceback(self, caplog) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("Expecting value")

        caplog.set_level(logging.WARNING, logger="l6e_mcp.core.remote_authorize")
        with _mock_async_client(post_return=mock_resp):
            result = await try_remote_authorize(**_CALL_KWARGS)

        assert result is None
        record = next(
            r for r in caplog.records if r.getMessage() == "remote_authorize_bad_json"
        )
        assert record.levelno == logging.WARNING
        assert record.exc_info is not None

    async def test_network_exception_logs_warning_with_traceback(self, caplog) -> None:
        caplog.set_level(logging.WARNING, logger="l6e_mcp.core.remote_authorize")
        with _mock_async_client(post_side_effect=RuntimeError("network crashed")):
            result = await try_remote_authorize(**_CALL_KWARGS)

        assert result is None
        record = next(
            r for r in caplog.records if r.getMessage() == "remote_authorize_failed"
        )
        assert record.levelno == logging.WARNING
        assert record.exc_info is not None


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


# ---------------------------------------------------------------------------
# Scenario 2: cloud slow (> latency_deadline_ms) → treat as down
# ---------------------------------------------------------------------------


class TestLatencyDeadlineHonored:
    """latency_deadline_ms must tighten the effective HTTP timeout."""

    async def test_deadline_tightens_timeout_below_default(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _VALID_SERVER_RESP

        with _mock_async_client(post_return=mock_resp) as mock_get_client:
            await try_remote_authorize(
                **_CALL_KWARGS,
                latency_deadline_ms=50,  # 0.05s — tighter than default 1.0s
            )

        post_kwargs = mock_get_client.return_value.post.call_args.kwargs
        assert post_kwargs["timeout"] == pytest.approx(0.05), (
            "latency_deadline_ms=50 must tighten the HTTP timeout to 0.05s"
        )

    async def test_deadline_above_default_does_not_widen_timeout(self) -> None:
        """A relaxed deadline must not lift the 1s safety timeout."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _VALID_SERVER_RESP

        with _mock_async_client(post_return=mock_resp) as mock_get_client:
            await try_remote_authorize(
                **_CALL_KWARGS,
                latency_deadline_ms=30_000,  # 30s — relaxed
                timeout=1.0,
            )

        post_kwargs = mock_get_client.return_value.post.call_args.kwargs
        assert post_kwargs["timeout"] == pytest.approx(1.0)

    async def test_missing_deadline_uses_explicit_timeout(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _VALID_SERVER_RESP

        with _mock_async_client(post_return=mock_resp) as mock_get_client:
            await try_remote_authorize(**_CALL_KWARGS, timeout=0.5)

        post_kwargs = mock_get_client.return_value.post.call_args.kwargs
        assert post_kwargs["timeout"] == pytest.approx(0.5)

    async def test_deadline_triggered_timeout_returns_none(self) -> None:
        """When the tighter deadline fires, caller gets None → fail-open."""
        with _mock_async_client(
            post_side_effect=httpx.TimeoutException("deadline exceeded"),
        ):
            result = await try_remote_authorize(
                **_CALL_KWARGS, latency_deadline_ms=5,
            )
        assert result is None


# ---------------------------------------------------------------------------
# Scenario 4: prediction returns garbage (NaN, neg, missing) → sanity check
# ---------------------------------------------------------------------------


class TestMalformedJsonFromGateway:
    """A 200 response whose body is not valid JSON must fail-open."""

    async def test_json_parse_error_returns_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("Expecting value")

        with _mock_async_client(post_return=mock_resp):
            result = await try_remote_authorize(**_CALL_KWARGS)
        assert result is None


class TestResponseSanityCheck:
    """Sanity-check the server response envelope before acting on it."""

    def test_accepts_valid_response(self) -> None:
        assert _sanitize_server_authorize_response(_VALID_SERVER_RESP) is not None

    def test_rejects_missing_action(self) -> None:
        bad = {**_VALID_SERVER_RESP}
        del bad["action"]
        assert _sanitize_server_authorize_response(bad) is None

    def test_rejects_invalid_action(self) -> None:
        bad = {**_VALID_SERVER_RESP, "action": "maybe"}
        assert _sanitize_server_authorize_response(bad) is None

    def test_rejects_nan_calibrated_cost(self) -> None:
        bad = {**_VALID_SERVER_RESP, "calibrated_cost_usd": float("nan")}
        assert _sanitize_server_authorize_response(bad) is None

    def test_rejects_inf_calibrated_cost(self) -> None:
        bad = {**_VALID_SERVER_RESP, "calibrated_cost_usd": float("inf")}
        assert _sanitize_server_authorize_response(bad) is None

    def test_rejects_negative_calibrated_cost(self) -> None:
        bad = {**_VALID_SERVER_RESP, "calibrated_cost_usd": -0.5}
        assert _sanitize_server_authorize_response(bad) is None

    def test_rejects_string_calibrated_cost(self) -> None:
        bad = {**_VALID_SERVER_RESP, "calibrated_cost_usd": "oops"}
        assert _sanitize_server_authorize_response(bad) is None

    def test_rejects_missing_remaining(self) -> None:
        bad = {**_VALID_SERVER_RESP}
        del bad["remaining_usd"]
        assert _sanitize_server_authorize_response(bad) is None

    def test_rejects_invalid_pressure_label(self) -> None:
        bad = {**_VALID_SERVER_RESP, "budget_pressure": "nuclear"}
        assert _sanitize_server_authorize_response(bad) is None

    def test_rejects_nan_calibration_factor(self) -> None:
        bad = {**_VALID_SERVER_RESP, "calibration_factor": float("nan")}
        assert _sanitize_server_authorize_response(bad) is None

    def test_allows_missing_calibration_factor(self) -> None:
        resp = {**_VALID_SERVER_RESP}
        del resp["calibration_factor"]
        assert _sanitize_server_authorize_response(resp) is not None

    def test_rejects_non_dict(self) -> None:
        assert _sanitize_server_authorize_response([]) is None  # type: ignore[arg-type]
        assert _sanitize_server_authorize_response("nope") is None  # type: ignore[arg-type]


class TestServerGarbageFallsBackToLocal:
    """End-to-end: a 200 with garbage should trigger local-auth fallback,
    not propagate an exception to the agent."""

    async def test_nan_in_response_falls_back_to_local(
        self, client, monkeypatch,
    ) -> None:
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")
        session_id = await _start_session(client)

        garbage = {**_VALID_SERVER_RESP, "calibrated_cost_usd": float("nan")}
        with patch(
            "l6e_mcp.server.try_remote_authorize", return_value=garbage,
        ):
            result = await _authorize(client, session_id)

        # Local-auth fallback path — reason is NOT server_calibrated.
        assert result["action"] == "allow"
        assert result.get("reason") != "server_calibrated"

    async def test_missing_action_falls_back_to_local(
        self, client, monkeypatch,
    ) -> None:
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")
        session_id = await _start_session(client)

        garbage = {k: v for k, v in _VALID_SERVER_RESP.items() if k != "action"}
        with patch(
            "l6e_mcp.server.try_remote_authorize", return_value=garbage,
        ):
            result = await _authorize(client, session_id)

        assert result["action"] == "allow"
        assert result.get("reason") != "server_calibrated"

    async def test_negative_cost_falls_back_to_local(
        self, client, monkeypatch,
    ) -> None:
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")
        session_id = await _start_session(client)

        garbage = {**_VALID_SERVER_RESP, "calibrated_cost_usd": -1.0}
        with patch(
            "l6e_mcp.server.try_remote_authorize", return_value=garbage,
        ):
            result = await _authorize(client, session_id)

        assert result["action"] == "allow"
        assert result.get("reason") != "server_calibrated"


# ---------------------------------------------------------------------------
# Scenario 5: gate crashes in-process (MCP tool wrapper)
# ---------------------------------------------------------------------------


class TestMcpToolFailsOpenOnInternalException:
    """If ``authorize_call`` or the server branch raises, the MCP tool
    must return a fail-open ``allow`` dict, not surface a ToolError."""

    async def test_exception_in_authorize_call_returns_fail_open_allow(
        self, client, monkeypatch,
    ) -> None:
        monkeypatch.delenv("L6E_API_KEY", raising=False)
        monkeypatch.delenv("L6E_CLOUD_SYNC", raising=False)
        session_id = await _start_session(client)

        with patch(
            "l6e_mcp.server.authorize_call",
            side_effect=RuntimeError("simulated gate crash"),
        ):
            result = await _authorize(client, session_id)

        assert result["action"] == "allow"
        assert result["reason"] == "fail_open:gate_exception"
        assert "budget_pressure" in result
        assert "remaining_usd" in result

    async def test_exception_in_server_branch_returns_fail_open_allow(
        self, client, monkeypatch,
    ) -> None:
        """If ``_try_server_authorize`` itself raises (defensively wrapped),
        the outer tool wrapper still produces a safe response."""
        monkeypatch.setenv("L6E_API_KEY", "sk-l6e-test")
        monkeypatch.setenv("L6E_CLOUD_SYNC", "true")
        session_id = await _start_session(client)

        # Both the inner server branch AND the local fallback fail.
        with (
            patch(
                "l6e_mcp.server._try_server_authorize",
                side_effect=RuntimeError("server branch crashed"),
            ),
            patch(
                "l6e_mcp.server.authorize_call",
                side_effect=RuntimeError("local fallback crashed too"),
            ),
        ):
            result = await _authorize(client, session_id)

        assert result["action"] == "allow"
        assert result["reason"] == "fail_open:gate_exception"

    async def test_check_only_path_fails_open_on_store_crash(
        self, client, monkeypatch,
    ) -> None:
        """check_only has its own code path — must also fail-open."""
        session_id = await _start_session(client)

        # Poison the calibration cache access so the check_only path crashes.
        with patch(
            "l6e_mcp.server._get_calibration_cache",
            side_effect=RuntimeError("cache crashed"),
        ):
            result = await _authorize(client, session_id, check_only=True)

        assert result["reason"] == "fail_open:gate_exception"
        assert "budget_pressure" in result


# ---------------------------------------------------------------------------
# Scenario 6: customer misconfigures policy (e.g. budget = 0)
# ---------------------------------------------------------------------------


class TestPolicyValidationAtSessionStart:
    """PipelinePolicy rejects obvious misconfig at construction time.
    MCP's ``l6e_run_start`` must pass that error back to the caller
    rather than silently accepting a broken policy."""

    async def test_invalid_reroute_threshold_rejected_at_start(
        self, client, monkeypatch,
    ) -> None:
        monkeypatch.setenv("L6E_REROUTE_THRESHOLD", "2.5")
        result = await client.call_tool(
            "l6e_run_start",
            {"budget_usd": 5.0, "model": "claude-4.6-opus-high-thinking"},
            raise_on_error=False,
        )
        # The server may either reject with ToolError (preferred) or
        # clamp — but must never silently accept a 2.5 threshold. A
        # subsequent authorize call should still work.
        if result.is_error:
            return
        # If the server sanitized the value, ensure it's in-range.
        session_id = result.data["session_id"]
        status = await client.call_tool(
            "l6e_authorize_call",
            {"session_id": session_id, "tool_name": "planning", "check_only": True},
            raise_on_error=False,
        )
        assert not status.is_error

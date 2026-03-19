"""Unit tests for StatusTelemetryWorker."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from l6e_mcp.core.status_telemetry import StatusTelemetryPayload, StatusTelemetryWorker

_SAMPLE_PAYLOAD = StatusTelemetryPayload(
    session_id="session_test_001",
    model="gpt-4o",
    estimated_prompt_tokens=2000,
    estimated_completion_tokens=400,
    raw_projected_cost_usd=0.01,
    calibrated_projected_cost_usd=0.05,
    calibration_factor=5.0,
    calibration_source="personal",
    budget_usd=5.0,
    spent_usd=0.3,
    budget_pressure="low",
)


def test_enqueue_sends_post():
    worker = StatusTelemetryWorker(api_key="sk-test", endpoint="http://localhost:9999")
    with patch("l6e_mcp.core.status_telemetry.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=202)
        worker.enqueue(_SAMPLE_PAYLOAD)
        worker.shutdown(timeout=2.0)
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs.kwargs["json"]["session_id"] == "session_test_001"
    assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert "/v1/status-telemetry" in call_kwargs.args[0]


def test_enqueue_swallows_post_exception():
    worker = StatusTelemetryWorker(api_key="sk-test", endpoint="http://localhost:9999")
    with patch("l6e_mcp.core.status_telemetry.httpx.post", side_effect=Exception("boom")):
        worker.enqueue(_SAMPLE_PAYLOAD)
        worker.shutdown(timeout=2.0)
    # No exception should propagate — fire-and-forget.


def test_shutdown_without_enqueue():
    worker = StatusTelemetryWorker(api_key="sk-test", endpoint="http://localhost:9999")
    worker.shutdown(timeout=0.5)  # No thread started, should be a no-op


def test_multiple_payloads_drained_in_order():
    posted: list[dict] = []
    worker = StatusTelemetryWorker(api_key="sk-test", endpoint="http://localhost:9999")

    def capture_post(url, json=None, **kwargs):
        posted.append(json)
        return MagicMock(status_code=202)

    with patch("l6e_mcp.core.status_telemetry.httpx.post", side_effect=capture_post):
        for i in range(3):
            payload = StatusTelemetryPayload(
                session_id=f"session_{i}",
                model="gpt-4o",
                estimated_prompt_tokens=100,
                estimated_completion_tokens=50,
                raw_projected_cost_usd=0.001,
                calibrated_projected_cost_usd=0.005,
                calibration_factor=5.0,
                calibration_source="personal",
                budget_usd=5.0,
                spent_usd=0.0,
                budget_pressure="low",
            )
            worker.enqueue(payload)
        worker.shutdown(timeout=2.0)

    assert len(posted) == 3
    assert [p["session_id"] for p in posted] == ["session_0", "session_1", "session_2"]

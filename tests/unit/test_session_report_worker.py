"""Unit tests for SessionReportWorker."""
from __future__ import annotations

from unittest.mock import patch

from l6e_mcp.core.session_report_worker import SessionReportWorker


def _sample_payload(session_id: str = "session_test_001") -> dict:
    return {
        "session_id": session_id,
        "model": "gpt-5.3",
        "source": "mcp",
        "total_cost_usd": 0.42,
        "calls_made": 3,
    }


def test_enqueue_posts_and_removes_outbox_file():
    worker = SessionReportWorker(api_key="sk-test", endpoint="http://localhost:9999")
    with (
        patch("l6e_mcp.outbox.try_send", return_value=True) as mock_send,
        patch("l6e_mcp.outbox.remove") as mock_remove,
    ):
        worker.enqueue(_sample_payload())
        worker.shutdown(timeout=2.0)

    mock_send.assert_called_once()
    payload_arg = mock_send.call_args[0][0]
    assert payload_arg["session_id"] == "session_test_001"
    assert mock_send.call_args[0][1] == "sk-test"
    assert mock_send.call_args[0][2] == "http://localhost:9999"
    mock_remove.assert_called_once_with("session_test_001")


def test_failed_send_does_not_remove_outbox_file():
    worker = SessionReportWorker(api_key="sk-test", endpoint="http://localhost:9999")
    with (
        patch("l6e_mcp.outbox.try_send", return_value=False),
        patch("l6e_mcp.outbox.remove") as mock_remove,
    ):
        worker.enqueue(_sample_payload())
        worker.shutdown(timeout=2.0)

    mock_remove.assert_not_called()


def test_exception_in_send_does_not_propagate():
    worker = SessionReportWorker(api_key="sk-test", endpoint="http://localhost:9999")
    with (
        patch("l6e_mcp.outbox.try_send", side_effect=Exception("boom")),
        patch("l6e_mcp.outbox.remove") as mock_remove,
    ):
        worker.enqueue(_sample_payload())
        worker.shutdown(timeout=2.0)

    mock_remove.assert_not_called()


def test_shutdown_without_enqueue():
    worker = SessionReportWorker(api_key="sk-test", endpoint="http://localhost:9999")
    worker.shutdown(timeout=0.5)


def test_multiple_payloads_drained_in_order():
    sent: list[str] = []
    removed: list[str] = []

    def fake_send(payload, api_key, endpoint):
        sent.append(payload["session_id"])
        return True

    def fake_remove(session_id):
        removed.append(session_id)

    worker = SessionReportWorker(api_key="sk-test", endpoint="http://localhost:9999")
    with (
        patch("l6e_mcp.outbox.try_send", side_effect=fake_send),
        patch("l6e_mcp.outbox.remove", side_effect=fake_remove),
    ):
        for i in range(3):
            worker.enqueue(_sample_payload(f"session_{i}"))
        worker.shutdown(timeout=2.0)

    assert sent == ["session_0", "session_1", "session_2"]
    assert removed == ["session_0", "session_1", "session_2"]

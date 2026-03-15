"""Tests for l6e_mcp.outbox — local file outbox + drain logic."""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from l6e_mcp import outbox


@pytest.fixture(autouse=True)
def _use_tmp_outbox(monkeypatch, tmp_path):
    outbox_dir = tmp_path / "outbox"
    monkeypatch.setenv("L6E_OUTBOX_DIR", str(outbox_dir))
    return outbox_dir


def _sample_payload(session_id: str = "session_test_001") -> dict:
    return {
        "session_id": session_id,
        "model": "gpt-5.3",
        "source": "mcp",
        "total_cost_usd": 0.42,
        "calls_made": 3,
    }


def test_enqueue_creates_file(tmp_path):
    payload = _sample_payload()
    path = outbox.enqueue(payload)
    assert path.exists()
    assert path.name == "session_test_001.json"
    data = json.loads(path.read_text())
    assert data["session_id"] == "session_test_001"


def test_try_send_success():
    payload = _sample_payload()
    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        mock_httpx.post.return_value.status_code = 201
        result = outbox.try_send(payload, "sk-l6e-test", "https://api.l6e.ai")
    assert result is True
    mock_httpx.post.assert_called_once()
    call_args = mock_httpx.post.call_args
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer sk-l6e-test"


def test_try_send_failure_returns_false():
    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        mock_httpx.post.side_effect = ConnectionError("refused")
        result = outbox.try_send(_sample_payload(), "sk-l6e-test", "https://api.l6e.ai")
    assert result is False


def test_try_send_409_is_success():
    """Duplicate (409) should be treated as success — already delivered."""
    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        mock_httpx.post.return_value.status_code = 409
        result = outbox.try_send(_sample_payload(), "sk-l6e-test", "https://api.l6e.ai")
    assert result is True


def test_try_send_500_is_failure():
    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        mock_httpx.post.return_value.status_code = 500
        mock_httpx.post.return_value.text = "Internal server error"
        result = outbox.try_send(_sample_payload(), "sk-l6e-test", "https://api.l6e.ai")
    assert result is False


def test_drain_sends_and_deletes():
    p1 = outbox.enqueue(_sample_payload("session_a"))
    p2 = outbox.enqueue(_sample_payload("session_b"))
    assert p1.exists()
    assert p2.exists()

    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        mock_httpx.post.return_value.status_code = 201
        outbox.drain("sk-l6e-test", "https://api.l6e.ai")

    assert not p1.exists()
    assert not p2.exists()
    assert mock_httpx.post.call_count == 2


def test_drain_leaves_failed_files():
    p = outbox.enqueue(_sample_payload("session_fail"))

    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        mock_httpx.post.return_value.status_code = 500
        mock_httpx.post.return_value.text = "error"
        outbox.drain("sk-l6e-test", "https://api.l6e.ai")

    assert p.exists()


def test_drain_discards_stale_files(tmp_path):
    p = outbox.enqueue(_sample_payload("session_stale"))
    stale_time = time.time() - (8 * 24 * 3600)
    import os
    os.utime(p, (stale_time, stale_time))

    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        outbox.drain("sk-l6e-test", "https://api.l6e.ai")

    assert not p.exists()
    mock_httpx.post.assert_not_called()


def test_drain_noop_when_empty():
    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        outbox.drain("sk-l6e-test", "https://api.l6e.ai")
    mock_httpx.post.assert_not_called()

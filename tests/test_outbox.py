"""Tests for l6e_mcp.outbox — local file outbox + drain logic."""
from __future__ import annotations

import json
import time
from decimal import Decimal
from unittest.mock import patch

import pytest
from l6e._types import BudgetMode, PipelinePolicy

from l6e_mcp import outbox
from l6e_mcp.session_store import LocalSessionStore
from l6e_mcp.store._connection import make_connection
from l6e_mcp.store._serialization import _policy_to_json
from l6e_mcp.store.calls import CallRepository
from l6e_mcp.store.sessions import SessionRepository


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


def test_remove_deletes_existing_file(tmp_path):
    path = outbox.enqueue(_sample_payload("session_to_remove"))
    assert path.exists()
    outbox.remove("session_to_remove")
    assert not path.exists()


def test_remove_noop_when_missing():
    outbox.remove("session_nonexistent")


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


# ---------------------------------------------------------------------------
# recover_stale_sessions tests
# ---------------------------------------------------------------------------


def _policy(budget: float = 1.5) -> PipelinePolicy:
    return PipelinePolicy(budget=budget, budget_mode=BudgetMode.HALT)


def _create_backdated_session(db_path, session_id, *, age_seconds, n_calls=0):
    """Insert a session with created_at in the past, optionally with backdated calls."""
    repo = SessionRepository(db_path)  # noqa: F841 — ensures schema init
    created_at = time.time() - age_seconds
    with make_connection(db_path) as conn:
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
            (
                session_id,
                "gpt-4o",
                _policy_to_json(_policy()),
                "mcp",
                None,
                "estimate_only",
                "none",
                0,
                0,
                0,
                created_at,
            ),
        )
    if n_calls > 0:
        call_repo = CallRepository(db_path)
        for _ in range(n_calls):
            call = call_repo.create(
                session_id=session_id,
                tool_name="implement",
                model_requested="gpt-4o",
                model_used="gpt-4o",
                estimated_prompt_tokens=2000,
                estimated_completion_tokens=400,
                estimated_cost_usd=Decimal("0.01"),
                rerouted=False,
            )
            with make_connection(db_path) as conn:
                conn.execute(
                    "UPDATE calls SET created_at = ? WHERE call_id = ?",
                    (created_at, call.call_id),
                )


def test_recover_stale_session_with_calls_is_synced(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setenv("L6E_SESSION_DB_PATH", str(db))
    monkeypatch.setenv("L6E_LOG_PATH", str(tmp_path / "runs.jsonl"))
    _create_backdated_session(db, "stale_with_calls", age_seconds=7200, n_calls=3)

    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        mock_httpx.post.return_value.status_code = 201
        outbox.recover_stale_sessions("sk-test", "https://api.l6e.ai")

    mock_httpx.post.assert_called_once()
    payload = mock_httpx.post.call_args.kwargs["json"]
    assert payload["session_id"] == "stale_with_calls"
    assert len(payload["calls"]) == 3

    store = LocalSessionStore(db)
    session = store.get_session("stale_with_calls")
    assert session is not None
    assert session.state == "finalized"


def test_recover_zero_call_session_is_dropped(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setenv("L6E_SESSION_DB_PATH", str(db))
    monkeypatch.setenv("L6E_LOG_PATH", str(tmp_path / "runs.jsonl"))
    _create_backdated_session(db, "zero_calls", age_seconds=7200, n_calls=0)

    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        outbox.recover_stale_sessions("sk-test", "https://api.l6e.ai")

    mock_httpx.post.assert_not_called()

    store = LocalSessionStore(db)
    session = store.get_session("zero_calls")
    assert session is not None
    assert session.state == "finalized"


def test_recover_ancient_session_is_dropped(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setenv("L6E_SESSION_DB_PATH", str(db))
    monkeypatch.setenv("L6E_LOG_PATH", str(tmp_path / "runs.jsonl"))
    _create_backdated_session(
        db, "ancient_session", age_seconds=31 * 24 * 3600, n_calls=5,
    )

    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        outbox.recover_stale_sessions("sk-test", "https://api.l6e.ai")

    mock_httpx.post.assert_not_called()

    store = LocalSessionStore(db)
    session = store.get_session("ancient_session")
    assert session is not None
    assert session.state == "finalized"


def test_recover_leaves_recent_session_alone(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setenv("L6E_SESSION_DB_PATH", str(db))
    repo = SessionRepository(db)
    repo.create(
        session_id="recent_session",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )

    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        outbox.recover_stale_sessions("sk-test", "https://api.l6e.ai")

    mock_httpx.post.assert_not_called()
    session = repo.get("recent_session")
    assert session is not None
    assert session.state == "active"


def test_recover_failure_does_not_block_others(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setenv("L6E_SESSION_DB_PATH", str(db))
    monkeypatch.setenv("L6E_LOG_PATH", str(tmp_path / "runs.jsonl"))
    _create_backdated_session(db, "will_fail", age_seconds=7200, n_calls=2)
    _create_backdated_session(db, "will_succeed", age_seconds=7200, n_calls=2)

    call_count = 0

    def _mock_finalize(session_id, **kwargs):
        nonlocal call_count
        call_count += 1
        if session_id == "will_fail":
            raise RuntimeError("simulated failure")
        return _original_finalize(session_id, **kwargs)

    store = LocalSessionStore(db)
    _original_finalize = store.finalize_session

    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        mock_httpx.post.return_value.status_code = 201
        with patch.object(
            LocalSessionStore, "finalize_session", side_effect=_mock_finalize,
        ):
            outbox.recover_stale_sessions("sk-test", "https://api.l6e.ai")

    store2 = LocalSessionStore(db)
    failed = store2.get_session("will_fail")
    assert failed is not None
    assert failed.state == "active"

    succeeded = store2.get_session("will_succeed")
    assert succeeded is not None
    assert succeeded.state == "finalized"


def test_recover_sync_failure_enqueues_to_outbox(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setenv("L6E_SESSION_DB_PATH", str(db))
    monkeypatch.setenv("L6E_LOG_PATH", str(tmp_path / "runs.jsonl"))
    _create_backdated_session(db, "sync_fail_session", age_seconds=7200, n_calls=2)

    with patch("l6e_mcp.outbox.httpx") as mock_httpx:
        mock_httpx.post.return_value.status_code = 500
        mock_httpx.post.return_value.text = "Internal server error"
        outbox.recover_stale_sessions("sk-test", "https://api.l6e.ai")

    outbox_dir = outbox._outbox_dir()
    outbox_files = list(outbox_dir.glob("*.json"))
    assert len(outbox_files) == 1
    payload = json.loads(outbox_files[0].read_text())
    assert payload["session_id"] == "sync_fail_session"

    store = LocalSessionStore(db)
    session = store.get_session("sync_fail_session")
    assert session is not None
    assert session.state == "finalized"

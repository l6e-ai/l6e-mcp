"""Unit tests for SessionRepository."""
from __future__ import annotations

import time

import pytest
from l6e._types import BudgetMode, PipelinePolicy

from l6e_mcp.store.calls import CallRepository
from l6e_mcp.store.sessions import SessionRepository


def _policy(budget: float = 1.5) -> PipelinePolicy:
    return PipelinePolicy(budget=budget, budget_mode=BudgetMode.HALT)


def _repo(tmp_path) -> SessionRepository:
    return SessionRepository(tmp_path / "sessions.db")


def test_create_and_get_session(tmp_path):
    repo = _repo(tmp_path)
    session = repo.create(
        session_id="session_cursor_2026-03-12_aabbccdd",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    assert session.session_id == "session_cursor_2026-03-12_aabbccdd"
    assert session.model == "gpt-4o"
    assert session.state == "active"

    fetched = repo.get("session_cursor_2026-03-12_aabbccdd")
    assert fetched is not None
    assert fetched.session_id == session.session_id


def test_get_missing_session_returns_none(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get("nonexistent") is None


def test_require_active_raises_for_unknown(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(KeyError, match="nonexistent"):
        repo.require_active("nonexistent")


def test_require_active_raises_for_finalized(tmp_path):
    repo = _repo(tmp_path)
    repo.create(
        session_id="session_cursor_2026-03-12_finalized1",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    repo.finalize("session_cursor_2026-03-12_finalized1")
    with pytest.raises(KeyError):
        repo.require_active("session_cursor_2026-03-12_finalized1")


def test_finalize_session(tmp_path):
    repo = _repo(tmp_path)
    repo.create(
        session_id="session_cursor_2026-03-12_finalize2",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    session = repo.finalize("session_cursor_2026-03-12_finalize2")
    assert session.state == "finalized"
    assert session.finalized_at is not None


def test_finalize_already_finalized_raises(tmp_path):
    repo = _repo(tmp_path)
    repo.create(
        session_id="session_cursor_2026-03-12_finalize3",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    repo.finalize("session_cursor_2026-03-12_finalize3")
    with pytest.raises(KeyError):
        repo.finalize("session_cursor_2026-03-12_finalize3")


def test_increment_checkpoint_calls(tmp_path):
    repo = _repo(tmp_path)
    repo.create(
        session_id="session_cursor_2026-03-12_checkpoint1",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    repo.increment_checkpoint_calls("session_cursor_2026-03-12_checkpoint1", increment_by=3)
    session = repo.get("session_cursor_2026-03-12_checkpoint1")
    assert session is not None
    assert session.checkpoint_calls == 3


def test_increment_status_calls(tmp_path):
    repo = _repo(tmp_path)
    repo.create(
        session_id="session_cursor_2026-03-12_status1",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    repo.increment_status_calls("session_cursor_2026-03-12_status1")
    session = repo.get("session_cursor_2026-03-12_status1")
    assert session is not None
    assert session.status_calls == 1


def test_increment_checkpoint_noop_for_zero(tmp_path):
    repo = _repo(tmp_path)
    repo.create(
        session_id="session_cursor_2026-03-12_noop1",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    repo.increment_checkpoint_calls("session_cursor_2026-03-12_noop1", increment_by=0)
    session = repo.get("session_cursor_2026-03-12_noop1")
    assert session is not None
    assert session.checkpoint_calls == 0


def test_increment_finalized_session_raises(tmp_path):
    repo = _repo(tmp_path)
    repo.create(
        session_id="session_cursor_2026-03-12_finalized4",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    repo.finalize("session_cursor_2026-03-12_finalized4")
    with pytest.raises(KeyError):
        repo.increment_checkpoint_calls("session_cursor_2026-03-12_finalized4")


def test_create_session_with_mode_coverage_overrides(tmp_path):
    repo = _repo(tmp_path)
    session = repo.create(
        session_id="session_cursor_2026-03-12_coverage1",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
        ask_mode_exact_capable=True,
        plan_mode_exact_capable=True,
        agent_mode_exact_capable=False,
    )
    assert session.ask_mode_exact_capable is True
    assert session.plan_mode_exact_capable is True
    assert session.agent_mode_exact_capable is False


def test_finalize_unknown_session_raises(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(KeyError, match="nonexistent"):
        repo.finalize("nonexistent")


def test_increment_checkpoint_calls_on_finalized_session_raises(tmp_path):
    repo = _repo(tmp_path)
    repo.create(
        session_id="session_cursor_2026-03-12_fin_ckpt1",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    repo.finalize("session_cursor_2026-03-12_fin_ckpt1")
    with pytest.raises(KeyError, match="session_cursor_2026-03-12_fin_ckpt1"):
        repo.increment_checkpoint_calls("session_cursor_2026-03-12_fin_ckpt1")


def test_increment_status_calls_on_finalized_session_raises(tmp_path):
    repo = _repo(tmp_path)
    repo.create(
        session_id="session_cursor_2026-03-12_fin_stat1",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    repo.finalize("session_cursor_2026-03-12_fin_stat1")
    with pytest.raises(KeyError, match="session_cursor_2026-03-12_fin_stat1"):
        repo.increment_status_calls("session_cursor_2026-03-12_fin_stat1")


# ---------------------------------------------------------------------------
# list_stale_active tests
# ---------------------------------------------------------------------------


def _create_stale_session(repo, db_path, session_id, *, age_seconds, n_calls=0):
    """Create a session with created_at in the past, optionally with backdated calls."""
    created_at = time.time() - age_seconds
    from l6e_mcp.store._connection import get_connection
    from l6e_mcp.store._serialization import _policy_to_json

    conn = get_connection(db_path)
    with conn:
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
        from decimal import Decimal

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
            with conn:
                conn.execute(
                    "UPDATE calls SET created_at = ? WHERE call_id = ?",
                    (created_at, call.call_id),
                )


def test_list_stale_active_returns_stale_sessions(tmp_path):
    db = tmp_path / "sessions.db"
    repo = SessionRepository(db)
    _create_stale_session(repo, db, "stale_with_calls", age_seconds=7200, n_calls=3)
    _create_stale_session(repo, db, "stale_zero_calls", age_seconds=7200, n_calls=0)

    result = repo.list_stale_active(max_idle_seconds=3600)
    ids = {s.session_id for s in result}
    assert "stale_with_calls" in ids
    assert "stale_zero_calls" in ids


def test_list_stale_active_reports_call_count(tmp_path):
    db = tmp_path / "sessions.db"
    repo = SessionRepository(db)
    _create_stale_session(repo, db, "has_calls", age_seconds=7200, n_calls=5)
    _create_stale_session(repo, db, "no_calls", age_seconds=7200, n_calls=0)

    result = {s.session_id: s for s in repo.list_stale_active(max_idle_seconds=3600)}
    assert result["has_calls"].call_count == 5
    assert result["no_calls"].call_count == 0


def test_list_stale_active_excludes_recent_sessions(tmp_path):
    db = tmp_path / "sessions.db"
    repo = SessionRepository(db)
    repo.create(
        session_id="recent_session",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    result = repo.list_stale_active(max_idle_seconds=3600)
    assert len(result) == 0


def test_list_stale_active_excludes_finalized_sessions(tmp_path):
    db = tmp_path / "sessions.db"
    repo = SessionRepository(db)
    _create_stale_session(repo, db, "old_finalized", age_seconds=7200, n_calls=2)
    repo.finalize("old_finalized")

    result = repo.list_stale_active(max_idle_seconds=3600)
    assert len(result) == 0


def test_list_stale_active_uses_call_timestamp(tmp_path):
    """A session created long ago but with a recent call should NOT be stale."""
    db = tmp_path / "sessions.db"
    repo = SessionRepository(db)
    _create_stale_session(repo, db, "old_session_recent_call", age_seconds=7200, n_calls=0)

    call_repo = CallRepository(db)
    from decimal import Decimal

    call_repo.create(
        session_id="old_session_recent_call",
        tool_name="implement",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=2000,
        estimated_completion_tokens=400,
        estimated_cost_usd=Decimal("0.01"),
        rerouted=False,
    )

    result = repo.list_stale_active(max_idle_seconds=3600)
    assert len(result) == 0

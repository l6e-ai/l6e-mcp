"""Unit tests for SessionRepository."""
from __future__ import annotations

import pytest
from l6e._types import BudgetMode, PipelinePolicy

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


def test_create_session_with_proxy_mode(tmp_path):
    repo = _repo(tmp_path)
    session = repo.create(
        session_id="session_cursor_2026-03-12_proxy1",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
        proxy_mode=True,
    )
    assert session.proxy_mode is True


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

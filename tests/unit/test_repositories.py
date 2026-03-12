"""Unit tests for LocalRepositories aggregator."""
from __future__ import annotations

from l6e._types import BudgetMode, PipelinePolicy

from l6e_mcp.store.calls import CallRepository
from l6e_mcp.store.diagnostics import DiagnosticsRepository
from l6e_mcp.store.repositories import LocalRepositories
from l6e_mcp.store.sessions import SessionRepository


def _policy() -> PipelinePolicy:
    return PipelinePolicy(budget=1.0, budget_mode=BudgetMode.WARN)


def test_explicit_db_path_exposes_correct_repo_types(tmp_path):
    db = tmp_path / "sessions.db"
    repos = LocalRepositories(db_path=db)
    assert isinstance(repos.sessions, SessionRepository)
    assert isinstance(repos.calls, CallRepository)
    assert isinstance(repos.diagnostics, DiagnosticsRepository)


def test_all_repos_share_same_db_path(tmp_path):
    db = tmp_path / "sessions.db"
    repos = LocalRepositories(db_path=db)
    repos.sessions.create(
        session_id="session_cursor_2026-03-12_repotest1",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    # A second LocalRepositories on the same path should see the session
    repos2 = LocalRepositories(db_path=db)
    session = repos2.sessions.get("session_cursor_2026-03-12_repotest1")
    assert session is not None
    assert session.session_id == "session_cursor_2026-03-12_repotest1"


def test_calls_repo_shares_db_with_sessions_repo(tmp_path):
    db = tmp_path / "sessions.db"
    repos = LocalRepositories(db_path=db)
    repos.sessions.create(
        session_id="session_cursor_2026-03-12_repotest2",
        model="gpt-4o",
        policy=_policy(),
        source="mcp",
        log_path=None,
    )
    call = repos.calls.create(
        session_id="session_cursor_2026-03-12_repotest2",
        tool_name="planning",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=100,
        estimated_completion_tokens=40,
        estimated_cost_usd=0.002,
        rerouted=False,
    )
    listed = repos.calls.list_for_session("session_cursor_2026-03-12_repotest2")
    assert len(listed) == 1
    assert listed[0].call_id == call.call_id


def test_default_db_path_fallback_does_not_raise(monkeypatch, tmp_path):
    # Redirect the default DB location to tmp_path so we don't touch the real DB
    monkeypatch.setenv("L6E_DB_PATH", str(tmp_path / "sessions.db"))
    repos = LocalRepositories(db_path=None)
    assert isinstance(repos.sessions, SessionRepository)
    assert isinstance(repos.calls, CallRepository)
    assert isinstance(repos.diagnostics, DiagnosticsRepository)

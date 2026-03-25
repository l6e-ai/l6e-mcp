"""Unit tests for DB connection helpers and schema migrations."""
from __future__ import annotations

import sqlite3

from l6e_mcp.store._connection import _create_connection, get_connection
from l6e_mcp.store._migrations import (
    LATEST_VERSION,
    _drop_column,
    _ensure_column,
    init_schema,
)
from l6e_mcp.store.sessions import SessionRepository


def test_create_connection_returns_row_factory(tmp_path):
    conn = _create_connection(tmp_path / "test.db")
    conn.execute("CREATE TABLE t (a TEXT)")
    conn.execute("INSERT INTO t VALUES ('hello')")
    row = conn.execute("SELECT * FROM t").fetchone()
    assert row["a"] == "hello"
    conn.close()


def test_init_schema_creates_tables(tmp_path):
    db = tmp_path / "sessions.db"
    conn = get_connection(db)
    with conn:
        init_schema(conn)
    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "sessions" in tables
    assert "calls" in tables
    assert "orphan_callbacks" in tables
    assert "reconciliation_attempts" in tables
    assert "unmatched_usage_events" in tables


def test_init_schema_is_idempotent(tmp_path):
    db = tmp_path / "sessions.db"
    conn = get_connection(db)
    with conn:
        init_schema(conn)
    with conn:
        init_schema(conn)  # must not raise


def test_ensure_column_adds_missing_column(tmp_path):
    db = tmp_path / "test.db"
    conn = get_connection(db)
    with conn:
        conn.execute("CREATE TABLE t (a TEXT)")
        _ensure_column(conn, "t", "b", "INTEGER NOT NULL DEFAULT 0")
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(t)").fetchall()}
    assert "b" in cols


def test_ensure_column_noops_on_existing(tmp_path):
    db = tmp_path / "test.db"
    conn = get_connection(db)
    with conn:
        conn.execute("CREATE TABLE t (a TEXT, b INTEGER)")
        _ensure_column(conn, "t", "b", "INTEGER")
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(t)").fetchall()]
    assert cols.count("b") == 1


def test_drop_column_removes_existing_column(tmp_path):
    db = tmp_path / "test.db"
    conn = get_connection(db)
    with conn:
        conn.execute("CREATE TABLE t (a TEXT, b INTEGER)")
        _drop_column(conn, "t", "b")
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(t)").fetchall()}
    assert "b" not in cols
    assert "a" in cols


def test_drop_column_noops_on_missing_column(tmp_path):
    db = tmp_path / "test.db"
    conn = get_connection(db)
    with conn:
        conn.execute("CREATE TABLE t (a TEXT)")
        _drop_column(conn, "t", "nonexistent")  # must not raise
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(t)").fetchall()}
    assert "a" in cols


def test_init_schema_drops_proxy_mode_from_old_database(tmp_path):
    """Old databases with proxy_mode NOT NULL should be migrated on init_schema."""
    db = tmp_path / "sessions.db"
    # Simulate an old database schema that still has proxy_mode and advanced_fallback_enabled
    conn = get_connection(db)
    with conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                source TEXT NOT NULL,
                log_path TEXT,
                proxy_mode INTEGER NOT NULL,
                advanced_fallback_enabled INTEGER NOT NULL DEFAULT 0,
                accounting_mode TEXT NOT NULL DEFAULT 'estimate_only',
                usage_channel TEXT NOT NULL DEFAULT 'none',
                ask_mode_exact_capable INTEGER NOT NULL DEFAULT 0,
                plan_mode_exact_capable INTEGER NOT NULL DEFAULT 0,
                agent_mode_exact_capable INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL,
                next_call_index INTEGER NOT NULL,
                checkpoint_calls INTEGER NOT NULL DEFAULT 0,
                status_calls INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                ended_at REAL,
                finalized_at REAL
            )
            """
        )

    # Running init_schema should apply the drop migrations
    with conn:
        init_schema(conn)

    # Now sessions can be created without proxy_mode
    from l6e._types import BudgetMode, PipelinePolicy
    repo = SessionRepository(db)
    session = repo.create(
        session_id="session_cursor_2026-03-14_test1",
        model="claude-sonnet-4-6",
        policy=PipelinePolicy(budget=1.0, budget_mode=BudgetMode.WARN),
        source="mcp",
        log_path=None,
    )
    assert session.session_id == "session_cursor_2026-03-14_test1"

    # Verify proxy_mode column is gone
    with conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    assert "proxy_mode" not in cols
    assert "advanced_fallback_enabled" not in cols


def test_create_connection_enables_wal_without_init_schema(tmp_path):
    """WAL mode should be active on every connection from _create_connection,
    even if SessionRepository.init_schema has never been called.
    """
    db = tmp_path / "wal_test.db"
    from l6e._types import BudgetMode, PipelinePolicy
    sessions = SessionRepository(db)
    sessions.create(
        session_id="session_cursor_2026-03-12_wal1",
        model="gpt-4o",
        policy=PipelinePolicy(budget=1.0, budget_mode=BudgetMode.HALT),
        source="mcp",
        log_path=None,
    )

    fresh_db = tmp_path / "wal_fresh.db"
    conn = _create_connection(fresh_db)
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal", f"Expected WAL mode but got: {row[0]}"
    conn.close()


def test_init_schema_sets_version_to_latest(tmp_path):
    db = tmp_path / "sessions.db"
    conn = get_connection(db)
    with conn:
        init_schema(conn)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row is not None
    assert int(row["version"]) == LATEST_VERSION


def test_init_schema_creates_schema_version_table(tmp_path):
    db = tmp_path / "sessions.db"
    conn = get_connection(db)
    with conn:
        init_schema(conn)
    tables = {
        row[0]
        for row in sqlite3.connect(db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "schema_version" in tables


def test_init_schema_on_existing_unversioned_db_migrates_to_latest(tmp_path):
    """An old DB with tables but no schema_version should be upgraded."""
    db = tmp_path / "sessions.db"
    conn = get_connection(db)
    with conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                source TEXT NOT NULL,
                log_path TEXT,
                state TEXT NOT NULL,
                next_call_index INTEGER NOT NULL,
                created_at REAL NOT NULL,
                ended_at REAL,
                finalized_at REAL
            )
            """
        )
    with conn:
        init_schema(conn)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert int(row["version"]) == LATEST_VERSION
    cols = {
        str(r["name"])
        for r in conn.execute("PRAGMA table_info(sessions)").fetchall()
    }
    assert "accounting_mode" in cols
    assert "start_summary" in cols


def test_init_schema_skips_already_applied_migrations(tmp_path):
    """When version is already at LATEST, no migration functions should run."""
    db = tmp_path / "sessions.db"
    conn = get_connection(db)
    with conn:
        init_schema(conn)
    row_before = conn.execute("SELECT applied_at FROM schema_version").fetchone()
    with conn:
        init_schema(conn)
    row_after = conn.execute("SELECT applied_at FROM schema_version").fetchone()
    assert row_before["applied_at"] == row_after["applied_at"]


def test_create_connection_long_path_does_not_leak_symlink_dirs(tmp_path, monkeypatch):
    """When a db path exceeds the macOS socket path limit and a symlink is created,
    repeated calls to _create_connection for the same db must reuse the same symlink
    directory rather than creating a new one on each call.
    """
    import tempfile
    from pathlib import Path

    from l6e_mcp.store import _connection as conn_mod

    # Force the path-too-long branch by lowering the limit
    monkeypatch.setattr(conn_mod, "_MACOS_SOCKET_PATH_LIMIT", 5)

    db = tmp_path / "sessions.db"
    l6e_db_dir = Path(tempfile.gettempdir()).resolve() / "l6e_db"

    # Count subdirectories before
    before = set(l6e_db_dir.glob("*")) if l6e_db_dir.exists() else set()

    c1 = conn_mod._create_connection(db)
    c1.close()
    c2 = conn_mod._create_connection(db)
    c2.close()
    c3 = conn_mod._create_connection(db)
    c3.close()

    after = set(l6e_db_dir.glob("*"))
    new_dirs = after - before
    assert len(new_dirs) == 1, (
        f"Expected exactly 1 new symlink dir after 3 calls, got {len(new_dirs)}: {new_dirs}"
    )

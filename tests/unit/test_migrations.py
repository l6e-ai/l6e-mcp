"""Unit tests for DB connection helpers and schema migrations."""
from __future__ import annotations

import sqlite3

from l6e_mcp.store._connection import make_connection
from l6e_mcp.store._migrations import _ensure_column, init_schema
from l6e_mcp.store.sessions import SessionRepository


def test_make_connection_returns_row_factory(tmp_path):
    conn = make_connection(tmp_path / "test.db")
    conn.execute("CREATE TABLE t (a TEXT)")
    conn.execute("INSERT INTO t VALUES ('hello')")
    row = conn.execute("SELECT * FROM t").fetchone()
    assert row["a"] == "hello"
    conn.close()


def test_init_schema_creates_tables(tmp_path):
    db = tmp_path / "sessions.db"
    with make_connection(db) as conn:
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
    with make_connection(db) as conn:
        init_schema(conn)
    with make_connection(db) as conn:
        init_schema(conn)  # must not raise


def test_ensure_column_adds_missing_column(tmp_path):
    db = tmp_path / "test.db"
    with make_connection(db) as conn:
        conn.execute("CREATE TABLE t (a TEXT)")
        _ensure_column(conn, "t", "b", "INTEGER NOT NULL DEFAULT 0")
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(t)").fetchall()}
    assert "b" in cols


def test_ensure_column_noops_on_existing(tmp_path):
    db = tmp_path / "test.db"
    with make_connection(db) as conn:
        conn.execute("CREATE TABLE t (a TEXT, b INTEGER)")
        _ensure_column(conn, "t", "b", "INTEGER")
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(t)").fetchall()]
    assert cols.count("b") == 1


def test_make_connection_enables_wal_without_init_schema(tmp_path):
    """WAL mode should be active on every connection from make_connection,
    even if SessionRepository.init_schema has never been called.
    Opening a CallRepository alone (which does NOT call init_schema) must
    still result in WAL mode on connections it opens.
    """
    db = tmp_path / "wal_test.db"
    # Initialize schema via SessionRepository (creates tables)
    from l6e._types import BudgetMode, PipelinePolicy
    sessions = SessionRepository(db)
    sessions.create(
        session_id="session_cursor_2026-03-12_wal1",
        model="gpt-4o",
        policy=PipelinePolicy(budget=1.0, budget_mode=BudgetMode.HALT),
        source="mcp",
        log_path=None,
    )

    # Open a raw make_connection (simulating what CallRepository does) on a DIFFERENT
    # fresh db — this never goes through init_schema. WAL pragma must still be set.
    fresh_db = tmp_path / "wal_fresh.db"
    conn = make_connection(fresh_db)
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal", f"Expected WAL mode but got: {row[0]}"
    conn.close()


def test_make_connection_long_path_does_not_leak_symlink_dirs(tmp_path, monkeypatch):
    """When a db path exceeds the macOS socket path limit and a symlink is created,
    repeated calls to make_connection for the same db must reuse the same symlink
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

    c1 = conn_mod.make_connection(db)
    c1.close()
    c2 = conn_mod.make_connection(db)
    c2.close()
    c3 = conn_mod.make_connection(db)
    c3.close()

    after = set(l6e_db_dir.glob("*"))
    new_dirs = after - before
    assert len(new_dirs) == 1, (
        f"Expected exactly 1 new symlink dir after 3 calls, got {len(new_dirs)}: {new_dirs}"
    )

"""Unit tests for DB connection helpers and schema migrations."""
from __future__ import annotations

import sqlite3

import pytest

from l6e_mcp.store._connection import make_connection
from l6e_mcp.store._migrations import init_schema, _ensure_column


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

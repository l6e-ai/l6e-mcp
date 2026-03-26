"""DB path resolution and connection factory with thread-local caching."""
from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

_DEFAULT_DB_PATH = Path.home() / ".l6e" / "sessions.db"

# macOS enforces a 104-character limit on Unix domain socket paths. SQLite in
# WAL mode creates a lock socket next to the database file, so any db path
# longer than ~100 chars fails with "unable to open database file".
_MACOS_SOCKET_PATH_LIMIT = 100

_thread_local = threading.local()


def _db_path() -> Path:
    raw = os.environ.get("L6E_SESSION_DB_PATH")
    return Path(raw) if raw else _DEFAULT_DB_PATH


def _create_connection(path: Path) -> sqlite3.Connection:
    """Open a fresh SQLite connection with WAL mode and row-factory configured."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(str(path)) > _MACOS_SOCKET_PATH_LIMIT:
        key = hashlib.md5(str(path).encode()).hexdigest()[:8]
        short_dir = Path(tempfile.gettempdir()).resolve() / "l6e_db" / key
        short_dir.mkdir(parents=True, exist_ok=True)
        short_path = short_dir / "s.db"
        if not short_path.exists():
            short_path.symlink_to(path)
        path = short_path
    conn = sqlite3.connect(path, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_connection(path: Path) -> sqlite3.Connection:
    """Return a cached per-thread connection for *path*, creating one if needed.

    Each thread gets its own long-lived connection per resolved DB path.
    SQLite WAL mode handles concurrent readers across threads natively.
    """
    resolved = str(path.resolve())
    cache: dict[str, sqlite3.Connection] | None = getattr(
        _thread_local, "connections", None
    )
    if cache is None:
        _thread_local.connections = cache = {}
    conn = cache.get(resolved)
    if conn is None:
        conn = _create_connection(path)
        cache[resolved] = conn
    return conn


def close_thread_connections() -> None:
    """Close all cached connections on the calling thread.

    Call this before changing L6E_SESSION_DB_PATH (e.g. in test fixtures)
    so the next access creates a connection to the new path.
    """
    cache: dict[str, sqlite3.Connection] | None = getattr(
        _thread_local, "connections", None
    )
    if cache is None:
        return
    for conn in cache.values():
        with contextlib.suppress(Exception):
            conn.close()
    cache.clear()

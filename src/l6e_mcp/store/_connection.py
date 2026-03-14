"""DB path resolution and connection factory."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

_DEFAULT_DB_PATH = Path.home() / ".l6e" / "sessions.db"

# macOS enforces a 104-character limit on Unix domain socket paths. SQLite in
# WAL mode creates a lock socket next to the database file, so any db path
# longer than ~100 chars fails with "unable to open database file".
_MACOS_SOCKET_PATH_LIMIT = 100


def _db_path() -> Path:
    raw = os.environ.get("L6E_SESSION_DB_PATH")
    return Path(raw) if raw else _DEFAULT_DB_PATH


def make_connection(path: Path) -> sqlite3.Connection:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(str(path)) > _MACOS_SOCKET_PATH_LIMIT:
        # Path too long for SQLite WAL mode on macOS. Symlink the db file into
        # a short temp directory so SQLite's socket path fits within the limit.
        # Use a deterministic key derived from the db path so the same directory
        # is reused across calls (avoids leaking a new dir per connection).
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

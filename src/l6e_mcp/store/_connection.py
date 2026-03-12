"""DB path resolution and connection factory."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import uuid
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
        short_dir = Path(tempfile.gettempdir()).resolve() / "l6e_db" / uuid.uuid4().hex[:8]
        short_dir.mkdir(parents=True, exist_ok=True)
        short_path = short_dir / "s.db"
        if not short_path.exists():
            short_path.symlink_to(path)
        path = short_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

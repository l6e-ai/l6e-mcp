"""DB path resolution and connection factory."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_DEFAULT_DB_PATH = Path.home() / ".l6e" / "sessions.db"


def _db_path() -> Path:
    raw = os.environ.get("L6E_SESSION_DB_PATH")
    return Path(raw) if raw else _DEFAULT_DB_PATH


def make_connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

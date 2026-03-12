"""Repository aggregator for service-layer boundaries."""
from __future__ import annotations

from pathlib import Path

from l6e_mcp.store._connection import _db_path
from l6e_mcp.store.calls import CallRepository
from l6e_mcp.store.diagnostics import DiagnosticsRepository
from l6e_mcp.store.sessions import SessionRepository


class LocalRepositories:
    """Wires the three focused repositories over a shared DB path."""

    def __init__(self, db_path: Path | None = None) -> None:
        path = db_path or _db_path()
        self.sessions = SessionRepository(path)
        self.calls = CallRepository(path)
        self.diagnostics = DiagnosticsRepository(path)

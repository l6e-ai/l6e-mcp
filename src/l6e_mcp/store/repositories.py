"""Repository wrapper around the local SQLite session store."""
from __future__ import annotations

from l6e_mcp.session_store import LocalSessionStore


class LocalRepositories:
    """Thin repository aggregator for service-layer boundaries."""

    def __init__(self, store: LocalSessionStore | None = None) -> None:
        self.store = store or LocalSessionStore()

    @property
    def sessions(self) -> LocalSessionStore:
        return self.store

    @property
    def calls(self) -> LocalSessionStore:
        return self.store

    @property
    def diagnostics(self) -> LocalSessionStore:
        return self.store

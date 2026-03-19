"""In-memory calibration factor cache with TTL expiration.

Populated as a side effect of server-side authorize calls. Consumed by
the check_only path of l6e_authorize_call to apply calibrated cost
projections without a server round-trip.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

_DEFAULT_TTL_SECONDS = 300  # 5 minutes


@dataclass(frozen=True)
class CachedCalibration:
    factor: float
    source: str
    confidence: str | None
    factor_range: dict | None
    fetched_at: float


class CalibrationCache:
    """Thread-safe, TTL-based cache keyed by session_id."""

    def __init__(self, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, CachedCalibration] = {}
        self._lock = threading.Lock()

    def update(
        self,
        session_id: str,
        *,
        factor: float,
        source: str,
        confidence: str | None = None,
        factor_range: dict | None = None,
    ) -> None:
        entry = CachedCalibration(
            factor=factor,
            source=source,
            confidence=confidence,
            factor_range=factor_range,
            fetched_at=time.time(),
        )
        with self._lock:
            self._entries[session_id] = entry

    def get(self, session_id: str) -> CachedCalibration | None:
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                return None
            if time.time() - entry.fetched_at > self._ttl:
                del self._entries[session_id]
                return None
            return entry

    def clear(self, session_id: str | None = None) -> None:
        with self._lock:
            if session_id is not None:
                self._entries.pop(session_id, None)
            else:
                self._entries.clear()

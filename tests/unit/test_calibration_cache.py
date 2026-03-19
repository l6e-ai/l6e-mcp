"""Unit tests for CalibrationCache TTL, thread safety, and cleanup."""
from __future__ import annotations

import threading
import time

from l6e_mcp.core.calibration_cache import CalibrationCache


def test_get_returns_none_for_unknown_session():
    cache = CalibrationCache()
    assert cache.get("nonexistent") is None


def test_update_then_get_returns_entry():
    cache = CalibrationCache()
    cache.update("s1", factor=3.5, source="personal", confidence="high")
    entry = cache.get("s1")
    assert entry is not None
    assert entry.factor == 3.5
    assert entry.source == "personal"
    assert entry.confidence == "high"
    assert entry.factor_range is None


def test_ttl_expiration():
    cache = CalibrationCache(ttl_seconds=0.05)
    cache.update("s1", factor=2.0, source="test")
    assert cache.get("s1") is not None
    time.sleep(0.06)
    assert cache.get("s1") is None


def test_clear_specific_session():
    cache = CalibrationCache()
    cache.update("s1", factor=1.0, source="a")
    cache.update("s2", factor=2.0, source="b")
    cache.clear("s1")
    assert cache.get("s1") is None
    assert cache.get("s2") is not None


def test_clear_all():
    cache = CalibrationCache()
    cache.update("s1", factor=1.0, source="a")
    cache.update("s2", factor=2.0, source="b")
    cache.clear()
    assert cache.get("s1") is None
    assert cache.get("s2") is None


def test_update_overwrites_existing():
    cache = CalibrationCache()
    cache.update("s1", factor=1.0, source="old")
    cache.update("s1", factor=5.0, source="new", confidence="medium")
    entry = cache.get("s1")
    assert entry is not None
    assert entry.factor == 5.0
    assert entry.source == "new"


def test_thread_safety():
    """Concurrent updates and reads must not corrupt state."""
    cache = CalibrationCache()
    errors: list[Exception] = []

    def writer(session_id: str, factor: float):
        try:
            for _ in range(100):
                cache.update(session_id, factor=factor, source="thread")
        except Exception as e:
            errors.append(e)

    def reader(session_id: str):
        try:
            for _ in range(100):
                cache.get(session_id)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=writer, args=("s1", 1.0)),
        threading.Thread(target=writer, args=("s1", 2.0)),
        threading.Thread(target=reader, args=("s1",)),
        threading.Thread(target=reader, args=("s2",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0


def test_factor_range_stored():
    cache = CalibrationCache()
    fr = {"p25": 1.5, "p75": 8.0, "effective_sample_size": 50.0}
    cache.update("s1", factor=3.0, source="personal", factor_range=fr)
    entry = cache.get("s1")
    assert entry is not None
    assert entry.factor_range == fr

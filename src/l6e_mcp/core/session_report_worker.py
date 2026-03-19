"""Background worker for real-time session report delivery.

POSTs session reports to the hosted edge as soon as they are enqueued,
using the same daemon-thread + SimpleQueue pattern as StatusTelemetryWorker.

The file-based outbox (written synchronously in l6e_run_end) is the durable
fallback; on successful POST this worker deletes the outbox file so the
next l6e_run_start drain doesn't re-send it.
"""
from __future__ import annotations

import logging
import queue
import threading

from l6e_mcp import outbox as _outbox

_logger = logging.getLogger(__name__)


class SessionReportWorker:
    """Best-effort background poster for session report payloads."""

    def __init__(self, api_key: str, endpoint: str) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._queue: queue.SimpleQueue[dict | None] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def enqueue(self, payload: dict) -> None:
        self._ensure_thread()
        self._queue.put(payload)

    def shutdown(self, timeout: float = 5.0) -> None:
        self._queue.put(None)  # sentinel
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._drain_loop, daemon=True,
            )
            self._thread.start()

    def _drain_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            try:
                if _outbox.try_send(item, self._api_key, self._endpoint):
                    _outbox.remove(item.get("session_id", ""))
            except Exception:
                _logger.debug("session_report_send_failed", exc_info=True)

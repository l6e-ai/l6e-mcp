"""Fire-and-forget status telemetry worker.

Sends check_only pressure-check data to the hosted edge
``POST /v1/status-telemetry`` endpoint for observability. Uses a daemon
thread and a SimpleQueue — enqueue is non-blocking and never raises.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import asdict, dataclass

import httpx

_logger = logging.getLogger(__name__)

_POST_TIMEOUT = 2.0


@dataclass(frozen=True)
class StatusTelemetryPayload:
    session_id: str
    model: str
    estimated_prompt_tokens: int | None
    estimated_completion_tokens: int | None
    raw_projected_cost_usd: float | None
    calibrated_projected_cost_usd: float | None
    calibration_factor: float | None
    calibration_source: str | None
    budget_usd: float
    spent_usd: float
    budget_pressure: str


class StatusTelemetryWorker:
    """Best-effort background poster for status telemetry payloads."""

    def __init__(self, api_key: str, endpoint: str) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._queue: queue.SimpleQueue[StatusTelemetryPayload | None] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def enqueue(self, payload: StatusTelemetryPayload) -> None:
        self._ensure_thread()
        self._queue.put(payload)

    def shutdown(self, timeout: float = 3.0) -> None:
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
        url = f"{self._endpoint}/v1/status-telemetry"
        while True:
            item = self._queue.get()
            if item is None:
                break
            try:
                httpx.post(
                    url,
                    json=asdict(item),
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=_POST_TIMEOUT,
                )
            except Exception:
                _logger.debug("status_telemetry_post_failed", exc_info=True)

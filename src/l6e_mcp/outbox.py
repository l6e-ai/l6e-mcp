"""Local file-based outbox for reliable cloud sync delivery.

Reports are written as JSON files to ~/.l6e/outbox/{session_id}.json.
On the next l6e_run_start, the outbox is drained in a background thread.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_OUTBOX_DIR = Path.home() / ".l6e" / "outbox"
_STALE_SECONDS = 7 * 24 * 3600  # 7 days


def _outbox_dir() -> Path:
    raw = os.environ.get("L6E_OUTBOX_DIR")
    return Path(raw) if raw else _OUTBOX_DIR


def enqueue(payload: dict) -> Path:
    """Write a session report payload to the outbox directory. Returns the file path."""
    outbox = _outbox_dir()
    outbox.mkdir(parents=True, exist_ok=True)
    session_id = payload.get("session_id", "unknown")
    path = outbox / f"{session_id}.json"
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    return path


def try_send(
    payload: dict,
    api_key: str,
    endpoint: str,
    timeout: float = 3.0,
) -> bool:
    """Attempt a synchronous POST. Returns True on success (2xx or duplicate)."""
    url = f"{endpoint}/v1/session-reports"
    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        if resp.status_code in (200, 201):
            return True
        if resp.status_code == 409:
            return True
        logger.warning(
            "cloud_sync_rejected",
            extra={"status": resp.status_code, "body": resp.text[:200]},
        )
        return False
    except Exception:
        logger.debug("cloud_sync_send_failed", exc_info=True)
        return False


def drain(api_key: str, endpoint: str) -> None:
    """Drain all pending outbox files. Best-effort: never raises."""
    outbox = _outbox_dir()
    if not outbox.is_dir():
        return
    try:
        files = sorted(outbox.glob("*.json"))
    except OSError:
        return

    now = time.time()
    for path in files:
        try:
            age = now - path.stat().st_mtime
            if age > _STALE_SECONDS:
                logger.debug("outbox_stale_discard", extra={"path": str(path)})
                path.unlink(missing_ok=True)
                continue

            payload = json.loads(path.read_text(encoding="utf-8"))
            if try_send(payload, api_key, endpoint, timeout=5.0):
                path.unlink(missing_ok=True)
        except Exception:
            logger.debug("outbox_drain_item_failed", exc_info=True)

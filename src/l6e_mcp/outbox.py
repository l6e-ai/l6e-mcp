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
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from l6e_mcp.session_store import LocalSessionStore

logger = logging.getLogger(__name__)

_OUTBOX_DIR = Path.home() / ".l6e" / "outbox"
_STALE_SECONDS = 7 * 24 * 3600  # 7 days
_MAX_RECOVERY_AGE_SECONDS = 30 * 24 * 3600  # 30 days


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


def drain(
    api_key: str,
    endpoint: str,
    deadline: float | None = None,
) -> None:
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
        if deadline is not None and time.time() >= deadline:
            logger.debug("outbox_drain_deadline_reached")
            break
        try:
            age = now - path.stat().st_mtime
            if age > _STALE_SECONDS:
                logger.debug("outbox_stale_discard", extra={"path": str(path)})
                path.unlink(missing_ok=True)
                continue

            payload = json.loads(path.read_text(encoding="utf-8"))
            if try_send(payload, api_key, endpoint):
                path.unlink(missing_ok=True)
        except Exception:
            logger.debug("outbox_drain_item_failed", exc_info=True)


def recover_stale_sessions(
    api_key: str,
    endpoint: str,
    max_idle_seconds: float = 3600,
    store: LocalSessionStore | None = None,
    deadline: float | None = None,
) -> None:
    """Finalize stale active sessions and sync recoverable ones to the hosted edge.

    Called after drain in the l6e_run_start background thread. Best-effort: never raises.

    Categories:
    - Zero calls: finalize locally, skip sync (noise).
    - Age > 30 days: finalize locally, skip sync (too old).
    - Has calls, within 30 days: finalize, build report, sync or enqueue.
    """
    from l6e._log import LocalRunLog

    from l6e_mcp.session_store import LocalSessionStore
    from l6e_mcp.store.summary import build_session_report, session_run_summary

    try:
        if store is None:
            store = LocalSessionStore()
        stale = store.list_stale_active(max_idle_seconds)
    except Exception:
        logger.debug("recover_stale_list_failed", exc_info=True)
        return

    if not stale:
        return

    now = time.time()
    for info in stale:
        if deadline is not None and time.time() >= deadline:
            logger.debug("recover_stale_deadline_reached")
            break
        try:
            if info.call_count == 0:
                store.finalize_session(info.session_id)
                logger.debug(
                    "stale_session_dropped_zero_calls",
                    extra={"session_id": info.session_id},
                )
                continue

            if now - info.session_created_at > _MAX_RECOVERY_AGE_SECONDS:
                store.finalize_session(info.session_id)
                logger.debug(
                    "stale_session_dropped_too_old",
                    extra={"session_id": info.session_id},
                )
                continue

            session = store.get_session(info.session_id)
            if session is None or session.state != "active":
                continue

            calls = store.list_calls_for_session(info.session_id)
            summary = session_run_summary(session, calls)

            store.finalize_session(info.session_id)

            log = (
                LocalRunLog(path=Path(session.log_path))
                if session.log_path is not None
                else LocalRunLog()
            )
            log.append(summary)

            payload = build_session_report(session, summary, calls)
            if not try_send(payload, api_key, endpoint):
                enqueue(payload)
                logger.debug(
                    "stale_session_enqueued",
                    extra={"session_id": info.session_id},
                )
            else:
                logger.debug(
                    "stale_session_recovered",
                    extra={"session_id": info.session_id},
                )
        except Exception:
            logger.debug(
                "stale_session_recovery_failed",
                exc_info=True,
                extra={"session_id": info.session_id},
            )

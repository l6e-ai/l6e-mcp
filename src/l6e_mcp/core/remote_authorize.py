"""Async HTTP client for server-side authorize with calibration factors.

When cloud sync is enabled and an API key is set, the MCP client calls
the hosted-edge ``POST /v1/authorize`` endpoint to get calibrated budget
decisions. The server applies per-user, per-model cost multipliers derived
from billing reconciliation.

This module is best-effort: it returns ``None`` on any failure (network,
timeout, non-200, JSON parse) so the caller can fall back to local auth.

The shared ``httpx.AsyncClient`` reuses TCP connections across calls,
eliminating per-request DNS and TLS overhead.
"""
from __future__ import annotations

import atexit
import logging
import threading

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 1.0

_client: httpx.AsyncClient | None = None
_client_lock = threading.Lock()


def _get_async_client(timeout: float = _DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    global _client  # noqa: PLW0603
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        _client = httpx.AsyncClient(timeout=timeout)
        return _client


def _shutdown_client() -> None:
    global _client  # noqa: PLW0603
    with _client_lock:
        to_close = _client
        _client = None
    if to_close is not None:
        try:
            # Best-effort sync close; the event loop may already be torn down.
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(to_close.aclose())
            except RuntimeError:
                asyncio.run(to_close.aclose())
        except Exception:
            pass


atexit.register(_shutdown_client)


def _reset_client() -> None:
    """Clear the cached client. Used by tests for isolation."""
    global _client  # noqa: PLW0603
    with _client_lock:
        _client = None


async def try_remote_authorize(
    *,
    api_key: str,
    endpoint: str,
    session_id: str,
    model: str,
    tool_name: str,
    estimated_cost_usd: float,
    budget_usd: float,
    spent_usd: float,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict | None:
    """POST to server-side authorize. Returns response dict or None on failure."""
    url = f"{endpoint}/v1/authorize"
    client = _get_async_client(timeout)
    try:
        resp = await client.post(
            url,
            json={
                "session_id": session_id,
                "model": model,
                "tool_name": tool_name,
                "estimated_cost_usd": estimated_cost_usd,
                "budget_usd": budget_usd,
                "spent_usd": spent_usd,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code != 200:
            logger.debug(
                "remote_authorize_rejected",
                extra={"status": resp.status_code, "body": resp.text[:200]},
            )
            return None
        return resp.json()
    except httpx.TimeoutException:
        logger.debug("remote_authorize_timeout", extra={"url": url})
        return None
    except Exception:
        logger.debug("remote_authorize_failed", exc_info=True)
        return None

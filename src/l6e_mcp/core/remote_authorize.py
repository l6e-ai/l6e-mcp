"""Thin HTTP client for server-side authorize with calibration factors.

When cloud sync is enabled and an API key is set, the MCP client calls
the hosted-edge ``POST /v1/authorize`` endpoint to get calibrated budget
decisions. The server applies per-user, per-model cost multipliers derived
from billing reconciliation.

This module is best-effort: it returns ``None`` on any failure (network,
timeout, non-200, JSON parse) so the caller can fall back to local auth.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 2.0


def try_remote_authorize(
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
    try:
        resp = httpx.post(
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
            timeout=timeout,
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

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
    session_client: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    # --- Additive schema (L6E-37). All optional; forwarded only when set.
    # Token-precision additions (any tier may supply):
    estimated_prompt_tokens: int | None = None,
    estimated_completion_tokens: int | None = None,
    # Margin-tier identity / policy additions:
    user_id: str | None = None,
    tenant_id: str | None = None,
    cohort_hint: str | None = None,
    request_embedding: list[float] | None = None,
    latency_deadline_ms: int | None = None,
    quality_floor: str | None = None,
    # --- Gate-policy additions (L6E-40). All optional; forwarded only when
    # set. These give the server enough inputs to produce decisions
    # identical to the in-process ``ConstraintGate``. MCP doesn't set
    # them today — they're plumbed so SDK callers and future stage-aware
    # MCP tools can opt into the full core ladder.
    stage: str | None = None,
    prompt_complexity: str | None = None,
    budget_mode: str | None = None,
    reroute_threshold: float | None = None,
    stage_overrides: dict[str, str] | None = None,
    stage_routing: dict[str, str] | None = None,
) -> dict | None:
    """POST to server-side authorize. Returns response dict or None on failure.

    The Margin-tier kwargs (``user_id``, ``tenant_id``, ``cohort_hint``,
    ``request_embedding``, ``latency_deadline_ms``, ``quality_floor``) are
    all optional and default to ``None``. MCP callers should leave them
    unset; the SDK/gateway/framework adapters populate them when Margin
    is active. Unset fields are not serialized, preserving the wire shape
    for existing MCP deployments.

    When ``latency_deadline_ms`` is supplied the effective HTTP timeout is
    the tighter of ``timeout`` and ``latency_deadline_ms / 1000``. This is
    the iron-rule "cloud slow → treat as down" path: Margin callers that
    declare a latency budget get a timeout honored locally too, so a
    200-at-10s never arrives after the customer has already had to
    degrade.
    """
    effective_timeout = timeout
    if latency_deadline_ms is not None and latency_deadline_ms > 0:
        deadline_seconds = latency_deadline_ms / 1000.0
        effective_timeout = min(timeout, deadline_seconds)
    url = f"{endpoint}/v1/authorize"
    # ``_get_async_client`` creates the shared client once; its default
    # timeout is fixed at first-call time. We pass ``timeout=`` per-call
    # so ``latency_deadline_ms`` can tighten it dynamically.
    http_client = _get_async_client(timeout)
    body: dict = {
        "session_id": session_id,
        "model": model,
        "tool_name": tool_name,
        "estimated_cost_usd": estimated_cost_usd,
        "budget_usd": budget_usd,
        "spent_usd": spent_usd,
    }
    if session_client:
        body["client"] = session_client
    if estimated_prompt_tokens is not None:
        body["estimated_prompt_tokens"] = estimated_prompt_tokens
    if estimated_completion_tokens is not None:
        body["estimated_completion_tokens"] = estimated_completion_tokens
    if user_id is not None:
        body["user_id"] = user_id
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
    if cohort_hint is not None:
        body["cohort_hint"] = cohort_hint
    if request_embedding is not None:
        body["request_embedding"] = request_embedding
    if latency_deadline_ms is not None:
        body["latency_deadline_ms"] = latency_deadline_ms
    if quality_floor is not None:
        body["quality_floor"] = quality_floor
    if stage is not None:
        body["stage"] = stage
    if prompt_complexity is not None:
        body["prompt_complexity"] = prompt_complexity
    if budget_mode is not None:
        body["budget_mode"] = budget_mode
    if reroute_threshold is not None:
        body["reroute_threshold"] = reroute_threshold
    if stage_overrides is not None:
        body["stage_overrides"] = stage_overrides
    if stage_routing is not None:
        body["stage_routing"] = stage_routing
    try:
        resp = await http_client.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=effective_timeout,
        )
        if resp.status_code != 200:
            logger.warning(
                "remote_authorize_rejected",
                extra={"status": resp.status_code, "body": resp.text[:200]},
            )
            return None
        try:
            return resp.json()
        except Exception:
            # Malformed JSON from the gateway is just as dangerous as a
            # 500. Fail-open: caller falls back to local auth.
            logger.warning("remote_authorize_bad_json", exc_info=True)
            return None
    except httpx.TimeoutException:
        logger.warning(
            "remote_authorize_timeout",
            extra={
                "url": url,
                "timeout": effective_timeout,
                "effective_timeout": effective_timeout,
            },
        )
        return None
    except Exception:
        logger.warning("remote_authorize_failed", exc_info=True)
        return None

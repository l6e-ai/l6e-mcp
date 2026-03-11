"""l6e LiteLLM callback server.

Receives LiteLLM proxy success callbacks and forwards actual token counts to
l6e_reconcile_call via the MCP tool. Run alongside the LiteLLM proxy to get
accurate per-call cost tracking instead of fixed-token estimates.

Usage:
    uvicorn l6e_mcp.litellm_proxy.callback_server:app --port 9000

Or via the helper CLI entry point:
    python -m l6e_mcp.litellm_proxy.callback_server
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastmcp import Client

from l6e_mcp.integrations.litellm.webhook_ingress import (
    extract_model,
    extract_usage,
    normalize_payload,
)
from l6e_mcp.transport.http.unmatched_usage import persist_unmatched_usage

logger = logging.getLogger(__name__)

app = FastAPI(
    title="l6e LiteLLM callback server",
    description=(
        "Receives LiteLLM success callbacks and forwards actual token counts "
        "to l6e_reconcile_call so the MCP budget session reflects real API spend."
    ),
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment variables)
# ---------------------------------------------------------------------------

# Where the MCP server writes the active session_id when proxy_mode=True.
ACTIVE_SESSION_FILE = Path(
    os.environ.get("L6E_ACTIVE_SESSION_FILE", Path.home() / ".l6e" / "active_session")
)
ACTIVE_CALL_FILE = Path(
    os.environ.get("L6E_ACTIVE_CALL_FILE", Path.home() / ".l6e" / "active_call")
)

# The MCP server's HTTP base URL. The FastMCP HTTP app exposes the MCP
# transport at `/mcp`, which the callback server uses via `fastmcp.Client`.
# Default port: 8000.
MCP_HTTP_URL = os.environ.get("L6E_MCP_HTTP_URL", "http://127.0.0.1:8000")


# ---------------------------------------------------------------------------
# Active session helpers
# ---------------------------------------------------------------------------

def _read_active_session() -> str | None:
    """Return the session_id from the active session file, or None if absent."""
    try:
        text = ACTIVE_SESSION_FILE.read_text().strip()
        return text if text else None
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not read active session file %s: %s", ACTIVE_SESSION_FILE, exc)
        return None


def _read_active_call() -> str | None:
    """Return the current call_id from the active call file, or None if absent."""
    try:
        text = ACTIVE_CALL_FILE.read_text().strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not read active call file %s: %s", ACTIVE_CALL_FILE, exc)
        return None

    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    call_id = payload.get("call_id")
    return str(call_id) if call_id else None


def _extract_request_id(payload: dict[str, Any]) -> str | None:
    raw = payload.get("id")
    return str(raw) if raw else None


def _extract_trace_id(payload: dict[str, Any]) -> str | None:
    raw = payload.get("trace_id")
    return str(raw) if raw else None


def _extract_request_tag_value(payload: dict[str, Any], key: str) -> str | None:
    raw_tags = payload.get("request_tags")
    if not isinstance(raw_tags, list):
        return None
    for tag in raw_tags:
        if not isinstance(tag, str):
            continue
        if tag.startswith(f"{key}:"):
            return tag.split(":", 1)[1] or None
        if tag.startswith(f"{key}="):
            return tag.split("=", 1)[1] or None
    return None


def _extract_call_id_from_request_tags(payload: dict[str, Any]) -> str | None:
    return _extract_request_tag_value(payload, "l6e_call_id")


def _extract_call_correlation(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract `(call_id, source)` from callback payload metadata when present."""
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        spend_logs = metadata.get("spend_logs_metadata")
        if isinstance(spend_logs, dict):
            call_id = spend_logs.get("l6e_call_id")
            if call_id:
                return str(call_id), "spend_logs_metadata"
        requester = metadata.get("requester_metadata")
        if isinstance(requester, dict):
            call_id = requester.get("l6e_call_id")
            if call_id:
                return str(call_id), "requester_metadata"
        call_id = metadata.get("l6e_call_id")
        if call_id:
            return str(call_id), "metadata"
    tagged_call_id = _extract_call_id_from_request_tags(payload)
    if tagged_call_id:
        return tagged_call_id, "request_tags"
    return None, None


def _extract_actor_correlation(payload: dict[str, Any]) -> dict[str, str | None]:
    metadata = payload.get("metadata")
    spend_logs: dict[str, Any] | None = None
    if isinstance(metadata, dict):
        candidate = metadata.get("spend_logs_metadata")
        if isinstance(candidate, dict):
            spend_logs = candidate
    actor_type = None
    actor_id = None
    actor_name = None
    parent_call_id = None
    if spend_logs is not None:
        actor_type = spend_logs.get("l6e_actor_type")
        actor_id = spend_logs.get("l6e_actor_id")
        actor_name = spend_logs.get("l6e_actor_name")
        parent_call_id = spend_logs.get("l6e_parent_call_id")
    actor_type = actor_type or _extract_request_tag_value(payload, "l6e_actor_type")
    actor_id = actor_id or _extract_request_tag_value(payload, "l6e_actor_id")
    actor_name = actor_name or _extract_request_tag_value(payload, "l6e_actor_name")
    parent_call_id = parent_call_id or _extract_request_tag_value(payload, "l6e_parent_call_id")
    return {
        "actor_type": str(actor_type) if actor_type else None,
        "actor_id": str(actor_id) if actor_id else None,
        "actor_name": str(actor_name) if actor_name else None,
        "parent_call_id": str(parent_call_id) if parent_call_id else None,
    }


def _callback_summary(payload: dict[str, Any]) -> dict[str, Any]:
    correlated_call_id, correlation_source = _extract_call_correlation(payload)
    usage = _extract_usage(payload)
    actor_correlation = _extract_actor_correlation(payload)
    return {
        "request_id": _extract_request_id(payload),
        "trace_id": _extract_trace_id(payload),
        "model": _extract_model(payload),
        "has_usage": usage is not None,
        "correlated_call_id": correlated_call_id,
        "correlation_source": correlation_source,
        "request_tag_count": len(payload.get("request_tags", []))
        if isinstance(payload.get("request_tags"), list)
        else 0,
        "metadata_keys": sorted(payload.get("metadata", {}).keys())
        if isinstance(payload.get("metadata"), dict)
        else [],
        **actor_correlation,
    }


# ---------------------------------------------------------------------------
# LiteLLM callback payload helpers
# ---------------------------------------------------------------------------

def _extract_usage(payload: dict[str, Any]) -> tuple[int, int] | None:
    """Backward-compatible wrapper around shared webhook usage extraction."""
    return extract_usage(payload)


def _extract_model(payload: dict[str, Any]) -> str:
    """Backward-compatible wrapper around shared webhook model extraction."""
    return extract_model(payload)


def _normalize_payload(payload: Any) -> dict[str, Any] | None:
    """Backward-compatible wrapper around shared payload normalization."""
    return normalize_payload(payload)


# ---------------------------------------------------------------------------
# MCP reconciliation caller
# ---------------------------------------------------------------------------


def _mcp_transport_url() -> str:
    """Return the FastMCP HTTP transport URL for the configured MCP server."""
    return f"{MCP_HTTP_URL.rstrip('/')}/mcp"


async def _call_l6e_reconcile_call(
    call_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    model: str,
    request_id: str | None = None,
    trace_id: str | None = None,
    correlation_key: str | None = None,
    correlation_source: str | None = None,
) -> dict[str, Any]:
    """Send actual token counts to `l6e_reconcile_call` over FastMCP HTTP transport."""
    arguments: dict[str, Any] = {
        "call_id": call_id,
        "actual_prompt_tokens": prompt_tokens,
        "actual_completion_tokens": completion_tokens,
        "model_used": model,
    }
    if request_id is not None:
        arguments["callback_request_id"] = request_id
    if trace_id is not None:
        arguments["callback_trace_id"] = trace_id
    if correlation_key is not None:
        arguments["correlation_key"] = correlation_key
    if correlation_source is not None:
        arguments["correlation_source"] = correlation_source
    async with Client(_mcp_transport_url(), timeout=5.0) as client:
        result = await client.call_tool(
            "l6e_reconcile_call",
            arguments,
            raise_on_error=False,
        )
    if result.is_error:
        message = result.content[0].text if result.content else "Unknown MCP error"
        raise RuntimeError(message)
    return result.data or {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/callback/success")
async def litellm_success_callback(request: Request) -> JSONResponse:
    """Receive a LiteLLM success callback and record actual token usage.

    LiteLLM proxy configuration (config.yaml):
        callbacks: ["l6e_proxy_webhook"]
    """
    try:
        raw_payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    payload = _normalize_payload(raw_payload)
    if payload is None:
        raise HTTPException(status_code=400, detail="Unsupported callback payload shape")
    summary = _callback_summary(payload)
    logger.info(
        "LiteLLM callback received: request_id=%s trace_id=%s model=%s has_usage=%s "
        "correlated_call_id=%s correlation_source=%s request_tags=%s metadata_keys=%s",
        summary["request_id"],
        summary["trace_id"],
        summary["model"],
        summary["has_usage"],
        summary["correlated_call_id"],
        summary["correlation_source"],
        summary["request_tag_count"],
        summary["metadata_keys"],
    )

    session_id = _read_active_session()
    if session_id is None:
        # No active session — proxy is running but l6e_session_start with
        # proxy_mode=True hasn't been called yet, or session already ended.
        logger.warning(
            "No active l6e session; skipping callback for request_id=%s",
            summary["request_id"],
        )
        return JSONResponse({"status": "no_active_session"})

    usage = _extract_usage(payload)
    if usage is None:
        logger.warning(
            "Could not extract token usage from callback payload; skipping request_id=%s",
            summary["request_id"],
        )
        return JSONResponse({"status": "no_usage_data"})

    prompt_tokens, completion_tokens = usage
    model = _extract_model(payload)
    request_id = _extract_request_id(payload)
    trace_id = _extract_trace_id(payload)
    correlated_call_id, correlation_source = _extract_call_correlation(payload)
    call_id = correlated_call_id or _read_active_call()
    if call_id is None:
        persist_unmatched_usage(
            session_id=session_id,
            usage_source="none",
            reason="no_correlation_match",
            payload=payload,
            call_id=None,
            request_id=request_id,
            trace_id=trace_id,
        )
        logger.warning(
            "No correlated l6e call; recorded orphan callback for request_id=%s",
            summary["request_id"],
        )
        return JSONResponse({"status": "no_active_call", "session_id": session_id})
    effective_source = correlation_source or "active_call_fallback"

    try:
        result = await _call_l6e_reconcile_call(
            call_id=call_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model,
            request_id=request_id,
            trace_id=trace_id,
            correlation_key=call_id,
            correlation_source=effective_source,
        )
    except Exception as exc:
        persist_unmatched_usage(
            session_id=session_id,
            usage_source=effective_source,
            reason="reconcile_error",
            payload=payload,
            call_id=call_id,
            request_id=request_id,
            trace_id=trace_id,
        )
        logger.error("Failed to call l6e_reconcile_call for call_id=%s: %s", call_id, exc)
        return JSONResponse({"status": "checkpoint_error", "detail": str(exc)}, status_code=502)

    logger.info(
        "Reconciled call %s with %d prompt + %d completion tokens for session %s via %s",
        call_id,
        prompt_tokens,
        completion_tokens,
        session_id,
        effective_source,
    )
    return JSONResponse({
        "status": "recorded",
        "session_id": session_id,
        "call_id": call_id,
        "correlation_source": effective_source,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reconcile": result,
    })


@app.get("/health")
async def health() -> JSONResponse:
    """Health check — also reports the currently active session."""
    return JSONResponse({
        "status": "ok",
        "active_session": _read_active_session(),
        "active_call": _read_active_call(),
        "active_session_file": str(ACTIVE_SESSION_FILE),
        "active_call_file": str(ACTIVE_CALL_FILE),
        "mcp_http_url": MCP_HTTP_URL,
    })


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn

    port = int(os.environ.get("L6E_CALLBACK_PORT", "9000"))
    uvicorn.run(
        "l6e_mcp.litellm_proxy.callback_server:app",
        host="127.0.0.1",
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()

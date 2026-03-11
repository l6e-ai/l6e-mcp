"""LiteLLM webhook parsing helpers used by callback transport adapters."""
from __future__ import annotations

from typing import Any


def normalize_payload(payload: Any) -> dict[str, Any] | None:
    """Normalize LiteLLM webhook payloads to a single dict record."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return None


def extract_usage(payload: dict[str, Any]) -> tuple[int, int] | None:
    """Extract (prompt_tokens, completion_tokens) from common callback shapes."""
    for path in (
        ("response", "usage"),
        ("usage",),
    ):
        try:
            usage = payload
            for key in path:
                usage = usage[key]
            return int(usage["prompt_tokens"]), int(usage["completion_tokens"])
        except (KeyError, TypeError, ValueError):
            continue
    try:
        return int(payload["prompt_tokens"]), int(payload["completion_tokens"])
    except (KeyError, TypeError, ValueError):
        return None


def extract_model(payload: dict[str, Any]) -> str:
    """Extract model from callback payload with safe fallback."""
    try:
        return str(payload["response"]["model"])
    except (KeyError, TypeError):
        return str(payload.get("model", payload.get("metadata", {}).get("model", "unknown")))

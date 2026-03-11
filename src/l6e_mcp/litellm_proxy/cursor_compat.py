"""LiteLLM proxy hooks for Cursor compatibility.

Cursor currently sends ``stream_options.include_usage`` on some OpenAI-style
streaming requests. LiteLLM's OpenAI Responses path can forward that field to
OpenAI, which rejects it for certain models/endpoints. Strip the flag at the
proxy boundary so Cursor requests can complete.

The hook also emits low-risk diagnostics so local proxy users can tell:
- whether Cursor traffic actually reached LiteLLM
- whether a request looked multimodal
- whether a request completed or failed upstream
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from l6e_mcp.integrations.litellm.cursor_normalizer import strip_include_usage

logger = logging.getLogger(__name__)
_IMAGE_PART_TYPES = {"image", "image_url", "input_image"}


def _iter_content_parts(data: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for key in ("messages", "input"):
        container = data.get(key)
        if not isinstance(container, list):
            continue
        for item in container:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                parts.extend(part for part in content if isinstance(part, dict))
    return parts


def _request_summary(data: dict[str, Any], call_type: str) -> dict[str, Any]:
    parts = _iter_content_parts(data)
    part_types = sorted(
        {
            str(part_type)
            for part in parts
            for part_type in [part.get("type")]
            if isinstance(part_type, str)
        }
    )
    has_multimodal_input = any(part_type != "text" for part_type in part_types)
    return {
        "call_type": call_type,
        "api_style": "responses" if "input" in data else "chat",
        "model": data.get("model"),
        "stream": bool(data.get("stream")),
        "message_count": (
            len(data.get("messages", []))
            if isinstance(data.get("messages"), list)
            else 0
        ),
        "input_count": len(data.get("input", [])) if isinstance(data.get("input"), list) else 0,
        "content_part_types": part_types,
        "has_multimodal_input": has_multimodal_input,
        "has_image_input": any(part_type in _IMAGE_PART_TYPES for part_type in part_types),
        "tool_count": len(data.get("tools", [])) if isinstance(data.get("tools"), list) else 0,
        "has_include_usage": isinstance(data.get("stream_options"), dict)
        and "include_usage" in data["stream_options"],
        "metadata_keys": sorted(data.get("metadata", {}).keys())
        if isinstance(data.get("metadata"), dict)
        else [],
    }


def _capture_path() -> Path | None:
    raw_path = os.environ.get("L6E_CURSOR_PROXY_CAPTURE_FILE")
    if not raw_path:
        return None
    return Path(raw_path).expanduser()


def _append_capture_record(event: str, payload: dict[str, Any]) -> None:
    path = _capture_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": event, **payload}, default=str))
            handle.write("\n")
    except OSError as exc:
        logger.warning("Could not write Cursor proxy capture file %s: %s", path, exc)


def _duration_ms(start_time: Any, end_time: Any) -> int | None:
    if isinstance(start_time, datetime) and isinstance(end_time, datetime):
        return int((end_time - start_time).total_seconds() * 1000)
    return None


def _response_usage_summary(response_obj: Any) -> dict[str, int] | None:
    usage = (
        response_obj.get("usage")
        if isinstance(response_obj, dict)
        else getattr(response_obj, "usage", None)
    )
    if not usage:
        return None
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
    else:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
    if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
    return None


def _response_id(response_obj: Any) -> str | None:
    raw = (
        response_obj.get("id")
        if isinstance(response_obj, dict)
        else getattr(response_obj, "id", None)
    )
    return str(raw) if raw else None


class CursorCompatibilityHook(CustomLogger):
    """Normalize Cursor request payloads before LiteLLM forwards them upstream."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any]:
        summary = _request_summary(data, call_type)
        data, include_usage_removed = strip_include_usage(data)

        logger.info(
            "Cursor proxy request: model=%s call_type=%s api_style=%s stream=%s "
            "messages=%s input_items=%s tools=%s multimodal=%s image=%s include_usage_removed=%s",
            summary["model"],
            summary["call_type"],
            summary["api_style"],
            summary["stream"],
            summary["message_count"],
            summary["input_count"],
            summary["tool_count"],
            summary["has_multimodal_input"],
            summary["has_image_input"],
            include_usage_removed,
        )

        if summary["has_multimodal_input"] or include_usage_removed:
            _append_capture_record(
                "pre_call",
                {
                    "summary": {
                        **summary,
                        "include_usage_removed": include_usage_removed,
                    },
                    "payload": data,
                },
            )

        return data

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        duration_ms = _duration_ms(start_time, end_time)
        usage = _response_usage_summary(response_obj)
        response_id = _response_id(response_obj)
        logger.info(
            "Cursor proxy success: model=%s response_id=%s stream=%s duration_ms=%s usage=%s",
            kwargs.get("model"),
            response_id,
            kwargs.get("stream"),
            duration_ms,
            usage,
        )
        _append_capture_record(
            "success",
            {
                "summary": {
                    "model": kwargs.get("model"),
                    "stream": kwargs.get("stream"),
                    "duration_ms": duration_ms,
                    "usage": usage,
                    "response_id": response_id,
                }
            },
        )

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        duration_ms = _duration_ms(start_time, end_time)
        logger.warning(
            "Cursor proxy failure: model=%s stream=%s duration_ms=%s error=%s",
            kwargs.get("model"),
            kwargs.get("stream"),
            duration_ms,
            response_obj,
        )
        _append_capture_record(
            "failure",
            {
                "summary": {
                    "model": kwargs.get("model"),
                    "stream": kwargs.get("stream"),
                    "duration_ms": duration_ms,
                    "error": str(response_obj),
                }
            },
        )


proxy_handler_instance = CursorCompatibilityHook()

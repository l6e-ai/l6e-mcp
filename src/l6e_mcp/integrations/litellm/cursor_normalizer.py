"""Cursor request normalization helpers for LiteLLM proxy hooks."""
from __future__ import annotations

from typing import Any


def strip_include_usage(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Strip `stream_options.include_usage` if present."""
    stream_options = data.get("stream_options")
    if not isinstance(stream_options, dict) or "include_usage" not in stream_options:
        return data, False
    updated_data = dict(data)
    updated_stream_options = dict(stream_options)
    updated_stream_options.pop("include_usage", None)
    if updated_stream_options:
        updated_data["stream_options"] = updated_stream_options
    else:
        updated_data.pop("stream_options", None)
    return updated_data, True

"""Tests for the Cursor LiteLLM compatibility hook."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from l6e_mcp.litellm_proxy import cursor_compat as compat


def test_request_summary_detects_multimodal_image_input():
    data = {
        "model": "gpt-4o",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "describe this"},
                    {"type": "input_image", "image_url": "data:image/png;base64,abc"},
                ],
            }
        ],
        "stream": True,
    }

    summary = compat._request_summary(data, "responses")

    assert summary["api_style"] == "responses"
    assert summary["has_multimodal_input"] is True
    assert summary["has_image_input"] is True
    assert summary["content_part_types"] == ["input_image", "input_text"]


async def test_async_pre_call_hook_strips_include_usage_and_captures_request(tmp_path, monkeypatch):
    capture_file = tmp_path / "cursor-proxy-capture.jsonl"
    monkeypatch.setenv("L6E_CURSOR_PROXY_CAPTURE_FILE", str(capture_file))

    data = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "x"}}],
            }
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    updated = await compat.proxy_handler_instance.async_pre_call_hook(
        None,
        None,
        data,
        "completion",
    )

    assert "stream_options" not in updated
    records = [json.loads(line) for line in capture_file.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["event"] == "pre_call"
    assert records[-1]["summary"]["has_image_input"] is True
    assert records[-1]["summary"]["include_usage_removed"] is True


async def test_async_log_success_event_captures_usage(tmp_path, monkeypatch):
    capture_file = tmp_path / "cursor-proxy-capture.jsonl"
    monkeypatch.setenv("L6E_CURSOR_PROXY_CAPTURE_FILE", str(capture_file))

    class Usage:
        prompt_tokens = 12
        completion_tokens = 3

    class Response:
        id = "resp_123"
        usage = Usage()

    start_time = datetime.now()
    end_time = start_time + timedelta(milliseconds=250)

    await compat.proxy_handler_instance.async_log_success_event(
        {"model": "gpt-4o", "stream": True},
        Response(),
        start_time,
        end_time,
    )

    records = [json.loads(line) for line in capture_file.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["event"] == "success"
    assert records[-1]["summary"]["usage"] == {"prompt_tokens": 12, "completion_tokens": 3}
    assert records[-1]["summary"]["response_id"] == "resp_123"

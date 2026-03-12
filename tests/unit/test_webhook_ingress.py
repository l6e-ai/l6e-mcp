"""Unit tests for LiteLLM webhook ingress helpers."""
from __future__ import annotations

import pytest

from l6e_mcp.integrations.litellm.webhook_ingress import (
    extract_model,
    extract_usage,
    normalize_payload,
)

# --- normalize_payload ---


def test_normalize_payload_dict_passthrough():
    payload = {"model": "gpt-4o", "usage": {}}
    assert normalize_payload(payload) is payload


def test_normalize_payload_list_returns_first_element():
    first = {"model": "gpt-4o"}
    second = {"model": "claude-3"}
    result = normalize_payload([first, second])
    assert result is first


def test_normalize_payload_empty_list_returns_none():
    assert normalize_payload([]) is None


def test_normalize_payload_list_of_non_dicts_returns_none():
    assert normalize_payload([42, "string"]) is None


def test_normalize_payload_string_returns_none():
    assert normalize_payload("not a dict") is None


def test_normalize_payload_none_returns_none():
    assert normalize_payload(None) is None


# --- extract_usage ---


def test_extract_usage_nested_response_usage():
    payload = {"response": {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}}
    assert extract_usage(payload) == (10, 5)


def test_extract_usage_top_level_usage_key():
    payload = {"usage": {"prompt_tokens": 20, "completion_tokens": 8}}
    assert extract_usage(payload) == (20, 8)


def test_extract_usage_flat_top_level_tokens():
    """Flat fallback path — prompt_tokens/completion_tokens at root level."""
    payload = {"prompt_tokens": 30, "completion_tokens": 12}
    assert extract_usage(payload) == (30, 12)


def test_extract_usage_nested_bad_value_falls_through_to_none():
    # "bad" can't be cast to int, so it falls through all paths
    payload = {"response": {"usage": {"prompt_tokens": "bad", "completion_tokens": 5}}}
    assert extract_usage(payload) is None


def test_extract_usage_empty_payload_returns_none():
    assert extract_usage({}) is None


def test_extract_usage_missing_completion_tokens_returns_none():
    payload = {"prompt_tokens": 10}
    assert extract_usage(payload) is None


def test_extract_usage_string_tokens_are_cast():
    payload = {"prompt_tokens": "100", "completion_tokens": "50"}
    assert extract_usage(payload) == (100, 50)


# --- extract_model ---


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"response": {"model": "gpt-4o"}}, "gpt-4o"),
        ({"model": "claude-3"}, "claude-3"),
        ({"metadata": {"model": "gemini-pro"}}, "gemini-pro"),
        ({}, "unknown"),
    ],
)
def test_extract_model_variants(payload, expected):
    assert extract_model(payload) == expected


def test_extract_model_response_takes_priority_over_model():
    payload = {"response": {"model": "from-response"}, "model": "top-level"}
    assert extract_model(payload) == "from-response"


def test_extract_model_model_takes_priority_over_metadata():
    payload = {"model": "top-level", "metadata": {"model": "in-metadata"}}
    assert extract_model(payload) == "top-level"


def test_extract_model_response_without_model_key_falls_back():
    payload = {"response": {}, "model": "fallback-model"}
    assert extract_model(payload) == "fallback-model"

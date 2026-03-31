"""Tests for l6e_mcp.anthropic_sync — local Anthropic Admin API fetch + upload."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from l6e_mcp.anthropic_sync import (
    _TOKEN_PRICING,
    UsageBucket,
    _compute_cost,
    _resolve_pricing,
    _usage_buckets_to_row_dicts,
    fetch_usage_buckets,
    sync_and_upload,
)

# ---------------------------------------------------------------------------
# _compute_cost
# ---------------------------------------------------------------------------

class TestComputeCost:
    def test_sonnet_basic(self):
        cost = _compute_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model="claude-sonnet-4-6",
        )
        assert cost == Decimal("18.00000000")

    def test_opus_basic(self):
        cost = _compute_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model="claude-opus-4-0",
        )
        assert cost == Decimal("90.00000000")

    def test_zero_tokens(self):
        cost = _compute_cost(0, 0, 0, 0, "claude-sonnet-4-6")
        assert cost == Decimal("0.00000000")

    def test_cache_tokens_included(self):
        cost = _compute_cost(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=1_000_000,
            cache_creation_tokens=1_000_000,
            model="claude-sonnet-4-6",
        )
        assert cost == Decimal("4.05000000")


class TestResolvePricing:
    def test_exact_model(self):
        assert _resolve_pricing("claude-sonnet-4-6") == _TOKEN_PRICING["claude-sonnet-4-6"]

    def test_opus_fallback(self):
        assert _resolve_pricing("some-new-opus-model") == _TOKEN_PRICING["claude-opus-4-0"]

    def test_haiku_fallback(self):
        assert _resolve_pricing("claude-haiku-99") == _TOKEN_PRICING["claude-haiku-4-5"]

    def test_unknown_model_uses_sonnet_fallback(self):
        assert _resolve_pricing("totally-unknown-model") == _TOKEN_PRICING["claude-sonnet-4-6"]


# ---------------------------------------------------------------------------
# _usage_buckets_to_row_dicts
# ---------------------------------------------------------------------------

def _make_bucket(**kwargs) -> UsageBucket:
    defaults = dict(
        starting_at=datetime(2026, 3, 26, 10, 0, tzinfo=UTC),
        ending_at=datetime(2026, 3, 26, 10, 1, tzinfo=UTC),
        model="claude-sonnet-4-6",
        api_key_id="key_abc",
        workspace_id="ws_123",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=200,
        cache_creation_tokens=100,
    )
    defaults.update(kwargs)
    return UsageBucket(**defaults)


class TestBucketsToRowDicts:
    def test_basic_conversion(self):
        rows = _usage_buckets_to_row_dicts([_make_bucket()])
        assert len(rows) == 1
        row = rows[0]
        assert row["provider"] == "anthropic"
        assert row["model_used"] == "claude-sonnet-4-6"
        assert row["user_id"] == "key_abc"
        assert row["workspace_id"] == "ws_123"
        assert row["billing_kind"] == "api_usage"
        assert row["input_tokens"] == 1300  # 1000 + 200 + 100
        assert row["output_tokens"] == 500
        assert Decimal(row["cost_usd"]) > Decimal("0")

    def test_zero_token_bucket_skipped(self):
        rows = _usage_buckets_to_row_dicts([_make_bucket(
            input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_creation_tokens=0,
        )])
        assert len(rows) == 0

    def test_fingerprint_is_deterministic(self):
        b = _make_bucket()
        rows1 = _usage_buckets_to_row_dicts([b])
        rows2 = _usage_buckets_to_row_dicts([b])
        assert rows1[0]["content_fingerprint"] == rows2[0]["content_fingerprint"]

    def test_multiple_buckets(self):
        rows = _usage_buckets_to_row_dicts([
            _make_bucket(model="claude-sonnet-4-6"),
            _make_bucket(
                model="claude-opus-4-0",
                starting_at=datetime(2026, 3, 26, 10, 1, tzinfo=UTC),
                ending_at=datetime(2026, 3, 26, 10, 2, tzinfo=UTC),
            ),
        ])
        assert len(rows) == 2
        models = {r["model_used"] for r in rows}
        assert models == {"claude-sonnet-4-6", "claude-opus-4-0"}


# ---------------------------------------------------------------------------
# fetch_usage_buckets (mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetchUsageBuckets:
    def test_single_page(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "starting_at": "2026-03-26T10:00:00+00:00",
                    "ending_at": "2026-03-26T10:01:00+00:00",
                    "results": [
                        {
                            "model": "claude-sonnet-4-6",
                            "api_key_id": "key_abc",
                            "workspace_id": None,
                            "uncached_input_tokens": 500,
                            "output_tokens": 200,
                            "cache_read_input_tokens": 100,
                            "cache_creation_input_tokens": 50,
                        },
                    ],
                },
            ],
            "has_more": False,
        }
        mock_response.raise_for_status = MagicMock()

        with patch("l6e_mcp.anthropic_sync.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MagicMock())
            MockClient.return_value.__enter__.return_value.get.return_value = mock_response
            MockClient.return_value.__exit__ = MagicMock(return_value=False)

            buckets = fetch_usage_buckets(
                admin_key="sk-ant-admin-test",
                start=datetime(2026, 3, 26, tzinfo=UTC),
                end=datetime(2026, 3, 27, tzinfo=UTC),
            )

        assert len(buckets) == 1
        assert buckets[0].input_tokens == 500
        assert buckets[0].output_tokens == 200
        assert buckets[0].cache_read_tokens == 100

    def test_pagination(self):
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = {
            "data": [{
                "starting_at": "2026-03-26T10:00:00+00:00",
                "ending_at": "2026-03-26T10:01:00+00:00",
                "results": [{
                    "model": "claude-sonnet-4-6",
                    "uncached_input_tokens": 100, "output_tokens": 50,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                }],
            }],
            "has_more": True,
            "next_page": "page2token",
        }
        page1.raise_for_status = MagicMock()

        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = {
            "data": [{
                "starting_at": "2026-03-26T10:01:00+00:00",
                "ending_at": "2026-03-26T10:02:00+00:00",
                "results": [{
                    "model": "claude-sonnet-4-6",
                    "uncached_input_tokens": 200, "output_tokens": 100,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                }],
            }],
            "has_more": False,
        }
        page2.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.side_effect = [page1, page2]

        with patch("l6e_mcp.anthropic_sync.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)

            buckets = fetch_usage_buckets(
                admin_key="sk-ant-admin-test",
                start=datetime(2026, 3, 26, tzinfo=UTC),
                end=datetime(2026, 3, 27, tzinfo=UTC),
            )

        assert len(buckets) == 2
        assert mock_client.get.call_count == 2


# ---------------------------------------------------------------------------
# sync_and_upload (mocked HTTP for both Anthropic + hosted-edge)
# ---------------------------------------------------------------------------

class TestSyncAndUpload:
    def _mock_cost_report_response(self):
        """cost_report body: data[] time buckets, each with results[] (amount in cents)."""
        resp = MagicMock()
        resp.json.return_value = {
            "data": [
                {
                    "starting_at": "2026-03-26T00:00:00+00:00",
                    "ending_at": "2026-03-27T00:00:00+00:00",
                    "results": [
                        {
                            "amount": "100",
                            "model": "claude-sonnet-4-6",
                            "workspace_id": None,
                        },
                    ],
                },
            ],
            "has_more": False,
        }
        resp.raise_for_status = MagicMock()
        return resp

    def _mock_edge_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "status": "accepted",
            "batch_id": "batch-123",
            "rows_inserted": 1,
            "rows_deduplicated": 0,
        }
        resp.raise_for_status = MagicMock()
        return resp

    @patch("l6e_mcp.anthropic_sync._config")
    def test_full_sync(self, mock_config):
        mock_config.get_api_key.return_value = "sk-l6e-test"
        mock_config.get_cloud_endpoint.return_value = "https://api.l6e.ai"

        anthropic_resp = self._mock_cost_report_response()
        edge_resp = self._mock_edge_response()

        with patch("l6e_mcp.anthropic_sync.httpx.Client") as MockClient, \
             patch("l6e_mcp.anthropic_sync.httpx.post", return_value=edge_resp):
            MockClient.return_value.__enter__ = MagicMock(return_value=MagicMock())
            MockClient.return_value.__enter__.return_value.get.return_value = anthropic_resp
            MockClient.return_value.__exit__ = MagicMock(return_value=False)

            result = sync_and_upload(
                admin_key="sk-ant-admin-test",
                date_start="2026-03-26",
                date_end="2026-03-27",
            )

        assert result.buckets_fetched == 1
        assert result.rows_sent == 1
        assert result.total_cost_usd == Decimal("1")
        assert result.server_response["status"] == "accepted"
        assert result.source == "cost_report"
        assert not result.warnings

    @patch("l6e_mcp.anthropic_sync._config")
    def test_empty_response(self, mock_config):
        mock_config.get_api_key.return_value = "sk-l6e-test"
        mock_config.get_cloud_endpoint.return_value = "https://api.l6e.ai"

        empty_resp = MagicMock()
        empty_resp.json.return_value = {"data": [], "has_more": False}
        empty_resp.raise_for_status = MagicMock()

        with patch("l6e_mcp.anthropic_sync.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MagicMock())
            MockClient.return_value.__enter__.return_value.get.return_value = empty_resp
            MockClient.return_value.__exit__ = MagicMock(return_value=False)

            result = sync_and_upload(
                admin_key="sk-ant-admin-test",
                date_start="2026-03-26",
                date_end="2026-03-27",
            )

        assert result.buckets_fetched == 0
        assert result.rows_sent == 0
        assert any("No billing data returned from Anthropic" in w for w in result.warnings)

    def test_missing_api_key_raises(self):
        with patch("l6e_mcp.anthropic_sync._config") as mock_config:
            mock_config.get_api_key.return_value = None
            with pytest.raises(RuntimeError, match="L6E_API_KEY"):
                sync_and_upload(
                    admin_key="sk-ant-admin-test",
                    date_start="2026-03-26",
                    date_end="2026-03-27",
                )

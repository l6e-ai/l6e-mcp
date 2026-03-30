"""Anthropic Admin API sync — fetches usage data locally and POSTs normalized
billing rows to hosted-edge.

The admin key never leaves the user's machine. It is used only in the
Authorization header of requests to the Anthropic API.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from l6e_mcp import config as _config

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.anthropic.com/v1/organizations"
_USAGE_ENDPOINT = f"{_BASE_URL}/usage_report/messages"
_ANTHROPIC_TIMEOUT = 30.0
_HOSTED_EDGE_TIMEOUT = 15.0

_TOKEN_PRICING: dict[str, dict[str, Decimal]] = {
    "claude-opus-4-0": {
        "input": Decimal("15.00"),
        "output": Decimal("75.00"),
        "cache_read": Decimal("1.50"),
        "cache_creation": Decimal("18.75"),
    },
    "claude-sonnet-4-5": {
        "input": Decimal("3.00"),
        "output": Decimal("15.00"),
        "cache_read": Decimal("0.30"),
        "cache_creation": Decimal("3.75"),
    },
    "claude-sonnet-4-6": {
        "input": Decimal("3.00"),
        "output": Decimal("15.00"),
        "cache_read": Decimal("0.30"),
        "cache_creation": Decimal("3.75"),
    },
    "claude-haiku-4-5": {
        "input": Decimal("0.80"),
        "output": Decimal("4.00"),
        "cache_read": Decimal("0.08"),
        "cache_creation": Decimal("1.00"),
    },
    "claude-haiku-3-5": {
        "input": Decimal("0.25"),
        "output": Decimal("1.25"),
        "cache_read": Decimal("0.025"),
        "cache_creation": Decimal("0.30"),
    },
}

_PER_MILLION = Decimal("1000000")
_FALLBACK_PRICING = _TOKEN_PRICING["claude-sonnet-4-6"]


@dataclass(frozen=True)
class UsageBucket:
    starting_at: datetime
    ending_at: datetime
    model: str
    api_key_id: str | None
    workspace_id: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


@dataclass(frozen=True)
class SyncResult:
    buckets_fetched: int
    rows_sent: int
    total_cost_usd: Decimal
    server_response: dict[str, Any]
    warnings: list[str]


def _resolve_pricing(model: str) -> dict[str, Decimal]:
    normalized = model.lower().strip()
    for key, pricing in _TOKEN_PRICING.items():
        if key in normalized or normalized in key:
            return pricing
    if "opus" in normalized:
        return _TOKEN_PRICING["claude-opus-4-0"]
    if "haiku" in normalized:
        return _TOKEN_PRICING["claude-haiku-4-5"]
    if "sonnet" in normalized:
        return _TOKEN_PRICING["claude-sonnet-4-6"]
    return _FALLBACK_PRICING


def _compute_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    model: str,
) -> Decimal:
    pricing = _resolve_pricing(model)
    cost = (
        Decimal(input_tokens) * pricing["input"]
        + Decimal(output_tokens) * pricing["output"]
        + Decimal(cache_read_tokens) * pricing["cache_read"]
        + Decimal(cache_creation_tokens) * pricing["cache_creation"]
    ) / _PER_MILLION
    return cost.quantize(Decimal("0.00000001"))


def _bucket_fingerprint(b: UsageBucket, cost: Decimal) -> str:
    parts = "|".join([
        "anthropic",
        b.model,
        str(cost),
        str(b.input_tokens),
        str(b.output_tokens),
        str(b.cache_read_tokens),
        str(b.cache_creation_tokens),
        b.starting_at.isoformat(),
        b.ending_at.isoformat(),
        b.api_key_id or "__null__",
        b.workspace_id or "__null__",
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def fetch_usage_buckets(
    *,
    admin_key: str,
    start: datetime,
    end: datetime,
    api_key_id: str | None = None,
) -> list[UsageBucket]:
    """Fetch 1-minute usage buckets from the Anthropic Admin API.

    Runs locally — the admin_key is used only in the Authorization header.
    """
    buckets: list[UsageBucket] = []
    page_token: str | None = None

    headers = {
        "x-api-key": admin_key,
        "anthropic-version": "2023-06-01",
        "User-Agent": "l6e-billing-sync/1.0",
    }

    params: dict[str, str] = {
        "bucket_width": "1m",
        "group_by": "api_key_id,model",
        "starting_at": start.isoformat(),
        "ending_at": end.isoformat(),
    }
    if api_key_id:
        params["api_key_ids"] = api_key_id

    with httpx.Client(timeout=_ANTHROPIC_TIMEOUT) as client:
        while True:
            if page_token:
                params["page"] = page_token

            resp = client.get(_USAGE_ENDPOINT, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("results", []):
                buckets.append(_parse_bucket(item))

            if not data.get("has_more"):
                break
            page_token = data.get("next_page")
            if not page_token:
                break

    return buckets


def _parse_bucket(item: dict[str, Any]) -> UsageBucket:
    return UsageBucket(
        starting_at=datetime.fromisoformat(item["starting_at"]),
        ending_at=datetime.fromisoformat(item["ending_at"]),
        model=item.get("model", "unknown"),
        api_key_id=item.get("api_key_id"),
        workspace_id=item.get("workspace_id"),
        input_tokens=int(item.get("uncached_input_tokens", 0)),
        output_tokens=int(item.get("output_tokens", 0)),
        cache_read_tokens=int(item.get("cache_read_input_tokens", 0)),
        cache_creation_tokens=int(item.get("cache_creation_input_tokens", 0)),
    )


def _buckets_to_row_dicts(buckets: list[UsageBucket]) -> list[dict[str, Any]]:
    """Convert usage buckets to JSON-serializable row dicts for the import-rows endpoint."""
    rows: list[dict[str, Any]] = []
    for b in buckets:
        if b.input_tokens == 0 and b.output_tokens == 0 and b.cache_read_tokens == 0 \
            and b.cache_creation_tokens == 0:
            continue

        cost = _compute_cost(
            b.input_tokens, b.output_tokens,
            b.cache_read_tokens, b.cache_creation_tokens,
            b.model,
        )
        total_input = b.input_tokens + b.cache_read_tokens + b.cache_creation_tokens

        rows.append({
            "provider": "anthropic",
            "model_used": b.model,
            "input_tokens": total_input if total_input > 0 else None,
            "output_tokens": b.output_tokens if b.output_tokens > 0 else None,
            "cost_usd": str(cost),
            "billing_kind": "api_usage",
            "workspace_id": b.workspace_id,
            "user_id": b.api_key_id,
            "started_at": b.starting_at.timestamp(),
            "finished_at": b.ending_at.timestamp(),
            "content_fingerprint": _bucket_fingerprint(b, cost),
        })
    return rows


def sync_and_upload(
    *,
    admin_key: str,
    date_start: str,
    date_end: str,
    api_key_id: str | None = None,
) -> SyncResult:
    """Fetch usage from Anthropic, normalize, and POST to hosted-edge.

    The admin_key is used only for the Anthropic API requests and is never
    sent to hosted-edge.
    """
    api_key = _config.get_api_key()
    if not api_key:
        raise RuntimeError(
            "L6E_API_KEY is not configured. Set it in ~/.l6e/config.toml or "
            "the L6E_API_KEY environment variable."
        )
    endpoint = _config.get_cloud_endpoint()

    start = datetime.strptime(date_start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(date_end, "%Y-%m-%d").replace(tzinfo=UTC)

    buckets = fetch_usage_buckets(
        admin_key=admin_key,
        start=start,
        end=end,
        api_key_id=api_key_id,
    )

    row_dicts = _buckets_to_row_dicts(buckets)
    total_cost = sum((Decimal(r["cost_usd"]) for r in row_dicts), Decimal("0"))

    warnings: list[str] = []
    if not buckets:
        warnings.append("No usage data returned from Anthropic for the requested date range.")

    if not row_dicts:
        return SyncResult(
            buckets_fetched=len(buckets),
            rows_sent=0,
            total_cost_usd=total_cost,
            server_response={},
            warnings=warnings,
        )

    payload = {
        "source": "anthropic_api",
        "rows": row_dicts,
        "metadata": {
            "sync_type": "anthropic_admin_api",
            "date_start": date_start,
            "date_end": date_end,
            "api_key_filter": api_key_id,
            "buckets_fetched": len(buckets),
        },
    }

    url = f"{endpoint}/v1/billing/import-rows"
    resp = httpx.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_HOSTED_EDGE_TIMEOUT,
    )
    resp.raise_for_status()
    server_response = resp.json()

    return SyncResult(
        buckets_fetched=len(buckets),
        rows_sent=len(row_dicts),
        total_cost_usd=total_cost,
        server_response=server_response,
        warnings=warnings,
    )

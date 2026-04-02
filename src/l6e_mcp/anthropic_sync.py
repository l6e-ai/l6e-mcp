"""Anthropic Admin API sync — fetches billing data locally and POSTs normalized
rows to hosted-edge.

The admin key never leaves the user's machine. It is used only in the
Authorization header of requests to the Anthropic API. Users should prefer a
short-lived key and revoke it in Anthropic after a successful import.

Uses cost_report (actual dollar amounts) as primary source, with
usage_report/messages (token counts) as fallback. The cost_report endpoint
is more reliable — the usage_report endpoint has a known rate-limiter bug
(github.com/anthropics/claude-code/issues/31637).
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from l6e_mcp import config as _config

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.anthropic.com/v1/organizations"
_COST_ENDPOINT = f"{_BASE_URL}/cost_report"
_USAGE_ENDPOINT = f"{_BASE_URL}/usage_report/messages"
_CLAUDE_CODE_ENDPOINT = f"{_BASE_URL}/usage_report/claude_code"
_ANTHROPIC_TIMEOUT = 30.0
_HOSTED_EDGE_TIMEOUT = 15.0
_MAX_RETRIES = 4
_RETRY_BASE_SECONDS = 2.0
_CENTS_PER_DOLLAR = Decimal("100")

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
class CostRow:
    """One model's aggregated cost for a single day from cost_report."""
    starting_at: datetime
    ending_at: datetime
    model: str
    workspace_id: str | None
    cost_usd: Decimal


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
class ClaudeCodeRecord:
    """One model's usage for a single user on a single day from claude_code report."""
    date: datetime
    user_id: str
    actor_type: str
    terminal_type: str
    customer_type: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_cents: int


@dataclass
class SyncResult:
    buckets_fetched: int
    rows_sent: int
    total_cost_usd: Decimal
    server_response: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    source: str = "cost_report"
    claude_code_records_fetched: int = 0
    claude_code_rows_sent: int = 0


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _make_headers(admin_key: str) -> dict[str, str]:
    return {
        "x-api-key": admin_key,
        "anthropic-version": "2023-06-01",
        "User-Agent": "l6e-billing-sync/1.0",
    }


def _request_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any],
) -> httpx.Response:
    for attempt in range(_MAX_RETRIES + 1):
        resp = client.get(url, headers=headers, params=params)
        if resp.status_code != 429 or attempt == _MAX_RETRIES:
            resp.raise_for_status()
            return resp
        wait = _RETRY_BASE_SECONDS * (2 ** attempt)
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                wait = max(wait, float(retry_after))
            except ValueError:
                logger.warning(
                    "Invalid retry-after header: %s. Using default retry time.",
                    retry_after,
                )
        logger.info(
            "Rate limited (429), retrying in %.1fs (attempt %d/%d)",
            wait, attempt + 1, _MAX_RETRIES,
        )
        time.sleep(wait)
    return resp  # unreachable, keeps type checkers happy


# ---------------------------------------------------------------------------
# cost_report — primary source (actual dollar amounts from Anthropic billing)
# ---------------------------------------------------------------------------

def fetch_cost_rows(
    *,
    admin_key: str,
    start: datetime,
    end: datetime,
) -> list[CostRow]:
    """Fetch daily cost data from the Anthropic cost_report endpoint.

    Returns one CostRow per model per workspace per day, with actual dollar
    amounts from Anthropic's billing system.
    """
    headers = _make_headers(admin_key)
    params: dict[str, Any] = {
        "starting_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ending_at": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bucket_width": "1d",
        "group_by[]": ["description", "workspace_id"],
    }

    # Aggregate by (day, model, workspace) since the API returns separate rows
    # per token_type (input, output, cache_read, cache_creation).
    AggKey = tuple[str, str, str, str | None]  # (start, end, model, workspace)
    agg: dict[AggKey, Decimal] = defaultdict(Decimal)
    pages = 0

    with httpx.Client(timeout=_ANTHROPIC_TIMEOUT) as client:
        page_token: str | None = None
        while True:
            if page_token:
                params["page"] = page_token

            resp = _request_with_retry(client, _COST_ENDPOINT, headers=headers, params=params)
            body = resp.json()
            pages += 1

            for bucket in body.get("data", []):
                ts_start = bucket["starting_at"]
                ts_end = bucket["ending_at"]
                for result in bucket.get("results", []):
                    amount = Decimal(str(result.get("amount", "0")))
                    if amount == 0:
                        continue
                    model = result.get("model") or "unknown"
                    ws = result.get("workspace_id")
                    key: AggKey = (ts_start, ts_end, model, ws)
                    agg[key] += amount

            if not body.get("has_more"):
                break
            page_token = body.get("next_page")
            if not page_token:
                break

    logger.info("Fetched %d cost pages, %d model-day aggregates", pages, len(agg))

    rows: list[CostRow] = []
    for (ts_start, ts_end, model, ws), total_cents in agg.items():
        rows.append(CostRow(
            starting_at=datetime.fromisoformat(ts_start),
            ending_at=datetime.fromisoformat(ts_end),
            model=model,
            workspace_id=ws,
            cost_usd=(total_cents / _CENTS_PER_DOLLAR).quantize(Decimal("0.000001")),
        ))
    return rows


def _cost_fingerprint(row: CostRow) -> str:
    parts = "|".join([
        "anthropic",
        "cost_report",
        row.model,
        str(row.cost_usd),
        row.starting_at.isoformat(),
        row.ending_at.isoformat(),
        row.workspace_id or "__null__",
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def _cost_rows_to_row_dicts(rows: list[CostRow]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "provider": "anthropic",
            "model_used": r.model,
            "cost_usd": str(r.cost_usd),
            "billing_kind": "api_usage",
            "workspace_id": r.workspace_id,
            "started_at": r.starting_at.timestamp(),
            "finished_at": r.ending_at.timestamp(),
            "content_fingerprint": _cost_fingerprint(r),
        })
    return out


# ---------------------------------------------------------------------------
# usage_report/messages — fallback (token counts, self-computed cost)
# ---------------------------------------------------------------------------

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


def _choose_bucket_width(start: datetime, end: datetime) -> str:
    span_hours = (end - start).total_seconds() / 3600
    if span_hours <= 2:
        return "1m"
    if span_hours <= 168:
        return "1h"
    return "1d"


def fetch_usage_buckets(
    *,
    admin_key: str,
    start: datetime,
    end: datetime,
    api_key_id: str | None = None,
) -> list[UsageBucket]:
    """Fetch usage buckets from the Anthropic usage_report/messages endpoint.

    Bucket width adapts to the date range. Retries on 429 with exponential
    backoff. The admin_key is used only in the Authorization header.
    """
    buckets: list[UsageBucket] = []
    bucket_width = _choose_bucket_width(start, end)
    headers = _make_headers(admin_key)

    params: dict[str, Any] = {
        "bucket_width": bucket_width,
        "group_by[]": ["api_key_id", "model"],
        "starting_at": start.isoformat(),
        "ending_at": end.isoformat(),
    }
    if api_key_id:
        params["api_key_ids[]"] = [api_key_id]

    with httpx.Client(timeout=_ANTHROPIC_TIMEOUT) as client:
        page_token: str | None = None
        while True:
            if page_token:
                params["page"] = page_token

            resp = _request_with_retry(client, _USAGE_ENDPOINT, headers=headers, params=params)
            body = resp.json()

            for time_bucket in body.get("data", []):
                ts_start = datetime.fromisoformat(time_bucket["starting_at"])
                ts_end = datetime.fromisoformat(time_bucket["ending_at"])
                for result in time_bucket.get("results", []):
                    buckets.append(_parse_usage_result(result, ts_start, ts_end))

            if not body.get("has_more"):
                break
            page_token = body.get("next_page")
            if not page_token:
                break

    return buckets


def _parse_usage_result(
    result: dict[str, Any],
    bucket_start: datetime,
    bucket_end: datetime,
) -> UsageBucket:
    cache_creation = result.get("cache_creation") or {}
    cache_creation_tokens = (
        int(cache_creation.get("ephemeral_5m_input_tokens", 0))
        + int(cache_creation.get("ephemeral_1h_input_tokens", 0))
    )
    return UsageBucket(
        starting_at=bucket_start,
        ending_at=bucket_end,
        model=result.get("model") or "unknown",
        api_key_id=result.get("api_key_id"),
        workspace_id=result.get("workspace_id"),
        input_tokens=int(result.get("uncached_input_tokens", 0)),
        output_tokens=int(result.get("output_tokens", 0)),
        cache_read_tokens=int(result.get("cache_read_input_tokens", 0)),
        cache_creation_tokens=cache_creation_tokens,
    )


def _usage_fingerprint(b: UsageBucket, cost: Decimal) -> str:
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


def _usage_buckets_to_row_dicts(buckets: list[UsageBucket]) -> list[dict[str, Any]]:
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
            "content_fingerprint": _usage_fingerprint(b, cost),
        })
    return rows


# ---------------------------------------------------------------------------
# usage_report/claude_code — Claude Code analytics (per-user, per-day)
# ---------------------------------------------------------------------------

def fetch_claude_code_records(
    *,
    admin_key: str,
    start: datetime,
    end: datetime,
) -> list[ClaudeCodeRecord]:
    """Fetch Claude Code analytics from the Anthropic Admin API.

    The endpoint returns data for a single day at a time, so we iterate
    day-by-day over the [start, end) range. Each user record's
    model_breakdown is flattened into individual ClaudeCodeRecords.
    """
    headers = _make_headers(admin_key)
    records: list[ClaudeCodeRecord] = []
    current = start

    with httpx.Client(timeout=_ANTHROPIC_TIMEOUT) as client:
        while current < end:
            day_str = current.strftime("%Y-%m-%d")
            page_token: str | None = None

            while True:
                params: dict[str, Any] = {
                    "starting_at": day_str,
                    "limit": 1000,
                }
                if page_token:
                    params["page"] = page_token

                resp = _request_with_retry(
                    client, _CLAUDE_CODE_ENDPOINT, headers=headers, params=params,
                )
                body = resp.json()

                for user_record in body.get("data", []):
                    records.extend(_parse_claude_code_record(user_record))

                if not body.get("has_more"):
                    break
                page_token = body.get("next_page")
                if not page_token:
                    break

            current += timedelta(days=1)

    logger.info("Fetched %d Claude Code model-user-day records", len(records))
    return records


def _parse_claude_code_record(record: dict[str, Any]) -> list[ClaudeCodeRecord]:
    date = datetime.fromisoformat(record["date"])
    actor = record.get("actor", {})
    if actor.get("type") == "user_actor":
        user_id = actor.get("email_address", "unknown")
    else:
        user_id = actor.get("api_key_name", "unknown")
    actor_type = actor.get("type", "unknown")
    terminal_type = record.get("terminal_type", "unknown")
    customer_type = record.get("customer_type", "unknown")

    out: list[ClaudeCodeRecord] = []
    for mb in record.get("model_breakdown", []):
        tokens = mb.get("tokens", {})
        cost_info = mb.get("estimated_cost", {})
        out.append(ClaudeCodeRecord(
            date=date,
            user_id=user_id,
            actor_type=actor_type,
            terminal_type=terminal_type,
            customer_type=customer_type,
            model=mb.get("model", "unknown"),
            input_tokens=int(tokens.get("input", 0)),
            output_tokens=int(tokens.get("output", 0)),
            cache_read_tokens=int(tokens.get("cache_read", 0)),
            cache_creation_tokens=int(tokens.get("cache_creation", 0)),
            cost_cents=int(cost_info.get("amount", 0)),
        ))
    return out


def _claude_code_fingerprint(r: ClaudeCodeRecord) -> str:
    parts = "|".join([
        "anthropic",
        "claude_code",
        r.user_id,
        r.model,
        r.date.isoformat(),
        str(r.cost_cents),
        str(r.input_tokens),
        str(r.output_tokens),
        str(r.cache_read_tokens),
        str(r.cache_creation_tokens),
        r.terminal_type,
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def _claude_code_records_to_row_dicts(
    records: list[ClaudeCodeRecord],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in records:
        if r.cost_cents == 0 and r.input_tokens == 0 and r.output_tokens == 0:
            continue

        cost_usd = (Decimal(r.cost_cents) / _CENTS_PER_DOLLAR).quantize(
            Decimal("0.000001"),
        )
        total_input = r.input_tokens + r.cache_read_tokens + r.cache_creation_tokens
        day_end = r.date + timedelta(days=1)

        rows.append({
            "provider": "anthropic",
            "model_used": r.model,
            "input_tokens": total_input if total_input > 0 else None,
            "output_tokens": r.output_tokens if r.output_tokens > 0 else None,
            "cost_usd": str(cost_usd),
            "billing_kind": "claude_code",
            "user_id": r.user_id,
            "started_at": r.date.timestamp(),
            "finished_at": day_end.timestamp(),
            "content_fingerprint": _claude_code_fingerprint(r),
            "terminal_type": r.terminal_type,
            "customer_type": r.customer_type,
            "actor_type": r.actor_type,
        })
    return rows


# ---------------------------------------------------------------------------
# Orchestrator — tries cost_report first, falls back to usage_report
# ---------------------------------------------------------------------------

def sync_and_upload(
    *,
    admin_key: str,
    date_start: str,
    date_end: str,
    api_key_id: str | None = None,
    include_claude_code: bool = True,
) -> SyncResult:
    """Fetch billing data from Anthropic, normalize, and POST to hosted-edge.

    Tries cost_report first (actual dollar amounts, more reliable). Falls
    back to usage_report/messages if cost_report fails. When
    include_claude_code is True (default), also fetches Claude Code
    analytics from the usage_report/claude_code endpoint and merges the
    rows into a single upload.

    The admin_key is used only for Anthropic API requests and is never
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

    warnings: list[str] = []
    row_dicts: list[dict[str, Any]] = []
    source = "cost_report"
    buckets_fetched = 0

    # Primary: cost_report (actual Anthropic billing amounts)
    try:
        cost_rows = fetch_cost_rows(admin_key=admin_key, start=start, end=end)
        buckets_fetched = len(cost_rows)
        row_dicts = _cost_rows_to_row_dicts(cost_rows)
        if api_key_id:
            warnings.append(
                "cost_report does not support api_key filtering; "
                "showing all keys. Use usage_report for key-level detail."
            )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "cost_report failed (HTTP %s). Falling back to usage_report.",
            exc.response.status_code
        )
        warnings.append(
            f"""
            cost_report unavailable (HTTP {exc.response.status_code}). 
            Falling back to usage_report.
            """
        )
        source = "usage_report"
        usage_buckets = fetch_usage_buckets(
            admin_key=admin_key, start=start, end=end, api_key_id=api_key_id,
        )
        buckets_fetched = len(usage_buckets)
        row_dicts = _usage_buckets_to_row_dicts(usage_buckets)

    # Claude Code analytics (additive — merges with API usage rows)
    claude_code_records_fetched = 0
    claude_code_rows_sent = 0
    if include_claude_code:
        try:
            cc_records = fetch_claude_code_records(
                admin_key=admin_key, start=start, end=end,
            )
            claude_code_records_fetched = len(cc_records)
            cc_row_dicts = _claude_code_records_to_row_dicts(cc_records)
            claude_code_rows_sent = len(cc_row_dicts)
            row_dicts.extend(cc_row_dicts)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "claude_code report failed (HTTP %s). Skipping Claude Code data.",
                exc.response.status_code,
            )
            warnings.append(
                f"Claude Code analytics unavailable (HTTP {exc.response.status_code}). "
                "API usage rows were still synced."
            )

    total_cost = sum((Decimal(r["cost_usd"]) for r in row_dicts), Decimal("0"))

    if buckets_fetched == 0 and claude_code_records_fetched == 0:
        warnings.append("No billing data returned from Anthropic for the requested date range.")

    if not row_dicts:
        return SyncResult(
            buckets_fetched=buckets_fetched,
            rows_sent=0,
            total_cost_usd=total_cost,
            server_response={},
            warnings=warnings,
            source=source,
            claude_code_records_fetched=claude_code_records_fetched,
            claude_code_rows_sent=claude_code_rows_sent,
        )

    sources = [source]
    if claude_code_rows_sent > 0:
        sources.append("claude_code")

    payload = {
        "source": "anthropic_api",
        "rows": row_dicts,
        "metadata": {
            "sync_type": f"anthropic_admin_api_{'+'.join(sources)}",
            "date_start": date_start,
            "date_end": date_end,
            "api_key_filter": api_key_id,
            "buckets_fetched": buckets_fetched,
            "claude_code_records_fetched": claude_code_records_fetched,
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
        buckets_fetched=buckets_fetched,
        rows_sent=len(row_dicts),
        total_cost_usd=total_cost,
        server_response=server_response,
        warnings=warnings,
        source="+".join(sources),
        claude_code_records_fetched=claude_code_records_fetched,
        claude_code_rows_sent=claude_code_rows_sent,
    )

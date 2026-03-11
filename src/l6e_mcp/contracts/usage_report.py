"""Usage report contract shared by hosted and self-hosted ingestion paths."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UsageReport:
    """Exact usage record for a previously created l6e call."""

    call_id: str
    usage_source: str
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    provider_request_id: str | None = None
    provider_trace_id: str | None = None
    hosted_ledger_id: str | None = None
    request_started_at: float | None = None
    request_finished_at: float | None = None
    idempotency_key: str | None = None

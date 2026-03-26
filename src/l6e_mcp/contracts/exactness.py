"""Exactness coverage states shared across transport and storage."""
from __future__ import annotations

from enum import StrEnum


class ExactnessState(StrEnum):
    """Call-level accounting precision state."""

    ESTIMATE_ONLY = "estimate_only"
    EXACT_PENDING = "exact_pending"
    EXACT_RECORDED = "exact_recorded"
    EXACT_UNAVAILABLE = "exact_unavailable"


class RunExactnessState(StrEnum):
    """Run-level projection for exactness coverage."""

    ALL_ESTIMATE_ONLY = "all_estimate_only"
    PARTIAL_EXACT = "partial_exact"
    FULLY_EXACT_FOR_SUPPORTED_CALLS = "fully_exact_for_supported_calls"
    EXACTNESS_DEGRADED = "exactness_degraded"

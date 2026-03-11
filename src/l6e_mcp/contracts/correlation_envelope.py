"""Provider-agnostic transport envelope carrying l6e call correlation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CorrelationEnvelope:
    """Canonical envelope emitted by checkpoint responses."""

    call_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    request_tags: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "metadata": self.metadata,
            "request_tags": self.request_tags,
            "headers": self.headers,
        }

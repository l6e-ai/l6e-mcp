"""Mode coverage contracts for exactness capability reporting."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeCoverage:
    ask_mode_exact_capable: bool
    plan_mode_exact_capable: bool
    agent_mode_exact_capable: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "ask_mode_exact_capable": self.ask_mode_exact_capable,
            "plan_mode_exact_capable": self.plan_mode_exact_capable,
            "agent_mode_exact_capable": self.agent_mode_exact_capable,
        }


def mode_exact_capable_for_call_mode(coverage: ModeCoverage, call_mode: str | None) -> bool | None:
    if call_mode is None:
        return None
    normalized = call_mode.strip().lower()
    if normalized == "ask":
        return coverage.ask_mode_exact_capable
    if normalized == "plan":
        return coverage.plan_mode_exact_capable
    if normalized == "agent":
        return coverage.agent_mode_exact_capable
    return None

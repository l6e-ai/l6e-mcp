"""Unit tests for mode_exact_capable_for_call_mode."""
from __future__ import annotations

import pytest

from l6e_mcp.contracts.mode_coverage import ModeCoverage, mode_exact_capable_for_call_mode


def _coverage(ask: bool = True, plan: bool = True, agent: bool = False) -> ModeCoverage:
    return ModeCoverage(
        ask_mode_exact_capable=ask,
        plan_mode_exact_capable=plan,
        agent_mode_exact_capable=agent,
    )


def test_ask_mode_returns_ask_capable():
    cov = _coverage(ask=True, plan=False, agent=False)
    assert mode_exact_capable_for_call_mode(cov, "ask") is True


def test_plan_mode_returns_plan_capable():
    cov = _coverage(ask=False, plan=True, agent=False)
    assert mode_exact_capable_for_call_mode(cov, "plan") is True


def test_agent_mode_returns_agent_capable():
    cov = _coverage(ask=False, plan=False, agent=True)
    assert mode_exact_capable_for_call_mode(cov, "agent") is True


def test_none_call_mode_returns_none():
    cov = _coverage()
    assert mode_exact_capable_for_call_mode(cov, None) is None


def test_unknown_call_mode_returns_none():
    cov = _coverage()
    assert mode_exact_capable_for_call_mode(cov, "debug") is None


def test_whitespace_and_case_normalization():
    cov = _coverage(ask=True, plan=True, agent=True)
    assert mode_exact_capable_for_call_mode(cov, "  Ask  ") is True
    assert mode_exact_capable_for_call_mode(cov, "PLAN") is True
    assert mode_exact_capable_for_call_mode(cov, "Agent") is True


@pytest.mark.parametrize(
    "mode, capable",
    [
        ("ask", True),
        ("plan", False),
        ("agent", True),
    ],
)
def test_capability_reflects_coverage_values(mode, capable):
    cov = ModeCoverage(
        ask_mode_exact_capable=True,
        plan_mode_exact_capable=False,
        agent_mode_exact_capable=True,
    )
    assert mode_exact_capable_for_call_mode(cov, mode) is capable

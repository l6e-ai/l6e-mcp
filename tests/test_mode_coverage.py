"""Mode coverage persistence and call classification tests."""
from __future__ import annotations

from l6e._types import BudgetMode, PipelinePolicy

from l6e_mcp.session_store import LocalSessionStore


def test_mode_coverage_defaults_for_self_hosted_proxy(tmp_path):
    store = LocalSessionStore(tmp_path / "sessions.db")
    policy = PipelinePolicy(budget=5.0, budget_mode=BudgetMode.WARN)
    session = store.create_session(
        session_id="session_mode_default",
        model="gpt-4o",
        policy=policy,
        source="mcp",
        log_path=str(tmp_path / "runs.jsonl"),
        proxy_mode=True,
    )
    assert session.ask_mode_exact_capable is True
    assert session.plan_mode_exact_capable is True
    assert session.agent_mode_exact_capable is False


def test_call_mode_classifies_exact_unavailable_when_mode_not_capable(tmp_path):
    store = LocalSessionStore(tmp_path / "sessions.db")
    policy = PipelinePolicy(budget=5.0, budget_mode=BudgetMode.WARN)
    session = store.create_session(
        session_id="session_mode_agent_unavailable",
        model="gpt-4o",
        policy=policy,
        source="mcp",
        log_path=str(tmp_path / "runs.jsonl"),
        proxy_mode=True,
    )
    call = store.create_call(
        session_id=session.session_id,
        tool_name="read_files",
        model_requested="gpt-4o",
        model_used="gpt-4o",
        estimated_prompt_tokens=100,
        estimated_completion_tokens=0,
        estimated_cost_usd=0.05,
        rerouted=False,
        call_mode="agent",
    )
    assert call.call_mode == "agent"
    assert call.mode_exact_capable is False
    assert call.exactness_state == "exact_unavailable"

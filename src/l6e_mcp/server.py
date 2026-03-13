"""l6e-mcp server — session-scoped budget enforcement via FastMCP."""
from __future__ import annotations

import os
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from l6e._log import LocalRunLog
from l6e._types import BudgetMode, PipelinePolicy, UnknownModelPricingMode

from l6e_mcp.contracts.exactness import ExactnessState
from l6e_mcp.contracts.usage_report import UsageReport
from l6e_mcp.core.authorization import authorize_call
from l6e_mcp.core.exactness import run_exactness_state
from l6e_mcp.core.reconciliation import reconcile_usage_report
from l6e_mcp.session_store import LocalSessionStore, SessionState, session_run_summary

mcp = FastMCP(
    name="l6e-budget",
    instructions=(
        "l6e enforces session budgets for AI coding assistants. "
        "Every task follows one lifecycle: "
        "l6e_run_start (once, before any work) → "
        "l6e_authorize_call (blocking gate before sub-agents and stage transitions) / "
        "l6e_run_status (lightweight spend check within a stage) → "
        "l6e_run_end (once, at task end). "
        "l6e_run_end is mandatory even on failure or cancellation — "
        "it is the only way to flush the run log. "
        "l6e_authorize_call returns allow, reroute, or halt; "
        "always honor the decision before proceeding."
    ),
)


def _make_session_id(client: str = "unknown") -> str:
    token = secrets.token_hex(4)
    return f"session_{client}_{date.today().isoformat()}_{token}"


def _get_log_path() -> Path | None:
    raw = os.environ.get("L6E_LOG_PATH")
    return Path(raw) if raw else None


def _get_session_store() -> LocalSessionStore:
    return LocalSessionStore()


def _require_session(session_id: str) -> SessionState:
    try:
        return _get_session_store().require_active_session(session_id)
    except KeyError as exc:
        raise ToolError(exc.args[0]) from exc


def _budget_pressure(pct_used: float) -> str:
    if pct_used < 50.0:
        return "low"
    if pct_used < 80.0:
        return "moderate"
    if pct_used < 95.0:
        return "high"
    return "critical"


def _spend_snapshot(session: SessionState) -> dict:
    calls = _get_session_store().list_calls_for_session(session.session_id)
    summary = session_run_summary(session, calls)
    spent = summary.total_cost
    budget = session.policy.budget
    remaining = budget - spent
    pct_used = (spent / budget * 100.0) if budget > 0 else 0.0
    return {
        "spent_usd": round(spent, 6),
        "remaining_usd": round(remaining, 6),
        "budget_usd": budget,
        "budget_pressure": _budget_pressure(pct_used),
        "pct_used": round(pct_used, 2),
        "calls_made": summary.calls_made,
        "reroutes": summary.reroutes,
    }


@mcp.tool
def l6e_run_start(
    budget_usd: Annotated[float, "Hard budget ceiling in USD for this session"],
    model: Annotated[str, "Billing model ID for this session"],
    client: Annotated[
        str,
        "MCP client name for session_id labelling — e.g. cursor, claude-code, windsurf",
    ] = "unknown",
    accounting_mode: Annotated[
        str | None,
        "Optional accounting mode: estimate_only, exact_optional, or exact_required.",
    ] = None,
    usage_channel: Annotated[
        str | None,
        "Optional usage channel: none, hosted_edge, self_hosted_relay, or manual_import.",
    ] = None,
    ask_mode_exact_capable: Annotated[
        bool | None,
        "Optional override for Ask-mode exactness capability.",
    ] = None,
    plan_mode_exact_capable: Annotated[
        bool | None,
        "Optional override for Plan-mode exactness capability.",
    ] = None,
    agent_mode_exact_capable: Annotated[
        bool | None,
        "Optional override for Agent-mode exactness capability.",
    ] = None,
    unknown_model_pricing_mode: Annotated[
        str,
        "Unknown pricing policy mode: warn_only, reroute_required, or halt_on_unknown_pricing.",
    ] = "warn_only",
) -> dict:
    """Start a new budget-enforced session. Call once at the start of every task before any other work. Returns session_id in the response — store it and pass it to all subsequent l6e calls. Do NOT pass session_id or task_description as inputs; they are not valid parameters for this tool."""  # noqa: E501 — MCP tool docstring surfaces verbatim to agents; truncating it degrades guidance quality
    model = model.strip() or "unknown"
    try:
        pricing_mode = UnknownModelPricingMode(unknown_model_pricing_mode)
    except ValueError as exc:
        raise ToolError(
            "unknown_model_pricing_mode must be one of: "
            "warn_only, reroute_required, halt_on_unknown_pricing"
        ) from exc
    session_id = _make_session_id(client)
    policy = PipelinePolicy(
        budget=budget_usd,
        budget_mode=BudgetMode.WARN,
        unknown_model_pricing_mode=pricing_mode,
    )
    log_path = _get_log_path()
    _get_session_store().create_session(
        session_id=session_id,
        model=model,
        policy=policy,
        source="mcp",
        log_path=str(log_path) if log_path is not None else None,
        accounting_mode=accounting_mode,
        usage_channel=usage_channel,
        ask_mode_exact_capable=ask_mode_exact_capable,
        plan_mode_exact_capable=plan_mode_exact_capable,
        agent_mode_exact_capable=agent_mode_exact_capable,
    )
    return {"session_id": session_id}


@mcp.tool
def l6e_authorize_call(
    session_id: Annotated[str, "Session ID from l6e_run_start"],
    tool_name: Annotated[str, "Name of the tool or stage about to run — pass the stage label here (e.g. 'planning', 'implement'). This is NOT a 'stage' parameter; the field is called tool_name."],  # noqa: E501 — Annotated string is the MCP parameter description shown verbatim to agents; must be unambiguous
    estimated_tokens: Annotated[int, "Estimated prompt token count for this call"] = 2000,
    estimated_prompt_tokens: Annotated[
        int | None,
        "Optional explicit prompt token estimate.",
    ] = None,
    estimated_completion_tokens: Annotated[
        int | None,
        "Optional explicit completion token estimate.",
    ] = None,
    actor_type: Annotated[
        str,
        "Optional actor type for attribution. Use 'subagent' for child agent work.",
    ] = "parent_agent",
    actor_id: Annotated[
        str | None,
        "Optional stable sub-agent identifier shared across that child agent's calls.",
    ] = None,
    actor_name: Annotated[
        str | None,
        "Optional display name for the child agent making this call.",
    ] = None,
    parent_call_id: Annotated[
        str | None,
        "Optional parent call that launched this child agent or delegated this work.",
    ] = None,
    call_mode: Annotated[
        str | None,
        "Optional host mode for this call (ask, plan, or agent).",
    ] = None,
    actual_prompt_tokens: Annotated[
        int | None,
        "Actual prompt tokens from a completed LLM call. "
        "When provided together with actual_completion_tokens, records a reconciled "
        "call directly for cost accounting.",
    ] = None,
    actual_completion_tokens: Annotated[
        int | None,
        "Actual completion tokens from a completed LLM call. "
        "Must be provided alongside actual_prompt_tokens to take effect.",
    ] = None,
) -> dict:
    """Blocking gate: call before launching any sub-agent (actor_type='subagent') and at every stage boundary (planning, search, implement, test, debug). Pass the stage label in tool_name — there is no 'stage' parameter. Returns allow, reroute, or halt — proceed only on allow."""  # noqa: E501 — MCP tool docstring surfaces verbatim to agents; truncating it degrades guidance quality
    session = _require_session(session_id)
    store = _get_session_store()
    decision = authorize_call(
        store=store,
        session=session,
        tool_name=tool_name,
        estimated_tokens=estimated_tokens,
        estimated_prompt_tokens=estimated_prompt_tokens,
        estimated_completion_tokens=estimated_completion_tokens,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_name=actor_name,
        parent_call_id=parent_call_id,
        call_mode=call_mode,
        actual_prompt_tokens=actual_prompt_tokens,
        actual_completion_tokens=actual_completion_tokens,
    )
    store.increment_checkpoint_calls(session_id)

    snapshot = _spend_snapshot(session)
    result = {
        "action": decision.action,
        "remaining_usd": snapshot["remaining_usd"],
        "budget_pressure": snapshot["budget_pressure"],
        "reason": decision.reason,
    }
    if decision.pricing_warning is not None:
        result["pricing_warning"] = decision.pricing_warning
    if decision.call_id is not None:
        result["call_id"] = decision.call_id
    if decision.action == "reroute" and decision.target_model is not None:
        result["target_model"] = decision.target_model
    return result


@mcp.tool
def l6e_record_usage(
    call_id: Annotated[str, "Call ID from a previous l6e_authorize_call result"],
    actual_prompt_tokens: Annotated[int, "Actual prompt tokens for the completed call"],
    actual_completion_tokens: Annotated[int, "Actual completion tokens for the completed call"],
    model_used: Annotated[
        str | None,
        "Optional actual model used for the completed call. Defaults to the stored model_used.",
    ] = None,
    callback_request_id: Annotated[
        str | None,
        "Optional provider request ID for auditability and correlation diagnostics.",
    ] = None,
    callback_trace_id: Annotated[
        str | None,
        "Optional provider trace ID for auditability and correlation diagnostics.",
    ] = None,
    correlation_key: Annotated[
        str | None,
        "Optional correlation key extracted from callback metadata or request tags.",
    ] = None,
    correlation_source: Annotated[
        str | None,
        "Optional source for the correlation key, such as spend_logs_metadata or request_tags.",
    ] = None,
    hosted_ledger_id: Annotated[
        str | None,
        "Optional hosted-ledger identifier for this exact usage record.",
    ] = None,
    idempotency_key: Annotated[
        str | None,
        "Optional idempotency key from usage ingestion.",
    ] = None,
) -> dict:
    """Reconcile a pending call with actual token usage after the call completes. Idempotent for the same values on the same call_id."""  # noqa: E501 — MCP tool docstring surfaces verbatim to agents; truncating it degrades guidance quality
    store = _get_session_store()
    existing = store.get_call(call_id)
    if existing is None:
        raise ToolError(f"Unknown call '{call_id}'.")
    session = store.get_session(existing.session_id)
    if session is None:
        raise ToolError(f"Unknown session '{existing.session_id}'. Call l6e_run_start first.")

    report = UsageReport(
        call_id=call_id,
        usage_source=correlation_source or "direct_tool",
        model_used=model_used or existing.model_used,
        prompt_tokens=actual_prompt_tokens,
        completion_tokens=actual_completion_tokens,
        provider_request_id=callback_request_id,
        provider_trace_id=callback_trace_id,
        hosted_ledger_id=hosted_ledger_id,
        idempotency_key=idempotency_key,
    )
    try:
        reconciled = reconcile_usage_report(
            store=store,
            session=session,
            existing_call=existing,
            report=report,
        )
    except KeyError as exc:
        raise ToolError(exc.args[0]) from exc

    snapshot = _spend_snapshot(session)
    return {
        "call_id": reconciled.call_id,
        "session_id": reconciled.session_id,
        "status": reconciled.status,
        "exactness_state": reconciled.exactness_state,
        "spend_so_far_usd": snapshot["spent_usd"],
        "remaining_usd": snapshot["remaining_usd"],
        "budget_pressure": snapshot["budget_pressure"],
    }


@mcp.tool
def l6e_run_status(
    session_id: Annotated[str, "Session ID from l6e_run_start"],
    estimated_prompt_tokens: Annotated[
        int | None,
        "Estimated prompt tokens for the next stage. Providing this forces a cost-aware "
        "assessment before you proceed — omitting it reduces budget accuracy.",
    ] = None,
    estimated_completion_tokens: Annotated[
        int | None,
        "Estimated completion tokens for the next stage. Provide alongside "
        "estimated_prompt_tokens for best-effort spend projection.",
    ] = None,
) -> dict:
    """Read-only spend snapshot. No call recorded, no gate action. Use within a stage to monitor budget pressure without burning a checkpoint. Pass estimated_prompt_tokens and estimated_completion_tokens for the next stage to force a cost-aware assessment."""  # noqa: E501 — MCP tool docstring surfaces verbatim to agents; truncating it degrades guidance quality
    store = _get_session_store()
    try:
        store.increment_status_calls(session_id)
    except KeyError as exc:
        raise ToolError(exc.args[0]) from exc
    session = _require_session(session_id)
    snapshot = _spend_snapshot(session)
    return {
        "budget_pressure": snapshot["budget_pressure"],
        "remaining_usd": snapshot["remaining_usd"],
        "pct_used": snapshot["pct_used"],
    }


@mcp.tool
def l6e_run_end(
    session_id: Annotated[str, "Session ID from l6e_run_start"],
) -> dict:
    """End the session and flush the run log. Call at task end, including on failure or cancellation — this is the only way to persist the run log."""  # noqa: E501 — MCP tool docstring surfaces verbatim to agents; truncating it degrades guidance quality
    store = _get_session_store()
    session = store.get_session(session_id)
    if session is None or session.state == "finalized":
        raise ToolError(
            f"Unknown session '{session_id}'. "
            "Already ended or never started."
        )
    calls = store.list_calls_for_session(session_id)
    summary = session_run_summary(session, calls)
    log = (
        LocalRunLog(path=Path(session.log_path))
        if session.log_path is not None
        else LocalRunLog()
    )
    try:
        store.finalize_session(session_id)
    except KeyError as exc:
        raise ToolError(exc.args[0]) from exc
    log.append(summary)

    call_exactness_states = [ExactnessState(c.exactness_state) for c in calls]
    run_exactness = run_exactness_state(call_exactness_states)
    pending_exact_calls = sum(
        1 for c in calls if c.exactness_state == ExactnessState.EXACT_PENDING
    )
    reconciled_times = [c.reconciled_at for c in calls if c.reconciled_at is not None]
    last_reconciled_at = max(reconciled_times) if reconciled_times else None
    mode_coverage = {
        "ask_mode_exact_capable": session.ask_mode_exact_capable,
        "plan_mode_exact_capable": session.plan_mode_exact_capable,
        "agent_mode_exact_capable": session.agent_mode_exact_capable,
    }
    mode_coverage_gaps = [
        mode
        for mode, capable in [
            ("ask", session.ask_mode_exact_capable),
            ("plan", session.plan_mode_exact_capable),
            ("agent", session.agent_mode_exact_capable),
        ]
        if not capable
    ]
    return {
        "session_id": session_id,
        "total_cost_usd": round(summary.total_cost, 6),
        "calls_made": summary.calls_made,
        "savings_confidence": summary.savings_confidence,
        "pending_exact_calls": pending_exact_calls,
        "exactness_state": run_exactness.value,
        "last_reconciled_at": last_reconciled_at,
        "mode_coverage": mode_coverage,
        "mode_coverage_gaps": mode_coverage_gaps,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

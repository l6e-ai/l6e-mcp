"""l6e-mcp server — session-scoped budget enforcement via FastMCP."""
from __future__ import annotations

import dataclasses
import json
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from l6e._log import LocalRunLog
from l6e._types import BudgetMode, PipelinePolicy, UnknownModelPricingMode
from l6e.costs import LiteLLMCostEstimator
from l6e.gate import ConstraintGate
from l6e.router import LocalRouter
from l6e.store import InMemoryRunStore

from l6e_mcp.contracts.correlation_envelope import CorrelationEnvelope
from l6e_mcp.contracts.exactness import ExactnessState
from l6e_mcp.contracts.mode_coverage import ModeCoverage
from l6e_mcp.contracts.usage_report import UsageReport
from l6e_mcp.core.authorization import authorize_call
from l6e_mcp.core.exactness import run_exactness_state
from l6e_mcp.core.reconciliation import reconcile_usage_report
from l6e_mcp.session_store import LocalSessionStore, SessionState, session_run_summary

mcp = FastMCP(
    name="l6e-budget",
    instructions=(
        "l6e enforces session budgets for AI coding assistants. "
        "Call l6e_run_start at the beginning of a task to set a USD budget. "
        "Call l6e_authorize_call before each expensive tool call to get a routing decision. "
        "Call l6e_run_status at any time to check how much has been spent. "
        "Call l6e_run_end when the task is complete to flush the run log. "
        "IMPORTANT: always call l6e_run_end — it is the only way to write the run log."
    ),
)

# Retained only for backward-compatibility in tests; persisted state is authoritative.
_sessions: dict[str, tuple] = {}

_ACTIVE_SESSION_FILE = Path.home() / ".l6e" / "active_session"
_ACTIVE_CALL_FILE = Path.home() / ".l6e" / "active_call"


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


def _runtime(session: SessionState):
    estimator = LiteLLMCostEstimator(
        fallback_cost_per_1k_tokens=session.policy.unknown_model_cost_per_1k_tokens
    )
    store = InMemoryRunStore(
        run_id=session.session_id,
        policy=session.policy,
        estimator=estimator,
        source=session.source,
    )
    for call in _get_session_store().list_calls_for_session(session.session_id):
        store.record_call(call.effective_record())
    gate = ConstraintGate(policy=session.policy, router=LocalRouter())
    return estimator, gate, store


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
    estimator = LiteLLMCostEstimator(
        fallback_cost_per_1k_tokens=session.policy.unknown_model_cost_per_1k_tokens
    )
    spent = summary.total_cost
    budget = session.policy.budget
    remaining = budget - spent
    pct_used = (spent / budget * 100.0) if budget > 0 else 0.0
    call_exactness = [ExactnessState(call.exactness_state) for call in calls]
    run_exactness = run_exactness_state(call_exactness)
    pending_exact_calls = sum(
        1 for state in call_exactness if state == ExactnessState.EXACT_PENDING
    )
    unavailable_exact_calls = sum(
        1 for state in call_exactness if state == ExactnessState.EXACT_UNAVAILABLE
    )
    last_reconciled = max(
        (call.reconciled_at for call in calls if call.reconciled_at),
        default=None,
    )
    exactness_breakdown = {
        ExactnessState.ESTIMATE_ONLY.value: sum(
            1 for state in call_exactness if state == ExactnessState.ESTIMATE_ONLY
        ),
        ExactnessState.EXACT_PENDING.value: pending_exact_calls,
        ExactnessState.EXACT_RECORDED.value: sum(
            1 for state in call_exactness if state == ExactnessState.EXACT_RECORDED
        ),
        ExactnessState.EXACT_UNAVAILABLE.value: unavailable_exact_calls,
    }
    mode_coverage = ModeCoverage(
        ask_mode_exact_capable=session.ask_mode_exact_capable,
        plan_mode_exact_capable=session.plan_mode_exact_capable,
        agent_mode_exact_capable=session.agent_mode_exact_capable,
    )
    coverage_gaps: list[str] = []
    if not mode_coverage.ask_mode_exact_capable:
        coverage_gaps.append("ask")
    if not mode_coverage.plan_mode_exact_capable:
        coverage_gaps.append("plan")
    if not mode_coverage.agent_mode_exact_capable:
        coverage_gaps.append("agent")
    exactness_reason = "fully_exact_coverage"
    if run_exactness.value == "partial_exact":
        exactness_reason = "mixed_exact_and_non_exact_calls"
    elif run_exactness.value == "exactness_degraded":
        exactness_reason = "non_exact_capable_modes_or_unmatched_usage"
    elif run_exactness.value == "all_estimate_only":
        exactness_reason = "estimate_only_or_no_reconciliation"
    pricing_warnings: list[dict[str, str]] = []
    warned_models: set[str] = set()
    for call in calls:
        model = call.model_used
        if model in warned_models:
            continue
        meta = estimator.estimate_with_metadata(
            model=model,
            prompt_tokens=max(1, call.estimated_prompt_tokens),
            completion_tokens=max(0, call.estimated_completion_tokens),
            emit_warning=False,
        )
        if meta.model_pricing_known:
            continue
        warned_models.add(model)
        pricing_warnings.append(
            {
                "model": model,
                "pricing_source": meta.pricing_source,
                "pricing_confidence": meta.pricing_confidence,
                "warning": meta.warning or "unknown model pricing",
            }
        )
    return {
        "spent_usd": round(spent, 6),
        "remaining_usd": round(remaining, 6),
        "budget_usd": budget,
        "calls_made": summary.calls_made,
        "reroutes": summary.reroutes,
        "budget_pressure": _budget_pressure(pct_used),
        "pct_used": round(pct_used, 2),
        "subagent_calls": summary.subagent_calls,
        "subagent_spend_usd": round(summary.subagent_spend_usd, 6),
        "subagents": [dataclasses.asdict(subagent) for subagent in summary.subagents],
        "exactness_state": run_exactness.value,
        "exactness_reason": exactness_reason,
        "exactness_breakdown": exactness_breakdown,
        "pending_exact_calls": pending_exact_calls,
        "unavailable_exact_calls": unavailable_exact_calls,
        "last_reconciled_at": round(last_reconciled, 6) if last_reconciled is not None else None,
        "exact_calls": sum(1 for state in call_exactness if state.value == "exact_recorded"),
        "mode_coverage": mode_coverage.as_dict(),
        "mode_coverage_gaps": coverage_gaps,
        "pricing_warnings": pricing_warnings,
    }


def _write_active_call(session_id: str, call_id: str) -> None:
    _ACTIVE_CALL_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_CALL_FILE.write_text(
        json.dumps({"session_id": session_id, "call_id": call_id}),
        encoding="utf-8",
    )


def _correlation_envelope(
    session_id: str,
    call_id: str,
    tool_name: str,
    *,
    actor_type: str = "parent_agent",
    actor_id: str | None = None,
    actor_name: str | None = None,
    parent_call_id: str | None = None,
) -> dict:
    spend_logs_metadata = {
        "l6e_call_id": call_id,
        "l6e_session_id": session_id,
        "l6e_tool_name": tool_name,
        "l6e_actor_type": actor_type,
    }
    request_tags = [
        f"l6e_call_id:{call_id}",
        f"l6e_session_id:{session_id}",
        f"l6e_tool_name:{tool_name}",
        f"l6e_actor_type:{actor_type}",
    ]
    if actor_id is not None:
        spend_logs_metadata["l6e_actor_id"] = actor_id
        request_tags.append(f"l6e_actor_id:{actor_id}")
    if actor_name is not None:
        spend_logs_metadata["l6e_actor_name"] = actor_name
        request_tags.append(f"l6e_actor_name:{actor_name}")
    if parent_call_id is not None:
        spend_logs_metadata["l6e_parent_call_id"] = parent_call_id
        request_tags.append(f"l6e_parent_call_id:{parent_call_id}")
    return CorrelationEnvelope(
        call_id=call_id,
        metadata={"spend_logs_metadata": spend_logs_metadata},
        request_tags=request_tags,
    ).as_dict()


def _clear_active_session_file(session_id: str) -> None:
    try:
        if (
            _ACTIVE_SESSION_FILE.exists()
            and _ACTIVE_SESSION_FILE.read_text(encoding="utf-8").strip() == session_id
        ):
            _ACTIVE_SESSION_FILE.unlink()
    except OSError:
        pass


def _clear_active_call_file(session_id: str) -> None:
    try:
        if not _ACTIVE_CALL_FILE.exists():
            return
        raw = _ACTIVE_CALL_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if payload.get("session_id") == session_id:
            _ACTIVE_CALL_FILE.unlink()
    except OSError:
        pass


@mcp.tool
def l6e_run_start(
    budget_usd: Annotated[float, "Hard budget ceiling in USD for this session"],
    model: Annotated[str, "Billing model ID for this session"],
    client: Annotated[
        str,
        "MCP client name for session_id labelling — e.g. cursor, claude-code, windsurf",
    ] = "unknown",
    proxy_mode: Annotated[
        bool,
        "When True, enables self-hosted relay reconciliation paths. "
        "Legacy active-file fallback is controlled by advanced_fallback. Default: False.",
    ] = False,
    advanced_fallback: Annotated[
        bool,
        "Enable legacy active_session/active_call fallback handshake. Default: False.",
    ] = False,
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
    """Start a new budget-enforced session. Returns session_id to pass to other tools."""
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
        proxy_mode=proxy_mode,
        accounting_mode=accounting_mode,
        usage_channel=usage_channel,
        advanced_fallback_enabled=advanced_fallback,
        ask_mode_exact_capable=ask_mode_exact_capable,
        plan_mode_exact_capable=plan_mode_exact_capable,
        agent_mode_exact_capable=agent_mode_exact_capable,
    )

    if proxy_mode and advanced_fallback:
        _ACTIVE_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ACTIVE_SESSION_FILE.write_text(session_id, encoding="utf-8")

    result = {
        "session_id": session_id,
        "budget_usd": budget_usd,
        "model": model,
        "accounting_mode": accounting_mode or ("exact_optional" if proxy_mode else "estimate_only"),
        "usage_channel": usage_channel or ("self_hosted_relay" if proxy_mode else "none"),
        "advanced_fallback_enabled": advanced_fallback,
        "fallback_correlation_capability": "active_file" if advanced_fallback else "metadata_only",
        "unknown_model_pricing_mode": policy.unknown_model_pricing_mode.value,
    }
    if proxy_mode and advanced_fallback:
        result["proxy_mode"] = True
        result["active_session_file"] = str(_ACTIVE_SESSION_FILE)
        result["active_call_file"] = str(_ACTIVE_CALL_FILE)
    return result


@mcp.tool
def l6e_authorize_call(
    session_id: Annotated[str, "Session ID from l6e_run_start"],
    tool_name: Annotated[str, "Name of the tool or stage about to run"],
    estimated_tokens: Annotated[int, "Estimated prompt token count for this call"] = 500,
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
    """Check whether to allow, reroute, or halt before an expensive tool call."""
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
    call_id = decision.call_id
    use_actual = (
        actual_prompt_tokens is not None and actual_completion_tokens is not None
    )
    if (
        call_id is not None
        and session.proxy_mode
        and session.advanced_fallback_enabled
        and not use_actual
    ):
        _write_active_call(session_id, call_id)

    snapshot = _spend_snapshot(session)
    result = {
        "action": decision.action,
        "spend_so_far_usd": snapshot["spent_usd"],
        "remaining_usd": snapshot["remaining_usd"],
        "budget_pressure": snapshot["budget_pressure"],
        "reason": decision.reason,
        "exactness_state": snapshot["exactness_state"],
    }
    if decision.pricing_warning is not None:
        result["pricing_warning"] = decision.pricing_warning
    if decision.pricing_confidence is not None:
        result["pricing_confidence"] = decision.pricing_confidence
    if decision.pricing_source is not None:
        result["pricing_source"] = decision.pricing_source
    if decision.model_pricing_known is not None:
        result["model_pricing_known"] = decision.model_pricing_known
    if decision.estimate_source is not None:
        result["estimate_source"] = decision.estimate_source
    if decision.estimate_prompt_tokens is not None:
        result["estimate_prompt_tokens"] = decision.estimate_prompt_tokens
    if decision.estimate_completion_tokens is not None:
        result["estimate_completion_tokens"] = decision.estimate_completion_tokens
    if decision.calibration_multiplier is not None:
        result["calibration_multiplier"] = decision.calibration_multiplier
    if decision.effective_multiplier is not None:
        result["effective_multiplier"] = decision.effective_multiplier
    if decision.estimate_reasoning_tokens is not None:
        result["estimate_reasoning_tokens"] = decision.estimate_reasoning_tokens
    if decision.internal_turns_multiplier is not None:
        result["internal_turns_multiplier"] = decision.internal_turns_multiplier
    result["pricing_warnings"] = snapshot.get("pricing_warnings", [])
    if call_id is not None:
        result["call_id"] = call_id
        result["correlation"] = _correlation_envelope(
            session_id,
            call_id,
            tool_name,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            parent_call_id=parent_call_id,
        )
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
        "Optional LiteLLM callback request ID for auditability and correlation diagnostics.",
    ] = None,
    callback_trace_id: Annotated[
        str | None,
        "Optional LiteLLM trace ID for auditability and correlation diagnostics.",
    ] = None,
    correlation_key: Annotated[
        str | None,
        "Optional correlation key extracted from callback metadata or request tags.",
    ] = None,
    correlation_source: Annotated[
        str | None,
        "Optional source for the correlation key, such as spend_logs_metadata, "
        "request_tags, or active_call fallback.",
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
    """Reconcile a pending call with actual token usage after the call completes."""
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
) -> dict:
    """Get a read-only spend snapshot. Does not record a call or advance the budget."""
    session = _require_session(session_id)
    return _spend_snapshot(session)


@mcp.tool
def l6e_run_end(
    session_id: Annotated[str, "Session ID from l6e_run_start"],
) -> dict:
    """End the session and flush the run log to .l6e/runs.jsonl (or L6E_LOG_PATH)."""
    store = _get_session_store()
    session = store.get_session(session_id)
    if session is None or session.state == "finalized":
        raise ToolError(
            f"Unknown session '{session_id}'. "
            "Already ended or never started."
        )
    summary = session_run_summary(session, store.list_calls_for_session(session_id))
    log = (
        LocalRunLog(path=Path(session.log_path))
        if session.log_path is not None
        else LocalRunLog()
    )
    log.append(summary)
    try:
        store.finalize_session(session_id)
    except KeyError as exc:
        raise ToolError(exc.args[0]) from exc
    if session.advanced_fallback_enabled:
        _clear_active_session_file(session_id)
        _clear_active_call_file(session_id)
    return {
        "session_id": session_id,
        "total_cost_usd": round(summary.total_cost, 6),
        "calls_made": summary.calls_made,
        "reroutes": summary.reroutes,
        "savings_usd": round(summary.savings_usd, 6),
        "source": summary.source,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

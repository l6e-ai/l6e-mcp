"""Budget authorization service used by MCP checkpoint transport."""
from __future__ import annotations

from dataclasses import dataclass

from l6e.costs import LiteLLMCostEstimator
from l6e.gate import ConstraintGate
from l6e.router import LocalRouter
from l6e.store import InMemoryRunStore

from l6e_mcp.session_store import LocalSessionStore, SessionState


@dataclass(frozen=True)
class AuthorizationDecision:
    """Checkpoint decision with durable call creation context."""

    action: str
    reason: str
    call_id: str | None
    target_model: str | None


def authorize_call(
    *,
    store: LocalSessionStore,
    session: SessionState,
    tool_name: str,
    estimated_tokens: int,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    parent_call_id: str | None,
    call_mode: str | None,
    actual_prompt_tokens: int | None,
    actual_completion_tokens: int | None,
) -> AuthorizationDecision:
    """Run gate check and persist a pending (or reconciled) call row."""
    estimator = LiteLLMCostEstimator(
        fallback_cost_per_1k_tokens=session.policy.unknown_model_cost_per_1k_tokens
    )
    runtime_store = InMemoryRunStore(
        run_id=session.session_id,
        policy=session.policy,
        estimator=estimator,
        source=session.source,
    )
    for call in store.list_calls_for_session(session.session_id):
        runtime_store.record_call(call.effective_record())
    gate = ConstraintGate(policy=session.policy, router=LocalRouter())

    use_actual = (
        actual_prompt_tokens is not None and actual_completion_tokens is not None
    )
    prompt_tokens = actual_prompt_tokens if use_actual else estimated_tokens
    completion_tokens = actual_completion_tokens if use_actual else 0
    estimated_cost = estimator.estimate(session.model, prompt_tokens, completion_tokens)
    decision = gate.check(
        runtime_store,
        model=session.model,
        estimated_cost=estimated_cost,
        stage=tool_name,
        complexity=None,
    )
    response_action = (
        "reroute"
        if decision.action == "allow" and decision.reason == "warn:budget_pressure"
        else decision.action
    )
    if decision.action == "halt":
        return AuthorizationDecision(
            action=response_action,
            reason=decision.reason,
            call_id=None,
            target_model=decision.target_model,
        )

    model_used = decision.target_model if decision.action == "reroute" else session.model
    call = store.create_call(
        session_id=session.session_id,
        tool_name=tool_name,
        model_requested=session.model,
        model_used=model_used,
        estimated_prompt_tokens=estimated_tokens,
        estimated_completion_tokens=0,
        estimated_cost_usd=estimator.estimate(session.model, estimated_tokens, 0),
        rerouted=decision.action == "reroute",
        actual_prompt_tokens=actual_prompt_tokens if use_actual else None,
        actual_completion_tokens=actual_completion_tokens if use_actual else None,
        actual_cost_usd=estimated_cost if use_actual else None,
        status="reconciled" if use_actual else "pending",
        actor_type=actor_type,
        actor_id=actor_id,
        actor_name=actor_name,
        parent_call_id=parent_call_id,
        call_mode=call_mode,
    )
    return AuthorizationDecision(
        action=response_action,
        reason=decision.reason,
        call_id=call.call_id,
        target_model=decision.target_model,
    )

"""Budget authorization service used by MCP checkpoint transport."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from l6e.costs import LiteLLMCostEstimator
from l6e.gate import ConstraintGate
from l6e.router import LocalRouter
from l6e.store import InMemoryRunStore

from l6e_mcp.session_store import LocalSessionStore, SessionState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthorizationDecision:
    """Checkpoint decision with durable call creation context."""

    action: str
    reason: str
    call_id: str | None
    target_model: str | None
    pricing_warning: str | None
    pricing_confidence: str | None
    pricing_source: str | None
    model_pricing_known: bool | None
    calibration_factor: float | None = None
    calibration_source: str | None = None


def authorize_call(
    *,
    store: LocalSessionStore,
    session: SessionState,
    tool_name: str,
    estimated_tokens: int | None,
    estimated_prompt_tokens: int | None,
    estimated_completion_tokens: int | None,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    parent_call_id: str | None,
    call_mode: str | None,
    actual_prompt_tokens: int | None,
    actual_completion_tokens: int | None,
    calibration_factor: float | None = None,
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

    if actual_prompt_tokens is not None and actual_completion_tokens is not None:
        use_actual = True
        prompt_tokens = actual_prompt_tokens
        completion_tokens = actual_completion_tokens
    else:
        use_actual = False
        prompt_tokens = estimated_prompt_tokens or estimated_tokens or 2000
        completion_tokens = estimated_completion_tokens or 400

    estimate_meta = estimator.estimate_with_metadata(
        session.model,
        prompt_tokens,
        completion_tokens,
    )
    estimated_cost = estimate_meta.cost_usd

    applied_factor: float | None = None
    if calibration_factor is not None and calibration_factor > 0:
        estimated_cost = estimated_cost * Decimal(str(calibration_factor))
        applied_factor = calibration_factor
        logger.debug(
            "manual_calibration_applied",
            extra={"model": session.model, "factor": calibration_factor},
        )

    if not estimate_meta.model_pricing_known:
        mode = session.policy.unknown_model_pricing_mode.value
        if mode == "halt_on_unknown_pricing":
            return AuthorizationDecision(
                action="halt",
                reason="unknown_model_pricing:halt",
                call_id=None,
                target_model=None,
                pricing_warning=estimate_meta.warning,
                pricing_confidence=estimate_meta.pricing_confidence,
                pricing_source=estimate_meta.pricing_source,
                model_pricing_known=False,
            )
        if mode == "reroute_required":
            local_model = LocalRouter().best_local_model()
            if local_model is None:
                return AuthorizationDecision(
                    action="halt",
                    reason="unknown_model_pricing:no_local_model",
                    call_id=None,
                    target_model=None,
                    pricing_warning=estimate_meta.warning,
                    pricing_confidence=estimate_meta.pricing_confidence,
                    pricing_source=estimate_meta.pricing_source,
                    model_pricing_known=False,
                )
            local_meta = estimator.estimate_with_metadata(
                local_model,
                prompt_tokens,
                completion_tokens,
            )
            if not local_meta.model_pricing_known:
                return AuthorizationDecision(
                    action="halt",
                    reason="unknown_model_pricing:local_model_unpriced",
                    call_id=None,
                    target_model=None,
                    pricing_warning=estimate_meta.warning,
                    pricing_confidence=estimate_meta.pricing_confidence,
                    pricing_source=estimate_meta.pricing_source,
                    model_pricing_known=False,
                )
            call = store.create_call(
                session_id=session.session_id,
                tool_name=tool_name,
                model_requested=session.model,
                model_used=local_model,
                estimated_prompt_tokens=prompt_tokens if prompt_tokens is not None else 0,
                estimated_completion_tokens=(
                    completion_tokens if completion_tokens is not None else 0
                ),
                estimated_cost_usd=local_meta.cost_usd,
                rerouted=True,
                actual_prompt_tokens=actual_prompt_tokens if use_actual else None,
                actual_completion_tokens=actual_completion_tokens if use_actual else None,
                actual_cost_usd=local_meta.cost_usd if use_actual else None,
                status="reconciled" if use_actual else "pending",
                actor_type=actor_type,
                actor_id=actor_id,
                actor_name=actor_name,
                parent_call_id=parent_call_id,
                call_mode=call_mode,
            )
            return AuthorizationDecision(
                action="reroute",
                reason="unknown_model_pricing:reroute_required",
                call_id=call.call_id,
                target_model=local_model,
                pricing_warning=estimate_meta.warning,
                pricing_confidence=estimate_meta.pricing_confidence,
                pricing_source=estimate_meta.pricing_source,
                model_pricing_known=False,
            )

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
            pricing_warning=estimate_meta.warning,
            pricing_confidence=estimate_meta.pricing_confidence,
            pricing_source=estimate_meta.pricing_source,
            model_pricing_known=estimate_meta.model_pricing_known,
        )

    model_used = decision.target_model if decision.action == "reroute" else session.model
    model_cost_meta = estimator.estimate_with_metadata(
        model_used,
        prompt_tokens,
        completion_tokens,
    )
    final_cost = model_cost_meta.cost_usd
    if applied_factor is not None:
        final_cost = final_cost * Decimal(str(applied_factor))

    call = store.create_call(
        session_id=session.session_id,
        tool_name=tool_name,
        model_requested=session.model,
        model_used=model_used,
        estimated_prompt_tokens=prompt_tokens if prompt_tokens is not None else 0,
        estimated_completion_tokens=completion_tokens if completion_tokens is not None else 0,
        estimated_cost_usd=final_cost,
        rerouted=response_action == "reroute",
        actual_prompt_tokens=actual_prompt_tokens if use_actual else None,
        actual_completion_tokens=actual_completion_tokens if use_actual else None,
        actual_cost_usd=model_cost_meta.cost_usd if use_actual else None,
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
        pricing_warning=estimate_meta.warning,
        pricing_confidence=estimate_meta.pricing_confidence,
        pricing_source=estimate_meta.pricing_source,
        model_pricing_known=estimate_meta.model_pricing_known,
        calibration_factor=applied_factor,
        calibration_source="manual" if applied_factor is not None else None,
    )

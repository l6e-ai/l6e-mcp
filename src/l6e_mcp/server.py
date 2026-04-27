"""l6e-mcp server — session-scoped budget enforcement via FastMCP."""
from __future__ import annotations

import atexit
import logging
import math
import os
import secrets
import threading
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Generic, TypeVar

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from l6e._log import LocalRunLog
from l6e._types import BudgetMode, PipelinePolicy, UnknownModelPricingMode
from l6e.costs import LiteLLMCostEstimator, refresh_model_cost_map_async

from l6e_mcp import config as _config
from l6e_mcp import outbox as _outbox
from l6e_mcp.contracts.exactness import ExactnessState
from l6e_mcp.core.authorization import authorize_call
from l6e_mcp.core.calibration_cache import CalibrationCache
from l6e_mcp.core.exactness import run_exactness_state
from l6e_mcp.core.remote_authorize import try_remote_authorize
from l6e_mcp.core.session_report_worker import SessionReportWorker
from l6e_mcp.core.status_telemetry import StatusTelemetryPayload, StatusTelemetryWorker
from l6e_mcp.session_store import (
    LocalSessionStore,
    ReconcileRequest,
    SessionState,
    session_run_summary,
)
from l6e_mcp.store import schema as store_schema
from l6e_mcp.store.summary import build_session_report

_logger = logging.getLogger(__name__)

# Captured at module import — proxy for MCP server process start time. Used
# only by the read-only diagnostic tool ``l6e_debug_pricing_state`` to make
# audit trails unambiguous about which process produced which output.
_PROCESS_STARTED_UNIX = time.time()

mcp = FastMCP(
    name="l6e-budget",
    instructions=(
        "l6e enforces session budgets for AI coding assistants. "
        "Every task follows one lifecycle: "
        "l6e_run_start (once, before any work) → "
        "l6e_authorize_call (blocking gate at stage boundaries; "
        "pass check_only=True for lightweight mid-stage pressure checks) → "
        "l6e_run_end (once, at task end). "
        "l6e_run_end is mandatory even on failure or cancellation — "
        "it is the only way to flush the run log. "
        "l6e_authorize_call returns allow, reroute, or halt; "
        "always honor the decision before proceeding."
    ),
)

_BACKGROUND_SYNC_DEADLINE_SECONDS = 30


def _make_session_id(client: str = "unknown") -> str:
    """Generate an opaque session identifier. Do not parse the format."""
    token = secrets.token_hex(4)
    return f"session_{client}_{date.today().isoformat()}_{token}"


def _get_log_path() -> Path | None:
    raw = os.environ.get("L6E_LOG_PATH")
    return Path(raw) if raw else None


_T = TypeVar("_T")


class _Singleton(Generic[_T]):
    """Thread-safe lazy singleton with double-checked locking and optional teardown."""

    __slots__ = ("_factory", "_teardown", "_instance", "_lock")

    def __init__(
        self,
        factory: Callable[[], _T] | None = None,
        teardown: Callable[[_T], None] | None = None,
    ) -> None:
        self._factory = factory
        self._teardown = teardown
        self._instance: _T | None = None
        self._lock = threading.Lock()

    @property
    def instance(self) -> _T | None:
        return self._instance

    def get(self) -> _T:
        inst = self._instance
        if inst is not None:
            return inst
        if self._factory is None:
            raise RuntimeError("No factory configured; use get_or() with an explicit factory.")
        with self._lock:
            if self._instance is not None:
                return self._instance
            self._instance = self._factory()
            return self._instance

    def get_or(self, factory: Callable[[], _T]) -> _T:
        """Like get(), but uses a caller-supplied factory instead of the default."""
        inst = self._instance
        if inst is not None:
            return inst
        with self._lock:
            if self._instance is not None:
                return self._instance
            self._instance = factory()
            return self._instance

    def reset(self) -> None:
        with self._lock:
            inst = self._instance
            if inst is not None and self._teardown is not None:
                self._teardown(inst)
            self._instance = None


_store = _Singleton(LocalSessionStore)
_calibration_cache = _Singleton(CalibrationCache)
_telemetry_worker: _Singleton[StatusTelemetryWorker] = _Singleton(
    teardown=lambda w: w.shutdown(timeout=0.5),
)
_report_worker: _Singleton[SessionReportWorker] = _Singleton(
    teardown=lambda w: w.shutdown(timeout=0.5),
)
_report_worker_atexit_registered = False


def _get_session_store() -> LocalSessionStore:
    return _store.get()


def _reset_session_store() -> None:
    _store.reset()


def _get_calibration_cache() -> CalibrationCache:
    return _calibration_cache.get()


def _reset_calibration_cache() -> None:
    _calibration_cache.reset()


def _get_telemetry_worker() -> StatusTelemetryWorker | None:
    """Return the telemetry worker if cloud sync is enabled, else None."""
    api_key = _config.get_api_key()
    if not api_key or not _config.is_cloud_sync_enabled():
        return None
    return _telemetry_worker.get_or(
        lambda: StatusTelemetryWorker(api_key=api_key, endpoint=_config.get_cloud_endpoint())
    )


def _reset_telemetry_worker() -> None:
    _telemetry_worker.reset()


def _get_report_worker() -> SessionReportWorker | None:
    """Return the report worker if cloud sync is enabled, else None."""
    global _report_worker_atexit_registered  # noqa: PLW0603
    api_key = _config.get_api_key()
    if not api_key or not _config.is_cloud_sync_enabled():
        return None

    def _factory() -> SessionReportWorker:
        global _report_worker_atexit_registered  # noqa: PLW0603
        w = SessionReportWorker(api_key=api_key, endpoint=_config.get_cloud_endpoint())
        if not _report_worker_atexit_registered:
            atexit.register(_shutdown_report_worker)
            _report_worker_atexit_registered = True
        return w

    return _report_worker.get_or(_factory)


def _shutdown_report_worker() -> None:
    """Flush pending session reports on clean process exit."""
    inst = _report_worker.instance
    if inst is not None:
        inst.shutdown(timeout=5.0)


def _reset_report_worker() -> None:
    global _report_worker_atexit_registered  # noqa: PLW0603
    _report_worker.reset()
    _report_worker_atexit_registered = False


def _require_session(
    session_id: str, store: LocalSessionStore | None = None,
) -> SessionState:
    store = store or _get_session_store()
    try:
        return store.require_active_session(session_id)
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


def _spend_snapshot(
    session: SessionState,
    store: LocalSessionStore | None = None,
    calls: list | None = None,
) -> dict:
    store = store or _get_session_store()
    if calls is None:
        calls = store.list_calls_for_session(session.session_id)
    summary = session_run_summary(session, calls)
    spent = summary.total_cost
    budget = Decimal(str(session.policy.budget))
    remaining = budget - spent
    pct_used = (spent / budget * 100) if budget > 0 else Decimal("0")
    return {
        "spent_usd": float(round(spent, 6)),
        "remaining_usd": float(round(remaining, 6)),
        "budget_usd": session.policy.budget,
        "budget_pressure": _budget_pressure(float(pct_used)),
        "pct_used": float(round(pct_used, 2)),
        "calls_made": summary.calls_made,
        "reroutes": summary.reroutes,
    }


@mcp.tool(timeout=10)
async def l6e_run_start(
    budget_usd: Annotated[float, "Hard budget ceiling in USD for this session"],
    model: Annotated[str, "Billing model ID for this session"],
    client: Annotated[
        str,
        "MCP client name for session_id labelling — e.g. cursor, claude-code, windsurf",
    ] = "unknown",
    task_summary: Annotated[
        str | None,
        "Optional 5-10 word task label, like a commit subject. Null is fine.",
    ] = None,
    parent_session_id: Annotated[
        str | None,
        "Optional session_id of a parent/manager session. Use for multi-session orchestration "
        "where a coordinator spawns child sessions with independent budgets.",
    ] = None,
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
    """Start a new budget-enforced session. Call once at the start of every task before any other work. Returns session_id in the response — store it and pass it to all subsequent l6e calls. Do NOT pass session_id or task_description — use task_summary for a brief task label."""  # noqa: E501 — MCP tool docstring surfaces verbatim to agents; truncating it degrades guidance quality
    if not math.isfinite(budget_usd) or budget_usd <= 0:
        raise ToolError("budget_usd must be a positive finite number.")
    model = model.strip() or "unknown"
    start_summary = task_summary[:200] if task_summary else None
    try:
        pricing_mode = UnknownModelPricingMode(unknown_model_pricing_mode)
    except ValueError as exc:
        raise ToolError(
            "unknown_model_pricing_mode must be one of: "
            "warn_only, reroute_required, halt_on_unknown_pricing"
        ) from exc
    if accounting_mode is not None:
        accounting_mode = accounting_mode.strip().lower()
        if accounting_mode not in store_schema.VALID_ACCOUNTING_MODES:
            allowed = ", ".join(sorted(store_schema.VALID_ACCOUNTING_MODES))
            raise ToolError(f"accounting_mode must be one of: {allowed}")
    if usage_channel is not None:
        usage_channel = usage_channel.strip().lower()
        if usage_channel not in store_schema.VALID_USAGE_CHANNELS:
            allowed = ", ".join(sorted(store_schema.VALID_USAGE_CHANNELS))
            raise ToolError(f"usage_channel must be one of: {allowed}")
    session_id = _make_session_id(client)
    policy = PipelinePolicy(
        budget=budget_usd,
        budget_mode=BudgetMode.WARN,
        unknown_model_pricing_mode=pricing_mode,
    )
    log_path = _get_log_path()
    store = _get_session_store()
    store.create_session(
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
        start_summary=start_summary,
        parent_session_id=parent_session_id,
        client=client,
    )

    api_key = _config.get_api_key()
    if api_key and _config.is_cloud_sync_enabled():
        threading.Thread(
            target=_background_sync,
            args=(api_key, _config.get_cloud_endpoint(), store),
            daemon=True,
        ).start()

    return {"session_id": session_id}


def _background_sync(
    api_key: str, endpoint: str, store: LocalSessionStore | None = None,
) -> None:
    """Drain outbox, then recover any stale sessions. Best-effort, time-capped."""
    deadline = time.time() + _BACKGROUND_SYNC_DEADLINE_SECONDS
    _outbox.drain(api_key, endpoint, deadline=deadline)
    if time.time() < deadline:
        _outbox.recover_stale_sessions(
            api_key, endpoint, store=store, deadline=deadline,
        )

_VALID_SERVER_ACTIONS: frozenset[str] = frozenset({"allow", "reroute", "halt"})
_VALID_BUDGET_PRESSURE: frozenset[str] = frozenset(
    {"low", "moderate", "high", "critical"}
)


def _finite_non_negative(value: object) -> float | None:
    """Coerce a server-supplied numeric field to a sane float.

    Returns ``None`` if the value is missing, non-numeric, NaN, infinite,
    or negative. Used to sanity-check the authorize response before we
    let its values drive downstream cost accounting — a negative or NaN
    ``calibrated_cost_usd`` would corrupt the session's spend snapshot.
    """
    if value is None:
        return None
    try:
        coerced = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(coerced) or math.isinf(coerced) or coerced < 0:
        return None
    return coerced


def _sanitize_server_authorize_response(resp: dict) -> dict | None:
    """Validate a ``/v1/authorize`` response. Returns ``None`` on garbage.

    The server is meant to return a well-formed envelope but we do not
    trust the wire — a 200 response with a NaN ``calibrated_cost_usd``
    or a missing ``action`` key would crash or corrupt the local
    session. Falling back to local auth on sanity failure is the
    iron-rule "prediction returns garbage → fall back to population
    prior" path.
    """
    if not isinstance(resp, dict):
        return None
    action = resp.get("action")
    if action not in _VALID_SERVER_ACTIONS:
        return None
    calibrated_cost = _finite_non_negative(resp.get("calibrated_cost_usd"))
    if calibrated_cost is None:
        return None
    remaining = _finite_non_negative(resp.get("remaining_usd"))
    if remaining is None:
        return None
    pressure = resp.get("budget_pressure")
    if pressure not in _VALID_BUDGET_PRESSURE:
        return None
    # calibration_factor is a float multiplier; server can legitimately
    # return 1.0 when no calibration exists. Reject NaN/inf/negative but
    # allow missing (defaulted to 1.0 downstream).
    factor_raw = resp.get("calibration_factor")
    if factor_raw is not None and _finite_non_negative(factor_raw) is None:
        return None
    return resp


async def _try_server_authorize(
    *,
    api_key: str,
    session: SessionState,
    store: LocalSessionStore,
    tool_name: str,
    estimated_tokens: int | None,
    estimated_prompt_tokens: int | None,
    estimated_completion_tokens: int | None,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    parent_call_id: str | None,
    call_mode: str | None,
    model_override: str | None = None,
) -> dict | None:
    """Try server-side authorize with calibrated cost factors.

    Returns the MCP response dict on success, or None to fall back to local auth.

    Fail-open contract: any exception — remote call, response parsing,
    local store write — is caught and converted to ``None`` so the
    caller falls back to local auth. The gate must never break an
    in-flight agent session because of a cloud hiccup.
    """
    try:
        return await _try_server_authorize_inner(
            api_key=api_key,
            session=session,
            store=store,
            tool_name=tool_name,
            estimated_tokens=estimated_tokens,
            estimated_prompt_tokens=estimated_prompt_tokens,
            estimated_completion_tokens=estimated_completion_tokens,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            parent_call_id=parent_call_id,
            call_mode=call_mode,
            model_override=model_override,
        )
    except Exception:
        _logger.warning("try_server_authorize_failed_fail_open", exc_info=True)
        return None


async def _try_server_authorize_inner(
    *,
    api_key: str,
    session: SessionState,
    store: LocalSessionStore,
    tool_name: str,
    estimated_tokens: int | None,
    estimated_prompt_tokens: int | None,
    estimated_completion_tokens: int | None,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    parent_call_id: str | None,
    call_mode: str | None,
    model_override: str | None = None,
) -> dict | None:
    billing_model = model_override or session.model

    prompt_tokens = estimated_prompt_tokens or estimated_tokens or 2000
    completion_tokens = estimated_completion_tokens or 400

    estimator = LiteLLMCostEstimator(
        fallback_cost_per_1k_tokens=session.policy.unknown_model_cost_per_1k_tokens
    )
    raw_cost = estimator.estimate(billing_model, prompt_tokens, completion_tokens)

    calls = store.list_calls_for_session(session.session_id)
    snapshot = _spend_snapshot(session, store=store, calls=calls)

    server_resp = await try_remote_authorize(
        api_key=api_key,
        endpoint=_config.get_cloud_endpoint(),
        session_id=session.session_id,
        model=billing_model,
        tool_name=tool_name,
        estimated_cost_usd=float(raw_cost),
        budget_usd=session.policy.budget,
        spent_usd=snapshot["spent_usd"],
        session_client=session.client,
    )
    if server_resp is None:
        return None

    validated = _sanitize_server_authorize_response(server_resp)
    if validated is None:
        _logger.warning(
            "server_authorize_response_invalid",
            extra={"session_id": session.session_id, "keys": sorted(server_resp.keys())},
        )
        return None
    server_resp = validated

    calibrated_cost = Decimal(str(server_resp["calibrated_cost_usd"]))
    call = store.create_call(
        session_id=session.session_id,
        tool_name=tool_name,
        model_requested=session.model,
        model_used=billing_model,
        estimated_prompt_tokens=prompt_tokens,
        estimated_completion_tokens=completion_tokens,
        estimated_cost_usd=calibrated_cost,
        rerouted=server_resp["action"] == "reroute",
        actor_type=actor_type,
        actor_id=actor_id,
        actor_name=actor_name,
        parent_call_id=parent_call_id,
        call_mode=call_mode,
        raw_estimated_cost_usd=raw_cost,
    )
    store.increment_checkpoint_calls(session.session_id)

    _get_calibration_cache().update(
        session.session_id,
        factor=server_resp.get("calibration_factor", 1.0),
        source=server_resp.get("calibration_source", "none"),
        confidence=server_resp.get("calibration_confidence"),
        factor_range=server_resp.get("factor_range"),
    )

    result: dict = {
        "action": server_resp["action"],
        "remaining_usd": server_resp["remaining_usd"],
        "budget_pressure": server_resp["budget_pressure"],
        "reason": "server_calibrated",
        "call_id": call.call_id,
        "calibration_factor": server_resp.get("calibration_factor", 1.0),
        "calibration_source": server_resp.get("calibration_source", "none"),
    }
    if "calibration_confidence" in server_resp:
        result["calibration_confidence"] = server_resp["calibration_confidence"]
    if "factor_range" in server_resp:
        result["factor_range"] = server_resp["factor_range"]
    return result


@mcp.tool(timeout=10)
async def l6e_authorize_call(
    session_id: Annotated[str, "Session ID from l6e_run_start"],
    tool_name: Annotated[str, "Name of the tool or stage about to run — pass the stage label here (e.g. 'planning', 'implement'). This is NOT a 'stage' parameter; the field is called tool_name."],  # noqa: E501 — Annotated string is the MCP parameter description shown verbatim to agents; must be unambiguous
    model: Annotated[
        str | None,
        "Optional model for this specific call, overriding the session model. "
        "Use when the client delegates to a different model "
        "(e.g. Haiku for sub-agent work in an Opus session).",
    ] = None,
    estimated_tokens: Annotated[int, "Estimated prompt token count for this call"] = 2000,
    estimated_prompt_tokens: Annotated[
        int | None,
        "Optional explicit prompt token estimate.",
    ] = None,
    estimated_completion_tokens: Annotated[
        int | None,
        "Optional explicit completion token estimate.",
    ] = None,
    check_only: Annotated[
        bool,
        "If True, records a lightweight checkpoint call for spend tracking "
        "but does not make a gate decision (no allow/reroute/halt). "
        "Use for mid-stage pressure checks.",
    ] = False,
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
    """Budget gate and status check. Call at every stage boundary and before sub-agents. Pass check_only=True for lightweight mid-stage pressure checks (records spend but no gate action). Otherwise returns allow, reroute, or halt — proceed only on allow."""  # noqa: E501 — MCP tool docstring surfaces verbatim to agents; truncating it degrades guidance quality
    store = _get_session_store()
    session = _require_session(session_id, store=store)

    actor_type = actor_type.strip().lower()
    if actor_type not in store_schema.VALID_ACTOR_TYPES:
        raise ToolError(
            f"actor_type must be one of: {', '.join(sorted(store_schema.VALID_ACTOR_TYPES))}"
        )
    if call_mode is not None:
        call_mode = call_mode.strip().lower()
        if call_mode not in store_schema.VALID_CALL_MODES:
            raise ToolError(
                f"call_mode must be one of: {', '.join(sorted(store_schema.VALID_CALL_MODES))}"
            )

    billing_model = model.strip() if model else session.model

    # Everything past this point is gate/store work that must fail-open.
    # Input validation above uses ``ToolError`` deliberately — those are
    # customer-visible contract violations. Past here, any exception
    # means our gate broke, and per the iron rule we pass through with
    # ``allow`` so the agent can keep working. See
    # ``docs/runbooks/fails-open-matrix.md``.
    try:
        return await _l6e_authorize_call_impl(
            store=store,
            session=session,
            session_id=session_id,
            tool_name=tool_name,
            billing_model=billing_model,
            estimated_tokens=estimated_tokens,
            estimated_prompt_tokens=estimated_prompt_tokens,
            estimated_completion_tokens=estimated_completion_tokens,
            check_only=check_only,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            parent_call_id=parent_call_id,
            call_mode=call_mode,
            actual_prompt_tokens=actual_prompt_tokens,
            actual_completion_tokens=actual_completion_tokens,
        )
    except ToolError:
        raise
    except Exception:
        _logger.exception(
            "l6e_authorize_call_failed_fail_open",
            extra={"session_id": session_id, "tool_name": tool_name},
        )
        return _fail_open_allow_response(
            session=session,
            store=store,
            session_id=session_id,
            check_only=check_only,
        )


def _fail_open_allow_response(
    *,
    session: SessionState,
    store: LocalSessionStore,
    session_id: str,
    check_only: bool,
) -> dict:
    """Build a safe fail-open response when the gate itself crashes.

    Returns an ``allow`` shaped like a normal authorize response but
    with ``reason="fail_open:gate_exception"`` so operators can filter
    for this in logs. The spend snapshot is best-effort; if the store
    is also broken we return a conservative "budget healthy" snapshot.
    """
    remaining: float
    pressure: str
    pct_used: float
    spent_usd: float
    try:
        calls = store.list_calls_for_session(session_id)
        snapshot = _spend_snapshot(session, store=store, calls=calls)
        remaining = snapshot["remaining_usd"]
        pressure = snapshot["budget_pressure"]
        pct_used = snapshot["pct_used"]
        spent_usd = snapshot["spent_usd"]
    except Exception:
        _logger.warning("l6e_fail_open_snapshot_failed", exc_info=True)
        budget = float(getattr(session.policy, "budget", 0.0) or 0.0)
        remaining = budget
        pressure = "low"
        pct_used = 0.0
        spent_usd = 0.0

    if check_only:
        return {
            "budget_pressure": pressure,
            "remaining_usd": remaining,
            "pct_used": pct_used,
            "reason": "fail_open:gate_exception",
        }
    return {
        "action": "allow",
        "remaining_usd": remaining,
        "budget_pressure": pressure,
        "reason": "fail_open:gate_exception",
        "spent_usd": spent_usd,
    }


async def _l6e_authorize_call_impl(
    *,
    store: LocalSessionStore,
    session: SessionState,
    session_id: str,
    tool_name: str,
    billing_model: str,
    estimated_tokens: int,
    estimated_prompt_tokens: int | None,
    estimated_completion_tokens: int | None,
    check_only: bool,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    parent_call_id: str | None,
    call_mode: str | None,
    actual_prompt_tokens: int | None,
    actual_completion_tokens: int | None,
) -> dict:
    if check_only:
        prompt_tokens = estimated_prompt_tokens or estimated_tokens or 2000
        completion_tokens = estimated_completion_tokens or 400
        estimator = LiteLLMCostEstimator(
            fallback_cost_per_1k_tokens=session.policy.unknown_model_cost_per_1k_tokens
        )
        raw_cost = estimator.estimate(billing_model, prompt_tokens, completion_tokens)

        cached = _get_calibration_cache().get_with_manual_fallback(session_id, billing_model)
        if cached is not None:
            estimated_cost = raw_cost * Decimal(str(cached.factor))
            calibration_applied = True
        else:
            estimated_cost = raw_cost
            calibration_applied = False

        store.create_call(
            session_id=session.session_id,
            tool_name=tool_name,
            model_requested=session.model,
            model_used=billing_model,
            estimated_prompt_tokens=prompt_tokens,
            estimated_completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost,
            rerouted=False,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            parent_call_id=parent_call_id,
            call_mode=call_mode,
            raw_estimated_cost_usd=raw_cost if calibration_applied else None,
        )
        store.increment_checkpoint_calls(session_id)
        session = store.require_active_session(session_id)
        calls = store.list_calls_for_session(session_id)
        snapshot = _spend_snapshot(session, store=store, calls=calls)

        result: dict = {
            "budget_pressure": snapshot["budget_pressure"],
            "remaining_usd": snapshot["remaining_usd"],
            "pct_used": snapshot["pct_used"],
        }
        if calibration_applied:
            result["calibration_applied"] = True
            result["calibration_source"] = cached.source if cached else None

        worker = _get_telemetry_worker()
        if worker is not None:
            try:
                worker.enqueue(StatusTelemetryPayload(
                    session_id=session_id,
                    model=billing_model,
                    estimated_prompt_tokens=prompt_tokens,
                    estimated_completion_tokens=completion_tokens,
                    raw_projected_cost_usd=float(raw_cost),
                    calibrated_projected_cost_usd=float(estimated_cost),
                    calibration_factor=cached.factor if cached else None,
                    calibration_source=cached.source if cached else None,
                    budget_usd=session.policy.budget,
                    spent_usd=snapshot["spent_usd"],
                    budget_pressure=snapshot["budget_pressure"],
                ))
            except Exception:
                # Telemetry is fire-and-forget; never fail the gate on it.
                _logger.warning("status_telemetry_enqueue_failed", exc_info=True)

        return result

    use_actual = (
        actual_prompt_tokens is not None and actual_completion_tokens is not None
    )
    api_key = _config.get_api_key()
    if api_key and _config.is_cloud_sync_enabled() and not use_actual:
        server_result = await _try_server_authorize(
            api_key=api_key,
            session=session,
            store=store,
            tool_name=tool_name,
            estimated_tokens=estimated_tokens,
            estimated_prompt_tokens=estimated_prompt_tokens,
            estimated_completion_tokens=estimated_completion_tokens,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            parent_call_id=parent_call_id,
            call_mode=call_mode,
            model_override=billing_model if billing_model != session.model else None,
        )
        if server_result is not None:
            return server_result

    manual_factors = _config.get_manual_calibration_factors()
    manual_factor = manual_factors.get(billing_model)

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
        calibration_factor=manual_factor,
        model_override=billing_model if billing_model != session.model else None,
    )
    store.increment_checkpoint_calls(session_id)
    session = store.require_active_session(session_id)

    calls = store.list_calls_for_session(session_id)
    snapshot = _spend_snapshot(session, store=store, calls=calls)
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
    if decision.calibration_factor is not None:
        result["calibration_factor"] = decision.calibration_factor
        result["calibration_source"] = decision.calibration_source
    return result


@mcp.tool(timeout=10)
async def l6e_record_usage(
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
) -> dict:
    """Reconcile a pending call with actual token usage after the call completes. Idempotent for the same values on the same call_id."""  # noqa: E501 — MCP tool docstring surfaces verbatim to agents; truncating it degrades guidance quality
    store = _get_session_store()
    existing = store.get_call(call_id)
    if existing is None:
        raise ToolError(f"Unknown call '{call_id}'.")
    session = store.get_session(existing.session_id)
    if session is None:
        raise ToolError(f"Unknown session '{existing.session_id}'. Call l6e_run_start first.")

    resolved_model = model_used or existing.model_used
    estimator = LiteLLMCostEstimator(
        fallback_cost_per_1k_tokens=session.policy.unknown_model_cost_per_1k_tokens
    )
    actual_cost = estimator.estimate(resolved_model, actual_prompt_tokens, actual_completion_tokens)
    request = ReconcileRequest(
        call_id=call_id,
        actual_prompt_tokens=actual_prompt_tokens,
        actual_completion_tokens=actual_completion_tokens,
        actual_cost_usd=actual_cost,
        model_used=resolved_model,
        callback_request_id=callback_request_id,
        callback_trace_id=callback_trace_id,
        correlation_key=correlation_key,
        correlation_source=correlation_source,
        hosted_ledger_id=hosted_ledger_id,
    )
    try:
        reconciled = store.reconcile_call(request)
    except KeyError as exc:
        raise ToolError(exc.args[0]) from exc

    snapshot = _spend_snapshot(session, store=store)
    return {
        "call_id": reconciled.call_id,
        "session_id": reconciled.session_id,
        "status": reconciled.status,
        "exactness_state": reconciled.exactness_state,
        "spend_so_far_usd": snapshot["spent_usd"],
        "remaining_usd": snapshot["remaining_usd"],
        "budget_pressure": snapshot["budget_pressure"],
    }


@mcp.tool(timeout=10)
async def l6e_run_end(
    session_id: Annotated[str, "Session ID from l6e_run_start"],
    task_summary: Annotated[
        str | None,
        "Optional 5-10 word summary of what was accomplished. Null is fine.",
    ] = None,
) -> dict:
    """End the session and flush the run log. Call at task end, including on failure or cancellation — this is the only way to persist the run log."""  # noqa: E501 — MCP tool docstring surfaces verbatim to agents; truncating it degrades guidance quality
    end_summary = task_summary[:200] if task_summary else None
    store = _get_session_store()
    session = store.get_session(session_id)
    if session is None or session.state == "finalized":
        raise ToolError(
            f"Unknown session '{session_id}'. "
            "Already ended or never started."
        )
    calls = store.list_calls_for_session(session_id)
    summary = session_run_summary(session, calls)
    ended_at = max(c.created_at for c in calls) if calls else None
    log = (
        LocalRunLog(path=Path(session.log_path))
        if session.log_path is not None
        else LocalRunLog()
    )
    try:
        store.finalize_session(session_id, end_summary=end_summary, ended_at=ended_at)
    except KeyError as exc:
        raise ToolError(exc.args[0]) from exc
    log.append(summary)

    api_key = _config.get_api_key()
    if api_key and _config.is_cloud_sync_enabled():
        payload = build_session_report(session, summary, calls)
        _outbox.enqueue(payload)
        worker = _get_report_worker()
        if worker is not None:
            worker.enqueue(payload)

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
    modes_without_exact_coverage = [
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
        "total_cost_usd": float(round(summary.total_cost, 6)),
        "calls_made": summary.calls_made,
        "savings_confidence": summary.savings_confidence,
        "pending_exact_calls": pending_exact_calls,
        "exactness_state": run_exactness.value,
        "last_reconciled_at": last_reconciled_at,
        "mode_coverage": mode_coverage,
        "modes_without_exact_coverage": modes_without_exact_coverage,
    }


@mcp.tool(timeout=10)
async def l6e_list_billing_batches() -> dict:
    """List all active billing import batches. Returns batch IDs, source, row count, cost, and import date. Use to audit or identify stale imports before deletion."""  # noqa: E501
    api_key = _config.get_api_key()
    if not api_key:
        raise ToolError(
            "L6E_API_KEY is not configured. Set it in ~/.l6e/config.toml or "
            "the L6E_API_KEY environment variable."
        )
    endpoint = _config.get_cloud_endpoint()
    try:
        resp = httpx.get(
            f"{endpoint}/v1/billing/batches",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:200]
        raise ToolError(
            f"l6e cloud error (HTTP {exc.response.status_code}): {body}"
        ) from exc
    return resp.json()


@mcp.tool(timeout=10)
async def l6e_delete_billing_batch(
    batch_id: Annotated[str, "Batch ID to soft-delete (from l6e_list_billing_batches)"],
) -> dict:
    """Soft-delete a billing import batch and its truth rows. Use to clean up stale or test imports. The batch can be re-imported afterward."""  # noqa: E501
    api_key = _config.get_api_key()
    if not api_key:
        raise ToolError(
            "L6E_API_KEY is not configured. Set it in ~/.l6e/config.toml or "
            "the L6E_API_KEY environment variable."
        )
    endpoint = _config.get_cloud_endpoint()
    try:
        resp = httpx.delete(
            f"{endpoint}/v1/billing/batches/{batch_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ToolError(
                f"Batch '{batch_id}' not found or already deleted."
            ) from exc
        body = exc.response.text[:200]
        raise ToolError(
            f"l6e cloud error (HTTP {exc.response.status_code}): {body}"
        ) from exc
    return resp.json()


@mcp.tool(timeout=120)
async def l6e_sync_anthropic_usage(
    date_start: Annotated[str, "Start date YYYY-MM-DD"],
    date_end: Annotated[str, "End date YYYY-MM-DD"],
    admin_key: Annotated[
        str,
        "Anthropic Admin API key (sk-ant-admin...). Optional — falls back to the ANTHROPIC_ADMIN_KEY environment variable on the MCP server when omitted, so the key never appears in tool-call payloads or chat transcripts. Prefer a short-lived key: create for import, revoke in Anthropic after sync.",  # noqa: E501 — Annotated string is the MCP parameter description shown verbatim to agents; keep one line for schema / AI tooling
    ] = "",
    api_key_id: Annotated[str, "Optional: filter by Anthropic API key ID"] = "",
    include_claude_code: Annotated[bool, "Also sync Claude Code analytics (per-user productivity and cost metrics). Enabled by default."] = True,  # noqa: E501
) -> dict:
    """Sync Anthropic usage data locally via the Admin API. The admin key stays on your machine — only normalized billing rows are sent to l6e cloud. Requires an Anthropic organization account. Best practice: set ANTHROPIC_ADMIN_KEY in the MCP server's environment (e.g. .cursor/mcp.json) so the key never appears in tool-call payloads; or pass admin_key explicitly. Either way, prefer a short-lived key: create for import, revoke in Anthropic after sync."""  # noqa: E501
    # Env fallback so callers can avoid pasting the admin key into tool args
    # (which leak into chat transcripts). The internal sync_and_upload function
    # still requires admin_key as a positional kwarg — env resolution lives
    # only at this user-facing surface, in one place.
    if not admin_key:
        admin_key = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
    if not admin_key:
        raise ToolError(
            "admin_key was not provided and ANTHROPIC_ADMIN_KEY is not set in the MCP "
            "server's environment. Either pass admin_key explicitly, or set "
            "ANTHROPIC_ADMIN_KEY in the env block of your MCP client config "
            "(e.g. .cursor/mcp.json) and restart the MCP server. "
            "Get an Admin API key at https://platform.claude.com/settings/keys"
        )
    if not admin_key.startswith("sk-ant-admin"):
        raise ToolError(
            "admin_key must be an Anthropic Admin API key (starts with sk-ant-admin...). "
            "Get one at https://platform.claude.com/settings/keys"
        )
    from l6e_mcp.anthropic_sync import sync_and_upload

    try:
        result = sync_and_upload(
            admin_key=admin_key,
            date_start=date_start,
            date_end=date_end,
            api_key_id=api_key_id or None,
            include_claude_code=include_claude_code,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        is_anthropic = "api.anthropic.com" in str(exc.request.url)
        if is_anthropic and status in (401, 403):
            raise ToolError(
                "The Anthropic Admin API returned a 401/403 error. This usually means:\n"
                "1. The admin key is invalid or expired, OR\n"
                "2. Your Anthropic account is an individual account (not an organization).\n\n"
                "To use this sync, set up an organization at "
                "https://console.anthropic.com → Settings → Organization.\n\n"
                "Alternatively, export a cost CSV from the Anthropic Console and "
                "import it via the l6e dashboard at /reconciliation."
            ) from exc
        origin = "Anthropic API" if is_anthropic else "l6e cloud"
        raise ToolError(f"{origin} error (HTTP {status}): {exc.response.text[:200]}") from exc
    except RuntimeError as exc:
        raise ToolError(str(exc)) from exc

    resp: dict = {
        "status": "synced",
        "source": result.source,
        "buckets_fetched": result.buckets_fetched,
        "rows_sent": result.rows_sent,
        "total_cost_usd": float(result.total_cost_usd),
    }
    if result.claude_code_records_fetched > 0 or result.claude_code_rows_sent > 0:
        resp["claude_code_records_fetched"] = result.claude_code_records_fetched
        resp["claude_code_rows_sent"] = result.claude_code_rows_sent
    if result.server_response:
        resp["server_result"] = {
            k: result.server_response[k]
            for k in ("status", "batch_id", "rows_inserted", "rows_deduplicated",
                       "reconciliation", "calibration_factors_upserted", "sessions_reconciled")
            if k in result.server_response
        }
    if result.warnings:
        resp["warnings"] = result.warnings
    return resp


# ``l6e_debug_pricing_state`` is a read-only diagnostic tool used to investigate
# gate decisions that disagree with fresh-process repros (cf. L6E-86). It is
# *not* a canonical agent-facing tool — agents should never call it as part of
# normal budget enforcement, and exposing it via ``list_tools()`` by default
# would pollute the schema every client sees.
#
# The function body is defined unconditionally so the diagnostic path is
# preserved in-tree (cf. L6E-87: the resolver self-match failure mode would
# recur the same diagnostic chain if registration timing ever drifts again).
# Only MCP registration is gated: set ``L6E_DEBUG_TOOLS=1`` in the client's
# server env (e.g. ``.cursor/mcp.json``) and restart the MCP server to opt the
# tool into ``list_tools()`` for a diagnostic session.
async def l6e_debug_pricing_state(
    probe_models: Annotated[
        list[str] | None,
        "Optional list of model IDs to probe through litellm.cost_per_token, l6e.costs.resolve_model_id, and LiteLLMCostEstimator.estimate_with_metadata. Defaults to a representative set of opus-4-* dot/dash forms.",  # noqa: E501
    ] = None,
) -> dict:
    """Read-only diagnostic dump of in-process pricing state. Captures process metadata (PID, uptime, python executable, package versions), litellm's model_cost map source info, presence of specific opus keys, the l6e _LITELLM_BARE_KEYS resolver cache contents for opus-* keys, and per-probe results from cost_per_token / resolve_model_id / estimate_with_metadata. Use to investigate gate decisions that disagree with fresh-process repros. Does not mutate any state."""  # noqa: E501
    import sys
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    import litellm
    from l6e import costs as _l6e_costs
    from litellm.litellm_core_utils.get_model_cost_map import (
        get_model_cost_map_source_info,
    )

    if probe_models is None:
        probe_models = [
            "claude-opus-4-7",
            "claude-opus-4.7",
            "claude-opus-4-6",
            "claude-opus-4.6",
        ]

    def _pkg(name: str) -> str | None:
        try:
            return _pkg_version(name)
        except PackageNotFoundError:
            return None

    process_info = {
        "pid": os.getpid(),
        "started_at_unix": _PROCESS_STARTED_UNIX,
        "uptime_seconds": round(time.time() - _PROCESS_STARTED_UNIX, 3),
        "python_executable": sys.executable,
        "litellm_path": str(Path(litellm.__file__).resolve()),
        "l6e_costs_path": str(Path(_l6e_costs.__file__).resolve()),
        "litellm_version": _pkg("litellm"),
        "l6e_version": _pkg("l6e"),
    }

    try:
        source_info = get_model_cost_map_source_info()
    except Exception as exc:  # noqa: BLE001 — diagnostic must never raise
        source_info = {"error_type": type(exc).__name__, "error": str(exc)[:200]}

    model_cost_state = {
        "total_models": len(litellm.model_cost),
        "claude_opus_4_7_present": "claude-opus-4-7" in litellm.model_cost,
        "claude_opus_4_6_present": "claude-opus-4-6" in litellm.model_cost,
        "claude_opus_4_5_present": "claude-opus-4-5" in litellm.model_cost,
        "claude_opus_4_1_present": "claude-opus-4-1" in litellm.model_cost,
        "claude_4_opus_20250514_present": "claude-4-opus-20250514" in litellm.model_cost,
    }

    def _snapshot_bare_keys() -> dict:
        bare = _l6e_costs._LITELLM_BARE_KEYS
        if bare is None:
            return {"populated": False, "size": 0}
        opus_keys = sorted(
            orig for tokens, orig in bare if "opus" in tokens
        )
        return {
            "populated": True,
            "size": len(bare),
            "opus_keys_total": len(opus_keys),
            "opus_4_7_keys": [k for k in opus_keys if "4-7" in k],
            "opus_4_6_keys": [k for k in opus_keys if "4-6" in k],
            "opus_keys_sample": opus_keys[:30],
        }

    bare_state_before = _snapshot_bare_keys()

    estimator = LiteLLMCostEstimator()
    probes: list[dict] = []
    for mid in probe_models:
        try:
            p, c = litellm.cost_per_token(
                model=mid, prompt_tokens=1000, completion_tokens=200
            )
            direct: dict = {"ok": True, "cost_usd": float(p + c)}
        except Exception as exc:  # noqa: BLE001 — diagnostic must never raise
            direct = {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:160],
            }
        try:
            resolved = _l6e_costs.resolve_model_id(mid)
        except Exception as exc:  # noqa: BLE001
            resolved = f"<error: {type(exc).__name__}: {str(exc)[:80]}>"
        try:
            meta = estimator.estimate_with_metadata(
                model=mid,
                prompt_tokens=1000,
                completion_tokens=200,
                emit_warning=False,
            )
            estimate_summary: dict = {
                "pricing_source": meta.pricing_source,
                "resolved_model": meta.resolved_model,
                "model_pricing_known": meta.model_pricing_known,
                "warning_emitted": meta.warning is not None,
                "warning_text": meta.warning[:140] if meta.warning else None,
                "cost_usd": float(meta.cost_usd),
            }
        except Exception as exc:  # noqa: BLE001
            estimate_summary = {
                "error_type": type(exc).__name__,
                "error": str(exc)[:200],
            }
        probes.append(
            {
                "model_id": mid,
                "direct_cost_per_token": direct,
                "resolve_model_id_result": resolved,
                "estimate_with_metadata": estimate_summary,
            }
        )

    bare_state_after = _snapshot_bare_keys()

    return {
        "process": process_info,
        "litellm_cost_map_source_info": source_info,
        "litellm_model_cost_state": model_cost_state,
        "l6e_bare_keys_cache_before_probes": bare_state_before,
        "l6e_bare_keys_cache_after_probes": bare_state_after,
        "probes": probes,
    }


if os.environ.get("L6E_DEBUG_TOOLS") == "1":
    # Opt-in registration only. Keeps the canonical agent-facing tool surface
    # minimal in normal operation (pinned by ``test_tool_discovery_exposes_canonical_names_only``).
    l6e_debug_pricing_state = mcp.tool(timeout=10)(l6e_debug_pricing_state)


def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "install-rules":
        from l6e_mcp.cli import install_rules_cli

        install_rules_cli(sys.argv[2:])
        return

    _config.ensure_config_template()
    refresh_model_cost_map_async()
    mcp.run()


if __name__ == "__main__":
    main()

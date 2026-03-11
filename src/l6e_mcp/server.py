"""l6e-mcp server — session-scoped budget enforcement via FastMCP."""
from __future__ import annotations

import os
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from l6e._types import BudgetMode, CallRecord, PipelinePolicy
from l6e.pipeline import pipeline
from l6e.router import LocalRouter

mcp = FastMCP(
    name="l6e-budget",
    instructions=(
        "l6e enforces session budgets for AI coding assistants. "
        "Call l6e_session_start at the beginning of a task to set a USD budget. "
        "Call l6e_checkpoint before each expensive tool call to get a routing decision. "
        "Call l6e_spend at any time to check how much has been spent. "
        "Call l6e_session_end when the task is complete to flush the run log. "
        "IMPORTANT: always call l6e_session_end — it is the only way to write the run log."
    ),
)

# session_id → (PipelineContext, model: str, local_model: str | None)
_sessions: dict[str, tuple] = {}


def _make_session_id(client: str = "unknown") -> str:
    token = secrets.token_hex(4)
    return f"session_{client}_{date.today().isoformat()}_{token}"


def _get_log_path() -> Path | None:
    """Return an explicit log path if L6E_LOG_PATH is set.

    Required for Windsurf, which spawns MCP stdio servers with cwd=/,
    making the default relative path '.l6e/runs.jsonl' resolve to the
    filesystem root (permission denied on most systems).
    """
    raw = os.environ.get("L6E_LOG_PATH")
    return Path(raw) if raw else None


def _get_session(session_id: str) -> tuple:
    entry = _sessions.get(session_id)
    if entry is None:
        raise ToolError(
            f"Unknown session '{session_id}'. "
            "Call l6e_session_start first."
        )
    return entry  # (ctx, model, local_model)


@mcp.tool
def l6e_session_start(
    budget_usd: Annotated[float, "Hard budget ceiling in USD for this session"],
    model: Annotated[str, "Default model the assistant will use"] = "gpt-4o",
    client: Annotated[
        str,
        "MCP client name for session_id labelling — e.g. cursor, claude-code, windsurf",
    ] = "unknown",
) -> dict:
    """Start a new budget-enforced session. Returns session_id to pass to other tools.

    Check the 'local_model' field in the response — if null, no Ollama model is
    available and budget reroutes will fall back to halt.
    """
    local_model = LocalRouter().best_local_model()
    session_id = _make_session_id(client)
    policy = PipelinePolicy(budget=budget_usd, budget_mode=BudgetMode.REROUTE)
    log_path = _get_log_path()
    ctx = pipeline(
        run_id=session_id,
        policy=policy,
        log_path=log_path,
        source="mcp",
    ).__enter__()
    _sessions[session_id] = (ctx, model, local_model)
    return {
        "session_id": session_id,
        "budget_usd": budget_usd,
        "model": model,
        "local_model": local_model,
        "reroute_capable": local_model is not None,
    }


@mcp.tool
def l6e_checkpoint(
    session_id: Annotated[str, "Session ID from l6e_session_start"],
    tool_name: Annotated[str, "Name of the tool or stage about to run"],
    estimated_tokens: Annotated[int, "Estimated prompt token count for this call"] = 500,
) -> dict:
    """Check whether to allow, reroute, or halt before an expensive tool call.

    Records the estimated spend so budget tracking stays accurate across calls.
    On 'reroute', use the returned 'target_model' instead of the default model.
    On 'halt', do not proceed — the budget is exhausted.
    """
    ctx, model, _ = _get_session(session_id)
    decision = ctx.advise(model=model, prompts=[], stage=tool_name)

    # Book the estimated spend so the gate fires correctly on subsequent checkpoints.
    # Build CallRecord directly — avoids response-parsing indirection in ctx.record().
    if decision.action != "halt":
        cost = ctx._estimator.estimate(model, estimated_tokens, 0)
        record = CallRecord(
            call_index=ctx._call_index,
            model_requested=model,
            model_used=decision.target_model,
            prompt_tokens=estimated_tokens,
            completion_tokens=0,
            cost_usd=cost,
            rerouted=(decision.action == "reroute"),
            elapsed_ms=0.0,
            stage=tool_name,
        )
        ctx._store.record_call(record)
        ctx._call_index += 1

    status = ctx.budget_status()
    result: dict = {
        "action": decision.action,
        "spend_so_far_usd": round(status.spent_usd, 6),
        "remaining_usd": round(status.remaining_usd, 6),
        "budget_pressure": status.budget_pressure,
        "reason": decision.reason,
    }
    if decision.action == "reroute":
        result["target_model"] = decision.target_model
    return result


@mcp.tool
def l6e_spend(
    session_id: Annotated[str, "Session ID from l6e_session_start"],
) -> dict:
    """Get a read-only spend snapshot. Does not record a call or advance the budget."""
    ctx, _, _ = _get_session(session_id)
    status = ctx.budget_status()
    return {
        "spent_usd": round(status.spent_usd, 6),
        "remaining_usd": round(status.remaining_usd, 6),
        "budget_usd": status.budget_usd,
        "calls_made": status.calls_made,
        "reroutes": status.reroutes,
        "budget_pressure": status.budget_pressure,
        "pct_used": round(status.pct_used, 2),
    }


@mcp.tool
def l6e_session_end(
    session_id: Annotated[str, "Session ID from l6e_session_start"],
) -> dict:
    """End the session and flush the run log to .l6e/runs.jsonl (or L6E_LOG_PATH).

    This is the only way to persist the session run record. Always call this
    at the end of a task, even if earlier steps were halted.
    """
    entry = _sessions.pop(session_id, None)
    if entry is None:
        raise ToolError(
            f"Unknown session '{session_id}'. "
            "Already ended or never started."
        )
    ctx, _, _ = entry
    summary = ctx.run_summary()         # snapshot before __exit__
    ctx.__exit__(None, None, None)      # writes identical snapshot to log
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

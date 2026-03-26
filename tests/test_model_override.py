"""Tests for per-call model override on l6e_authorize_call.

When a client delegates work to a cheaper model (e.g. Haiku sub-agents in an
Opus session), passing `model` on the authorize call should price the call at
the override model's rate instead of the session model's rate.
"""
from __future__ import annotations

from l6e_mcp.session_store import LocalSessionStore


async def start_session(mcp_client, budget_usd: float = 5.0, **kwargs) -> dict:
    if "model" not in kwargs:
        kwargs["model"] = "gpt-4o"
    result = await mcp_client.call_tool(
        "l6e_run_start",
        {"budget_usd": budget_usd, **kwargs},
        raise_on_error=False,
    )
    assert not result.is_error, f"l6e_run_start failed: {result}"
    return result.data


# ---------------------------------------------------------------------------
# Full gate — model override
# ---------------------------------------------------------------------------


async def test_model_override_sets_model_used_on_call(client):
    """Passing model= on authorize_call should set model_used to the override
    while keeping model_requested as the session model."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "explore",
            "model": "gpt-4o-mini",
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    call = LocalSessionStore().get_call(result.data["call_id"])
    assert call is not None
    assert call.model_requested == "gpt-4o"
    assert call.model_used == "gpt-4o-mini"


async def test_model_override_produces_cheaper_cost(client):
    """A call with a cheaper model override should consume less budget than
    the same call at the session model's rate."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    sid = session["session_id"]

    expensive = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "step_a",
            "estimated_prompt_tokens": 5000,
            "estimated_completion_tokens": 1000,
        },
        raise_on_error=False,
    )
    assert not expensive.is_error

    cheap = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "step_b",
            "model": "gpt-4o-mini",
            "estimated_prompt_tokens": 5000,
            "estimated_completion_tokens": 1000,
        },
        raise_on_error=False,
    )
    assert not cheap.is_error

    expensive_call = LocalSessionStore().get_call(expensive.data["call_id"])
    cheap_call = LocalSessionStore().get_call(cheap.data["call_id"])
    assert expensive_call is not None and cheap_call is not None
    assert cheap_call.estimated_cost_usd < expensive_call.estimated_cost_usd, (
        "gpt-4o-mini override should produce a lower estimated cost than gpt-4o"
    )


async def test_model_override_omitted_uses_session_model(client):
    """When model is not passed, model_used should equal the session model."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "read_files",
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    call = LocalSessionStore().get_call(result.data["call_id"])
    assert call is not None
    assert call.model_requested == "gpt-4o"
    assert call.model_used == "gpt-4o"


async def test_model_override_strips_whitespace(client):
    """Leading/trailing whitespace on the model override should be stripped."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "explore",
            "model": "  gpt-4o-mini  ",
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    call = LocalSessionStore().get_call(result.data["call_id"])
    assert call is not None
    assert call.model_used == "gpt-4o-mini"


async def test_model_override_with_subagent_metadata(client):
    """Model override combined with actor_type=subagent should set both
    the model_used and actor metadata correctly on the call record."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "explore_codebase",
            "model": "gpt-4o-mini",
            "estimated_prompt_tokens": 3000,
            "estimated_completion_tokens": 800,
            "actor_type": "subagent",
            "actor_id": "explore_1",
            "actor_name": "Explore agent",
        },
        raise_on_error=False,
    )
    assert not result.is_error
    call = LocalSessionStore().get_call(result.data["call_id"])
    assert call is not None
    assert call.model_requested == "gpt-4o"
    assert call.model_used == "gpt-4o-mini"
    assert call.actor_type == "subagent"
    assert call.actor_id == "explore_1"


async def test_model_override_with_actual_tokens(client):
    """Model override combined with actual_prompt/completion_tokens should
    create a reconciled call priced at the override model's rate."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    result = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "sub_agent_done",
            "model": "gpt-4o-mini",
            "actual_prompt_tokens": 50_000,
            "actual_completion_tokens": 2_000,
        },
        raise_on_error=False,
    )
    assert not result.is_error
    call = LocalSessionStore().get_call(result.data["call_id"])
    assert call is not None
    assert call.status == "reconciled"
    assert call.model_used == "gpt-4o-mini"
    assert call.model_requested == "gpt-4o"
    assert call.actual_prompt_tokens == 50_000


async def test_model_override_then_record_usage(client):
    """The full flow: authorize with model override, then reconcile with
    l6e_record_usage. Both should agree on model_used."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    checkpoint = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": session["session_id"],
            "tool_name": "explore",
            "model": "gpt-4o-mini",
            "estimated_prompt_tokens": 5000,
            "estimated_completion_tokens": 1000,
        },
        raise_on_error=False,
    )
    assert not checkpoint.is_error
    call_id = checkpoint.data["call_id"]

    pending_call = LocalSessionStore().get_call(call_id)
    assert pending_call is not None
    assert pending_call.model_used == "gpt-4o-mini"
    assert pending_call.status == "pending"

    reconcile = await client.call_tool(
        "l6e_record_usage",
        {
            "call_id": call_id,
            "actual_prompt_tokens": 4200,
            "actual_completion_tokens": 800,
        },
        raise_on_error=False,
    )
    assert not reconcile.is_error

    reconciled_call = LocalSessionStore().get_call(call_id)
    assert reconciled_call is not None
    assert reconciled_call.status == "reconciled"
    assert reconciled_call.model_used == "gpt-4o-mini"
    assert reconciled_call.actual_prompt_tokens == 4200


# ---------------------------------------------------------------------------
# check_only — model override
# ---------------------------------------------------------------------------


async def test_check_only_model_override_sets_model_used(client):
    """check_only=True with model override should record the override model
    on the call, not the session model."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    sid = session["session_id"]

    await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "model": "gpt-4o-mini",
            "check_only": True,
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    calls = LocalSessionStore().list_calls_for_session(sid)
    assert len(calls) == 1
    assert calls[0].model_requested == "gpt-4o"
    assert calls[0].model_used == "gpt-4o-mini"


async def test_check_only_model_override_produces_less_spend(client):
    """check_only with a cheaper model override should consume less budget
    than check_only at the session model's rate."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    sid = session["session_id"]

    expensive = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "check_only": True,
            "estimated_prompt_tokens": 5000,
            "estimated_completion_tokens": 1000,
        },
        raise_on_error=False,
    )
    assert not expensive.is_error

    cheap = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "model": "gpt-4o-mini",
            "check_only": True,
            "estimated_prompt_tokens": 5000,
            "estimated_completion_tokens": 1000,
        },
        raise_on_error=False,
    )
    assert not cheap.is_error

    calls = LocalSessionStore().list_calls_for_session(sid)
    assert len(calls) == 2
    expensive_call, cheap_call = calls[0], calls[1]
    assert cheap_call.estimated_cost_usd < expensive_call.estimated_cost_usd


async def test_check_only_model_override_uses_correct_calibration(client):
    """check_only with model override should look up calibration for the
    override model, not the session model."""
    from unittest.mock import patch

    from l6e_mcp import config as _config

    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    sid = session["session_id"]

    with patch.object(
        _config,
        "get_manual_calibration_factors",
        return_value={"gpt-4o-mini": 10.0},
    ):
        calibrated = await client.call_tool(
            "l6e_authorize_call",
            {
                "session_id": sid,
                "tool_name": "status",
                "model": "gpt-4o-mini",
                "check_only": True,
                "estimated_prompt_tokens": 2000,
                "estimated_completion_tokens": 400,
            },
            raise_on_error=False,
        )
    assert not calibrated.is_error
    assert calibrated.data.get("calibration_applied") is True

    uncalibrated = await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "status",
            "model": "gpt-4o-mini",
            "check_only": True,
            "estimated_prompt_tokens": 2000,
            "estimated_completion_tokens": 400,
        },
        raise_on_error=False,
    )
    assert not uncalibrated.is_error
    assert "calibration_applied" not in uncalibrated.data


# ---------------------------------------------------------------------------
# End-to-end: multi-model session
# ---------------------------------------------------------------------------


async def test_multi_model_session_total_cost_reflects_mixed_models(client, tmp_path):
    """A session using both gpt-4o and gpt-4o-mini calls should have a total
    cost lower than if all calls were priced at gpt-4o rates."""
    session = await start_session(client, budget_usd=10.0, model="gpt-4o")
    sid = session["session_id"]

    await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "planning",
            "estimated_prompt_tokens": 3000,
            "estimated_completion_tokens": 500,
        },
        raise_on_error=False,
    )

    await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "explore",
            "model": "gpt-4o-mini",
            "estimated_prompt_tokens": 10000,
            "estimated_completion_tokens": 2000,
            "actor_type": "subagent",
            "actor_id": "explore_1",
        },
        raise_on_error=False,
    )

    await client.call_tool(
        "l6e_authorize_call",
        {
            "session_id": sid,
            "tool_name": "implement",
            "estimated_prompt_tokens": 3000,
            "estimated_completion_tokens": 500,
        },
        raise_on_error=False,
    )

    calls = LocalSessionStore().list_calls_for_session(sid)
    assert len(calls) == 3
    assert calls[0].model_used == "gpt-4o"
    assert calls[1].model_used == "gpt-4o-mini"
    assert calls[2].model_used == "gpt-4o"

    mini_cost = calls[1].estimated_cost_usd
    assert mini_cost < calls[0].estimated_cost_usd, (
        "The gpt-4o-mini call with more tokens should still cost less than gpt-4o"
    )

    end = await client.call_tool(
        "l6e_run_end",
        {"session_id": sid},
        raise_on_error=False,
    )
    assert not end.is_error
    assert end.data["calls_made"] == 3
    assert end.data["total_cost_usd"] > 0

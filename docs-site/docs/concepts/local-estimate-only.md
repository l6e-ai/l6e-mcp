---
id: local-estimate-only
title: Local Budget Enforcement Without a Backend Proxy
sidebar_label: Local Enforcement
sidebar_position: 1
---

This document describes how `l6e-mcp` works when running entirely locally — no remote backend, no hosted relay, no self-hosted LiteLLM proxy. This is the default configuration after `pip install l6e-mcp`.

---

## What you get

- A budget gate that fires before expensive operations
- A local SQLite session store that persists spend across the conversation
- A run log written to `.l6e/runs.jsonl` at session end
- `allow`, `reroute`, and `halt` decisions returned to the agent before each call (`reroute` tells the agent to stop and ask you to switch to a cheaper model)

## Directionally accurate out of the box

The MCP protocol has no mechanism for a server to intercept the response from your LLM provider. When your agent makes an LLM call, `l6e-mcp` never sees the provider's response envelope and cannot read the actual `prompt_tokens` and `completion_tokens` from it.

This means every call is accounted for using the token estimate the agent provides to `l6e_authorize_call` before the call goes out. The accumulated spend shown in `l6e_authorize_call` with `check_only=True` reflects those estimates, not your provider's billing records.

The estimates are directionally accurate for most models with known pricing (Claude, GPT-4, Gemini), but they will drift from actuals depending on how accurately the agent guesses token counts for each operation. Without [calibration](calibration), the estimate-to-billing ratio can be 2-10x off — but the behavioral enforcement is real from session one.

## Why it still changes agent behavior

Even imperfect accounting changes how an agent operates. An agent that must call `l6e_authorize_call` before expensive work, and that receives a response telling it how much budget remains, behaves differently than one with no cost awareness:

- It scopes tasks more tightly when budget pressure is reported as high
- It launches fewer speculative sub-agents
- It stops earlier when a task is running more expensive than anticipated
- It surfaces a structured message on `halt` rather than silently continuing past budget

The gate is a forcing function for proportionality. The agent knowing it has a $2 budget — even if the estimate-to-billing ratio is 2-5x off — is meaningfully different from the agent having no budget concept at all.

## Practical guidance for starting out

**Use small budgets.** Start with $1–3 per session and run a few tasks. After each session, compare what `l6e_authorize_call` reported as total spend against what your provider's dashboard shows for the same time window.

Common sources of drift:

- **Extended thinking / reasoning tokens.** Models with internal chain-of-thought (e.g. Claude with extended thinking enabled) generate reasoning tokens that count against your provider bill but are invisible to the agent's pre-call estimate.
- **Long context.** Prompt token counts are estimated with a character-based heuristic when `tiktoken` is unavailable, which underestimates long-context calls.
- **Model misidentification.** If the model string passed to `l6e_run_start` does not match what your client is actually billing, all cost estimates for the session will be wrong.

**Pick a budget that covers real tasks.** A $1 budget for a task that realistically costs $0.30–0.50 gives the gate room to operate. A $0.10 budget on a frontier model will halt immediately. The goal is to set a reasonable ceiling and observe where actual spend lands relative to it.

## The accounting path, precisely

The session opens in `estimate_only` accounting mode.

Each `l6e_authorize_call` call:
1. Reads persisted spend from the local SQLite store
2. Computes an estimated cost from `estimated_prompt_tokens` + `estimated_completion_tokens` (or falls back to `estimated_tokens` if only total tokens are provided)
3. Applies the gate decision (`allow`, `reroute`, `halt`) based on estimated cumulative spend
4. Records a pending call row with those estimates

No actual provider data enters the ledger unless you explicitly call `l6e_record_usage` with real token counts after a call completes. In pure local operation, most sessions end with `exactness_state: "all_estimate_only"`.

## Providing better estimates

The gate decision is only as good as the estimates it acts on. Dual-token estimates (`estimated_prompt_tokens` + `estimated_completion_tokens`) are more accurate than a single total because prompt and completion tokens have different per-token prices on most models.

If you are writing agent rules that call `l6e_authorize_call`, be conservative. An estimate that is too high may trigger a `reroute` decision earlier than necessary — but an estimate that is too low lets expensive work proceed without being accounted for. Erring toward overestimation is the safer default.

## Upgrading to exact accounting

If you need the gate to act on real token counts rather than estimates:

**Manual `l6e_record_usage` calls:** If your agent has access to the provider response, it can call `l6e_record_usage` directly with `actual_prompt_tokens` and `actual_completion_tokens`. This is idempotent and updates the existing call row rather than creating a duplicate spend record.

The pre-call gate decision is still based on estimates — the MCP protocol cannot block a call from going out and then wait for the response before deciding. The reconciliation path corrects the accumulated spend ledger after the fact, which improves future gate decisions for the same session.

---

## Summary

| What you have | What you don't have |
|---|---|
| Budget gate before every call | Real token counts from provider |
| `allow` / `reroute` (advisory) / `halt` decisions | Automatic post-call reconciliation |
| Local run log for every session | Cross-session [calibration](calibration) (requires billing import) |
| Session spend visible at any point | Guarantees on estimate accuracy |

Running locally without a proxy gives you real behavioral enforcement at no infrastructure cost. Out of the box, budgets are directionally accurate — good enough to change how agents operate, which is the point. [Calibration](calibration) makes them billing-accurate by learning your personal cost patterns from imported billing data.

---
id: calibration
title: Calibration
sidebar_label: Calibration
sidebar_position: 2
---

Out of the box, l6e budgets are directionally accurate — the agent has a cost concept and halts when it's spending too much, but the dollar amounts are based on token estimates, not your provider's billing. Calibration closes that gap.

There are two ways to calibrate: set manual per-model factors locally, or let l6e.ai compute them automatically from your billing data. Both are optional. Both are additive — you can start with manual factors and add cloud calibration later without changing anything.

## How calibration works

A calibration factor is a multiplier applied to the raw token-based cost estimate before every gate decision. When `l6e_authorize_call` estimates that a call will cost $0.02 and the calibration factor is 4.5x, the gate records $0.09 against your budget.

This makes `remaining_usd` and `budget_pressure` in gate responses reflect what your provider will actually bill — not what the agent guessed. Without calibration, the estimate-to-billing ratio can be 2-10x off. With calibration, it tightens to 2-3x.

## Manual per-model factors

Set a factor per model when you know your cost patterns or want calibration without a cloud account.

l6e creates `~/.l6e/config.toml` with commented-out examples when the MCP server starts. Uncomment the `[calibration]` section and set your factors:

```toml
[calibration]
claude-4-opus = 72.0
claude-4-sonnet = 45.0
claude-3.5-haiku = 12.0
```

If you configure l6e through env vars in your MCP config (most users do), add `L6E_CALIBRATION_FACTORS` to the same env block:

```json
{
  "env": {
    "L6E_LOG_PATH": "${HOME}/.l6e/runs.jsonl",
    "L6E_CALIBRATION_FACTORS": "claude-4-opus:72.0,claude-4-sonnet:45.0"
  }
}
```

Env vars take precedence over the TOML file.

**How to find your factor:** Run a few sessions, then compare what `l6e_authorize_call` reported as total spend against what your provider's billing dashboard shows for the same time window. Divide the billed amount by the estimated amount — that's your factor.

**Limitations:** One number per model. Manual factors don't adapt to task type, client, or scope of work — they're a fixed multiplier. For calibration that adapts as you use it, see cloud calibration below.

## Cloud calibration (l6e.ai)

Sign up at [app.l6e.ai](https://app.l6e.ai), enable cloud sync, and import your billing data. l6e computes your personal calibration factor automatically and keeps it fresh as you use it.

Every user's estimate-to-billing ratio is different. It depends on the models you use, your task types, your client (Cursor vs Claude Code vs Windsurf), and your prompting style. Cloud calibration accounts for all of this — the factor is personal, computed from your actual session data matched against your billing records.

**How it works:**

1. Enable cloud sync (`L6E_API_KEY` + `L6E_CLOUD_SYNC=1` in your MCP config)
2. Import your billing CSV at [app.l6e.ai/reconciliation](https://app.l6e.ai/reconciliation) — l6e matches billing line items against your sessions by timestamp and model
3. Your calibration factor updates immediately
4. Gate decisions on subsequent sessions use the calibrated factor — no added latency on the hot path (the factor is cached locally)
5. Import again after more sessions for a tighter factor

More provider import formats are on the roadmap. Cursor CSV is the only supported format today.

See [l6e.ai Integration](cloud-api) for the full setup guide, including how the caching and offline fallback work.

## Calibration confidence

Cloud calibration factors have a confidence level based on how much data backs them:

| Confidence | What it means |
|---|---|
| `low` | Few sessions or billing imports — the factor is a rough estimate. Consider importing more data. |
| `medium` | Enough data for a reasonable factor. No special action needed. |
| `high` | Substantial session and billing data across multiple models and task types. The factor is stable. |

`l6e_authorize_call` returns `calibration_confidence` in its response so the agent (and your rules) can adjust behavior accordingly.

Manual factors have no confidence concept — they're whatever you set.

## Factor precedence

When multiple calibration sources are configured, the most specific one wins:

1. **Cloud factor** (cached from server-side authorize) — highest priority
2. **Manual config factor** (env var or TOML) — used as fallback when cloud cache expires or cloud is unreachable
3. **No calibration** (raw token estimates) — the default when nothing is configured

If you have both cloud sync and manual factors configured, the cloud factor is used on full gate calls. Manual factors apply on `check_only` calls when the cloud cache has expired (5-minute TTL) and as a permanent offline fallback.

## Calibration is an enhancement, not a prerequisite

You do not need calibration to get value from l6e. Budget enforcement changes agent behavior from session one — the agent checkpoints, respects halt signals, and scopes work to fit the budget. That behavioral shift is the core product.

Calibration makes the dollar amounts accurate. It turns "I set a $3 budget and the agent halted somewhere around $1-8 of real spend" into "I set a $3 budget and the agent halted around $2-4 of real spend." Both are useful. The second is tighter.

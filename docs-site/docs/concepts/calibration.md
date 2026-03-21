---
id: calibration
title: Calibration
sidebar_label: Calibration
sidebar_position: 2
---

Out of the box, l6e budgets are directionally accurate — the agent has a cost concept and halts when it's spent too much, but the dollar amounts are based on token estimates, not your provider's billing. Calibration closes that gap.

## The calibration flywheel

1. **Run sessions with l6e.** Budget enforcement works immediately — no setup beyond the MCP server.
2. **Import your billing data.** Go to [app.l6e.ai/reconciliation](https://app.l6e.ai/reconciliation) and upload your Cursor billing CSV. Takes 30 seconds.
3. **l6e computes your personal calibration factor.** The system matches your l6e sessions against billing line items and computes a multiplier that corrects for the gap between estimates and reality.
4. **Your next sessions have tighter budgets.** The calibration factor is applied to every `l6e_authorize_call` gate decision, so budget pressure signals and halt thresholds reflect your actual cost patterns.
5. **Keep using, keep importing.** More sessions across different models, task types, and workflows means more data points. Each billing import refines the factor further.

**l6e gets better the more you use it.** That's the product.

## What calibration does, precisely

Every user's estimate-to-billing ratio is different. It depends on:

- **The models you use** — claude-4.6-opus has a different estimation pattern than claude-sonnet-4
- **Your task types** — a "refactor this module" task has a different ratio than "build a feature from scratch"
- **Your client** — Cursor, Claude Code, and Windsurf all have different overhead patterns and billing structures
- **Your prompting style** — verbose prompts, large repos, frequent interruptions all affect the ratio

The calibration factor is a personal multiplier that accounts for all of this. When `l6e_authorize_call` estimates that a call will cost $0.02, and your calibration factor is 4.5x, the gate records $0.09 against your budget. This makes the `remaining_usd` and `budget_pressure` fields in gate responses reflect what your provider will actually bill — not what the agent guessed.

## How to import billing data

1. Go to [app.l6e.ai/reconciliation](https://app.l6e.ai/reconciliation)
2. Download your billing CSV from your provider (Cursor: Settings → Billing → Download CSV)
3. Upload the CSV — l6e matches billing line items against your session data by timestamp and model
4. Your calibration factor updates immediately

More provider import formats are on the roadmap. Cursor CSV is the only supported format today.

## Calibration confidence

The calibration factor has a confidence level based on how much data backs it:

| Confidence | What it means |
|---|---|
| `low` | Few sessions or billing imports — the factor is a rough estimate. Consider importing more data. |
| `medium` | Enough data for a reasonable factor. No special action needed. |
| `high` | Substantial session and billing data across multiple models and task types. The factor is stable. |

`l6e_authorize_call` returns `calibration_confidence` in its response so the agent (and your rules) can adjust behavior accordingly.

## Calibration is an enhancement, not a prerequisite

You do not need to import billing data to get value from l6e. Budget enforcement changes agent behavior from session one — the agent checkpoints, respects halt signals, and scopes work to fit the budget. That behavioral shift is the core product.

Calibration makes the dollar amounts accurate. It turns "I set a $3 budget and the agent halted somewhere around $1-8 of real spend" into "I set a $3 budget and the agent halted around $2-4 of real spend." Both are useful. The second is tighter.

## Without calibration

When no calibration data exists, `l6e_authorize_call` uses raw token-based cost estimates. These are directionally correct — if one session uses more budget than another, it likely cost more in reality too — but the absolute dollar amounts can be 2-10x off from your provider's billing.

This is still meaningfully better than no budget at all. An agent with an imprecise $2 budget behaves differently than an agent with no concept of cost.

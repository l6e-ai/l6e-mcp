---
id: intro
title: Introduction
sidebar_label: Introduction
sidebar_position: 1
slug: /
---

# l6e-mcp

**l6e gives your AI coding agent a budget.**

Set a dollar limit per task, and your agent will checkpoint before expensive operations, get halt signals when it's spending too much, and give you a structured cost-aware workflow. No proxy, no SDK — just an MCP server that works with Cursor, Claude Code, Windsurf, and OpenClaw.

Import your billing data and l6e learns your cost patterns — the more you use it, the tighter the calibration gets.

:::tip Prompt Guide
Read the **[Prompt Guide](prompt-guide)** for practical patterns that make budget enforcement work well — including how to prompt through a full plan → implement → review lifecycle.
:::

## Quickstart

```bash
pip install l6e-mcp
```

Then follow the setup guide for your editor:

- **[Cursor →](setup/cursor)**
- **[Claude Code →](setup/claude-code)**
- **[Windsurf →](setup/windsurf)**
- **[OpenClaw →](setup/openclaw)**

## Tools

| Tool | Purpose |
|---|---|
| `l6e_run_start` | Open a new budget session. Returns `session_id`. Accepts optional `parent_session_id` to link child sessions in multi-session orchestration. |
| `l6e_authorize_call` | Gate-check a pending tool call and return a `call_id`. Pass `check_only=True` for a lightweight budget pressure check without recording a call. |
| `l6e_record_usage` | Attach exact token usage to an existing `call_id` (idempotent). |
| `l6e_run_end` | Close the session and flush the run log to `.l6e/runs.jsonl`. Returns exactness state, mode coverage gaps, and pending reconciliation count. |

## How it works

- Budget gate runs before each tool call via `l6e_authorize_call`
- Session state is persisted locally in SQLite (`~/.l6e/sessions.db`)
- Run logs are written to `~/.l6e/runs.jsonl` (set via `L6E_LOG_PATH`)
- Optional exact reconciliation via `l6e_record_usage` when actual token counts are available

## Running locally without a backend proxy

Out of the box, budgets are **directionally accurate**. The MCP protocol does not expose provider response data, so `l6e-mcp` accounts for spend using the token estimates the agent provides before each call. The dollar amounts reflect what the agent *estimated* it was about to spend, not what your provider billed.

That's enough to change how the agent works. An agent with a $2 budget scopes tasks more tightly, launches fewer sub-agents, and stops earlier when a task balloons — even if the estimate-to-billing ratio is off. The behavioral enforcement is the point; accurate accounting makes it quantitatively tighter.

**Calibration makes it billing-accurate.** Set manual per-model factors in `~/.l6e/config.toml` for immediate improvement, or sign up at [app.l6e.ai](https://app.l6e.ai) and import your billing data for automatic calibration that adapts as you use it. See [Calibration](concepts/calibration) for details.

**A practical starting point:** Set small budgets ($1–3) and observe how estimates track against your provider's actual costs. See [Local Enforcement](concepts/local-estimate-only) for a full explanation.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `L6E_LOG_PATH` | `.l6e/runs.jsonl` (relative to cwd) | Override the run log path. **Always set this to an absolute path** (e.g. `~/.l6e/runs.jsonl`). The default is relative to the MCP server's working directory, which varies by client. |
| `L6E_SESSION_DB_PATH` | `~/.l6e/sessions.db` | Override the local SQLite database path. |
| `L6E_API_KEY` | _(unset)_ | API key for cloud sync. Get a key at [app.l6e.ai](https://app.l6e.ai). Can also be set in `~/.l6e/config.toml` as `api_key`. |
| `L6E_CLOUD_SYNC` | `false` | Set to `1` or `true` to sync session run logs to the l6e cloud. Requires `L6E_API_KEY`. |
| `L6E_CALIBRATION_FACTORS` | _(unset)_ | Manual per-model calibration factors. Format: `"model1:factor1,model2:factor2"`. Can also be set in `~/.l6e/config.toml` under `[calibration]`. See [Calibration](concepts/calibration). |
| `L6E_SEND_TASK_SUMMARIES` | `true` | Whether to include task summaries in cloud-synced session reports. Summaries are always stored locally regardless of this setting. |

## Exactness states

`l6e_run_end` returns an `exactness_state` for the completed session:

| State | Meaning |
|---|---|
| `all_estimate_only` | All calls used pre-call estimates |
| `partial_exact` | Some calls reconciled with exact usage |
| `fully_exact_for_supported_calls` | All reconcilable calls have exact usage |
| `exactness_degraded` | Reconciliation expected but not received for some calls |

`l6e_run_end` also returns `pending_exact_calls`, `last_reconciled_at`, `mode_coverage`, and `mode_coverage_gaps`.

`l6e_authorize_call` with `check_only=True` does not report exactness mid-session — it is intentionally lightweight, returning only `budget_pressure`, `remaining_usd`, and `pct_used`. It accepts `estimated_prompt_tokens` and `estimated_completion_tokens` as a forcing function: the agent must think about the cost of its next stage before checking status.

## Mode coverage

`l6e_run_start` accepts per-mode exactness capability overrides to reflect what your setup can reconcile:

| `usage_channel` | Ask | Plan | Agent |
|---|---|---|---|
| `none` (default) | no | no | no |
| `self_hosted_relay` | yes | yes | no |
| `hosted_edge` | yes | yes | yes |
| `manual_import` | no | no | no |

Override these defaults by passing `ask_mode_exact_capable`, `plan_mode_exact_capable`, or `agent_mode_exact_capable` to `l6e_run_start`. Modes marked exact-capable that don't receive reconciliation appear in `mode_coverage_gaps` at session end.

## Known limitations

- **Rerouting requires a local Ollama instance.** When `l6e_authorize_call` returns `"action": "reroute"`, the local router needs a running Ollama process with a compatible model installed. Without it, rerouting cannot be executed. The MCP protocol also has no primitive for forcing a model switch — reroute is always advisory.
- **Estimate-based by default.** Exact real-time accounting requires `l6e_record_usage` calls from your agent with the actual token counts after each LLM call completes.
- **Cloud sync is opt-in.** Sessions persist locally in SQLite by default. Set `L6E_API_KEY` and `L6E_CLOUD_SYNC=1` to sync run logs to the l6e cloud.
- **Savings shows $0 when model pricing is unknown.** If the cost estimator returns `0.0` for either model, `savings_usd` will be `0.0` regardless of actual price difference. Check `savings_confidence` in `l6e_run_end` to gauge reliability.

## License

Apache 2.0

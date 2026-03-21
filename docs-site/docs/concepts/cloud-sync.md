---
id: cloud-sync
title: Cloud Sync
sidebar_label: Cloud Sync
sidebar_position: 3
---

By default, l6e runs fully local — session data stays in SQLite on your machine and run logs are written to `~/.l6e/runs.jsonl`. Cloud sync sends session metadata to the l6e dashboard so you get run history, usage charts, and [calibration](calibration).

## Getting an API key

1. Sign up at [app.l6e.ai](https://app.l6e.ai)
2. Go to **Settings → API Keys**
3. Create a key — it starts with `sk-l6e-`

## Enabling cloud sync

Set two environment variables in your MCP server config:

```json
{
  "env": {
    "L6E_API_KEY": "sk-l6e-...",
    "L6E_CLOUD_SYNC": "1"
  }
}
```

See the setup guide for your client ([Cursor](../setup/cursor), [Claude Code](../setup/claude-code), [Windsurf](../setup/windsurf), [OpenClaw](../setup/openclaw)) for the full config location.

After setting these, restart your client. Cloud sync activates on the next `l6e_run_end` call.

## What syncs

| Data | Synced | Notes |
|---|---|---|
| Session ID and timestamps | Yes | When the session started and ended |
| Model and client identifiers | Yes | Which model and MCP client were used |
| Per-call token estimates | Yes | The estimates the agent provided at each checkpoint |
| Per-call gate decisions | Yes | `allow`, `reroute`, `halt` for each call |
| Total estimated cost | Yes | Accumulated spend for the session |
| Task summaries | Configurable | Controlled by `L6E_SEND_TASK_SUMMARIES` (default: `true`). Set to `false` to omit. |
| Prompts and completions | **Never** | l6e never sees or stores prompt content |
| Source code | **Never** | l6e has no access to your codebase |

## What you get

- **Run history** — every session with cost, call count, gate decisions, and exactness state
- **Usage charts** — spend over time, broken down by model and client
- **Calibration** — import billing data and l6e computes your personal calibration factor. See [Calibration](calibration) for details.
- **Reconciliation** — match l6e sessions against provider billing to see how estimates compare to reality

Cloud sync is also the mechanism that enables automatic calibration. When cloud sync is on, `l6e_authorize_call` gate decisions use your personal calibration factor from l6e.ai — computed from your billing imports. See [Calibration](calibration) for the calibration paths and [l6e.ai Integration](cloud-api) for the full setup guide.

## How sync works

Session reports sync asynchronously via a local outbox. Reports are queued at `l6e_run_end` and drained in a background thread on the next `l6e_run_start`. This means cloud sync never blocks your agent's workflow — data appears in the dashboard after the next session starts. If the network is down, reports accumulate locally and sync when connectivity returns.

## Disabling cloud sync

Remove `L6E_CLOUD_SYNC` (or set it to `false`) and restart your client. Sessions continue to be stored locally. Previously synced data remains in the dashboard — delete it from **Settings → Data** if needed.

## Privacy

l6e syncs session metadata and cost data. It never receives, stores, or transmits prompt content, completions, or source code. The MCP server has no access to the LLM provider's request or response payloads — it only sees the token estimates and tool parameters that the agent passes to `l6e_authorize_call`.

Task summaries (the 5-10 word labels passed to `l6e_run_start` and `l6e_run_end`) are synced by default. Set `L6E_SEND_TASK_SUMMARIES=false` to omit them.

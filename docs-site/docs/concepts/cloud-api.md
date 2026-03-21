---
id: cloud-api
title: l6e.ai Integration
sidebar_label: l6e.ai Integration
sidebar_position: 4
---

The l6e MCP server works fully local — no account required. An [l6e.ai](https://l6e.ai) account adds automatic calibration, a dashboard, and run history. Everything is free with generous usage limits.

## What you get

- **Automatic calibration** — l6e computes a personal calibration factor from your billing data. Gate decisions use this factor so budget pressure and halt thresholds reflect your actual costs, not raw estimates. See [Calibration](calibration) for how factors work.
- **Dashboard** — session history with call-level detail, gate decisions, and exactness state at [app.l6e.ai](https://app.l6e.ai).
- **Usage charts** — estimated and calibrated spend over time, broken down by model and client.
- **Billing reconciliation** — import your provider's billing CSV and see how l6e estimates compare to what you were actually charged.

## Setup

1. Sign up at [app.l6e.ai](https://app.l6e.ai)
2. Go to **Settings → API Keys** and create a key (starts with `sk-l6e-`)
3. Add two env vars to your MCP config:

```json
{
  "env": {
    "L6E_API_KEY": "sk-l6e-...",
    "L6E_CLOUD_SYNC": "1"
  }
}
```

4. Restart your client

See the setup guide for your client ([Cursor](../setup/cursor), [Claude Code](../setup/claude-code), [Windsurf](../setup/windsurf), [OpenClaw](../setup/openclaw)) for the full config location and examples.

After restarting, session data syncs automatically on each `l6e_run_end`. Gate decisions on `l6e_authorize_call` (full gate, not `check_only`) use your calibrated factor from the server.

## Import your billing data

Calibration requires billing data to compute your factor. Without it, cloud sync still gives you the dashboard and run history — but gate decisions use raw estimates, same as local-only mode.

1. Go to [app.l6e.ai/reconciliation](https://app.l6e.ai/reconciliation)
2. Download your billing CSV from your provider (Cursor: **Settings → Billing → Download CSV**)
3. Upload the CSV — l6e matches billing line items against your session data by timestamp and model
4. Your calibration factor updates immediately and is used on subsequent sessions

Import periodically (weekly or after a billing cycle) to keep the factor fresh. More sessions across different models, task types, and workflows means more data points — each import refines the factor further.

More provider import formats are on the roadmap. Cursor CSV is the only supported format today.

## How it works (no added latency)

The cloud integration is designed so your agent's workflow is never blocked by network calls.

**Session reports** sync via a local outbox. Reports are queued at `l6e_run_end` and drained in a background thread on the next `l6e_run_start`. The agent never waits for the sync to complete.

**Calibrated gate decisions** work like this:

1. The first `l6e_authorize_call` (full gate, `check_only=False`) in a session makes a server round-trip. The server returns the gate decision with your calibrated factor applied.
2. The factor is cached locally with a 5-minute TTL.
3. Subsequent `check_only=True` calls use the cached factor — zero network overhead. These are the high-frequency calls that happen between tool calls.
4. The next full gate call after the cache expires refreshes it.

**If the server is unreachable** (network down, API outage), the MCP server falls back to [manual config factors](calibration#manual-per-model-factors) if configured, or raw estimates if not. Gate decisions always return — they never hang waiting for the cloud.

## What syncs (and what doesn't)

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

See [Cloud Sync](cloud-sync) for more details on the sync mechanism and privacy.

## Usage limits

Cloud features are free with usage limits on sessions synced, billing imports, and API requests per month. The limits are generous for individual use. Check your current usage at [app.l6e.ai](https://app.l6e.ai) under **Settings**.

## Working offline

Cloud sync is resilient to network issues:

- **Reports queue locally** and drain on the next session start. No data is lost if the network is down during `l6e_run_end`.
- **The calibration cache** serves the last-known factor for 5 minutes after the last server response.
- **Manual config factors** (set in `~/.l6e/config.toml` or `L6E_CALIBRATION_FACTORS`) provide a permanent offline fallback that never expires.
- **Stale sessions** (where `l6e_run_end` was never called, e.g. the editor crashed) are recovered and synced during the next outbox drain.

If you need calibration to work without any network dependency, [manual per-model factors](calibration#manual-per-model-factors) are the right path.

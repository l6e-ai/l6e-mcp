# l6e-mcp

[![pytest](https://github.com/l6e-ai/l6e-mcp/actions/workflows/pytest.yml/badge.svg?branch=main)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/pytest.yml)
[![coverage](https://raw.githubusercontent.com/l6e-ai/l6e-mcp/python-coverage-comment-action-data/badge.svg)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/pytest.yml)
[![mypy](https://github.com/l6e-ai/l6e-mcp/actions/workflows/mypy.yml/badge.svg?branch=main)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/mypy.yml)
[![ruff](https://github.com/l6e-ai/l6e-mcp/actions/workflows/ruff.yml/badge.svg?branch=main)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/ruff.yml)

**l6e makes your AI coding agent cost-efficient.**

Set a budget per task. Your agent checkpoints before expensive operations, gets halt signals when it's spending too much, and stops when it's done — not when it runs out of money. Import your billing data and l6e learns your actual cost patterns, so estimates get tighter over time.

No proxy. No SDK changes. Just an MCP server that works with Cursor, Claude Code, and Windsurf.

> **Dogfooding:** [docs.l6e.ai](https://docs.l6e.ai) is built and maintained using l6e itself.

## Quick start

**1. Install**

```bash
pip install l6e-mcp
# or, zero-install:
uvx l6e-mcp
```

**2. Add to your MCP config**

Cursor (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "l6e": {
      "command": "uvx",
      "args": ["l6e-mcp"]
    }
  }
}
```

See [docs.l6e.ai/setup](https://docs.l6e.ai/setup) for Claude Code and Windsurf configs.

**3. Add the enforcement rule**

Copy the [l6e budget enforcement rule](https://docs.l6e.ai/setup/cursor) to `.cursor/rules/` so your agent knows how to use the budget tools.

That's it — start a session, set a budget, and your agent is cost-aware.

**4. (Optional) Connect to the dashboard**

Create a free account at [app.l6e.ai](https://app.l6e.ai) for session history, spend tracking, and billing import for calibration:

```json
{
  "mcpServers": {
    "l6e": {
      "command": "uvx",
      "args": ["l6e-mcp"],
      "env": {
        "L6E_API_KEY": "sk-l6e-...",
        "L6E_CLOUD_SYNC": "1"
      }
    }
  }
}
```

## How it works

l6e sits as an MCP server between your IDE and your agent. At each checkpoint, the agent calls `l6e_authorize_call` — l6e checks the remaining budget and returns **allow** or **halt**.

- **allow** — proceed; check `budget_pressure` to decide how aggressively to economize
- **halt** — budget exhausted, stop the session

Session state is persisted locally in SQLite (`~/.l6e/sessions.db`). No LLM calls are proxied — l6e only sees the metadata your agent passes at each checkpoint (token estimates, model, stage label). It never sees your prompts, completions, or source code.

## Calibration

Out of the box, l6e uses raw token estimates from LiteLLM pricing. These are directionally accurate but can diverge significantly from what your provider actually bills, depending on your model and usage patterns.

Import your billing CSV from Cursor or your LLM provider at [app.l6e.ai](https://app.l6e.ai) and l6e computes a personal calibration factor for each model you use. The more sessions you run, the tighter the estimates get.

For manual calibration without cloud sync, add a `[calibration]` section to `~/.l6e/config.toml`:

```toml
[calibration]
claude-4-opus = 72.0
claude-4-sonnet = 45.0
claude-3.5-haiku = 12.0
```

## Free vs Pro

|  | Free | Pro ($15/mo) |
| --- | --- | --- |
| Budget enforcement | ✓ | ✓ |
| Local session storage | ✓ | ✓ |
| Cloud sync + dashboard | ✓ (90-day history) | ✓ (unlimited) |
| Billing import | ✓ (5/month) | ✓ (unlimited) |
| Per-model calibration | ✓ | ✓ |
| Community baseline factors | ✓ | ✓ |

[Upgrade at app.l6e.ai →](https://app.l6e.ai)

## MCP tools

| Tool | Purpose |
| --- | --- |
| `l6e_run_start` | Open a new budget session. Returns `session_id`. |
| `l6e_authorize_call` | Gate before sub-agents and stage transitions. Returns `allow` or `halt`. Pass `check_only=True` for a lightweight budget pressure check. |
| `l6e_record_usage` | Attach exact token counts to a call (optional, improves accuracy). |
| `l6e_run_end` | Close the session and flush the run log. |

Full tool reference at [docs.l6e.ai/tools](https://docs.l6e.ai/tools).

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `L6E_API_KEY` | _(unset)_ | API key for cloud sync |
| `L6E_CLOUD_SYNC` | `false` | Set to `1` to enable cloud sync |
| `L6E_CLOUD_ENDPOINT` | `https://api.l6e.ai` | Override the cloud sync endpoint |
| `L6E_LOG_PATH` | `.l6e/runs.jsonl` | Run log path — set to an absolute path |
| `L6E_SESSION_DB_PATH` | `~/.l6e/sessions.db` | Local SQLite database path |
| `L6E_CONFIG_PATH` | `~/.l6e/config.toml` | Config file path |

## Known limitations

- **Estimate-first by default.** Exact accounting requires `l6e_record_usage` calls with actual token counts after each LLM call. Without them, budgets are based on the agent's pre-call estimates.
- **Local persistence by default.** Sessions persist in a local SQLite database. Cloud sync is available with a free account at [app.l6e.ai](https://app.l6e.ai) — set `L6E_API_KEY` and `L6E_CLOUD_SYNC=1` to enable.

## Links

- [docs.l6e.ai](https://docs.l6e.ai) — setup guides, tool reference, calibration walkthrough
- [app.l6e.ai](https://app.l6e.ai) — dashboard, run history, billing import
- [l6e core library](https://github.com/l6e-ai/l6e) — embed budget enforcement in Python agent pipelines

## License

MIT

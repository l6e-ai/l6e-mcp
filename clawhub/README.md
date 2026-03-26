# l6e-budget: per-session cost guardrails for AI coding agents

l6e-budget gives your OpenClaw agent a hard budget ceiling and a blocking gate before expensive work. Before each stage transition or sub-agent launch, the agent calls `l6e_authorize_call` and gets back `allow`, `reroute`, or `halt`.

No proxy. No backend. Works out of the box.

## What it does

- **Hard budget ceiling** — set a dollar limit at the start of every task with `l6e_run_start`
- **Blocking gate** — `l6e_authorize_call` must return `allow` before the agent starts a new stage or launches a sub-agent
- **Lightweight spend check** — `l6e_run_status` gives a read-only snapshot without consuming a gate slot
- **Run log** — `l6e_run_end` flushes a JSON summary to `~/.l6e/runs.jsonl`

## Five MCP tools

| Tool | Purpose |
|---|---|
| `l6e_run_start` | Open a budget session. Returns `session_id`. |
| `l6e_authorize_call` | Gate-check before expensive work. Returns `allow`, `reroute`, or `halt`. |
| `l6e_record_usage` | Attach exact token counts to a call (idempotent). |
| `l6e_run_status` | Read-only spend snapshot for the current session. |
| `l6e_run_end` | Close the session and flush the run log. |

## Configuration

Add to `~/.openclaw/openclaw.json`:

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "uvx",
      "args": ["l6e-mcp"],
      "transport": "stdio",
      "env": {
        "L6E_LOG_PATH": "${HOME}/.l6e/runs.jsonl"
      }
    }
  }
}
```

Restart the gateway after saving.

## Requirements

- Python 3.11+ (for `uvx`/`pip install`)
- No API keys required
- No external services

## Session lifecycle

```
l6e_run_start(budget_usd=2.0, model="claude-sonnet-4-5", client="openclaw")
  → { session_id: "session_openclaw_2026-03-12_abc123" }

l6e_authorize_call(session_id=..., tool_name="planning")
  → { action: "allow", remaining_usd: 1.96 }

[... do work ...]

l6e_authorize_call(session_id=..., tool_name="implement")
  → { action: "reroute", reason: "budget threshold reached" }
  → agent tells user to switch to a cheaper model

l6e_run_end(session_id=...)
  → { calls_made: 3, total_cost_usd: 0.04 }
```

## Works alongside OpenClaw Adaptive Routing

OpenClaw's optional Adaptive Model Routing (`agents.defaults.model.adaptiveRouting`) tries cheap/local models first and escalates on quality failure. l6e is complementary: Adaptive Routing handles the local-first optimisation, l6e enforces the hard cost ceiling across the whole session.

## How estimation works

Without a backend proxy, all accounting is based on token estimates the agent constructs before each call using live litellm pricing data. The numbers are approximate — they reflect what the agent expected to spend, not what your provider billed. In practice this still works: an agent that knows it has a finite budget tends to scope tasks tighter, launch fewer sub-agents, and stop earlier.

For exact accounting, use `l6e_record_usage` to attach real token counts after each LLM call.

## Troubleshooting

**`spawn uvx ENOENT`** — The gateway can't find `uvx`. Use the full path:
```bash
which uvx  # e.g. /opt/homebrew/bin/uvx
```
Set `"command": "/opt/homebrew/bin/uvx"` in your config.

**No log file at `~/.l6e/runs.jsonl`** — Check that `L6E_LOG_PATH` is set in the `env` block of your config. Without it, the log writes relative to the gateway's working directory.

**Server shows as `stopped` in `openclaw mcp list`** — Check logs: `openclaw logs --mcp --server l6e-budget --tail 50`. Usually a PATH or Python version issue.

## Links

- [Full setup guide](https://github.com/l6e-ai/l6e-mcp)
- [GitHub](https://github.com/l6e-ai/l6e-mcp)
- [Issues](https://github.com/l6e-ai/l6e-mcp/issues)
- [License: MIT](https://github.com/l6e-ai/l6e-mcp/blob/main/LICENSE)

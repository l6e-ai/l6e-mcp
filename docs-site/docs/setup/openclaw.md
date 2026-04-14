---
id: openclaw
title: "Setup: OpenClaw"
sidebar_label: OpenClaw
sidebar_position: 3
---

Connect the `l6e-budget` MCP server to OpenClaw for session-scoped budget enforcement.

The agent gates calls using pre-call token estimates. Out of the box, budgets are directionally accurate — [calibration](../concepts/calibration) makes them billing-accurate. Call `l6e_record_usage` if you want to feed actual token counts back into the ledger for exact accounting.

## Install

No separate `pip install` is required if you use `uvx` (recommended). `uvx` runs `l6e-mcp` in an isolated environment on first use.

If you prefer a manual install:

```bash
pip install l6e-mcp
```

## Configure

Edit `~/.openclaw/openclaw.json` and add the `mcpServers` block. If the file already has other keys, merge this into the existing JSON:

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "uvx",
      "args": ["l6e-mcp"],
      "env": {
        "L6E_LOG_PATH": "${HOME}/.l6e/runs.jsonl",
        "L6E_API_KEY": "sk-l6e-...",
        "L6E_CLOUD_SYNC": "1"
      }
    }
  }
}
```

**`L6E_LOG_PATH` is required.** OpenClaw's gateway spawns MCP servers as child processes from `~/.openclaw/`, not your project directory. Without this env var, `runs.jsonl` will be written to an unpredictable location.

`L6E_API_KEY` and `L6E_CLOUD_SYNC` are optional — omit them to run fully local. When set, session run logs sync to the l6e cloud and gate decisions use your personal calibration factor. See [l6e.ai Integration](../concepts/cloud-api) for what cloud sync enables.

If `uvx` is not on the PATH that OpenClaw sees, use the full path:

```bash
which uvx  # then substitute the result in "command"
```

**After editing**, restart the gateway to pick up the change:

```bash
openclaw gateway restart
```

### Project-level config (team setups)

OpenClaw also reads `openclaw.config.json` from the project root, merging it with the global config. For team setups, add the `mcpServers` block there instead of editing the global file:

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "uvx",
      "args": ["l6e-mcp"],
      "env": {
        "L6E_LOG_PATH": "${HOME}/.l6e/runs.jsonl",
        "L6E_API_KEY": "sk-l6e-...",
        "L6E_CLOUD_SYNC": "1"
      }
    }
  }
}
```

### Per-agent scoping (optional)

By default, all agents can use `l6e-budget`. To restrict it to a specific agent, add `mcpServers` to that agent's entry in `agents.list`:

```json
{
  "agents": {
    "list": [
      {
        "id": "code",
        "mcpServers": ["l6e-budget"]
      }
    ]
  }
}
```

## Verify

```bash
openclaw mcp list
openclaw doctor
```

`l6e-budget` should appear with status `running` and four tools listed:

- `l6e_run_start`
- `l6e_authorize_call`
- `l6e_record_usage`
- `l6e_run_end`

`openclaw doctor` validates the full config and reports any parse errors. If `l6e-budget` does not appear, check that `uvx` is on your PATH (`which uvx`) or that `l6e-mcp` is installed (`pip show l6e-mcp`).

## Rules for AI

The enforcement rule is what teaches the agent the l6e lifecycle. It covers checkpoint policy (when to call `l6e_authorize_call`), estimation defaults, model identification, sub-agent budget gates, budget sizing guidance, and session safety. Without it, the MCP tools are available but the agent won't know how to use them correctly.

Add the rule to `AGENTS.md` in the agent workspace so it is loaded every session.

- **All agents (default workspace)**: `~/.openclaw/workspace/AGENTS.md`
- **Specific agent**: `~/.openclaw/agents/{agent_id}/AGENTS.md`

The fastest way to install the rule is from the bundled copy in your `l6e-mcp` package:

```bash
l6e-mcp install-rules --client openclaw
```

This writes the rule to `.openclaw/AGENTS.md` in the current directory. For manual installation, the rule content is also available at [`.openclaw/AGENTS.md`](https://github.com/l6e-ai/l6e-mcp/blob/main/.openclaw/AGENTS.md) in the repository.

See the [Prompt Guide](../prompt-guide) for always-apply vs on-demand patterns and how to override enforcement per-message.

## Example conversation starter

Use this at the start of a new chat to test the full flow:

```
Using the l6e-budget MCP tools, call l6e_run_start with budget_usd=1.00,
model="claude-sonnet-4-20250514", client="openclaw". Show me the full JSON
response including session_id. Then add a one-line docstring to any function
in this project. Call l6e_authorize_call before the edit and l6e_run_end
when done.
```

Note: pass the bare model ID without the provider prefix — use `"claude-sonnet-4-20250514"`, not `"anthropic/claude-sonnet-4-20250514"`. If you are unsure of the active model ID, pass `"unknown"`.

## Reading your run log

After a session ends:

```bash
# Most recent session
tail -1 ~/.l6e/runs.jsonl | python -m json.tool

# All sessions — cost summary
cat ~/.l6e/runs.jsonl | python -c "
import sys, json
for line in sys.stdin:
    r = json.loads(line)
    print(f\"{r['run_id']}  \${r['total_cost']:.4f}  {r['calls_made']} calls  {r['reroutes']} reroutes  source={r['source']}\")
"
```

### Verify the log path is correct

Run a minimal session to confirm `runs.jsonl` lands in `~/.l6e/` and not somewhere else:

```
Call l6e_run_start with budget_usd=0.10, model="claude-sonnet-4-20250514",
client="openclaw". Then immediately call l6e_run_end with the session_id.
```

Then check:

```bash
tail -1 ~/.l6e/runs.jsonl | python -m json.tool
```

If the file doesn't exist or is empty, `L6E_LOG_PATH` is not being passed to the server process. Re-check the `env` block in your config and run `openclaw gateway restart`.

## Known limitations

- **Always call `l6e_run_end`.** If the gateway stops before `l6e_run_end` is called, the run log for that session is not written.
- **Never import l6e_mcp directly.** The session registry lives only in the MCP server process. Importing `l6e_mcp.server` in a subprocess will always return "Unknown session".
- **Rerouting is advisory only.** When `l6e_authorize_call` returns `"action": "reroute"`, the agent stops work and tells you to switch to a cheaper model. The MCP protocol has no mechanism for forcing a model switch — the response is a signal to you, not an automatic redirect.
- **Gateway restart required.** Unlike Cursor, there is no hot-reload for MCP config changes. Run `openclaw gateway restart` after any edit to `mcpServers`.
- **Adaptive Model Routing.** OpenClaw's `adaptiveRouting` feature (opt-in via `agents.defaults.model.adaptiveRouting`) is complementary to l6e's budget enforcement: l6e signals when the budget ceiling is reached, Adaptive Routing handles local-first model selection independently. Both can be active simultaneously.

---
id: openclaw
title: "Setup: OpenClaw"
sidebar_label: OpenClaw
sidebar_position: 3
---

Connect the `l6e-budget` MCP server to OpenClaw for session-scoped budget enforcement.

This setup uses the estimate-first path. The agent gates calls using pre-call token estimates; call `l6e_record_usage` manually if you want to feed actual token counts back into the ledger for exact accounting.

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
        "L6E_LOG_PATH": "${HOME}/.l6e/runs.jsonl"
      }
    }
  }
}
```

**`L6E_LOG_PATH` is required.** OpenClaw's gateway spawns MCP servers as child processes from `~/.openclaw/`, not your project directory. Without this env var, `runs.jsonl` will be written to an unpredictable location.

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
        "L6E_LOG_PATH": "${HOME}/.l6e/runs.jsonl"
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

`l6e-budget` should appear with status `running` and five tools listed:

- `l6e_run_start`
- `l6e_authorize_call`
- `l6e_record_usage`
- `l6e_run_status`
- `l6e_run_end`

`openclaw doctor` validates the full config and reports any parse errors. If `l6e-budget` does not appear, check that `uvx` is on your PATH (`which uvx`) or that `l6e-mcp` is installed (`pip show l6e-mcp`).

## Rules for AI

Add the enforcement rule to `AGENTS.md` in the agent workspace so it is loaded every session.

- **All agents (default workspace)**: `~/.openclaw/workspace/AGENTS.md`
- **Specific agent**: `~/.openclaw/agents/{agent_id}/AGENTS.md`

The rule content is in [`mcp/.openclaw/AGENTS.md`](https://github.com/l6e-ai/l6e-mcp/blob/main/.openclaw/AGENTS.md) in the repository. Append its contents to your `AGENTS.md`.

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

## Known limitations

- **Always call `l6e_run_end`.** If the gateway stops before `l6e_run_end` is called, the run log for that session is not written.
- **Never import l6e_mcp directly.** The session registry lives only in the MCP server process. Importing `l6e_mcp.server` in a subprocess will always return "Unknown session".
- **Reroute requires Ollama.** Rerouting on budget pressure requires a local Ollama model to be available on your machine. If no Ollama model is detected, `l6e_authorize_call` returns `halt` instead of `reroute`.
- **Gateway restart required.** Unlike Cursor, there is no hot-reload for MCP config changes. Run `openclaw gateway restart` after any edit to `mcpServers`.
- **Adaptive Model Routing.** OpenClaw's `adaptiveRouting` feature (opt-in via `agents.defaults.model.adaptiveRouting`) is complementary to l6e's `reroute`: l6e enforces the cost ceiling, Adaptive Routing handles local-first model selection within it. Both can be active simultaneously.

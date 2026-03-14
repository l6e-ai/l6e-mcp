---
id: windsurf
title: "Setup: Windsurf"
sidebar_label: Windsurf
sidebar_position: 4
---

Connect the `l6e-budget` MCP server to Windsurf (Cascade) for session-scoped budget enforcement.

This setup uses the estimate-first path. The agent gates calls using pre-call token estimates; call `l6e_record_usage` manually if you want to feed actual token counts back into the ledger for exact accounting.

## Install

No separate `pip install` is required if you use `uvx` (recommended). `uvx` runs `l6e-mcp` in an isolated environment on first use.

If you prefer a manual install:

```bash
pip install l6e-mcp
```

## Configure

Edit `~/.codeium/windsurf/mcp_config.json`. If the file doesn't exist, create it.

You can also open it through the UI: **Command Palette** (`Cmd+Shift+P`) → `Windsurf: Configure MCP Servers`.

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

**`L6E_LOG_PATH` is required.** Windsurf spawns MCP stdio servers with `cwd=/`, so without this env var `runs.jsonl` will be written to `/.l6e/runs.jsonl`, which is typically permission-denied.

If you installed `l6e-mcp` manually instead of using `uvx`:

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "l6e-mcp",
      "env": {
        "L6E_LOG_PATH": "${HOME}/.l6e/runs.jsonl"
      }
    }
  }
}
```

**Restart Windsurf completely** after editing this file.

## Verify

Open the Cascade panel and click the **MCPs** icon in the top-right corner. The `l6e-budget` server should appear with five tools listed:

- `l6e_run_start`
- `l6e_authorize_call`
- `l6e_record_usage`
- `l6e_run_status`
- `l6e_run_end`

If the server does not appear, check that `uvx` is on your PATH (`which uvx`) or that `l6e-mcp` is installed (`pip show l6e-mcp`).

## Rules for AI

Add the enforcement rule to a Windsurf rules file so Cascade automatically follows the l6e lifecycle.

The rule content is in [`.cursor/rules/l6e-budget-enforcement.mdc`](https://github.com/l6e-ai/l6e-mcp/blob/main/.cursor/rules/l6e-budget-enforcement.mdc) in the repository. Paste it into your Windsurf rules (`Cmd+Shift+P` → `Windsurf: Open Rules`).

## Example conversation starter

Use this at the start of a new chat to test the full flow:

```
Using the l6e-budget MCP tools, call l6e_run_start with budget_usd=1.00,
model="gpt-4o", client="windsurf". Show me the full JSON response including
session_id. Then add a one-line docstring to any function in this project.
Call l6e_authorize_call before the edit and l6e_run_end when done.
```

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
Call l6e_run_start with budget_usd=0.10, model="gpt-4o",
client="windsurf". Then immediately call l6e_run_end with the session_id.
```

Then check:

```bash
tail -1 ~/.l6e/runs.jsonl | python -m json.tool
```

If the file doesn't exist or is empty, `L6E_LOG_PATH` is not being passed to the server process. Re-check the `env` block in your config and restart Windsurf.

## Known limitations

- **Always call `l6e_run_end`.** If the Windsurf window closes before `l6e_run_end` is called, the run log for that session is not written.
- **Never import l6e_mcp directly.** The session registry lives only in the MCP server process. Importing `l6e_mcp.server` in a subprocess will always return "Unknown session".
- **Rerouting is advisory only.** When `l6e_authorize_call` returns `"action": "reroute"`, the agent stops work and tells you to switch to a cheaper model. The MCP protocol has no mechanism for forcing a model switch — the response is a signal to you, not an automatic redirect.

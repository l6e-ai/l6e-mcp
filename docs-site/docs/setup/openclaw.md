---
id: openclaw
title: "Setup: OpenClaw"
sidebar_label: OpenClaw
sidebar_position: 4
---

Connect the `l6e-budget` MCP server to OpenClaw for session-scoped budget enforcement.

## Known issue: OpenClaw spawns MCP servers from the gateway working directory

OpenClaw runs MCP stdio servers as child processes of the gateway. The working directory is the gateway's startup directory (typically `~/.openclaw/` or the user home), not your project root. This means the default `l6e` run log path `.l6e/runs.jsonl` will resolve relative to the gateway — not where you expect.

**Recommended:** Set `L6E_LOG_PATH` in your MCP config to an absolute path so logs always land in the same place regardless of which project you're working on.

## Install

No separate `pip install` is required if you use `uvx` (recommended). `uvx` runs `l6e-mcp` in an isolated environment on first use.

If you prefer a manual install:

```bash
pip install l6e-mcp
```

## Configure

Edit `~/.openclaw/openclaw.json`. Add the `l6e-budget` entry under `mcpServers`:

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "uvx",
      "args": ["l6e-mcp"],
      "transport": "stdio",
      "env": {
        "L6E_LOG_PATH": "/Users/YOUR_USERNAME/.l6e/runs.jsonl"
      }
    }
  }
}
```

Replace `/Users/YOUR_USERNAME` with your actual home directory (`echo $HOME`).

If you installed `l6e-mcp` manually:

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "l6e-mcp",
      "transport": "stdio",
      "env": {
        "L6E_LOG_PATH": "/Users/YOUR_USERNAME/.l6e/runs.jsonl"
      }
    }
  }
}
```

After saving, restart the gateway:

```bash
openclaw gateway restart
```

## Verify

```bash
openclaw mcp list
```

You should see `l6e-budget` with status `running` and 5 tools:

```
SERVER          STATUS    TOOLS   TRANSPORT
l6e-budget      running   5       stdio
```

The five tools are:
- `l6e_run_start`
- `l6e_authorize_call`
- `l6e_record_usage`
- `l6e_run_status`
- `l6e_run_end`

## Agent rules (AGENTS.md)

OpenClaw uses `AGENTS.md` files in each agent's workspace directory to define operating instructions. Create or append to `~/.openclaw/workspace-main/AGENTS.md`:

```text
## l6e budget enforcement

Use l6e only via MCP tools. Never import `l6e` or `l6e_mcp` in Python.
Pass `model` as the exact active billing model ID (or `"unknown"`), and
set `client` to `"openclaw"`.

Always call MCP tools with only the parameters defined in the tool schema.
Never invent parameters.

- `l6e_run_start`: accepts `budget_usd`, `model`, `client`, and optional
  config fields. Do NOT pass `session_id` or `task_description`.
- `l6e_run_end`: accepts only `session_id`. Do NOT pass `status`.

Sub-agent gate (blocking): call `l6e_authorize_call` with
`actor_type="subagent"` and get `allow` before launching ANY sub-agent.

Stage transitions (blocking): call `l6e_authorize_call` at every stage
boundary. Respect `action`:
- `allow`: proceed
- `reroute`: tell the user to switch to a cheaper model
- `halt`: stop and report budget exhaustion

At the END of every task, call `l6e_run_end` with the `session_id`.
```

After editing `AGENTS.md`, restart the gateway:

```bash
openclaw gateway restart
```

## Per-agent scoping (optional)

Limit which agents have access to `l6e-budget` using per-agent MCP routing:

```json
{
  "agents": {
    "list": [
      {
        "id": "coder",
        "workspace": "~/.openclaw/workspace-coder",
        "mcpServers": ["l6e-budget", "github", "filesystem"]
      }
    ]
  }
}
```

## PATH issue with uvx

If you see `spawn uvx ENOENT` in `openclaw logs --mcp --server l6e-budget`, find the full path to `uvx`:

```bash
which uvx   # e.g. /opt/homebrew/bin/uvx
```

Then use it in your config:

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "/opt/homebrew/bin/uvx",
      "args": ["l6e-mcp"],
      "transport": "stdio",
      "env": {
        "L6E_LOG_PATH": "/Users/YOUR_USERNAME/.l6e/runs.jsonl"
      }
    }
  }
}
```

## Known limitations

- **Always call `l6e_run_end`.** If a conversation ends before `l6e_run_end` is called, the run log for that session is not written.
- **`L6E_LOG_PATH` is strongly recommended.** Without it, the log path resolves relative to the gateway's working directory.
- **Reroute is advisory.** `l6e_authorize_call` returning `reroute` does not automatically trigger OpenClaw's Adaptive Routing — that is a separate system.
- **Never import l6e_mcp directly.** The session registry lives only in the MCP server process.

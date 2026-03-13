---
id: windsurf
title: "Setup: Windsurf"
sidebar_label: Windsurf
sidebar_position: 2
---

Connect the `l6e-budget` MCP server to Windsurf's Cascade for session-scoped budget enforcement.

## Known issue: Windsurf spawns MCP servers with `cwd=/`

Windsurf runs MCP stdio servers with the working directory set to `/` (filesystem root). This means the default `l6e` run log path `.l6e/runs.jsonl` would resolve to `/.l6e/runs.jsonl`, which is a permission-denied location on most systems.

**You must set the `L6E_LOG_PATH` environment variable** in your Windsurf MCP config to point to an absolute path. See the configuration below.

## Install

No separate `pip install` is required if you use `uvx` (recommended).

If you prefer a manual install:

```bash
pip install l6e-mcp
```

## Configure

Edit `~/.codeium/windsurf/mcp_config.json`. Access it via the MCPs icon in the top-right of the Cascade panel → **Configure**.

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "uvx",
      "args": ["l6e-mcp"],
      "env": {
        "L6E_LOG_PATH": "/Users/YOUR_USERNAME/.l6e/runs.jsonl"
      }
    }
  }
}
```

Replace `/Users/YOUR_USERNAME` with your actual home directory path. On macOS you can find it by running `echo $HOME` in a terminal.

:::warning
Without `L6E_LOG_PATH`, the server attempts to write to `/.l6e/runs.jsonl` (root-relative) which fails silently with a permission error, and your session run data is lost.
:::

If you installed `l6e-mcp` manually instead of using `uvx`:

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "l6e-mcp",
      "env": {
        "L6E_LOG_PATH": "/Users/YOUR_USERNAME/.l6e/runs.jsonl"
      }
    }
  }
}
```

## Verify

Click the **MCPs** icon in the top-right corner of the Cascade panel. The `l6e-budget` server should appear with its five tools:

- `l6e_run_start`
- `l6e_authorize_call`
- `l6e_record_usage`
- `l6e_run_status`
- `l6e_run_end`

To confirm the log path is working, start a session and end it, then check:

```bash
tail -1 ~/.l6e/runs.jsonl
```

You should see a JSON object with `"source": "mcp"`.

## Example system prompt

Paste this into your Windsurf rules or at the start of a Cascade conversation:

```text
l6e-budget MCP tools are available. These are MCP tool calls — never invoke
them by importing l6e or l6e_mcp in Python.

At the start of EVERY task, call `l6e_run_start`:
- `budget_usd`: estimated task cost
- `client`: "windsurf"
- `model`: exact active billing model ID; if unknown, pass "unknown"
Store the returned `session_id`.

Sub-agent gate (blocking): call `l6e_authorize_call` with `actor_type="subagent"`
and get an `allow` before launching ANY sub-agent. No exceptions.

Stage transitions (blocking): call `l6e_authorize_call` at every stage boundary.
Respect `action`: allow → proceed, reroute → suggest cheaper model, halt → stop.

At the END of every task, call `l6e_run_end` with the `session_id`.
```

## Where run logs are written

Logs are written to the path you set in `L6E_LOG_PATH`. Using `~/.l6e/runs.jsonl` (absolute path) is recommended — it persists across projects.

## Known limitations

- **Always call `l6e_run_end`.** If Cascade ends before `l6e_run_end` is called, the run log is not written.
- **`L6E_LOG_PATH` is required.** Sessions will run correctly but logs will fail to write if it is not set.
- **Reroute requires Ollama.** Rerouting on budget pressure requires a local Ollama model to be available on your machine. If no Ollama model is detected, `l6e_authorize_call` returns `halt` instead of `reroute`.

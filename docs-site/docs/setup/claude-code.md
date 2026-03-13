---
id: claude-code
title: "Setup: Claude Code"
sidebar_label: Claude Code
sidebar_position: 3
---

Connect the `l6e-budget` MCP server to Claude Code for session-scoped budget enforcement.

## Install

No separate `pip install` is required if you use `uvx` (recommended). `uvx` runs `l6e-mcp` in an isolated environment on first use.

If you prefer a manual install:

```bash
pip install l6e-mcp
```

## Register the server

Run the following command once. It registers `l6e-budget` at **local scope** (visible only to you in the current project):

```bash
claude mcp add --transport stdio l6e-budget -- uvx l6e-mcp
```

To use a manual install instead of `uvx`:

```bash
claude mcp add --transport stdio l6e-budget -- l6e-mcp
```

Configuration is stored in `~/.claude.json`. No restart is required for stdio servers.

## Verify

```bash
# List registered servers
claude mcp list

# Inside a Claude Code session, check server status
/mcp
```

The `l6e-budget` server should appear in the `/mcp` output with its five tools:

- `l6e_run_start`
- `l6e_authorize_call`
- `l6e_record_usage`
- `l6e_run_status`
- `l6e_run_end`

## Example system prompt

Paste this into your Claude Code system prompt or at the start of a session:

```text
l6e-budget MCP tools are available. These are MCP tool calls — never invoke
them by importing l6e or l6e_mcp in Python.

At the start of EVERY task, call `l6e_run_start`:
- `budget_usd`: estimated task cost
- `client`: "claude-code"
- `model`: exact active billing model ID; if unknown, pass "unknown"
Store the returned `session_id`.

Sub-agent gate (blocking): call `l6e_authorize_call` with `actor_type="subagent"`
and get an `allow` before launching ANY sub-agent. No exceptions.

Stage transitions (blocking): call `l6e_authorize_call` at every stage boundary.
Respect `action`: allow → proceed, reroute → suggest cheaper model, halt → stop.

At the END of every task, call `l6e_run_end` with the `session_id`.
```

## Scope options

| Scope | Command flag | Stored in |
|---|---|---|
| Local (default) | *(omit flag)* | `~/.claude.json` under project path |
| User (all projects) | `--scope user` | `~/.claude.json` globally |
| Project (team-shared) | `--scope project` | `.mcp.json` in project root |

To share `l6e-budget` with your whole team, use project scope:

```bash
claude mcp add --transport stdio --scope project l6e-budget -- uvx l6e-mcp
```

## Where run logs are written

By default, logs are written to `.l6e/runs.jsonl` relative to the current working directory when Claude Code invokes the server (typically your project root).

## Known limitations

- **Always call `l6e_run_end`.** If Claude Code exits before `l6e_run_end` is called, the run log for that session is not written.
- **Reroute requires Ollama.** Rerouting on budget pressure requires a local Ollama model to be available on your machine. If no Ollama model is detected, `l6e_authorize_call` returns `halt` instead of `reroute`.

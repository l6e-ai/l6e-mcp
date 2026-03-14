---
id: claude-code
title: "Setup: Claude Code"
sidebar_label: Claude Code
sidebar_position: 2
---

Connect the `l6e-budget` MCP server to Claude Code for session-scoped budget enforcement.

This setup uses the estimate-first path. The agent gates calls using pre-call token estimates; call `l6e_record_usage` manually if you want to feed actual token counts back into the ledger for exact accounting.

## Install

No separate `pip install` is required if you use `uvx` (recommended). `uvx` runs `l6e-mcp` in an isolated environment on first use.

If you prefer a manual install:

```bash
pip install l6e-mcp
```

## Configure

Claude Code stores MCP server configurations at two scope levels. Use the CLI (recommended) or write the config file directly.

### CLI (recommended)

```bash
# User scope — available across all projects, stored in ~/.claude.json
claude mcp add --scope user -e "L6E_LOG_PATH=$HOME/.l6e/runs.jsonl" -- l6e-budget uvx l6e-mcp

# Project scope — checked into .mcp.json, shared with team
claude mcp add --scope project -e "L6E_LOG_PATH=$HOME/.l6e/runs.jsonl" -- l6e-budget uvx l6e-mcp
```

The `--` between the env var and the server name is required — `-e` accepts multiple values, so without it the CLI treats the server name as a second env var and errors.

If `uvx` is not on the PATH that Claude Code sees, use the full path:

```bash
which uvx  # then substitute the result
claude mcp add --scope user -e "L6E_LOG_PATH=$HOME/.l6e/runs.jsonl" -- l6e-budget /full/path/to/uvx l6e-mcp
```

### Manual config (`mcp.json`)

Claude Code uses `.mcp.json` in the project root (project scope, checked into git) or entries in `~/.claude.json` (user scope). The CLI above writes these files for you, but you can also write them directly.

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "uvx",
      "args": ["l6e-mcp"],
      "env": {
        "L6E_LOG_PATH": "${HOME:-~}/.l6e/runs.jsonl"
      }
    }
  }
}
```

**After adding the config**, restart Claude Code or run `/mcp` to pick up the new server.

On first use of a project-scoped `.mcp.json`, Claude Code will prompt for trust approval — this is expected, approve it.

## Verify

Run `/mcp` in the interactive REPL, or from the terminal:

```bash
claude mcp list
```

The `l6e-budget` server should appear with five tools listed:

- `l6e_run_start`
- `l6e_authorize_call`
- `l6e_record_usage`
- `l6e_run_status`
- `l6e_run_end`

If the server does not appear, check that `uvx` is on your PATH (`which uvx`) or that `l6e-mcp` is installed (`pip show l6e-mcp`).

## Rules for AI

Add the enforcement rule to a `CLAUDE.md` file so Claude Code automatically follows the l6e lifecycle.

- **User-global** (applies to all projects): `~/.claude/CLAUDE.md`
- **Project-level** (checked into git, shared with team): `CLAUDE.md` or `.claude/CLAUDE.md` in your project root

The rule content is in [`mcp/.claude/CLAUDE.md`](https://github.com/l6e-ai/l6e-mcp/blob/main/.claude/CLAUDE.md) in the repository. Copy its contents into your `CLAUDE.md`.

## Example conversation starter

Use this at the start of a new chat to test the full flow:

```
Using the l6e-budget MCP tools, call l6e_run_start with budget_usd=1.00,
model="claude-sonnet-4-6", client="claude-code". Show me the full JSON
response including session_id. Then add a one-line docstring to any function
in this project. Call l6e_authorize_call before the edit and l6e_run_end
when done.
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
Call l6e_run_start with budget_usd=0.10, model="claude-sonnet-4-5",
client="claude-code". Then immediately call l6e_run_end with the session_id.
```

Then check:

```bash
tail -1 ~/.l6e/runs.jsonl | python -m json.tool
```

If the file doesn't exist or is empty, `L6E_LOG_PATH` is not being passed to the server process. Re-check the `env` block in your config and restart Claude Code.

## Known limitations

- **Always call `l6e_run_end`.** If the Claude Code process exits before `l6e_run_end` is called, the run log for that session is not written.
- **Always call `l6e_run_start` at the start of each session.** Claude Code sessions can be resumed with `/resume`, but the MCP server process does not persist across restarts. A resumed session must call `l6e_run_start` again — the previous `session_id` is dead.
- **Never import l6e_mcp directly.** The session registry lives only in the MCP server process. Importing `l6e_mcp.server` in a subprocess will always return "Unknown session".
- **Rerouting is advisory only.** When `l6e_authorize_call` returns `"action": "reroute"`, the agent stops work and tells you to switch to a cheaper model. The MCP protocol has no mechanism for forcing a model switch — the response is a signal to you, not an automatic redirect.
- **`--dangerously-skip-permissions`.** When Claude Code is launched with this flag, MCP tool approval prompts are bypassed. This is harmless for l6e — l6e does not require user approval to function — but be aware of this if you use that flag in CI or scripted environments.

---
id: cursor
title: "Setup: Cursor"
sidebar_label: Cursor
sidebar_position: 1
---

Connect the `l6e-budget` MCP server to Cursor for session-scoped budget enforcement.

The agent gates calls using pre-call token estimates. Out of the box, budgets are directionally accurate — [calibration](../concepts/calibration) makes them billing-accurate. Call `l6e_record_usage` if you want to feed actual token counts back into the ledger for exact accounting.

## Install

No separate `pip install` is required if you use `uvx` (recommended). `uvx` runs `l6e-mcp` in an isolated environment on first use.

If you prefer a manual install:

```bash
pip install l6e-mcp
```

## Configure

Add the following to your MCP configuration file.

**Global** (applies to all projects): `~/.cursor/mcp.json`

**Project-level** (checked into git, shared with team): `.cursor/mcp.json` in your project root

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

`L6E_LOG_PATH` should always be an absolute path. Cursor spawns MCP servers as child processes, so without it `runs.jsonl` will be written relative to wherever the Cursor process started — which is not always your project directory, particularly with a global config.

`L6E_API_KEY` and `L6E_CLOUD_SYNC` are optional — omit them to run fully local. When set, session run logs sync to the l6e cloud and gate decisions use your personal calibration factor. See [l6e.ai Integration](../concepts/cloud-api) for what cloud sync enables.

If you installed `l6e-mcp` manually instead of using `uvx`:

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "l6e-mcp",
      "env": {
        "L6E_LOG_PATH": "${HOME}/.l6e/runs.jsonl",
        "L6E_API_KEY": "sk-l6e-...",
        "L6E_CLOUD_SYNC": "1"
      }
    }
  }
}
```

**Restart Cursor completely** (Cmd+Q → reopen) after editing this file. MCP server processes are spawned at startup.

## Verify

Open **Cursor Settings → Features → MCP**. The `l6e-budget` server should appear with a green dot and four tools listed:

- `l6e_run_start`
- `l6e_authorize_call`
- `l6e_record_usage`
- `l6e_run_end`

The "No MCP resources available" message in Cursor chat is expected and harmless — l6e-budget exposes tools, not resources. If the server dot is red, check that `uvx` is on your PATH (`which uvx`) or that `l6e-mcp` is installed (`pip show l6e-mcp`).

## Rules for AI

The enforcement rule is what teaches the agent the l6e lifecycle. It covers checkpoint policy (when to call `l6e_authorize_call`), estimation defaults, model identification, sub-agent budget gates, budget sizing guidance, and session safety. Without it, the MCP tools are available but the agent won't know how to use them correctly.

Set up a Cursor rule — either globally or per-project — with `alwaysApply: true` so every conversation gets enforcement automatically.

- **Project-level** (checked into git): `.cursor/rules/l6e-budget-enforcement.mdc`
- **Global** (all projects): `~/.cursor/rules/l6e-budget-enforcement.mdc`

The fastest way to install the rule is from the bundled copy in your `l6e-mcp` package:

```bash
l6e-mcp install-rules --client cursor
```

This writes the rule to `.cursor/rules/l6e-budget-enforcement.mdc` in the current directory. For manual installation, the rule content is also available at [`.cursor/rules/l6e-budget-enforcement.mdc`](https://github.com/l6e-ai/l6e-mcp/blob/main/.cursor/rules/l6e-budget-enforcement.mdc) in the repository.

See the [Prompt Guide](../prompt-guide) for always-apply vs on-demand patterns and how to override enforcement per-message.

## Example conversation starter

Use this at the start of a new chat to test the full flow:

```
Using the l6e-budget MCP tools, call l6e_run_start with budget_usd=1.00,
model="gpt-4o", client="cursor". Show me the full JSON response including
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
client="cursor". Then immediately call l6e_run_end with the session_id.
```

Then check:

```bash
tail -1 ~/.l6e/runs.jsonl | python -m json.tool
```

If the file doesn't exist or is empty, `L6E_LOG_PATH` is not being passed to the server process. Re-check the `env` block in your config and restart Cursor.

## Known limitations

- **Always call `l6e_run_end`.** If the Cursor window closes before `l6e_run_end` is called, the run log for that session is not written.
- **Never import l6e_mcp directly.** The session registry lives only in the MCP server process. Importing `l6e_mcp.server` in a subprocess will always return "Unknown session".
- **Rerouting is advisory only.** When `l6e_authorize_call` returns `"action": "reroute"`, the agent stops work and tells you to switch to a cheaper model. The MCP protocol has no mechanism for forcing a model switch — the response is a signal to you, not an automatic redirect.

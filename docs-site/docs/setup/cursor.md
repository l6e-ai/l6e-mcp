---
id: cursor
title: "Setup: Cursor"
sidebar_label: Cursor
sidebar_position: 1
---

Connect the `l6e-budget` MCP server to Cursor for session-scoped budget enforcement.

This setup is the OSS estimate-first path. For premium exact accounting, prefer
the hosted public edge design. The LiteLLM self-hosted route remains an
advanced fallback for operators and enterprise users.

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
      "args": ["l6e-mcp"]
    }
  }
}
```

If you installed `l6e-mcp` manually instead of using `uvx`:

```json
{
  "mcpServers": {
    "l6e-budget": {
      "command": "l6e-mcp"
    }
  }
}
```

**Restart Cursor completely** (Cmd+Q → reopen) after editing this file. MCP server processes are spawned at startup.

## Verify

Open **Cursor Settings → Features → MCP**. The `l6e-budget` server should appear with a green dot and five tools listed:

- `l6e_run_start`
- `l6e_authorize_call`
- `l6e_record_usage`
- `l6e_run_status`
- `l6e_run_end`

The "No MCP resources available" message in Cursor chat is expected and harmless — l6e-budget exposes tools, not resources. If the server dot is red, check that `uvx` is on your PATH (`which uvx`) or that `l6e-mcp` is installed (`pip show l6e-mcp`).

## Rules for AI

Set up a Cursor rule — either globally or per-project.
The rule from [`.cursor/rules/l6e-budget-enforcement.mdc`](https://github.com/l6e-ai/l6e-mcp/blob/main/.cursor/rules/l6e-budget-enforcement.mdc) on the latest release tag is always the best source.

## Exact accounting options

The default OSS experience is estimate-first:
- `l6e_authorize_call` gates expensive work using proportional estimates
- it also returns pricing confidence metadata (`model_pricing_known`, `pricing_confidence`, `pricing_warning`) for unknown model IDs
- `.l6e/runs.jsonl` gives local run summaries

### Advanced: self-hosted LiteLLM proxy add-on

If you want to run the self-hosted LiteLLM proxy + callback path, there are two pieces to configure:

1. Point Cursor itself at LiteLLM (see the [LiteLLM proxy setup guide](https://github.com/l6e-ai/l6e-mcp/blob/main/docs/mcp-setup-litellm-proxy.md))
2. Tell the agent to opt into proxy-mode by adding `proxy_mode: true` to your rule

:::caution
LiteLLM's current Cursor integration only honors custom API keys in **Ask** and **Plan** modes. In **Agent** mode, Cursor may bypass the proxy even when the MCP side is configured correctly. Do **not** add `proxy_mode: true` unless you are actually running the full stack: MCP HTTP server + `l6e-callback-server` + LiteLLM proxy.
:::

## Example conversation starter

Use this at the start of a new chat to test the full flow:

```
Using the l6e-budget MCP tools, call l6e_run_start with budget_usd=1.00,
model="gpt-4o", client="cursor". Show me the full JSON response including
session_id and local_model. Then add a one-line docstring to any function
in this project. Call l6e_authorize_call before the edit and l6e_run_end
when done.
```

## Reading your run log

After a session ends:

```bash
# Most recent session
tail -1 .l6e/runs.jsonl | python -m json.tool

# All sessions — cost summary
cat .l6e/runs.jsonl | python -c "
import sys, json
for line in sys.stdin:
    r = json.loads(line)
    print(f\"{r['run_id']}  \${r['total_cost']:.4f}  {r['calls_made']} calls  {r['reroutes']} reroutes  source={r['source']}\")
"
```

## Known limitations

- **Always call `l6e_run_end`.** If the Cursor window closes before `l6e_run_end` is called, the run log for that session is not written.
- **Never import l6e_mcp directly.** The session registry lives only in the MCP server process. Importing `l6e_mcp.server` in a subprocess will always return "Unknown session".
- **Reroute requires Ollama.** Rerouting on budget pressure requires a local Ollama model to be available on your machine. If no Ollama model is detected, `l6e_authorize_call` returns `halt` instead of `reroute`.

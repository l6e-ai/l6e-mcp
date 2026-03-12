# l6e-mcp setup: Cursor

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
Setup a cursor rule - either globally or per-project.
The rule from [`.cursor/rules/l6e-budget-enforcement.mdc`](https://github.com/l6e-ai/l6e-mcp/.cursor/rules/l6e-budget-enforcement.mdc?branch=main) on the latest release tag is always the best source.

## Exact accounting options

The default OSS experience is still estimate-first:
- `l6e_authorize_call` gates expensive work using proportional estimates
- it also returns pricing confidence metadata (`model_pricing_known`,
  `pricing_confidence`, `pricing_warning`) for unknown model IDs
- `.l6e/runs.jsonl` gives local run summaries
- `docs/mcp-reconcile-cursor-usage.md` remains the no-infra fallback for
  comparing estimates to actual billing later

If you want exact accounting from the request path itself, the recommended
product direction is a hosted public edge rather than a localhost Cursor proxy.
Cursor's BYOK routing can reject private-network endpoints and may still bypass
custom routing in some modes.

Design references:
- `docs/mcp-hosted-edge-contract.md`
- `docs/mcp-hosted-edge-relay.md`

## Advanced: self-hosted LiteLLM proxy add-on

If you still want to run the self-hosted Tier 2 LiteLLM proxy + callback path,
there are two separate pieces to configure:

1. Point Cursor itself at LiteLLM using
   `docs/mcp-setup-litellm-proxy.md`
   (`Override OpenAI Base URL = http://127.0.0.1:4000/cursor`, set the OpenAI
   API key, and add a custom model alias)
2. Tell the agent to opt into proxy-mode session tracking by adding the line
   below to step 1 of the rule above:

```text
- proxy_mode: true
- advanced_fallback: false
  (Recommended default. Metadata/request-tag `call_id` propagation is the
  canonical path. Set `advanced_fallback: true` only if you explicitly need
  legacy active-file fallback.)
```

LiteLLM's current Cursor integration only honors custom API keys in **Ask** and
**Plan** modes. If you test in **Agent** mode, Cursor may still bypass the
proxy even when the MCP side is configured correctly. In addition, Cursor can
reject private-network custom endpoints entirely, so this self-hosted path is
best treated as an advanced or experimental setup rather than the default exact
accounting story for all users.

If you do expose a self-hosted proxy publicly for Cursor compatibility, expose
only the LiteLLM front door. Keep the callback server and MCP HTTP server on
loopback unless you have a strong reason to widen the trust boundary.

Do **not** add `proxy_mode: true` unless you are actually running:

1. The MCP HTTP server
2. `l6e-callback-server`
3. The LiteLLM proxy

For Tier 1 proportional estimates or Tier 3 manual reconciliation, leave
`proxy_mode` out of the default rule. When `proxy_mode: true` is used, keep
`advanced_fallback: false` unless metadata propagation is unavailable.

## Example conversation starter

Use this at the start of a new chat to test the full flow before relying on the global rule:

```
Using the l6e-budget MCP tools, call l6e_run_start with budget_usd=1.00,
model="gpt-4o", client="cursor". Show me the full JSON response including
session_id and local_model. Then add a one-line docstring to the `remaining()`
method in l6e/src/l6e/store.py. Call l6e_authorize_call before the edit and
l6e_run_end when done.
```

This is a minimal task with one checkpoint and one edit — good for confirming the full loop works before using l6e on larger tasks.

## Reading your run log

After a session ends, check the log:

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

Each line is a `RunSummary` — the same format the cloud profiler will read.

## Where run logs are written

By default, logs are written to `.l6e/runs.jsonl` relative to Cursor's working directory (your project root). Each line is a JSON object representing one session's `RunSummary`.

## Known limitations

- **Always call `l6e_run_end`.** If the Cursor window closes or the conversation ends before `l6e_run_end` is called, the run log for that session is not written.
- **Never import l6e_mcp directly.** The session registry lives only in the MCP server process. Importing `l6e_mcp.server` in a subprocess will always return "Unknown session".
- **Reroute requires Ollama.** If `l6e_run_start` returns `"local_model": null`, no local Ollama model is available. Budget pressure will trigger `halt` instead of `reroute`.

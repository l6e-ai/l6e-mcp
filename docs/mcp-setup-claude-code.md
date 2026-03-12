# l6e-mcp setup: Claude Code

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

The `l6e-budget` server should appear in the `/mcp` output with its five tools listed:

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

Stage transitions (blocking): call `l6e_authorize_call` at every stage boundary
before beginning new work — after `l6e_run_start` (tool_name="planning"),
search → implement, implement → test, test → debug. Do not begin the next stage
until you have a `call_id`.

Todo list execution: before marking each todo item `in_progress`, run
`l6e_run_status`. If `budget_pressure` is "high" or "critical", escalate to
`l6e_authorize_call` before proceeding with that item.

Within a stage: you may skip checks for up to 3 lightweight tool calls. After
that, run `l6e_run_status`. If `budget_pressure` is "high" or "critical", run
`l6e_authorize_call` before further work.

After a progress update or revised plan: run a full check before the next batch.

Estimation: prefer dual-token inputs — `estimated_prompt_tokens` +
`estimated_completion_tokens`. Default: 2000 + 400. For large operations
(multi-file reads, long builds), double the default. Do NOT use line-based
formulas like `total_lines * 20`; they over-inflate estimates.
When in doubt, overestimate.

Respect `action`:
- `allow`: proceed
- `reroute`: stop and tell the user to switch to a cheaper model
- `halt`: stop and report budget exhaustion

Sub-agents reuse the parent `session_id` and authorize with
`actor_type="subagent"` plus a stable `actor_id`. Pass `parent_call_id` when
work is delegated. Sub-agents never call `l6e_run_start` or `l6e_run_end`.

At the END of every task, call `l6e_run_end` with the `session_id` even on
failure/cancel. Never recover or infer a `session_id` from transcripts or history.
```

## Where run logs are written

By default, logs are written to `.l6e/runs.jsonl` relative to the current working directory when Claude Code invokes the server. This is typically your project root.

## Scope options

| Scope | Command flag | Stored in |
|---|---|---|
| Local (default) | *(omit flag)* | `~/.claude.json` under project path |
| User (all projects) | `--scope user` | `~/.claude.json` globally |
| Project (team-shared) | `--scope project` | `.mcp.json` in project root (check into git) |

To share `l6e-budget` with your whole team, use project scope:

```bash
claude mcp add --transport stdio --scope project l6e-budget -- uvx l6e-mcp
```

## Known limitations

- **Always call `l6e_run_end`.** If Claude Code exits before `l6e_run_end` is called, the run log for that session is not written.
- **Reroute requires Ollama.** If `l6e_run_start` returns `"local_model": null`, no local model is available. Budget pressure will trigger `halt` instead of `reroute`.

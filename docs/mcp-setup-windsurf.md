# l6e-mcp setup: Windsurf

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

**Why this is required:** Without `L6E_LOG_PATH`, the server attempts to write to `/.l6e/runs.jsonl` (root-relative) which fails silently with a permission error, and your session run data is lost.

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

To confirm the log path is working, start a session and end it, then check that the file at your `L6E_LOG_PATH` has a new line:

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

Logs are written to the path you set in `L6E_LOG_PATH`. The default `~/.l6e/runs.jsonl` is a good choice — it persists across projects and accumulates a history of all sessions for the profiler to read when Phase 1 cloud sync ships.

## Known limitations

- **Always call `l6e_run_end`.** If Cascade ends before `l6e_run_end` is called, the run log for that session is not written.
- **`L6E_LOG_PATH` is required.** Due to the `cwd=/` issue, this env var is not optional for Windsurf. Sessions will run correctly but logs will fail to write if it is not set.
- **Reroute requires Ollama.** If `l6e_run_start` returns `"local_model": null`, no local Ollama model is available. Budget pressure will trigger `halt` instead of `reroute`.

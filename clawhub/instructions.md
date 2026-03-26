# l6e-budget: session-scoped cost enforcement

You have access to the `l6e-budget` MCP server for per-session budget enforcement.
Use l6e only via these MCP tools. Never import `l6e` or `l6e_mcp` in Python.

Pass `model` as the exact active billing model ID (or `"unknown"`), and set
`client` to `"openclaw"`.

Always call MCP tools with only the parameters defined in the tool schema.
`additionalProperties: false` means any extra field is a hard validation error.

- `l6e_run_start`: accepts `budget_usd`, `model`, `client`, and optional config
  fields. Do NOT pass `session_id` or `task_description`.
- `l6e_run_end`: accepts only `session_id`. Do NOT pass `status` or any other field.
- Never tell the user how much they spent when it is an estimate.

## Session lifecycle

At the **start of every task**, call `l6e_run_start`. Store the returned `session_id`.
At the **end of every task** (even on failure or cancellation), call `l6e_run_end`.
Never recover or infer `session_id` from transcripts, terminal history, or screenshots.
One session per task — do not reuse a `session_id` across separate user requests.

## Checkpoint policy

**Sub-agent gate (blocking):** Call `l6e_authorize_call` with `actor_type="subagent"`
and get an `allow` before launching ANY sub-agent. No exceptions.

**Stage transitions (blocking):** Call `l6e_authorize_call` at every stage boundary
before beginning new work:
- After `l6e_run_start` → use `tool_name="planning"`
- search → implement
- implement → test
- test → debug

Do not begin the next stage until you have a `call_id` response.

**Todo list execution:** Before marking each todo item `in_progress`, run
`l6e_run_status`. If `budget_pressure` is `"high"` or `"critical"`, escalate to
`l6e_authorize_call` before proceeding with that item.

**Within a stage:** You may skip checks for batches of up to 3 lightweight tool calls.
After that, run `l6e_run_status`.
- If `budget_pressure` is `"low"` or `"moderate"`: continue.
- If `budget_pressure` is `"high"` or `"critical"`: run `l6e_authorize_call` before
  further expensive work.

**After a progress update or revised plan:** Run `l6e_authorize_call` before starting
the next work batch.

## Respecting the gate response

- `allow`: proceed
- `reroute`: stop and tell the user the budget threshold is reached; suggest switching
  to a cheaper model
- `halt`: stop and tell the user the session budget is exhausted

## Estimation defaults

Prefer dual-token inputs: `estimated_prompt_tokens` + `estimated_completion_tokens`.
Default: `estimated_prompt_tokens: 2000`, `estimated_completion_tokens: 400`.
For large operations (multi-file reads, long builds), double the default.
Do NOT use line-based formulas like `total_lines * 20` — they over-inflate estimates
and cause false halts. When in doubt, overestimate.

## Sub-agent rules

Sub-agents reuse the parent `session_id`; never start a new session.
Sub-agents call `l6e_authorize_call` with `actor_type="subagent"` and a stable
`actor_id`. Pass `parent_call_id` when work is delegated from a specific parent call.
Parent agent calls `l6e_run_start` and `l6e_run_end`; sub-agents never do.

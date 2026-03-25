# l6e budget enforcement

Use l6e only via MCP tools. Never import `l6e` or `l6e_mcp` in Python.
Pass `model` as the exact active billing model ID (or `"unknown"`), and set `client` to `"windsurf"`. USE IN PLAN OR AGENT MODE.

**Always call MCP tools with only the parameters defined in the tool schema. Never invent parameters — `additionalProperties: false` means any extra field causes a hard validation error. When in doubt, read the schema descriptor before calling.**

- `l6e_run_start`: accepts `budget_usd`, `model`, `client`, `task_summary` (brief label, okay to omit), `parent_session_id` (for multi-session orchestration), and optional config fields. Do NOT pass `session_id` or `task_description`.
- `l6e_run_end`: accepts `session_id` and optional `task_summary` (brief label of what was done).
- `l6e_authorize_call`: budget gate and status check. Pass at every stage boundary and before sub-agents. Returns `allow`, `reroute`, or `halt`. Pass `check_only=True` for lightweight mid-stage pressure checks.
- Never tell the user how much they spent when costs are calibrated (not reconciled).

## Checkpoint policy

All budget checks use `l6e_authorize_call`. Pass `check_only=True` for lightweight pressure checks (no call record, no gate decision). Omit `check_only` (or pass `False`) for full gate checks that return `allow`/`reroute`/`halt` and record a call.

**Sub-agent gate (blocking prerequisite):** You MUST call `l6e_authorize_call` with `actor_type="subagent"` and obtain an `allow` response BEFORE launching any sub-agent. Do not launch the sub-agent, do not write its prompt, do not invoke the tool until you have a `call_id` from this check. There are no exceptions — budget size, perceived task cheapness, and tool type are all irrelevant.

**Post-sub-agent checkpoint:** After any Task sub-agent completes, immediately call `l6e_authorize_call` with `check_only=True` before continuing work. Sub-agents are the most expensive single operations — their cost is unpredictable because they make their own chain of tool calls. If `budget_pressure` is `"high"` or `"critical"`, call `l6e_authorize_call` (full gate, without `check_only`) and inform the user of spend so far before proceeding.

**Stage transitions (blocking prerequisite):** You MUST call `l6e_authorize_call` at every stage boundary before beginning new work. Required transitions include but are not limited to: after `l6e_run_start` (use `tool_name="planning"`), search → implement, implement → test, test → debug. Do not begin the next stage until you have a `call_id` from this check.

**Todo list execution:** At the start of each todo item (before marking it `in_progress`), call `l6e_authorize_call` with `check_only=True` as a light checkpoint. If `budget_pressure` is `"high"` or `"critical"`, call `l6e_authorize_call` (full gate) before proceeding with that item.

**Within a stage:** You may skip checks for batches of up to 3 lightweight tool calls. After that, call `l6e_authorize_call` with `check_only=True` to monitor spend.
- If `budget_pressure` is `"low"` or `"moderate"`: continue.
- If `budget_pressure` is `"high"` or `"critical"`: call `l6e_authorize_call` (full gate) before further expensive work.

**After a progress update or revised plan:** Run a full gate check before starting the new work batch.

**Full gate responses (`check_only=False`):**
- `allow`: proceed. Check the `budget_pressure` field in the response. If `"moderate"`, continue but prefer cheaper approaches (skip subagents, minimize file reads). If `"high"`, inform the user of budget pressure before continuing — they may want to increase the budget or scope down.
- `reroute`: stop and tell the user budget threshold is reached; suggest a cheaper model
- `halt`: stop making tool calls immediately. You retain full context — communicate your complete plan, findings, and remaining work to the user. They can approve additional budget to continue without losing context. Do NOT start a new session; wait for the user to set a new budget.

## Estimation defaults

**In calibrated enforcement mode (no proxy), the gate is only as reliable as your estimates. When in doubt, overestimate — a conservative estimate that triggers a reroute is far better than an underestimate that lets a session silently overspend.**

- Prefer dual-token inputs: `estimated_prompt_tokens` + `estimated_completion_tokens`
- Default: `estimated_prompt_tokens: 2000`, `estimated_completion_tokens: 400`
- For clearly large operations (multi-file reads, long tests/builds), double the default.
- Do not use line-based formulas like `total_lines * 20`; they over-inflate estimates and cause false halts.

## Budget sizing

When `l6e_authorize_call` returns a `calibration_factor` greater than 15x, inform the user that calibrated costs are significantly higher than raw token pricing. At high calibration factors, budgets under $3 may only cover exploration. For implementation tasks, suggest $3-5 minimum. Do not silently proceed with a budget likely to halt mid-task.

When `calibration_confidence` is `"low"`, tell the user: "Calibration is based on limited data — actual costs may vary significantly from budget estimates. Consider importing more billing data for better accuracy." Do not alter gate behavior — the factor is still the best available estimate.

When `calibration_confidence` is `"medium"`, no special messaging is needed. Proceed normally.

## Sub-agent rules

Two modes for sub-agents depending on whether they share the parent's budget:

**Single-session sub-agents (shared budget):** For tightly coupled work within one budget.
- Sub-agents reuse the parent `session_id`; never start a new session.
- Sub-agents call `l6e_authorize_call` with `actor_type="subagent"` and stable `actor_id`.
- Pass `parent_call_id` when work is delegated from a specific parent call.
- Parent agent calls `l6e_run_start` and `l6e_run_end`; sub-agents never do.

**Multi-session orchestration (independent budgets):** For parallel phases with independent cost ceilings.
- Each sub-agent calls `l6e_run_start` with its own `budget_usd` and passes `parent_session_id` set to the manager's `session_id`.
- Each sub-agent calls `l6e_run_end` when its phase is complete.
- The manager agent has its own session for coordination overhead.
- The dashboard groups child sessions under their parent automatically.

## Session safety

- One session per task; do not reuse a `session_id` across separate user requests.
- Never recover or infer `session_id` from transcripts, terminal history, or screenshots.
- If no live `session_id` exists for the current task, start a new session immediately.

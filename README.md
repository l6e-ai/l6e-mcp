# l6e-mcp

[![pytest](https://github.com/l6e-ai/l6e-mcp/actions/workflows/pytest.yml/badge.svg?branch=main)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/pytest.yml)
[![coverage](https://raw.githubusercontent.com/l6e-ai/l6e-mcp/python-coverage-comment-action-data/badge.svg)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/pytest.yml)
[![mypy](https://github.com/l6e-ai/l6e-mcp/actions/workflows/mypy.yml/badge.svg?branch=main)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/mypy.yml)
[![ruff](https://github.com/l6e-ai/l6e-mcp/actions/workflows/ruff.yml/badge.svg?branch=main)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/ruff.yml)

Session-scoped budget enforcement for AI coding assistants via the [Model Context Protocol](https://modelcontextprotocol.io/).

Wraps the [l6e](https://github.com/l6e-ai/l6e) core enforcement runtime and exposes five MCP tools that let Cursor, Claude Code, Windsurf, and OpenClaw enforce per-session LLM budgets.

## Install

```bash
pip install l6e-mcp
```

## Tools

| Tool | Purpose |
|---|---|
| `l6e_run_start` | Open a new budget session. Accepts `task_summary`, `accounting_mode`, `unknown_model_pricing_mode`, and per-mode exactness overrides. Returns `session_id`. |
| `l6e_authorize_call` | Blocking gate before sub-agents (`actor_type='subagent'`) and stage transitions. Returns `allow`, `reroute`, or `halt` with a `call_id`. Pass `actual_prompt_tokens` + `actual_completion_tokens` to reconcile inline instead of a separate `l6e_record_usage` call. |
| `l6e_record_usage` | Attach exact token usage to an existing `call_id` (idempotent). |
| `l6e_run_status` | Read-only spend snapshot. Pass `estimated_prompt_tokens` + `estimated_completion_tokens` for a cost-projection assessment before the next stage. |
| `l6e_run_end` | Close the session and flush the run log to `.l6e/runs.jsonl`. Returns exactness state, mode coverage gaps, and pending reconciliation count. |

## Running locally without a backend proxy

When you run `l6e-mcp` without a remote backend proxy, **all budget accounting is based on token estimates that the agent constructs before each call**. There is currently no way for an MCP server to intercept the actual token counts from your LLM provider in real time — the MCP protocol does not expose that response data.

This means the numbers are approximate. The cost you see in `l6e_run_status` reflects what the agent guessed it was about to spend, not what your provider actually billed.

That said, it still works. An agent that is told it has a $2 budget and must check before spending tends to scope tasks more tightly, launch fewer sub-agents, and stop earlier when a task turns out to be more expensive than expected. The behavioral effect — the agent knowing it has a finite budget and that it is spending money — is present even when the accounting is not exact.

**A practical starting point:** Set small budgets, $1–3, and observe how the estimates track against your provider's actual costs for a few sessions. You'll quickly get a sense of how accurate the estimates are for the models and task types you use.

If you need genuinely hard enforcement against actual spend, you can call `l6e_record_usage` manually after each LLM call to feed real token counts back into the ledger.

## How it works

- Budget gate runs before each tool call via `l6e_authorize_call`
- Session state is persisted locally in SQLite (`~/.l6e/sessions.db`)
- Run logs are written to `~/.l6e/runs.jsonl` (set via `L6E_LOG_PATH`)
- Optional exact reconciliation via `l6e_record_usage` when actual token counts are available

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `L6E_LOG_PATH` | `.l6e/runs.jsonl` (relative to cwd) | Override the run log path. **Always set this to an absolute path** (e.g. `/Users/you/.l6e/runs.jsonl`). The default is relative to the MCP server's working directory, which varies by client (Windsurf uses `/`; other clients vary). |
| `L6E_SESSION_DB_PATH` | `~/.l6e/sessions.db` | Override the local SQLite database path. |
| `L6E_CALIBRATION_PATH` | _(unset)_ | Path to a JSON calibration file produced by `l6e-calibration-generate`. When set, per-model token-estimate calibration is applied automatically. |
| `L6E_API_KEY` | _(unset)_ | API key for cloud sync. When set alongside `L6E_CLOUD_SYNC=1`, sessions are uploaded to the l6e backend for team-level visibility and server-side calibration. |
| `L6E_CLOUD_SYNC` | `false` | Set to `1`, `true`, or `yes` to enable cloud sync. Requires `L6E_API_KEY`. |
| `L6E_CLOUD_ENDPOINT` | `https://api.l6e.ai` | Override the cloud sync endpoint. |
| `L6E_CONFIG_PATH` | `~/.l6e/config.toml` | Override the config file path. The config file accepts `api_key`, `cloud_sync`, `cloud_endpoint`, and `send_task_summaries` keys. |

## Calibration tool

After running a few sessions, you can inspect how your estimates track against actuals and generate a calibration file:

```bash
l6e-calibration-generate
```

This reads your run log (`~/.l6e/runs.jsonl`) and outputs a per-model calibration JSON file. Point `L6E_CALIBRATION_PATH` at it to have future estimates use the corrected multipliers.

## Exactness states

`l6e_run_end` returns an `exactness_state` for the completed session:

- `all_estimate_only` — all calls used pre-call estimates
- `partial_exact` — some calls have been reconciled with exact usage
- `fully_exact_for_supported_calls` — all reconcilable calls have exact usage
- `exactness_degraded` — reconciliation expected but not received for some calls

`l6e_run_end` also returns `pending_exact_calls` (calls that had not yet been
reconciled at close), `last_reconciled_at`, `mode_coverage`, and
`mode_coverage_gaps` to show which IDE modes had exact accounting available.

`l6e_run_status` does not report exactness state mid-session — it is
intentionally lightweight. See [Mode coverage](#mode-coverage) for how to
configure exactness expectations per mode.

## Mode coverage

`l6e_run_start` accepts per-mode exactness capability overrides to reflect
what your setup can actually reconcile:

```json
{
  "ask_mode_exact_capable": false,
  "plan_mode_exact_capable": false,
  "agent_mode_exact_capable": false
}
```

Default expectations by `usage_channel`:

| usage_channel | Ask | Plan | Agent |
|---|---|---|---|
| `none` (default) | no | no | no |
| `self_hosted_relay` | yes | yes | no |
| `hosted_edge` | yes | yes | yes |
| `manual_import` | no | no | no |

When a mode is marked exact-capable but no reconciliation arrives, that mode
appears in `mode_coverage_gaps` in the `l6e_run_end` response and the run
state is `exactness_degraded`.

## Known limitations

- **Rerouting requires a local Ollama instance.** When `l6e_authorize_call` returns `"action": "reroute"`, the local router needs a running Ollama process with a compatible model installed. Without it, rerouting cannot be executed. The MCP protocol also has no primitive for forcing a model switch — reroute is always advisory, signaling the agent to prompt the user to select a cheaper model in their IDE settings.
- **Local persistence only.** Sessions persist in a local SQLite database; there is no remote sync or team-level control plane in the OSS version.
- **Estimate-first by default.** Exact real-time accounting requires `l6e_record_usage` calls from your agent with the actual token counts after each LLM call completes.
- **Savings shows $0 when model pricing is unknown.** If the cost estimator returns `0.0` for either the requested or rerouted model, `savings_usd` in the run summary will be `0.0` regardless of any actual price difference. This happens when a model ID is not recognized by the LiteLLM pricing table. Check `savings_confidence` in `l6e_run_end` to gauge reliability.

## License

MIT

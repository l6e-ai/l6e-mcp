# l6e-mcp

[![pytest](https://github.com/l6e-ai/l6e-mcp/actions/workflows/pytest.yml/badge.svg?branch=main)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/pytest.yml)
[![mypy](https://github.com/l6e-ai/l6e-mcp/actions/workflows/mypy.yml/badge.svg?branch=main)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/mypy.yml)
[![ruff](https://github.com/l6e-ai/l6e-mcp/actions/workflows/ruff.yml/badge.svg?branch=main)](https://github.com/l6e-ai/l6e-mcp/actions/workflows/ruff.yml)

Session-scoped budget enforcement for AI coding assistants via the [Model Context Protocol](https://modelcontextprotocol.io/).

Wraps the [l6e](https://github.com/your-org/l6e) OSS enforcement runtime and exposes five MCP tools that let Cursor, Claude Code, and Windsurf enforce per-session LLM budgets before any cloud infrastructure exists.

## Tools

| Tool | Purpose |
|---|---|
| `l6e_run_start` | Open a new budget session and declare accounting mode/channel. Returns `session_id`. |
| `l6e_authorize_call` | Gate-check a pending tool call, create a durable `call_id`, and return correlation hints. |
| `l6e_record_usage` | Attach exact usage to an existing pending `call_id` (idempotent for same values). |
| `l6e_run_status` | Read-only spend snapshot for the current session. |
| `l6e_run_end` | Close the session and flush the run log to `.l6e/runs.jsonl`. |

## Pipeline overview

The MCP pipeline has two layers:

1. Session/checkpoint/reconciliation tools in `src/l6e_mcp/server.py`
2. Shared core contracts/services in `src/l6e_mcp/core/`, `src/l6e_mcp/contracts/`, and `src/l6e_mcp/store/`
3. Advanced self-hosted LiteLLM adapters in `src/l6e_mcp/litellm_proxy/` and `src/l6e_mcp/integrations/litellm/`

Core MCP-session functions:

| Function | Role |
|---|---|
| `_make_session_id` | Builds the per-task session ID. |
| `_get_log_path` | Reads `L6E_LOG_PATH` when an explicit run-log path is configured. |
| `_get_session_store` | Resolves the local SQLite-backed session/call store. |
| `l6e_run_start` | Creates a persisted local session record with `accounting_mode` / `usage_channel` metadata. |
| `l6e_authorize_call` | Produces a budget decision, creates a persisted pending or reconciled call record, and returns a stable `call_id`. |
| `l6e_record_usage` | Updates an existing pending call with exact token usage and optional hosted-ledger metadata. |
| `l6e_run_status` | Returns the current spend snapshot derived from persisted call rows. |
| `l6e_run_end` | Finalizes the session, appends the run log, and clears active proxy files when applicable. |

Advanced self-hosted callback functions for actual token reconciliation:

| Function | Role |
|---|---|
| `_read_active_session` | Reads the session handshake file used only in advanced fallback mode. |
| `_read_active_call` | Reads the local fallback `call_id` pointer used only when advanced fallback is enabled. |
| `_normalize_payload` | Normalizes LiteLLM callback payload shapes. |
| `_extract_usage` | Pulls `prompt_tokens` and `completion_tokens` from the callback payload. |
| `_extract_model` | Extracts the reported model name for stage labeling. |
| `_extract_call_correlation` | Pulls `l6e_call_id` from LiteLLM metadata or request tags when available. |
| `_call_l6e_record_usage` | Sends actual token counts back to the MCP HTTP transport for reconciliation. |
| `litellm_success_callback` | Main webhook endpoint for successful LiteLLM responses. |
| `health` | Health endpoint for the callback server. |

For a deeper write-up of constraints and trade-offs, see
`../docs/mcp-budget-enforcement-constraints.md`. For the advanced self-hosted
proxy setup, see `../docs/mcp-setup-litellm-proxy.md`. For the hosted-edge
design seam, see `../docs/mcp-hosted-edge-contract.md` and
`../docs/mcp-hosted-edge-relay.md`.

## Quick start

```bash
pip install l6e-mcp
```

By default, `l6e-mcp` is an estimate-first OSS workflow:
- pre-call budget enforcement via `l6e_authorize_call`
- local SQLite-backed session state
- local `.l6e/runs.jsonl` run logs
- optional manual reconciliation against provider or IDE billing views

Exact real-time accounting is a separate concern from local budget gating. The
self-hosted LiteLLM path remains available as an advanced fallback, but the
long-term product direction is:
- OSS: high-quality estimates plus local enforcement
- hosted product: hosted-ledger-first exact accounting and synchronized spend telemetry
- enterprise: self-hosted relay or connector

See the setup guides in `docs/` for client-specific configuration:

- [Claude Code](../docs/mcp-setup-claude-code.md)
- [Cursor](../docs/mcp-setup-cursor.md)
- [Windsurf](../docs/mcp-setup-windsurf.md)

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `L6E_LOG_PATH` | `.l6e/runs.jsonl` (relative to cwd) | Override the run log path. **Required for Windsurf** — see setup guide. |
| `L6E_SESSION_DB_PATH` | `~/.l6e/sessions.db` | Override the local SQLite database used for session and call persistence. |

## Known limitations

- **Local persistence is authoritative, not cloud-backed.** Sessions and calls now persist in a local SQLite database so stdio and HTTP MCP processes can share state, but there is still no remote sync or team-level control plane in OSS.
- **Metadata correlation is required by default.** The callback server prefers `metadata.spend_logs_metadata.l6e_call_id` and compatible request tags. Local `active_call` fallback is disabled unless `advanced_fallback=true` is explicitly enabled per session. Unknown callbacks are quarantined as orphan diagnostics instead of being silently misapplied.
- **OSS exact accounting is not the primary Cursor path.** The optional LiteLLM proxy path is still useful for advanced self-hosted workflows, but Cursor's private-network restrictions and mode-specific routing behavior make it unsuitable as the main exact-accounting story for every user.
- **Rerouting is advisory only.** When `l6e_authorize_call` returns `"action": "reroute"`, it is a signal to the agent to prompt the user to select a cheaper model in their IDE settings. The MCP protocol has no primitive for forcing a model switch — no MCP server can instruct a host client (Cursor, Windsurf, Claude Code) to change its active model programmatically. Automatic model rerouting is a planned future feature pending MCP spec support.

## Exactness semantics

`l6e_run_status` reports exactness with a run-level state plus lag metadata:

- `exactness_state`: `all_estimate_only`, `partial_exact`,
  `fully_exact_for_supported_calls`, `exactness_degraded`
- `pending_exact_calls`: calls expected to reconcile but still pending
- `unavailable_exact_calls`: calls on routes not exact-capable for that run
- `last_reconciled_at`: timestamp of latest reconciled call, or `null`
- `mode_coverage`: per-mode exactness capability flags
- `mode_coverage_gaps`: list of modes not exact-capable

Call-level states persisted per call:

- `estimate_only`
- `exact_pending`
- `exact_recorded`
- `exact_unavailable`

Default mode coverage (when host does not override flags):

| usage_channel | Ask | Plan | Agent |
|---|---|---|---|
| `none` | estimate-only | estimate-only | estimate-only |
| `self_hosted_relay` | exact-capable | exact-capable | usually not exact-capable |
| `hosted_edge` | exact-capable | exact-capable | exact-capable |
| `manual_import` | estimate-only | estimate-only | estimate-only |

## OSS vs Paid

OSS owns the correctness-critical local workflow:
- session-scoped enforcement
- proportional estimates before expensive work
- local run logs and debuggable session state
- manual or post-hoc reconciliation workflows

Paid tiers should add exact accounting and the connected control plane:
- hosted public edge for authoritative token accounting
- synced session history across machines
- team governance and shared budgets
- dashboards, orphan-callback views, and anomaly detection
- privacy controls and retention guarantees for hosted relay traffic
- cross-customer policy recommendations and profiler intelligence

Advanced self-hosted exact accounting should remain available, but as a power
user or enterprise path rather than the default value proposition:
- self-operated public relay or tunnel
- private-network connector deployment

---
id: intro
title: Introduction
sidebar_label: Introduction
sidebar_position: 1
slug: /
---

# l6e-mcp

**Session-scoped budget enforcement for AI coding assistants via the [Model Context Protocol](https://modelcontextprotocol.io/).**

Wraps the [l6e](https://github.com/l6e-ai/l6e) core enforcement runtime and exposes five MCP tools that let Cursor, Claude Code, Windsurf, and OpenClaw enforce per-session LLM budgets.

## Quickstart

```bash
pip install l6e-mcp
```

Then follow the setup guide for your editor:

- **[Cursor →](setup/cursor)**
- **[Claude Code →](setup/claude-code)**
- **[Windsurf →](setup/windsurf)**
- **[OpenClaw →](setup/openclaw)**

## Tools

| Tool | Purpose |
|---|---|
| `l6e_run_start` | Open a new budget session. Returns `session_id`. |
| `l6e_authorize_call` | Gate-check a pending tool call and return a `call_id` with correlation hints. |
| `l6e_record_usage` | Attach exact token usage to an existing `call_id` (idempotent). |
| `l6e_run_status` | Read-only spend snapshot for the current session. |
| `l6e_run_end` | Close the session and flush the run log to `.l6e/runs.jsonl`. |

## How it works

- Budget gate runs before each tool call via `l6e_authorize_call`
- Session state is persisted locally in SQLite (`~/.l6e/sessions.db`)
- Run logs are written to `.l6e/runs.jsonl`
- Optional exact reconciliation via `l6e_record_usage` when actual token counts are available

## Running locally without a backend proxy

When you run `l6e-mcp` without a remote backend proxy, **all budget accounting is based on token estimates that the agent constructs before each call**. The MCP protocol does not expose provider response data in real time, so actual token counts are never visible to the server.

This means numbers are approximate. The cost shown in `l6e_run_status` reflects what the agent estimated it was about to spend, not what your provider billed.

That said, it still works. An agent told it has a $2 budget and that it must check before spending tends to scope tasks more tightly, launch fewer sub-agents, and stop earlier when a task runs more expensive than anticipated.

**A practical starting point:** Set small budgets ($1–3) and observe how estimates track against your provider's actual costs. See [Local Enforcement](concepts/local-estimate-only) for a full explanation.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `L6E_LOG_PATH` | `.l6e/runs.jsonl` (relative to cwd) | Override the run log path. **Required for Windsurf; strongly recommended for OpenClaw.** |
| `L6E_SESSION_DB_PATH` | `~/.l6e/sessions.db` | Override the local SQLite database path. |

## Exactness states

`l6e_run_status` reports an `exactness_state` for the current session:

| State | Meaning |
|---|---|
| `all_estimate_only` | All calls used pre-call estimates |
| `partial_exact` | Some calls reconciled with exact usage |
| `fully_exact_for_supported_calls` | All reconcilable calls have exact usage |
| `exactness_degraded` | Reconciliation expected but not received for some calls |

## Known limitations

- **Rerouting is advisory only.** When `l6e_authorize_call` returns `"action": "reroute"`, it signals the agent to prompt the user to select a cheaper model. The MCP protocol has no primitive for forcing a model switch.
- **Local persistence only.** Sessions persist in a local SQLite database; there is no remote sync or team-level control plane in the OSS version.
- **Estimate-first by default.** Exact real-time accounting requires either `l6e_record_usage` calls from your agent or the optional self-hosted LiteLLM proxy path.

## License

Apache 2.0

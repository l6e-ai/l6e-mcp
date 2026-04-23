# Changelog

All notable changes to l6e-mcp are documented here.

## 0.8.0 — 2026-04-23

- **Iron-rule fail-open hardening** (L6E-41). `l6e_authorize_call` now wraps its entire gate path in a fail-open guard — any internal exception (server branch, local gate, calibration cache) degrades to `{"action":"allow","reason":"fail_open:gate_exception"}` rather than surfacing a `ToolError` to the agent. Input-validation errors (unknown session, bad `actor_type`) still raise as before.
- **Server authorize response sanity check.** `/v1/authorize` responses are now validated for NaN / inf / negative `calibrated_cost_usd` / `remaining_usd`, missing `action`, and invalid `budget_pressure` labels before they drive local spend accounting. Garbage → fall back to local auth.
- **`latency_deadline_ms` honored on the wire.** `try_remote_authorize` now tightens the per-call HTTP timeout to `min(default, latency_deadline_ms/1000)` so Margin callers get cloud-slow → treat-as-down semantics locally too.
- **Defensive JSON parse.** Malformed JSON from the gateway now returns `None` (fall-back) instead of crashing the tool.

## 0.7.0 — 2026-04-02

- **Claude Code analytics in `l6e_sync_anthropic_usage`.** New `include_claude_code` flag (default `True`) pulls per-user Claude Code productivity and cost metrics alongside standard Anthropic usage. Response now surfaces `claude_code_records_fetched` / `claude_code_rows_sent` when present. Sync tool timeout raised from 60s to 120s to accommodate the extra pull.
- **Billing batch management tools.** `l6e_list_billing_batches` returns active import batches (ID, source, row count, cost, import date) for auditing. `l6e_delete_billing_batch` soft-deletes a batch and its truth rows so stale or test imports can be cleaned up and re-imported.

## 0.6.2 — 2026-03-27

- **Rewritten README for launch.** New lede, dogfooding callout, tighter quick start, free vs pro comparison table, and calibration config docs. Technical depth moved to [docs.l6e.ai](https://docs.l6e.ai).
- **Removed reroute references from README.** The MCP protocol has no model-switch primitive — documentation now presents `allow` / `halt` as the two gate outcomes.
- **PyPI metadata updated.** Description and documentation URL now point to [docs.l6e.ai](https://docs.l6e.ai).
- **Fixed schema migration for upgrades from ≤0.5.2.** Columns added after the v1 migration shipped (`client`, `start_summary`, `end_summary`, `parent_session_id`, `raw_estimated_cost_usd`) were never applied to existing databases because v1 was skipped on re-run. Added a v2 migration to catch up.

## 0.6.1 — 2026-03-26

- **Client attribution.** Sessions now record which IDE client started them (`cursor`, `claude-code`, `windsurf`), improving dashboard filtering and analytics.

## 0.6.0 — 2026-03-26

- **Multi-model sessions.** `l6e_authorize_call` accepts an optional `model` parameter to override the session model for a specific call. Use this when the primary model (e.g. Opus) delegates work to a cheaper model (e.g. Haiku) for sub-agents — cost estimation and gate decisions use the correct per-call pricing.

## 0.5.2 — 2026-03-26

Initial public release.

- **Budget enforcement via MCP.** Four tools — `l6e_run_start`, `l6e_authorize_call`, `l6e_record_usage`, `l6e_run_end` — give any MCP-compatible IDE per-session budget gates with `allow` / `halt` decisions.
- **Local-first storage.** Sessions and call history persist in SQLite (`~/.l6e/sessions.db`). Run logs append to `~/.l6e/runs.jsonl`.
- **Cloud sync.** Optional sync to [app.l6e.ai](https://app.l6e.ai) for dashboard, run history, and team visibility. Set `L6E_API_KEY` and `L6E_CLOUD_SYNC=1`.
- **Calibration.** Import billing CSVs at [app.l6e.ai](https://app.l6e.ai) for per-model calibration factors. Manual overrides via `~/.l6e/config.toml` `[calibration]` section.
- **Sub-agent orchestration.** `parent_session_id` on `l6e_run_start` groups child sessions under a manager. `actor_type` / `actor_id` on `l6e_authorize_call` attributes calls to specific sub-agents within a shared session.
- **Exactness tracking.** `l6e_run_end` reports whether calls were estimate-only, partially reconciled, or fully exact — and flags modes with coverage gaps.
- **Cursor, Claude Code, Windsurf, and OpenClaw** setup documented at [docs.l6e.ai](https://docs.l6e.ai).

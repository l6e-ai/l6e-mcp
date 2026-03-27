# Changelog

All notable changes to l6e-mcp are documented here.

## 0.6.2 — 2026-03-27

- **Rewritten README for launch.** New lede, dogfooding callout, tighter quick start, free vs pro comparison table, and calibration config docs. Technical depth moved to [docs.l6e.ai](https://docs.l6e.ai).
- **Removed reroute references from README.** The MCP protocol has no model-switch primitive — documentation now presents `allow` / `halt` as the two gate outcomes.
- **PyPI metadata updated.** Description and documentation URL now point to [docs.l6e.ai](https://docs.l6e.ai).

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

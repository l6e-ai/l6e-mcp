# l6e-mcp

Session-scoped budget enforcement for AI coding assistants via the [Model Context Protocol](https://modelcontextprotocol.io/).

Wraps the [l6e](https://github.com/your-org/l6e) OSS enforcement runtime and exposes four MCP tools that let Cursor, Claude Code, and Windsurf enforce per-session LLM budgets before any cloud infrastructure exists.

## Tools

| Tool | Purpose |
|---|---|
| `l6e_session_start` | Open a new budget session. Returns `session_id`. |
| `l6e_checkpoint` | Gate-check a pending tool call. Returns `allow`, `reroute`, or `halt`. |
| `l6e_spend` | Read-only spend snapshot for the current session. |
| `l6e_session_end` | Close the session and flush the run log to `.l6e/runs.jsonl`. |

## Quick start

```bash
pip install l6e-mcp
```

See the setup guides in `docs/` for client-specific configuration:

- [Claude Code](../docs/mcp-setup-claude-code.md)
- [Cursor](../docs/mcp-setup-cursor.md)
- [Windsurf](../docs/mcp-setup-windsurf.md)

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `L6E_LOG_PATH` | `.l6e/runs.jsonl` (relative to cwd) | Override the run log path. **Required for Windsurf** — see setup guide. |

## Known limitations

- **No session GC.** Sessions persist in memory until `l6e_session_end` is called or the MCP server process exits. If the client crashes before calling `l6e_session_end`, the run log for that session is never written. Always include `l6e_session_end` in your system prompt.
- **No concurrent session isolation.** The session registry is a plain in-process dict. Concurrent sessions are supported, but the server is single-process — this is appropriate for local stdio transport.
- **Rerouting is advisory only.** When `l6e_checkpoint` returns `"action": "reroute"`, it is a signal to the agent to prompt the user to select a cheaper model in their IDE settings. The MCP protocol has no primitive for forcing a model switch — no MCP server can instruct a host client (Cursor, Windsurf, Claude Code) to change its active model programmatically. Automatic model rerouting is a planned future feature pending MCP spec support.

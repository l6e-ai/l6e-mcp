"""CLI subcommands for l6e-mcp.

Dispatched from server.py:main() when the first positional arg is a known
subcommand (e.g. ``l6e-mcp install-rules --client cursor``).  With no args
the entry point falls through to the MCP server as usual.
"""
from __future__ import annotations

import argparse
import importlib.resources
import sys
from pathlib import Path

_START_MARKER = "<!-- l6e:start -->"
_END_MARKER = "<!-- l6e:end -->"

_CLIENTS: dict[str, dict] = {
    "cursor": {
        "file": "cursor.mdc",
        "dest": ".cursor/rules/l6e-budget-enforcement.mdc",
    },
    "claude-code": {
        "file": "claude-code.md",
        "dest": ".claude/CLAUDE.md",
        "merge": True,
    },
    "windsurf": {
        "file": "windsurf.md",
        "dest": ".windsurf/rules/l6e-budget-enforcement.md",
    },
    "openclaw": {
        "file": "openclaw.md",
        "dest": ".openclaw/AGENTS.md",
    },
}


def _load_bundled_rule(filename: str) -> str:
    """Read a bundled rule file from the package data."""
    return (
        importlib.resources.files("l6e_mcp.rules")
        .joinpath(filename)
        .read_text(encoding="utf-8")
    )


def _merge_with_markers(existing: str, content: str) -> str:
    """Replace or append an l6e section delimited by markers.

    Raises ValueError if markers are malformed (start without end or vice-versa).
    """
    has_start = _START_MARKER in existing
    has_end = _END_MARKER in existing

    if has_start != has_end:
        present = _START_MARKER if has_start else _END_MARKER
        missing = _END_MARKER if has_start else _START_MARKER
        raise ValueError(
            f"Found {present} but not {missing} — "
            "fix the markers manually before re-running."
        )

    block = f"{_START_MARKER}\n{content}\n{_END_MARKER}"

    if has_start and has_end:
        before = existing[: existing.index(_START_MARKER)]
        after = existing[existing.index(_END_MARKER) + len(_END_MARKER) :]
        return before + block + after

    # No markers yet — append with a blank-line separator.
    separator = "\n\n" if existing and not existing.endswith("\n\n") else ""
    if existing and not existing.endswith("\n"):
        separator = "\n\n"
    return existing + separator + block + "\n"


def _install_rules(client: str, dry_run: bool = False) -> None:
    spec = _CLIENTS[client]
    content = _load_bundled_rule(spec["file"])
    dest = Path.cwd() / spec["dest"]

    if spec.get("merge"):
        if dest.exists():
            existing = dest.read_text(encoding="utf-8")
            try:
                final = _merge_with_markers(existing, content)
            except ValueError as exc:
                print(f"error: {dest}: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            final = f"{_START_MARKER}\n{content}\n{_END_MARKER}\n"
    else:
        final = content

    if dry_run:
        print(f"[dry-run] Would write {len(final)} bytes to {dest}")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(final, encoding="utf-8")
    print(f"Rule installed to {spec['dest']} — restart your editor to pick it up.")


def install_rules_cli(argv: list[str] | None = None) -> None:
    """Entry point for ``l6e-mcp install-rules``."""
    parser = argparse.ArgumentParser(
        prog="l6e-mcp install-rules",
        description="Install the l6e enforcement rule for your AI coding client.",
    )
    parser.add_argument(
        "--client",
        required=True,
        choices=sorted(_CLIENTS),
        help="Which client to install the rule for.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching disk.",
    )
    args = parser.parse_args(argv)
    _install_rules(args.client, dry_run=args.dry_run)

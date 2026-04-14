"""Unit tests for the install-rules CLI."""
from __future__ import annotations

import pytest

from l6e_mcp.cli import _END_MARKER, _START_MARKER, _install_rules, _merge_with_markers

# ---------------------------------------------------------------------------
# _merge_with_markers
# ---------------------------------------------------------------------------


class TestMergeWithMarkers:
    def test_append_to_empty_string(self):
        result = _merge_with_markers("", "rule content")
        assert _START_MARKER in result
        assert _END_MARKER in result
        assert "rule content" in result

    def test_append_to_existing_content(self):
        existing = "# My project rules\n\nSome custom instructions.\n"
        result = _merge_with_markers(existing, "rule content")
        assert result.startswith("# My project rules")
        assert "Some custom instructions." in result
        assert result.index("Some custom instructions.") < result.index(_START_MARKER)
        assert "rule content" in result

    def test_replace_between_existing_markers(self):
        existing = (
            "before stuff\n"
            f"{_START_MARKER}\nold rule\n{_END_MARKER}\n"
            "after stuff\n"
        )
        result = _merge_with_markers(existing, "new rule")
        assert "old rule" not in result
        assert "new rule" in result
        assert result.startswith("before stuff\n")
        assert result.rstrip().endswith("after stuff")

    def test_preserves_content_outside_markers(self):
        before = "# Header\n\nImportant context.\n\n"
        after = "\n\n# Footer\n\nMore content.\n"
        existing = f"{before}{_START_MARKER}\nold\n{_END_MARKER}{after}"
        result = _merge_with_markers(existing, "new")
        assert result.startswith(before)
        assert result.endswith(after)

    def test_malformed_start_without_end(self):
        existing = f"some text\n{_START_MARKER}\nbroken"
        with pytest.raises(ValueError, match="but not"):
            _merge_with_markers(existing, "rule")

    def test_malformed_end_without_start(self):
        existing = f"some text\n{_END_MARKER}\nbroken"
        with pytest.raises(ValueError, match="but not"):
            _merge_with_markers(existing, "rule")


# ---------------------------------------------------------------------------
# _install_rules (integration-style, using tmp_path)
# ---------------------------------------------------------------------------


class TestInstallRules:
    def test_cursor_creates_file_and_parent_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _install_rules("cursor")
        dest = tmp_path / ".cursor" / "rules" / "l6e-budget-enforcement.mdc"
        assert dest.exists()
        content = dest.read_text()
        assert "l6e budget enforcement" in content

    def test_cursor_overwrites_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dest = tmp_path / ".cursor" / "rules" / "l6e-budget-enforcement.mdc"
        dest.parent.mkdir(parents=True)
        dest.write_text("old content")
        _install_rules("cursor")
        assert "old content" not in dest.read_text()
        assert "l6e budget enforcement" in dest.read_text()

    def test_claude_code_creates_with_markers(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _install_rules("claude-code")
        dest = tmp_path / ".claude" / "CLAUDE.md"
        assert dest.exists()
        content = dest.read_text()
        assert _START_MARKER in content
        assert _END_MARKER in content
        assert "l6e budget enforcement" in content

    def test_claude_code_preserves_existing_content(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dest = tmp_path / ".claude" / "CLAUDE.md"
        dest.parent.mkdir(parents=True)
        dest.write_text("# My custom rules\n\nDo not delete this.\n")
        _install_rules("claude-code")
        content = dest.read_text()
        assert "My custom rules" in content
        assert "Do not delete this." in content
        assert "l6e budget enforcement" in content

    def test_claude_code_replaces_existing_markers(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dest = tmp_path / ".claude" / "CLAUDE.md"
        dest.parent.mkdir(parents=True)
        dest.write_text(
            f"keep this\n\n{_START_MARKER}\nold l6e rule\n{_END_MARKER}\n\nkeep this too\n"
        )
        _install_rules("claude-code")
        content = dest.read_text()
        assert "old l6e rule" not in content
        assert "keep this" in content
        assert "keep this too" in content
        assert "l6e budget enforcement" in content

    def test_dry_run_does_not_write(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _install_rules("cursor", dry_run=True)
        dest = tmp_path / ".cursor" / "rules" / "l6e-budget-enforcement.mdc"
        assert not dest.exists()
        captured = capsys.readouterr()
        assert "[dry-run]" in captured.out

    def test_windsurf_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _install_rules("windsurf")
        dest = tmp_path / ".windsurf" / "rules" / "l6e-budget-enforcement.md"
        assert dest.exists()
        assert "l6e budget enforcement" in dest.read_text()

    def test_openclaw_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _install_rules("openclaw")
        dest = tmp_path / ".openclaw" / "AGENTS.md"
        assert dest.exists()
        assert "l6e budget enforcement" in dest.read_text()

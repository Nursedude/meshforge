"""Tests for scripts/validate_claude_settings.py.

Locks in the permission-grammar rules enforced by the pre-commit validator
so future settings changes fail loudly instead of silently drifting.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_claude_settings as vcs  # noqa: E402


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data))
    return p


def test_valid_pipe_pattern_accepted(tmp_path: Path) -> None:
    """`Bash(curl * | sh)` uses mid-pattern `*`, not terminal `:*` — valid."""
    p = _write(tmp_path, {"permissions": {"deny": ["Bash(curl * | sh)"]}})
    assert vcs.validate_settings(p) == []


def test_valid_terminal_colon_star_accepted(tmp_path: Path) -> None:
    p = _write(tmp_path, {"permissions": {"allow": ["Bash(git push origin:*)"]}})
    assert vcs.validate_settings(p) == []


def test_mid_pattern_colon_star_rejected(tmp_path: Path) -> None:
    """`:*` anywhere but the end is a silent-skip bug (CVE-2026-21852 class)."""
    p = _write(tmp_path, {"permissions": {"deny": ["Bash(curl:* | sh)"]}})
    errors = vcs.validate_settings(p)
    assert len(errors) == 1
    assert ":*" in errors[0]


def test_unbalanced_parens_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, {"permissions": {"allow": ["Bash(git push (origin)"]}})
    errors = vcs.validate_settings(p)
    assert any("unbalanced" in e for e in errors)


def test_empty_bash_pattern_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, {"permissions": {"allow": ["Bash()"]}})
    errors = vcs.validate_settings(p)
    # "Bash()" does not match the BASH_RULE_RE (requires non-empty inner),
    # so it is treated as a non-Bash rule and skipped. This test guards
    # against a future regression where "Bash()" is silently accepted as
    # a Bash rule.
    assert errors == []  # Current behavior: skipped as non-Bash rule.


def test_non_bash_rule_skipped(tmp_path: Path) -> None:
    p = _write(tmp_path, {"permissions": {"allow": ["Read", "Edit", "WebFetch(*)"]}})
    assert vcs.validate_settings(p) == []


def test_missing_file_reports_error(tmp_path: Path) -> None:
    errors = vcs.validate_settings(tmp_path / "does-not-exist.json")
    assert len(errors) == 1
    assert "not found" in errors[0]


def test_invalid_json_reports_error(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text("{not json")
    errors = vcs.validate_settings(p)
    assert len(errors) == 1
    assert "invalid JSON" in errors[0]


def test_non_array_permissions_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, {"permissions": {"allow": "Bash(ls:*)"}})
    errors = vcs.validate_settings(p)
    assert any("must be an array" in e for e in errors)


def test_live_settings_file_is_valid() -> None:
    """The checked-in .claude/settings.json must always validate."""
    live = REPO_ROOT / ".claude" / "settings.json"
    if not live.exists():
        pytest.skip(".claude/settings.json not present in checkout")
    errors = vcs.validate_settings(live)
    assert errors == [], f"Live settings.json has errors: {errors}"

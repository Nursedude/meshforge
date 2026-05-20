"""Tests for the MF017 systemd-sandbox-path audit in scripts/lint.py.

Pins the audit behavior so the Issue #58 class of drift can't recur
silently. The rule: a hardened systemd unit (ProtectHome=read-only)
must whitelist all three canonical MeshForge data buckets in
ReadWritePaths=, OR mark the omission with an inline
'# audit-skip: <reason>' comment.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

# scripts/lint.py is not packaged as a module; import by file path.
import importlib.util
_lint_path = Path(__file__).parent.parent / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


HARDENED_UNIT_OK = """\
[Unit]
Description=Test

[Service]
User=alice
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=@HOME@/.config/meshforge @HOME@/.cache/meshforge @HOME@/.local/share/meshforge

[Install]
WantedBy=multi-user.target
"""

HARDENED_UNIT_MISSING_DATA_DIR = """\
[Unit]
Description=Test

[Service]
User=alice
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=@HOME@/.config/meshforge @HOME@/.cache/meshforge

[Install]
WantedBy=multi-user.target
"""

HARDENED_UNIT_MISSING_CACHE_DIR = """\
[Unit]
Description=Test

[Service]
User=alice
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=@HOME@/.config/meshforge @HOME@/.local/share/meshforge

[Install]
WantedBy=multi-user.target
"""

UNHARDENED_UNIT = """\
[Unit]
Description=Test

[Service]
User=alice
ProtectSystem=false
ProtectHome=false

[Install]
WantedBy=multi-user.target
"""

HARDENED_UNIT_AUDIT_SKIP = """\
[Unit]
Description=Test

[Service]
User=alice
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=@HOME@/.config/meshforge  # audit-skip: only needs config for now

[Install]
WantedBy=multi-user.target
"""


def _write_unit(repo_root: Path, fname: str, content: str) -> Path:
    contrib = repo_root / "contrib" / "systemd"
    contrib.mkdir(parents=True, exist_ok=True)
    (contrib / fname).write_text(content)
    return contrib / fname


class TestMF017HappyPath:
    """A unit with all three buckets produces no issues."""

    def test_complete_whitelist_no_issues(self, tmp_path):
        _write_unit(tmp_path, "ok.service.in", HARDENED_UNIT_OK)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert issues == []


class TestMF017CatchesIssue58Shape:
    """The actual moc3 shape: missing the data dir."""

    def test_missing_data_dir_is_flagged(self, tmp_path):
        _write_unit(tmp_path, "gateway.service.in", HARDENED_UNIT_MISSING_DATA_DIR)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert len(issues) == 1
        assert issues[0].code == "MF017"
        assert ".local/share/meshforge" in issues[0].message
        # The file path in the issue uses the repo-rel form so a CI
        # log shows operators where to look
        assert "gateway.service.in" in issues[0].file
        # The line number points at the offending ReadWritePaths line
        # (line 8 in the fixture)
        assert issues[0].line == 8

    def test_missing_data_dir_remediation_hint_is_actionable(self, tmp_path):
        _write_unit(tmp_path, "gateway.service.in", HARDENED_UNIT_MISSING_DATA_DIR)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        msg = issues[0].message
        # Operator should be told what to add AND that audit-skip is the
        # escape hatch — no guesswork.
        assert "@HOME@" in msg or "ReadWritePaths" in msg
        assert "audit-skip" in msg


class TestMF017DifferentBuckets:
    """Each bucket is checked independently — not just data dir."""

    def test_missing_cache_dir_is_flagged(self, tmp_path):
        _write_unit(tmp_path, "x.service.in", HARDENED_UNIT_MISSING_CACHE_DIR)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert len(issues) == 1
        assert ".cache/meshforge" in issues[0].message


class TestMF017IgnoresUnhardenedUnits:
    """Units without ProtectHome=read-only aren't subject to the trap."""

    def test_unhardened_unit_produces_no_issues(self, tmp_path):
        _write_unit(tmp_path, "loose.service.in", UNHARDENED_UNIT)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert issues == []


class TestMF017AuditSkipMarker:
    """An inline '# audit-skip: <reason>' opts out of the check."""

    def test_audit_skip_comment_suppresses_issue(self, tmp_path):
        _write_unit(tmp_path, "skip.service.in", HARDENED_UNIT_AUDIT_SKIP)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert issues == []

    def test_audit_skip_on_one_unit_does_not_suppress_others(self, tmp_path):
        """Pattern-audit Finding #4 (2026-05-19): the pre-fix
        implementation used `return issues` from inside the per-unit
        iteration when it hit `# audit-skip:`, which exited the entire
        MF017 audit function. That meant an audit-skip on the
        alphabetically-first unit would silently suppress drift detection
        on every later unit. The fix factors per-unit body into
        `_audit_one_systemd_unit` so audit-skip only affects ITS unit.

        Regression: two units in the same directory, one with
        audit-skip and one with real drift — the drifting unit must
        still be flagged.
        """
        # Alphabetical order: "aa-skip.service.in" comes before
        # "bb-drift.service.in". Pre-fix the `aa-skip` audit-skip would
        # exit the function before "bb-drift" got audited.
        _write_unit(tmp_path, "aa-skip.service.in", HARDENED_UNIT_AUDIT_SKIP)
        _write_unit(tmp_path, "bb-drift.service.in", HARDENED_UNIT_MISSING_DATA_DIR)
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        # bb-drift's missing data-dir MUST surface as one issue.
        assert len(issues) == 1, (
            f"expected 1 issue from bb-drift.service.in, got {len(issues)}: "
            f"{[(i.file, i.code, i.message) for i in issues]}"
        )
        assert "bb-drift.service.in" in issues[0].file
        assert ".local/share/meshforge" in issues[0].message


class TestMF017HandlesMissingContribDir:
    """A repo without contrib/systemd/ shouldn't crash the audit."""

    def test_missing_contrib_dir_returns_no_issues(self, tmp_path):
        # tmp_path has no contrib/systemd/ subdir
        issues = lint.check_systemd_sandbox_paths(repo_root=str(tmp_path))
        assert issues == []


class TestMF017RealRepoUnits:
    """The real `contrib/systemd/` content must pass MF017 — locks the
    invariant that ships in the repo right now."""

    def test_real_contrib_systemd_passes(self):
        # Repo root is parent of tests/
        repo_root = str(Path(__file__).parent.parent)
        issues = lint.check_systemd_sandbox_paths(repo_root=repo_root)
        assert issues == [], (
            "MF017 audit found drift in real contrib/systemd/ units:\n  "
            + "\n  ".join(f"{i.file}:{i.line} {i.message}" for i in issues)
        )

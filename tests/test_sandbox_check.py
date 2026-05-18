"""Tests for utils.sandbox_check — runtime writability probe.

Pins the operator-actionable behavior: a hardened service hitting a
ReadWritePaths gap should fail at startup with a specific error, not
silently lose data in an exception handler hours later (Issue #58 class).
"""
import os
import stat
import sys
import logging
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.sandbox_check import (
    meshforge_writable_paths,
    verify_writable_paths,
    assert_writable_or_exit,
)


class TestMeshforgeWritablePaths:
    """The canonical writable-bucket list — keep it in sync with the
    contract documented in `contrib/systemd/meshforge-gateway.service.in`."""

    def test_returns_three_buckets(self):
        paths = meshforge_writable_paths()
        assert len(paths) == 3

    def test_all_three_buckets_are_meshforge_subdirs(self):
        paths = meshforge_writable_paths()
        names = [p.name for p in paths]
        assert names == ["meshforge", "meshforge", "meshforge"]

    def test_buckets_cover_config_data_cache(self):
        """The three canonical XDG-style buckets the codebase uses."""
        paths = meshforge_writable_paths()
        parents = {p.parent.name for p in paths}
        # `.config`, `share` (.local/share), and `.cache`
        assert "meshforge" not in parents  # double-check we're at parent level
        assert {".config", "share", ".cache"} == parents


class TestVerifyWritablePaths:
    """Per-path probe behavior."""

    def test_writable_dir_returns_no_failures(self, tmp_path):
        result = verify_writable_paths([tmp_path])
        assert result == []

    def test_multiple_writable_dirs_all_pass(self, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        result = verify_writable_paths([d1, d2])
        assert result == []

    def test_nonexistent_dir_reported_as_failure(self, tmp_path):
        ghost = tmp_path / "does_not_exist"
        result = verify_writable_paths([ghost])
        assert len(result) == 1
        assert result[0][0] == ghost
        assert "does not exist" in result[0][1]

    def test_file_instead_of_dir_reported_as_failure(self, tmp_path):
        not_a_dir = tmp_path / "i_am_a_file"
        not_a_dir.write_text("hi")
        result = verify_writable_paths([not_a_dir])
        assert len(result) == 1
        assert result[0][0] == not_a_dir
        assert "not a directory" in result[0][1]

    def test_readonly_dir_reported_as_failure(self, tmp_path):
        """Mimics the moc3 ProtectHome=read-only sandbox: dir exists, dir
        is a directory, but tempfile create fails with EACCES/EROFS."""
        if os.geteuid() == 0:
            pytest.skip("running as root bypasses mode bits")
        ro = tmp_path / "ro"
        ro.mkdir()
        try:
            os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)  # r-x, no write
            result = verify_writable_paths([ro])
            assert len(result) == 1
            assert result[0][0] == ro
            assert "OSError" in result[0][1] or "PermissionError" in result[0][1]
        finally:
            os.chmod(ro, stat.S_IRWXU)  # restore so cleanup works

    def test_mixed_writable_and_failing(self, tmp_path):
        good = tmp_path / "good"
        good.mkdir()
        bad = tmp_path / "missing"
        result = verify_writable_paths([good, bad])
        assert len(result) == 1
        assert result[0][0] == bad


class TestAssertWritableOrExit:
    """Service-startup integration: pass silently or exit loudly with a
    message the operator can act on."""

    def test_all_writable_returns_normally(self, tmp_path):
        # No exception, no exit
        assert_writable_or_exit("test-service", paths=[tmp_path])

    def test_failure_calls_sys_exit_with_code_2(self, tmp_path):
        ghost = tmp_path / "missing"
        with pytest.raises(SystemExit) as exc:
            assert_writable_or_exit("test-service", paths=[ghost])
        assert exc.value.code == 2

    def test_failure_logs_path_and_remediation_hint(self, tmp_path, caplog):
        ghost = tmp_path / "missing"
        with caplog.at_level(logging.ERROR, logger="utils.sandbox_check"):
            with pytest.raises(SystemExit):
                assert_writable_or_exit("test-service", paths=[ghost])
        log_text = "\n".join(r.message for r in caplog.records)
        assert "test-service" in log_text
        assert str(ghost) in log_text
        # Operator-actionable remediation hint
        assert "ReadWritePaths" in log_text
        assert "persistent_issues.md" in log_text or "Issue #58" in log_text

    def test_partial_failure_still_exits(self, tmp_path):
        """One bad path among many → exit. We don't want a 'mostly works'
        startup that loses one feature silently."""
        good = tmp_path / "good"
        good.mkdir()
        bad = tmp_path / "bad"  # nonexistent
        with pytest.raises(SystemExit):
            assert_writable_or_exit("test-service", paths=[good, bad])

    def test_default_paths_param_uses_meshforge_buckets(self, monkeypatch, tmp_path):
        """When called with no paths arg, uses meshforge_writable_paths()."""
        # Monkey-patch to a directory we control + know is writable.
        monkeypatch.setattr(
            "utils.sandbox_check.meshforge_writable_paths",
            lambda: [tmp_path],
        )
        assert_writable_or_exit("test-service")  # no paths arg


class TestIssue58ClassRegression:
    """End-to-end: simulate the moc3 incident shape exactly.

    moc3 had `~/.local/share/meshforge/` not in ReadWritePaths. The
    gateway started, ran for 18h, every LXMF callback hit
    sqlite3.OperationalError. With this helper wired into gateway
    startup, the same broken sandbox should refuse to even reach the
    first callback — it should exit at startup with the data dir named.
    """

    def test_data_dir_only_failure_names_the_data_dir(self, tmp_path, caplog):
        config_dir = tmp_path / ".config" / "meshforge"
        cache_dir = tmp_path / ".cache" / "meshforge"
        data_dir = tmp_path / ".local" / "share" / "meshforge"
        config_dir.mkdir(parents=True)
        cache_dir.mkdir(parents=True)
        # data_dir intentionally NOT created — Issue #58 shape

        with caplog.at_level(logging.ERROR, logger="utils.sandbox_check"):
            with pytest.raises(SystemExit) as exc:
                assert_writable_or_exit(
                    "meshforge-gateway",
                    paths=[config_dir, data_dir, cache_dir],
                )
        assert exc.value.code == 2
        msg = "\n".join(r.message for r in caplog.records)
        # The error names the data dir specifically — the operator
        # shouldn't have to guess which of the three buckets is missing
        assert ".local/share/meshforge" in msg
        # And NOT the buckets that DO work (no false alarm)
        # (config_dir + cache_dir lines exist as success-state in the
        # hint footer, but the per-path ✗ lines only carry failures)
        failure_lines = [r.message for r in caplog.records if "✗" in r.message]
        assert len(failure_lines) == 1
        assert str(data_dir) in failure_lines[0]

"""Launcher first-run wizard regression tests.

Pins the 2026-06-03 moc crash-loop diagnosis (Issue #60 sibling):
meshforge.service ran launcher.py as root under ProtectHome=read-only,
check_first_run() saw no /root marker, the wizard's skip branch tried to
mkdir /root/.meshforge -> OSError(EROFS) -> exit 1 -> Restart=on-failure
looped 26k times. Two invariants:

1. Non-interactive contexts (stdin not a TTY) NEVER trigger the wizard.
2. The setup-marker write is NEVER fatal — unwritable home degrades to
   a warning, the NOC keeps starting.
"""

import errno
from unittest.mock import MagicMock, patch

import launcher


class TestCheckFirstRunNonInteractive:
    def test_non_tty_stdin_is_never_first_run(self, tmp_path):
        """systemd/cron/piped stdin must not enter the wizard path."""
        with patch.object(launcher.sys.stdin, "isatty", return_value=False), \
             patch.object(launcher, "get_real_user_home",
                          return_value=tmp_path):
            # No marker exists, but non-interactive -> not first run.
            assert launcher.check_first_run() is False

    def test_tty_without_marker_is_first_run(self, tmp_path):
        with patch.object(launcher.sys.stdin, "isatty", return_value=True), \
             patch.object(launcher, "get_real_user_home",
                          return_value=tmp_path):
            assert launcher.check_first_run() is True

    def test_tty_with_marker_is_not_first_run(self, tmp_path):
        marker = tmp_path / ".meshforge" / ".setup_complete"
        marker.parent.mkdir(parents=True)
        marker.write_text("skipped")
        with patch.object(launcher.sys.stdin, "isatty", return_value=True), \
             patch.object(launcher, "get_real_user_home",
                          return_value=tmp_path):
            assert launcher.check_first_run() is False


class TestWriteSetupMarkerNeverFatal:
    def test_marker_written_when_home_writable(self, tmp_path):
        with patch.object(launcher, "get_real_user_home",
                          return_value=tmp_path):
            launcher._write_setup_marker("skipped")
        assert (tmp_path / ".meshforge" / ".setup_complete").read_text() \
            == "skipped"

    def test_read_only_home_does_not_raise(self, tmp_path, capsys):
        """The moc shape: mkdir raises EROFS under ProtectHome=read-only."""
        home = MagicMock()
        marker_parent = MagicMock()
        marker = MagicMock()
        home.__truediv__ = MagicMock(return_value=marker_parent)
        marker_parent.__truediv__ = MagicMock(return_value=marker)
        marker.parent.mkdir.side_effect = OSError(
            errno.EROFS, "Read-only file system", "/root/.meshforge")
        with patch.object(launcher, "get_real_user_home", return_value=home):
            launcher._write_setup_marker("skipped")  # must not raise
        out = capsys.readouterr().out
        assert "Could not persist setup marker" in out

    def test_permission_denied_does_not_raise(self, tmp_path):
        """EACCES variant (unwritable home, non-sandbox)."""
        target = tmp_path / "denied"
        target.mkdir(mode=0o555)
        with patch.object(launcher, "get_real_user_home",
                          return_value=target):
            launcher._write_setup_marker("skipped")  # must not raise


class TestWizardSkipBranchUsesSafeWriter:
    def test_skip_branch_survives_unwritable_home(self, capsys):
        """End-to-end: declining the wizard on a read-only home must not
        raise (the exact crash-loop entry point)."""
        home = MagicMock()
        marker_parent = MagicMock()
        marker = MagicMock()
        home.__truediv__ = MagicMock(return_value=marker_parent)
        marker_parent.__truediv__ = MagicMock(return_value=marker)
        marker.parent.mkdir.side_effect = OSError(
            errno.EROFS, "Read-only file system", "/root/.meshforge")
        with patch.object(launcher, "get_real_user_home",
                          return_value=home), \
             patch("builtins.input", side_effect=EOFError):
            launcher.run_setup_wizard()  # EOF -> skip branch -> marker write
        out = capsys.readouterr().out
        assert "Could not persist setup marker" in out

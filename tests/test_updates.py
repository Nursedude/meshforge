"""Regression guard for the rnsd dual-install silent-failure (Issue #24 /
TUI audit #15, 2026-05-29).

`_pip_install_meshtastic` runs a second, system-wide `sudo pip3 install` so
rnsd's Python can find `meshtastic`. `subprocess.run(capture_output=True)`
does NOT raise on a nonzero exit, so a failed rnsd install used to pass
silently and the caller reported full success — defeating Issue #24. The fix
keeps the user-level success (returns True) but surfaces the rnsd-copy failure
via an in-app msgbox.
"""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from handlers.updates import UpdatesHandler

RNSD_IFACE = "/etc/reticulum/interfaces/Meshtastic_Interface.py"


def _path_exists(rnsd_present):
    """Path-aware fake: rnsd interface presence is the only thing that varies;
    force the non-venv pip branch so subprocess args are deterministic."""
    def fake(self):
        s = str(self)
        if s == RNSD_IFACE:
            return rnsd_present
        return False  # venv_pip / .no-venv absent -> pip3 branch
    return fake


def _result(returncode, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _handler():
    h = UpdatesHandler()
    h.ctx = MagicMock()
    return h


def _msgbox_titles(dialog):
    return [c.args[0] for c in dialog.msgbox.call_args_list if c.args]


class TestRnsdDualInstallSurfacing:
    def test_rnsd_install_failure_is_surfaced_but_user_install_succeeds(self):
        h = _handler()
        runs = [_result(0, stdout="user ok"), _result(1, stderr="permission denied")]
        with patch.object(pathlib.Path, "exists", _path_exists(True)), \
                patch("handlers.updates.subprocess.run", side_effect=runs):
            success, _msg = h._pip_install_meshtastic(upgrade=True)
        assert success is True  # user-level install genuinely worked
        assert "rnsd Install Incomplete" in _msgbox_titles(h.ctx.dialog)

    def test_rnsd_install_success_shows_no_warning(self):
        h = _handler()
        runs = [_result(0, stdout="user ok"), _result(0, stdout="rnsd ok")]
        with patch.object(pathlib.Path, "exists", _path_exists(True)), \
                patch("handlers.updates.subprocess.run", side_effect=runs):
            success, _msg = h._pip_install_meshtastic(upgrade=True)
        assert success is True
        assert "rnsd Install Incomplete" not in _msgbox_titles(h.ctx.dialog)

    def test_no_rnsd_interface_means_no_second_install(self):
        h = _handler()
        run = MagicMock(side_effect=[_result(0, stdout="user ok")])
        with patch.object(pathlib.Path, "exists", _path_exists(False)), \
                patch("handlers.updates.subprocess.run", run):
            success, _msg = h._pip_install_meshtastic(upgrade=True)
        assert success is True
        assert run.call_count == 1  # only the user-level install ran
        assert "rnsd Install Incomplete" not in _msgbox_titles(h.ctx.dialog)

    def test_user_level_failure_returns_false_without_rnsd_attempt(self):
        h = _handler()
        run = MagicMock(side_effect=[_result(1, stderr="boom")])
        with patch.object(pathlib.Path, "exists", _path_exists(True)), \
                patch("handlers.updates.subprocess.run", run):
            success, msg = h._pip_install_meshtastic(upgrade=True)
        assert success is False
        assert "boom" in msg
        assert run.call_count == 1  # bailed before the rnsd dual-install


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

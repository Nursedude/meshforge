"""journalctl audit — In-Domain Principle Class 3 (logs must not eject).

A log view must CAPTURE journalctl output and show it in a scrollable in-pane
textbox, never stream it raw to the terminal (which ejects the operator out of
the TUI). The exception is an explicit live-follow (`-f`/`-fu`), which whiptail
can't host in a pane and is intentionally terminal-native.

These tests pin:
  * the shared BaseHandler._show_command_output (promoted from LogsHandler so
    every handler can show captured command output in-pane);
  * the converted log views call dialog.textbox (in-pane) instead of printing;
  * a source-scan invariant: every journalctl subprocess.run in the TUI is
    either captured (capture_output) or an intentional live-follow (-f/-fu).
"""

import os
import re
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context


def _proc(stdout="", stderr="", returncode=0):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def _textboxes(h):
    return [c for c in h.ctx.dialog.calls if c[0] == 'textbox']


# ----------------------------------------------------------------------
# Shared BaseHandler helper
# ----------------------------------------------------------------------
class TestBaseHandlerShowCommandOutput:

    def _h(self):
        from handler_protocol import BaseHandler

        class _H(BaseHandler):
            pass
        h = _H()
        h.set_context(make_handler_context())
        return h

    def test_captures_and_shows_in_pane(self):
        h = self._h()
        with patch('subprocess.run', return_value=_proc(stdout="logline\n")):
            h._show_command_output("T", ["journalctl", "-n", "5"])
        tb = _textboxes(h)
        assert tb and "logline" in tb[0][1][1]

    def test_stderr_included(self):
        h = self._h()
        with patch('subprocess.run', return_value=_proc(stdout="o", stderr="boom")):
            h._show_command_output("T", ["journalctl"])
        assert "boom" in _textboxes(h)[0][1][1]

    def test_timeout_shown_in_pane_not_raised(self):
        h = self._h()
        with patch('subprocess.run',
                   side_effect=subprocess.TimeoutExpired("journalctl", 5)):
            h._show_command_output("T", ["journalctl"], timeout=5)
        assert "timed out" in _textboxes(h)[0][1][1]

    def test_missing_binary_shown_in_pane(self):
        h = self._h()
        with patch('subprocess.run', side_effect=FileNotFoundError()):
            h._show_command_output("T", ["journalctl"])
        assert "could not run" in _textboxes(h)[0][1][1]


# ----------------------------------------------------------------------
# Converted log views -> in-pane
# ----------------------------------------------------------------------
class TestConvertedLogViewsAreInPane:

    def test_meshtasticd_logs_in_pane(self):
        from handlers.meshtasticd_config import MeshtasticdConfigHandler
        h = MeshtasticdConfigHandler()
        h.set_context(make_handler_context())
        with patch('subprocess.run', return_value=_proc(stdout="mtd line\n")):
            h._meshtasticd_logs()
        tb = _textboxes(h)
        assert tb and "mtd line" in tb[0][1][1]

    def test_daemon_logs_in_pane(self):
        from handlers.daemon import DaemonHandler
        h = DaemonHandler()
        h.set_context(make_handler_context())
        with patch('subprocess.run', return_value=_proc(stdout="daemon line\n")):
            h._daemon_logs()
        tb = _textboxes(h)
        assert tb and "daemon line" in tb[0][1][1]

    def test_service_action_logs_in_pane(self):
        from handlers.service_menu import ServiceMenuHandler
        h = ServiceMenuHandler()
        h.set_context(make_handler_context())
        # non-rnsd service => journalctl branch => _show_command_output => textbox
        with patch('subprocess.run', return_value=_proc(stdout="svc log\n")):
            h._service_action("meshtasticd", "logs")
        tb = _textboxes(h)
        assert tb and "svc log" in tb[0][1][1]

    def test_nomadnet_rnsd_journal_quick_in_pane(self):
        from handlers.nomadnet import NomadNetHandler
        h = NomadNetHandler()
        h.set_context(make_handler_context())
        with patch('subprocess.run', return_value=_proc(stdout="rnsd j\n")):
            h._show_rnsd_journal_quick()
        tb = _textboxes(h)
        assert tb and "rnsd j" in tb[0][1][1]

    def test_nomadnet_user_journal_in_pane(self):
        from handlers.nomadnet import NomadNetHandler
        h = NomadNetHandler()
        h.set_context(make_handler_context())
        with patch.object(h, '_capture_user_journal', return_value="user j line"):
            h._show_user_journal()
        tb = _textboxes(h)
        assert tb and "user j line" in tb[0][1][1]


# ----------------------------------------------------------------------
# Source-scan invariant: journalctl is captured OR an intentional follow
# ----------------------------------------------------------------------
class TestNoUncapturedJournalctlInTUI:

    def _tui_dir(self):
        return Path(__file__).parent.parent / "src" / "launcher_tui"

    def _calls(self, text):
        """Yield (lineno, call_src) for each subprocess.run/.call/.Popen call."""
        call_re = re.compile(r"subprocess\.(run|call|Popen)\(")
        for m in call_re.finditer(text):
            i = m.end() - 1
            depth = 0
            j = i
            while j < len(text):
                if text[j] == '(':
                    depth += 1
                elif text[j] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            yield text[:m.start()].count("\n") + 1, text[m.start():j + 1]

    def test_every_journalctl_subprocess_is_captured_or_follow(self):
        """Walk every subprocess call in the TUI; if its argv contains
        'journalctl', it must either capture output or be a live follow
        (-f/-fu). A raw, captureless, non-follow journalctl call streams to the
        terminal and ejects the operator (In-Domain Class 3)."""
        offenders = []
        for path in self._tui_dir().rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line, src in self._calls(text):
                if "journalctl" not in src:
                    continue
                follow = any(t in src for t in ("'-f'", '"-f"', "'-fu'", '"-fu"'))
                captured = ("capture_output" in src or "stdout=" in src
                            or "PIPE" in src)
                if not (follow or captured):
                    offenders.append(f"{path.name}:{line}")
        assert offenders == [], (
            "uncaptured non-follow journalctl calls (Class 3 ejects): "
            + ", ".join(offenders))

    def test_no_uncaptured_status_display_ejects(self):
        """`systemctl status`, `pgrep`, `docker logs`, and non-follow `tail`
        DISPLAY their output; uncaptured they stream to the terminal and eject
        the operator. (Silent actions — systemctl start/stop/restart/enable/
        daemon-reload/reboot — produce no display and are exempt.)"""
        offenders = []
        for path in self._tui_dir().rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line, src in self._calls(text):
                captured = ("capture_output" in src or "stdout=" in src
                            or "PIPE" in src)
                if captured:
                    continue
                follow = any(t in src for t in ("'-f'", '"-f"', "'-fu'", '"-fu"'))
                is_display = (
                    ("systemctl" in src and "status" in src)
                    or "'pgrep'" in src or '"pgrep"' in src
                    or ("docker" in src and "logs" in src)
                    or (("'tail'" in src or '"tail"' in src) and not follow)
                )
                if is_display:
                    offenders.append(f"{path.name}:{line}")
        assert offenders == [], (
            "uncaptured status-display ejects (Class 3): " + ", ".join(offenders))


class TestStatusDisplaysInPane:

    def test_qa_restart_meshtasticd_in_pane(self):
        from handlers.quick_actions import QuickActionsHandler
        h = QuickActionsHandler()
        h.set_context(make_handler_context())
        with patch('handlers.quick_actions.apply_config_and_restart',
                   return_value=(True, "restarted ok")):
            with patch('subprocess.run', return_value=_proc(stdout="active (running)")):
                h._qa_restart_meshtasticd()
        tb = _textboxes(h)
        assert tb, "expected an in-pane result"
        body = tb[0][1][1]
        assert "restarted ok" in body and "active (running)" in body

    def test_service_action_status_in_pane(self):
        from handlers.service_menu import ServiceMenuHandler
        h = ServiceMenuHandler()
        h.set_context(make_handler_context())
        with patch('subprocess.run', return_value=_proc(stdout="meshtasticd active")):
            h._service_action("meshtasticd", "status")
        tb = _textboxes(h)
        assert tb and "meshtasticd active" in tb[0][1][1]

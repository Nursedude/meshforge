"""In-app log viewing (MF018 Class 3 — logs no longer eject to the terminal).

The Log Viewer's snapshot views ran journalctl/dmesg as an uncaptured
subprocess to the terminal (ejecting the TUI). They now capture the output and
show it in an in-app scrollable pane (backend.textbox / --textbox). A failed or
timed-out command is shown in-pane too — never dumped, never silent. Live-follow
(`-f`) views are intentionally terminal-native (whiptail can't live-tail).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))


# ---------------------------------------------------------- backend.textbox


def test_backend_textbox_writes_text_and_shows_textbox():
    from backend import DialogBackend
    b = DialogBackend()
    captured = {}

    def fake_run(args, **k):
        captured["args"] = args
        i = args.index("--textbox")
        # The temp file exists during the _run call (deleted in finally after).
        captured["content"] = Path(args[i + 1]).read_text()
        return (0, "")

    b._run = fake_run
    b.textbox("Logs", "line1\nline2")
    assert "--textbox" in captured["args"]
    assert captured["content"] == "line1\nline2"


def test_backend_textbox_empty_shows_placeholder():
    from backend import DialogBackend
    b = DialogBackend()
    seen = {}

    def fake_run(args, **k):
        i = args.index("--textbox")
        seen["content"] = Path(args[i + 1]).read_text()
        return (0, "")

    b._run = fake_run
    b.textbox("Logs", "")
    assert seen["content"] == "(no output)"  # blank never reads as a clean pane


# ---------------------------------------------------------- logs handler


class TestLogsInApp:
    def _handler(self):
        from handlers.logs import LogsHandler
        h = LogsHandler()
        h.ctx = MagicMock()
        return h

    def test_show_command_output_captures_and_shows_in_app(self):
        h = self._handler()
        with patch("handlers.logs.subprocess.run",
                   return_value=MagicMock(stdout="LOG BODY", stderr="")) as sr:
            h._show_command_output("meshtasticd", ["journalctl", "-n", "5"])
        # captured, not dumped to the terminal
        assert sr.call_args.kwargs.get("capture_output") is True
        assert sr.call_args.kwargs.get("text") is True
        h.ctx.dialog.textbox.assert_called_once()
        body = h.ctx.dialog.textbox.call_args[0][1]
        assert "LOG BODY" in body

    def test_show_command_output_includes_stderr(self):
        h = self._handler()
        with patch("handlers.logs.subprocess.run",
                   return_value=MagicMock(stdout="out", stderr="a warning")):
            h._show_command_output("t", ["journalctl"])
        body = h.ctx.dialog.textbox.call_args[0][1]
        assert "a warning" in body

    def test_show_command_output_timeout_is_in_app(self):
        h = self._handler()
        with patch("handlers.logs.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("journalctl", 5)):
            h._show_command_output("t", ["journalctl"], timeout=5)
        body = h.ctx.dialog.textbox.call_args[0][1]
        assert "timed out" in body.lower()

    def test_show_command_output_missing_binary_is_in_app(self):
        h = self._handler()
        with patch("handlers.logs.subprocess.run",
                   side_effect=FileNotFoundError("no journalctl")):
            h._show_command_output("t", ["journalctl"])
        body = h.ctx.dialog.textbox.call_args[0][1]
        assert "could not run" in body.lower()

    def test_snapshot_views_route_in_app_never_terminal(self):
        # All 5 snapshot views go through _show_command_output (in-app); none
        # runs a bare subprocess to the terminal.
        h = self._handler()
        calls = []
        h._show_command_output = lambda title, cmd, **k: calls.append(cmd)
        h._view_error_logs()
        h._view_meshtasticd_recent()
        h._view_rnsd_recent()
        h._view_boot_messages()
        h._view_kernel_messages()
        assert len(calls) == 5
        assert any("meshtasticd" in c and "-n" in c for c in calls)
        assert any(c[0] == "dmesg" for c in calls)
        assert any("-b" in c for c in calls)  # boot messages

    def test_display_log_file_shows_in_pane(self, tmp_path):
        h = self._handler()
        f = tmp_path / "meshforge_x.log"
        f.write_text("\n".join(f"line {i}" for i in range(100)))
        h._display_log_file(f, tail_lines=10)
        h.ctx.dialog.textbox.assert_called_once()
        body = h.ctx.dialog.textbox.call_args[0][1]
        assert "line 99" in body          # the tail is shown
        assert "line 0" not in body.split("\n")  # only the tail, not the head

    def test_live_follow_still_terminal_native(self):
        # The live-follow views are an explicit streaming mode (whiptail can't
        # live-tail). They keep the Popen-to-terminal path — NOT a regression.
        src = (SRC / "launcher_tui" / "handlers" / "logs.py").read_text()
        assert "subprocess.Popen" in src  # live view still streams

"""Logs screens say what they CAN'T see (2026-09-25).

`journalctl -u <absent unit>` prints "-- No entries --" (reads as quiet); a
user-scope unit never appears under -u; and `rnsd --service` logs to its
config dir's 'logfile', so the rnsd screen (journal only) could never show an
RNS error."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, "..", "src"), os.path.join(HERE, "..", "src", "launcher_tui")):
    if p not in sys.path:
        sys.path.insert(0, p)

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
import handlers.logs as logs  # noqa: E402


def _handler(monkeypatch, system=(), user=()):
    monkeypatch.setattr(logs, "is_service_unit_installed",
                        lambda u, user_scope=False, **k: u in (user if k.get("user") else system))
    d = FakeDialog()
    h = logs.LogsHandler()
    h.set_context(make_handler_context(dialog=d))
    h._capture_command = lambda cmd, timeout=15: "CMD " + " ".join(cmd)
    return h, d


def test_absent_and_user_units_are_named_not_silent(monkeypatch):
    h, d = _handler(monkeypatch, system=("rnsd", "mosquitto"), user=("nomadnet",))
    h._view_error_logs()
    text = [a for k, a, _ in d.calls if k == "textbox"][0][1]
    assert "Not installed on this box: meshtasticd" in text
    assert "--user-unit nomadnet" in text and "-u rnsd" in text
    assert "-u meshtasticd" not in text


def test_no_units_at_all_does_not_run_an_unfiltered_journal(monkeypatch):
    h, d = _handler(monkeypatch)
    h._view_boot_messages()
    text = [a for k, a, _ in d.calls if k == "textbox"][0][1]
    assert "None of the mesh units exist" in text and "CMD" not in text


def test_rnsd_screen_reads_the_logfile_and_strips_nuls(monkeypatch, tmp_path):
    (tmp_path / "logfile").write_bytes(b"\x00\x00[2026-09-24 16:35:04] [Error]    torn down\n")
    monkeypatch.setattr(logs.ReticulumPaths, "get_config_dir", classmethod(lambda cls: tmp_path))
    h, d = _handler(monkeypatch, system=("rnsd",))
    h._view_rnsd_recent()
    text = [a for k, a, _ in d.calls if k == "textbox"][0][1]
    assert "[Error]    torn down" in text and "2 NUL byte(s) stripped" in text
    assert "CMD journalctl -u rnsd" in text


def test_missing_logfile_is_said_not_blank(monkeypatch, tmp_path):
    monkeypatch.setattr(logs.ReticulumPaths, "get_config_dir", classmethod(lambda cls: tmp_path))
    h, d = _handler(monkeypatch, system=("rnsd",))
    h._view_rnsd_recent()
    text = [a for k, a, _ in d.calls if k == "textbox"][0][1]
    assert "not found" in text

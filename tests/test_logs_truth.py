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


def _handler(monkeypatch, system=(), user=(), unknown=(), uid=1000):
    def presence(u, user=False, **k):
        if u in unknown:
            return "unknown"
        return "installed" if u in (user_units if user else system) else "absent"
    user_units = user
    monkeypatch.setattr(logs, "service_unit_presence", presence)
    monkeypatch.setattr(logs, "_journal_uid", lambda: uid)
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
    assert "_SYSTEMD_USER_UNIT=nomadnet.service _UID=1000" in text
    assert "_SYSTEMD_UNIT=rnsd.service" in text
    assert "meshtasticd.service" not in text


# Non-author review 2026-10-08 (VERIFIED): under sudo — the normal TUI launch —
# `systemctl --user cat` reached ROOT's absent user manager, the error read as
# "not installed", and a RUNNING nomadnet was declared ABSENT and filtered
# out. And `journalctl --user-unit` matches the CALLER's uid: measured on the
# dev/manager box, operator 14 lines, root 0, for the same unit. Field terms with
# the operator's _UID reproduce the operator's view exactly (16+14 = 30).

def test_a_failed_check_is_could_not_check_never_absent(monkeypatch):
    h, d = _handler(monkeypatch, system=("rnsd",), unknown=("nomadnet",))
    h._view_error_logs()
    text = [a for k, a, _ in d.calls if k == "textbox"][0][1]
    assert "Could not check" in text and "nomadnet" in text.split("Could not check")[1]
    assert "Not installed on this box: meshtasticd, mosquitto" in text   # the real absences
    assert "nomadnet" not in text.split("Not installed on this box:")[1].split("\n")[0]
    assert "_SYSTEMD_USER_UNIT=nomadnet.service" in text   # still read: unknown ≠ absent


def test_user_units_are_matched_by_the_operators_uid_under_sudo(monkeypatch):
    h, d = _handler(monkeypatch, system=("rnsd",), user=("nomadnet",), uid=1234)
    args, _ = h._mesh_journal_args()
    assert "--user-unit" not in args and "-u" not in args
    i = args.index("_SYSTEMD_USER_UNIT=nomadnet.service")
    assert args[i + 1] == "_UID=1234"
    heads = ("_SYSTEMD_UNIT=", "UNIT=", "COREDUMP_UNIT=", "OBJECT_SYSTEMD_UNIT=",
             "_SYSTEMD_USER_UNIT=", "USER_UNIT=", "COREDUMP_USER_UNIT=",
             "OBJECT_SYSTEMD_USER_UNIT=")
    assert args.count("+") == len([a for a in args if a.startswith(heads)]) - 1


def test_every_check_failing_never_says_none_exist(monkeypatch):
    h, d = _handler(monkeypatch, unknown=tuple(logs.LogsHandler.MESH_UNITS))
    h._view_boot_messages()
    text = [a for k, a, _ in d.calls if k == "textbox"][0][1]
    assert "None of the mesh units exist" not in text and "Could not check" in text


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
    assert "CMD journalctl -u rnsd" in text   # the start/stop lines, system unit


def test_missing_logfile_is_said_not_blank(monkeypatch, tmp_path):
    monkeypatch.setattr(logs.ReticulumPaths, "get_config_dir", classmethod(lambda cls: tmp_path))
    h, d = _handler(monkeypatch, system=("rnsd",))
    h._view_rnsd_recent()
    text = [a for k, a, _ in d.calls if k == "textbox"][0][1]
    assert "not found" in text



def test_coredumps_and_object_messages_are_matched_like_dash_u(monkeypatch):
    # review 2026-10-08: -u also matches COREDUMP_UNIT (+ its MESSAGE_ID) and
    # OBJECT_SYSTEMD_UNIT; a meshtasticd core dump is an err+ line
    h, d = _handler(monkeypatch, system=("meshtasticd",), user=("nomadnet",))
    args, _ = h._mesh_journal_args()
    assert "COREDUMP_UNIT=meshtasticd.service" in args
    assert "MESSAGE_ID=fc2e22bc6ee647b6b90729ab34a250b1" in args
    assert "OBJECT_SYSTEMD_UNIT=meshtasticd.service" in args
    assert "COREDUMP_USER_UNIT=nomadnet.service" in args


def test_root_without_sudo_user_never_says_a_user_unit_is_absent(monkeypatch):
    # root's OWN user manager answering "No files found" says nothing about
    # the operator's units (review 2026-10-08, L-a)
    h, d = _handler(monkeypatch, system=("rnsd",))
    monkeypatch.setattr(logs, "_operator_known", lambda: False)
    args, notes = h._mesh_journal_args()
    joined = "\n".join(notes)
    assert "nomadnet" in joined.split("Could not check")[1]
    assert "Not installed on this box:" not in joined   # nothing is provably absent

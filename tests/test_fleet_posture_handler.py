"""Tests for FleetPostureHandler — the TUI front door to fleet_power.py (P3).

The handler is a SURFACE over the one implementation (scripts/fleet_power.py):
these tests pin that it never writes posture itself, that it only offers to act
where the SSOT lives, and that the typed confirm is exact.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))

from handlers import fleet_posture as fph  # noqa: E402
from utils import fleet_posture as fp  # noqa: E402

NOW = 1_800_000_000.0


def _posture(**kw) -> fp.Posture:
    return fp.Posture(status=kw.pop("status", fp.DECLARED), path="/x/fleet_posture.json", **kw)


# --- where acting is allowed ------------------------------------------------

def test_actable_on_manager_with_graph(tmp_path):
    g = tmp_path / "fleet_offline_boxes.json"
    g.write_text("{}")
    ok, why = fph.actable(_posture(status=fp.UNDECLARED), str(g))
    assert ok, why


def test_not_actable_on_a_mirror(tmp_path):
    g = tmp_path / "g.json"
    g.write_text("{}")
    ok, why = fph.actable(_posture(is_mirror=True, mirror_from="manager-box"), str(g))
    assert not ok
    assert "manager-box" in why


def test_not_actable_without_dependency_graph(tmp_path):
    ok, why = fph.actable(_posture(status=fp.UNDECLARED), str(tmp_path / "absent.json"))
    assert not ok
    assert "manager" in why


@pytest.mark.parametrize("status", [fp.UNREADABLE, fp.INVALID])
def test_not_actable_on_a_broken_file(tmp_path, status):
    g = tmp_path / "g.json"
    g.write_text("{}")
    ok, why = fph.actable(_posture(status=status, detail="boom"), str(g))
    assert not ok
    assert "boom" in why


# --- typed confirm ------------------------------------------------------------

@pytest.mark.parametrize("typed", ["moc4", " moc4 ", "moc4,moc5", "moc5 moc4"])
def test_confirm_exact_set(typed):
    boxes = ["moc4"] if "moc5" not in typed else ["moc4", "moc5"]
    assert fph.confirm_matches(typed, boxes)


@pytest.mark.parametrize("typed", [None, "", "yes", "moc", "MOC4", "moc4 moc5", "moc4 moc4x"])
def test_confirm_rejects_anything_else(typed):
    assert not fph.confirm_matches(typed, ["moc4"])


# --- commands are fleet_power.py, never a second implementation ---------------

def test_down_command_shape():
    cmd = fph.build_down_cmd(["moc4", "moc5"], "travel", "kit trip", declare_only=True, apply=False)
    assert cmd[1].endswith("scripts/fleet_power.py")
    assert cmd[2:5] == ["down", "moc4", "moc5"]
    assert "--kind" in cmd and cmd[cmd.index("--kind") + 1] == "travel"
    assert cmd[cmd.index("--reason") + 1] == "kit trip"
    assert "--declare-only" in cmd
    assert "--apply" not in cmd


def test_down_apply_adds_apply_only_then():
    cmd = fph.build_down_cmd(["moc4"], "move", "", declare_only=False, apply=True)
    assert "--apply" in cmd and "--declare-only" not in cmd


def test_down_rejects_unknown_kind():
    with pytest.raises(ValueError):
        fph.build_down_cmd(["moc4"], "vacation", "", declare_only=True, apply=False)


def test_resume_command_shape():
    cmd = fph.build_resume_cmd(["kiai"], wait=300, apply=True)
    assert cmd[2:4] == ["resume", "kiai"]
    assert cmd[cmd.index("--wait") + 1] == "300"
    assert "--apply" in cmd


def test_kind_choices_come_from_the_shared_constant():
    # One table (hfm #5): the TUI offers exactly what fleet_power accepts.
    assert set(fph.kind_choices()) == set(fp.KINDS)


def test_reason_is_sanitised():
    assert fph.clean_reason("  bench\x00 work\n ") == "bench work"
    assert len(fph.clean_reason("x" * 500)) <= fph.REASON_MAX


# --- privilege drop -------------------------------------------------------------

def test_as_operator_drops_root_to_sudo_user():
    cmd, note = fph.as_operator(["python3", "x"], euid=0, sudo_user="wh")
    assert cmd[:5] == ["sudo", "-n", "-u", "wh", "-H"]
    assert note


def test_as_operator_plain_root_refuses():
    cmd, note = fph.as_operator(["python3", "x"], euid=0, sudo_user=None)
    assert cmd is None and "root" in note


def test_as_operator_user_passthrough():
    cmd, note = fph.as_operator(["python3", "x"], euid=1000, sudo_user=None)
    assert cmd == ["python3", "x"] and note is None


# --- read view --------------------------------------------------------------------

def test_render_undeclared_says_every_box_active():
    text = "\n".join(fph.render_posture(_posture(status=fp.UNDECLARED), now=NOW))
    assert "nothing declared" in text.lower()


def test_render_empty_file_does_not_say_declared():
    # 10-05 operator screenshot: "status : declared" over "Nothing declared".
    text = "\n".join(fph.render_posture(_posture(status=fp.DECLARED), now=NOW))
    assert "status : declared" not in text
    assert "0 boxes declared" in text


def test_render_box_rows():
    p = _posture(declared_by="operator", boxes={
        "kiai": fp.BoxPosture(name="kiai", state="detached", declared_state="detached",
                              since=NOW - 3600, until=NOW + 86400, reason="[travel] kit"),
    })
    text = "\n".join(fph.render_posture(p, now=NOW))
    assert "kiai" in text and "detached" in text and "[travel] kit" in text
    assert "until" in text


def test_render_broken_file_is_loud():
    text = "\n".join(fph.render_posture(_posture(status=fp.INVALID, detail="bad until"), now=NOW))
    assert "INVALID" in text and "bad until" in text
    assert "every box" in text.lower()


def test_render_expired_and_held_are_named():
    p = _posture(boxes={
        "a": fp.BoxPosture(name="a", state="active", declared_state="dormant", expired=True),
        "b": fp.BoxPosture(name="b", state="dormant", declared_state="dormant", held=True,
                           note="held: clock unconfirmed"),
    })
    text = "\n".join(fph.render_posture(p, now=NOW))
    assert "EXPIRED" in text and "HELD" in text


# --- the guarded flow ---------------------------------------------------------------

def _handler_with(dialog):
    h = fph.FleetPostureHandler()
    ctx = MagicMock()
    ctx.dialog = dialog
    h.set_context(ctx)
    return h


def test_mismatched_confirm_never_applies(monkeypatch):
    dialog = MagicMock()
    dialog.inputbox.return_value = "moc5"        # wrong box typed
    h = _handler_with(dialog)
    calls = []
    monkeypatch.setattr(h, "_stream", lambda cmd, timeout: calls.append(cmd) or 0)
    assert h._confirm_and_apply("Power off", ["moc4"], ["x", "--apply"], 60) is False
    assert calls == []


def test_matched_confirm_applies(monkeypatch):
    dialog = MagicMock()
    dialog.inputbox.return_value = "moc4"
    h = _handler_with(dialog)
    calls = []
    monkeypatch.setattr(h, "_stream", lambda cmd, timeout: calls.append(cmd) or 0)
    assert h._confirm_and_apply("Power off", ["moc4"], ["x", "--apply"], 60) is True
    assert calls == [["x", "--apply"]]
    h.ctx.report_action.assert_called_once()
    assert h.ctx.report_action.call_args[0][0] is True


def test_failed_apply_reports_failure(monkeypatch):
    dialog = MagicMock()
    dialog.inputbox.return_value = "moc4"
    h = _handler_with(dialog)
    monkeypatch.setattr(h, "_stream", lambda cmd, timeout: 1)
    h._confirm_and_apply("Power off", ["moc4"], ["x", "--apply"], 60)
    assert h.ctx.report_action.call_args[0][0] is False

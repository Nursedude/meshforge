"""Tests for the in-app remediation surface (src/launcher_tui/remediation.py).

The surface is the spine of the In-Domain Principle: a producer describes a
finding + proposed fixes; the surface ratifies, applies, and reports — all
in-app. These tests pin that contract with a fake dialog (no whiptail).
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "launcher_tui"))

from remediation import RemediationAction, propose_remediation  # noqa: E402


class FakeDialog:
    """Records calls; menu_return scripts the user's menu choice."""
    def __init__(self, menu_return="1", yesno_return=True):
        self.menu_return = menu_return
        self.yesno_return = yesno_return
        self.msgboxes = []
        self.menus = []
        self.yesnos = []

    def menu(self, title, text, choices, *a, **k):
        self.menus.append((title, text, choices))
        return self.menu_return

    def yesno(self, title, text, *a, **k):
        self.yesnos.append((title, text))
        return self.yesno_return

    def msgbox(self, title, text, *a, **k):
        self.msgboxes.append((title, text))


class Ctx:
    def __init__(self, dialog):
        self.dialog = dialog


def _action(calls, ok=True, msg="done", **kw):
    def apply():
        calls.append(True)
        return (ok, msg)
    return RemediationAction(label="Restart rnsd now", description="systemctl restart rnsd",
                             apply=apply, **kw)


def test_selecting_fix_applies_and_reports():
    calls = []
    d = FakeDialog(menu_return="1")
    res = propose_remediation(Ctx(d), "T", "rnsd is down.", [_action(calls)])
    assert calls == [True]                       # the fix ran
    assert res == (True, "done")
    assert d.msgboxes and d.msgboxes[-1][1].startswith("✓")


def test_do_nothing_does_not_apply():
    calls = []
    d = FakeDialog(menu_return="0")              # user chose "Do nothing"
    res = propose_remediation(Ctx(d), "T", "rnsd is down.", [_action(calls)])
    assert calls == [] and res is None


def test_menu_cancel_does_not_apply():
    calls = []
    d = FakeDialog(menu_return="")               # ESC / cancel
    res = propose_remediation(Ctx(d), "T", "f", [_action(calls)])
    assert calls == [] and res is None


def test_menu_always_includes_do_nothing():
    d = FakeDialog(menu_return="0")
    propose_remediation(Ctx(d), "T", "f", [_action([])])
    tags = [tag for tag, _label in d.menus[-1][2]]
    assert "0" in tags                            # decline is always offered


def test_failing_fix_reports_cross_not_check():
    d = FakeDialog(menu_return="1")
    res = propose_remediation(Ctx(d), "T", "f", [_action([], ok=False, msg="restart failed")])
    assert res == (False, "restart failed")
    assert d.msgboxes[-1][1].startswith("✗")


def test_apply_exception_never_crashes():
    def boom():
        raise RuntimeError("kaboom")
    d = FakeDialog(menu_return="1")
    a = RemediationAction("X", "d", apply=boom)
    res = propose_remediation(Ctx(d), "T", "f", [a])
    assert res[0] is False and "kaboom" in res[1]   # surfaced, not raised
    assert d.msgboxes[-1][1].startswith("✗")


def test_requires_ratify_asks_second_confirm():
    calls = []
    d = FakeDialog(menu_return="1", yesno_return=False)   # decline the confirm
    res = propose_remediation(Ctx(d), "T", "f", [_action(calls, requires_ratify=True)])
    assert calls == [] and res is None and d.yesnos        # confirm was shown, fix skipped


def test_requires_admin_blocks_without_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)       # non-root
    calls = []
    d = FakeDialog(menu_return="1")
    res = propose_remediation(Ctx(d), "T", "f", [_action(calls, requires_admin=True)])
    assert calls == []                                     # fix NOT attempted
    assert res == (False, "admin required")
    assert "Admin" in d.msgboxes[-1][1]


def test_no_actions_just_shows_finding():
    d = FakeDialog()
    res = propose_remediation(Ctx(d), "T", "just FYI", [])
    assert res is None and d.msgboxes[-1] == ("T", "just FYI")

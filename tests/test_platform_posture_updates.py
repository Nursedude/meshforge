"""TUI Update Readiness (``PlatformPostureHandler._show_updates``) — the
update-stamp caveat line, which had no test at all until dep-advisory review
r6 #5 (2026-11-05).

r6 #5: an ABSENT stamp printed "A count read from stale lists is not a
current count." — but an absent stamp says nothing about the lists (a manual
or cron `apt-get update` refreshes them without writing the stamp). What it
does say, per the operator's r6 #2a decision, is that apt.systemd.daily never
recorded a success, so unattended-upgrades may never have run. The caveat
must name THAT, and "stale lists" only when the stamp says the lists are old.
"""

import os
import sys

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "src", "launcher_tui"))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from handlers.platform_posture import PlatformPostureHandler     # noqa: E402
import utils.apt_lists as apt_lists                               # noqa: E402

STALE_LISTS = "A count read from stale lists is not a current count."


def _render(monkeypatch, stamp_present, upd_h, lists_h=2):
    real_exists = os.path.exists

    def exists(p):
        if p == apt_lists.UPDATE_STAMP:
            return stamp_present
        if p == "/var/run/reboot-required":
            return False
        return real_exists(p)

    monkeypatch.setattr(os.path, "exists", exists)
    monkeypatch.setattr(apt_lists, "update_stamp_age_h", lambda path, now: upd_h)
    monkeypatch.setattr(apt_lists, "apt_lists_age_h", lambda path, now: lists_h)
    # Pinned: the real call would read THIS box's /var/lib/apt/lists. A test
    # that needs a count overrides it after (setattr order: last wins).
    if not getattr(apt_lists.apt_lists_future_n, "_test_override", False):
        monkeypatch.setattr(apt_lists, "apt_lists_future_n", lambda path, now: 0)
    dialog = FakeDialog()
    h = PlatformPostureHandler()
    h.set_context(make_handler_context(dialog=dialog))
    monkeypatch.setattr(h, "_installed_kernels", lambda: [os.uname().release])
    monkeypatch.setattr(h, "_pending_count", lambda: 0)
    monkeypatch.setattr(h, "_holds", lambda: [])
    h._show_updates()
    return dialog.last_msgbox_text


def test_absent_stamp_with_fresh_lists_does_not_say_stale_lists(monkeypatch):
    text = _render(monkeypatch, stamp_present=False, upd_h=None, lists_h=2)
    assert "NEVER recorded" in text
    assert STALE_LISTS not in text
    assert "unattended-upgrades may never have run" in text


def test_a_stale_stamp_says_stale_lists(monkeypatch):
    text = _render(monkeypatch, stamp_present=True,
                   upd_h=apt_lists.APT_LISTS_STALE_H + 24, lists_h=300)
    assert STALE_LISTS in text


def test_an_unreadable_stamp_says_freshness_unknown(monkeypatch):
    text = _render(monkeypatch, stamp_present=True, upd_h=None)
    assert "Freshness UNKNOWN" in text and STALE_LISTS not in text


def test_a_fresh_stamp_carries_no_caveat(monkeypatch):
    text = _render(monkeypatch, stamp_present=True, upd_h=3)
    assert STALE_LISTS not in text and "Freshness UNKNOWN" not in text
    assert "may never have run" not in text


# r6 #4, the TUI half: the skipped future-dated files are said beside the age.
def test_future_dated_list_files_are_said(monkeypatch):
    fake = lambda path, now: 7  # noqa: E731
    fake._test_override = True
    monkeypatch.setattr(apt_lists, "apt_lists_future_n", fake)
    text = _render(monkeypatch, stamp_present=True, upd_h=3, lists_h=400)
    assert "excluding 7 future-dated file(s)" in text

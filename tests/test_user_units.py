"""Tests for utils.user_units — the SSOT for root-safe `systemd --user` reads.

Run: python3 -m pytest tests/test_user_units.py -v
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from utils.user_units import (  # noqa: E402
    enabled_user_timers,
    timer_wants_dirs,
    user_timer_enrolled,
)

UNIT = "meshforge-synth-soak.timer"


def _wants(home: Path) -> Path:
    d = home / ".config" / "systemd" / "user" / "timers.target.wants"
    d.mkdir(parents=True)
    return d


class TestEnabledUserTimers:
    def test_absent_wants_dir_is_observed_empty(self, tmp_path):
        assert enabled_user_timers(str(tmp_path)) == {}

    def test_enrolled_timer_maps_to_its_stem_service(self, tmp_path):
        (_wants(tmp_path) / UNIT).write_text("[Timer]\n", encoding="utf-8")
        assert enabled_user_timers(str(tmp_path)) == {
            UNIT: "meshforge-synth-soak.service"}

    def test_explicit_Unit_override_wins(self, tmp_path):
        (_wants(tmp_path) / UNIT).write_text(
            "[Timer]\nUnit=something-else.service\n", encoding="utf-8")
        assert enabled_user_timers(str(tmp_path))[UNIT] == "something-else.service"

    def test_unreadable_dir_is_None_never_empty(self, tmp_path):
        """Unobservable must not collapse into "no timers enrolled" — that is
        the whole defect class (honest_failure_modes #1)."""
        d = _wants(tmp_path)
        real = os.listdir

        def boom(path, *a, **kw):
            if str(path) == str(d):
                raise PermissionError(13, "Permission denied")
            return real(path, *a, **kw)

        # Patched, not chmod 000: a suite run as root reads a 000 dir, so the
        # verdict would depend on who ran it.
        with patch("os.listdir", side_effect=boom):
            assert enabled_user_timers(str(tmp_path)) is None


class TestHermeticInUserHome:
    """THE regression guard, from a same-day CI failure (2026-08-09).

    For one commit this module also consulted the GLOBAL
    /etc/systemd/user/timers.target.wants. Every fleet box lacks that
    directory, so it looked free — but CI's runner image has it, and an
    existing probe test that fully pinned a temp home started failing there
    while passing locally. A function parameterised by `user_home` must be
    DETERMINED by `user_home`.
    """

    def test_wants_dirs_are_all_inside_the_given_home(self, tmp_path):
        for d in timer_wants_dirs(str(tmp_path)):
            assert d.startswith(str(tmp_path)), (
                f"{d} escapes the given home — a global path makes every "
                f"caller's verdict depend on the host that runs it")

    def test_no_path_outside_the_home_is_ever_read(self, tmp_path):
        """Fail loudly if the implementation reaches outside its argument,
        whatever mechanism a future edit uses to do it."""
        (_wants(tmp_path) / UNIT).write_text("[Timer]\n", encoding="utf-8")
        seen = []
        real_listdir, real_isdir = os.listdir, os.path.isdir

        def rec_listdir(path, *a, **kw):
            seen.append(str(path))
            return real_listdir(path, *a, **kw)

        def rec_isdir(path, *a, **kw):
            seen.append(str(path))
            return real_isdir(path, *a, **kw)

        with patch("os.listdir", side_effect=rec_listdir), \
                patch("os.path.isdir", side_effect=rec_isdir):
            enabled_user_timers(str(tmp_path))
        outside = [p for p in seen if not p.startswith(str(tmp_path))]
        assert not outside, f"read outside the given home: {outside}"


class TestUserTimerEnrolled:
    def test_true_when_enrolled(self, tmp_path):
        (_wants(tmp_path) / UNIT).write_text("[Timer]\n", encoding="utf-8")
        assert user_timer_enrolled(UNIT, str(tmp_path)) is True

    def test_false_when_home_has_no_such_timer(self, tmp_path):
        _wants(tmp_path)
        assert user_timer_enrolled(UNIT, str(tmp_path)) is False

    def test_false_when_no_wants_dir_at_all(self, tmp_path):
        assert user_timer_enrolled(UNIT, str(tmp_path)) is False

    def test_unobservable_is_None_not_False(self, tmp_path):
        with patch("utils.user_units.enabled_user_timers", return_value=None):
            assert user_timer_enrolled(UNIT, str(tmp_path)) is None

    def test_unresolvable_operator_is_None(self):
        """No home passed and no operator found → unobservable, never
        "not enabled" (which would silently retire a caller's detector)."""
        with patch("utils.user_units.resolve_operator_home", return_value=None):
            assert user_timer_enrolled(UNIT) is None


@pytest.mark.parametrize("bad", ["", "  "])
def test_blank_home_does_not_resolve_to_root(bad):
    """A blank home must not become an absolute-path read of /.config/..."""
    for d in timer_wants_dirs(bad):
        assert not d.startswith("/.config"), d

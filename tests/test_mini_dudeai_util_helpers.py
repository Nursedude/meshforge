"""Tests for the shared _util helpers added by the 2026-07-04 review-fix
pass: iso_or_none (the RTC-safe timestamp idiom, previously copy-pasted
byte-identically in three modules) and operator_home (sudo-safe resolution
for OPERATOR-owned corpora — distinct from resolve_home, which locates mini
artifacts and honors MINI_DUDEAI_HOME)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_dudeai._util import iso_or_none, operator_home  # noqa: E402


class TestIsoOrNone:
    def test_valid_epoch_renders(self):
        s = iso_or_none(1_700_000_000.0)
        assert isinstance(s, str) and "T" in s

    def test_absurd_epoch_is_none_not_crash(self):
        assert iso_or_none(1e18) is None


class TestOperatorHome:
    def test_sudo_user_wins(self, monkeypatch):
        import pwd
        import types
        monkeypatch.setenv("SUDO_USER", "op")
        monkeypatch.setattr(pwd, "getpwnam",
                            lambda u: types.SimpleNamespace(pw_dir="/home/op"))
        assert operator_home() == "/home/op"

    def test_unknown_sudo_user_falls_back(self, monkeypatch):
        import pwd

        def raise_key(u):
            raise KeyError(u)
        monkeypatch.setenv("SUDO_USER", "ghost")
        monkeypatch.setattr(pwd, "getpwnam", raise_key)
        assert operator_home() == os.path.expanduser("~")

    def test_no_sudo_is_home(self, monkeypatch):
        monkeypatch.delenv("SUDO_USER", raising=False)
        assert operator_home() == os.path.expanduser("~")

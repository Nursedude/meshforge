"""harness_share.py windows are CALENDAR days (2026-11-15 finding).

`git log --since=2026-11-15` reads a bare date as that day at the CURRENT
wall-clock time, so an explicit `SINCE UNTIL` window started at ~22:5x and
read today's 15 commits as 0. Windows are now whole local days: SINCE
inclusive from 00:00, UNTIL exclusive at 00:00; `--days N` = the last N
calendar days INCLUDING today.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "harness_share.py"
_spec = importlib.util.spec_from_file_location("harness_share", _SCRIPT)
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)


def _git(repo: Path, *args: str, when: str | None = None) -> None:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(repo), "GIT_CONFIG_NOSYSTEM": "1"}
    if when:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = when
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                    *args], cwd=repo, env=env, check=True, timeout=30,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    # local-time stamps (no offset) — git reads --since bounds in local time too
    for i, when in enumerate(["2026-11-14T23:59:58",   # out (day before)
                              "2026-11-15T00:00:05",   # in  (first seconds)
                              "2026-11-15T23:59:55",   # in  (last seconds)
                              "2026-11-16T00:00:02"]): # out (next day)
        # out-of-window commits are HARNESS, in-window PRODUCT — so a window that
        # swaps one in for one out (same count) still fails on the class split
        top = "src" if i in (1, 2) else ".claude"
        f = tmp_path / top / f"m{i}.py"
        f.parent.mkdir(exist_ok=True)
        f.write_text(f"x = {i}\n")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-q", "-m", f"c{i}", when=when)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_explicit_window_is_whole_local_days(repo):
    c = hs.measure("2026-11-15", "2026-11-16")
    assert (c["total"], c["product"], c["harness"], c["days"]) == (2, 2, 0, 1), c


def test_two_day_window_counts_both_edges_only(repo):
    c = hs.measure("2026-11-14", "2026-11-16")
    assert (c["total"], c["product"], c["harness"], c["days"]) == (3, 2, 1, 2), c


def test_days_window_includes_today():
    since, until = hs.window_for_days(dt.date(2026, 11, 15), 7)
    assert (since, until) == ("2026-11-09", "2026-11-16")


def test_days_one_is_today_only():
    assert hs.window_for_days(dt.date(2026, 11, 15), 1) == ("2026-11-15", "2026-11-16")


@pytest.mark.parametrize("bad", [0, -3])
def test_days_must_be_positive(bad):
    with pytest.raises(ValueError):
        hs.window_for_days(dt.date(2026, 11, 15), bad)

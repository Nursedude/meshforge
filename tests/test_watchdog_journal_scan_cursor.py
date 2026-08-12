"""Incremental journal scanning in ``_journal_newest_match_status``.

2026-07-01 review finding 14, landed 2026-08-12. The moc5 pegged-core fix
bounded ``probe_channel_feed_dark``'s window (24h -> a derived 7h) but left the
CLASS: the NO-MATCH case re-scanned that whole window every 30s tick forever,
and ``probe_mqtt_root_drift`` did the same over a fixed 6h.

These tests assert on the ARGUMENTS the helper actually passes to journalctl,
not on a mocked return value — the saving IS the argument, so a test that only
checked the returned line would pass against a helper that still full-scans.
"""
import subprocess
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.watchdog_probe_core import (  # noqa: E402
    _journal_newest_match_status,
    _lookback_seconds,
    reset_journal_memo,
)

NOW = 1_780_000_000.0


class _Journal:
    """Fake journalctl. Records every argv it was handed and replays a queue
    of (rc, stdout, stderr) so a test can drive several ticks in a row."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc, out, err = self.results.pop(0) if self.results else (0, "", "")
        return subprocess.CompletedProcess(argv, rc, out, err)

    def since_of(self, i):
        argv = self.calls[i]
        return argv[argv.index("--since") + 1]


def _run(monkeypatch, journal, **kw):
    monkeypatch.setattr(subprocess, "run", journal)
    return _journal_newest_match_status("u", "pat", "6h", **kw)


# ── the saving itself ────────────────────────────────────────────────

def test_first_scan_is_the_full_window(monkeypatch):
    reset_journal_memo()
    j = _Journal((0, "", ""))
    _run(monkeypatch, j, now=NOW)
    assert j.since_of(0) == "-6h"      # nothing remembered yet


def test_second_scan_starts_where_the_first_finished(monkeypatch):
    """THE fix: tick 2 asks for journal since tick 1, not the whole window."""
    reset_journal_memo()
    j = _Journal((0, "", ""), (0, "", ""))
    _run(monkeypatch, j, now=NOW)
    _run(monkeypatch, j, now=NOW + 30)
    assert j.since_of(1) == "@%d" % int(NOW)
    assert j.since_of(1) != "-6h"


def test_a_match_also_advances_the_position(monkeypatch):
    reset_journal_memo()
    j = _Journal((0, "%d a match\n" % int(NOW - 60), ""), (0, "", ""))
    st, ln = _run(monkeypatch, j, now=NOW)
    assert (st, ln) == ("ok", "%d a match" % int(NOW - 60))
    _run(monkeypatch, j, now=NOW + 30)
    assert j.since_of(1) == "@%d" % int(NOW)


# ── correctness of the carried-forward answer ────────────────────────

def test_remembered_match_is_carried_while_still_in_window(monkeypatch):
    """An incremental scan finding nothing must not turn a live match into
    'no match' — the older line is still the newest one in the window."""
    reset_journal_memo()
    line = "%d a match" % int(NOW - 60)
    j = _Journal((0, line + "\n", ""), (0, "", ""))
    _run(monkeypatch, j, now=NOW)
    st, ln = _run(monkeypatch, j, now=NOW + 30)
    assert (st, ln) == ("ok", line)


def test_remembered_match_expires_when_it_ages_out(monkeypatch):
    """...and it must STOP being carried once its own timestamp leaves the
    window, or a probe would read healthy off a match from hours ago."""
    reset_journal_memo()
    line = "%d a match" % int(NOW - 60)
    j = _Journal((0, line + "\n", ""), (0, "", ""))
    _run(monkeypatch, j, now=NOW)
    st, ln = _run(monkeypatch, j, now=NOW + 6 * 3600 + 120)   # 6h window passed
    assert (st, ln) == ("ok", None)


def test_a_newer_match_replaces_the_carried_one(monkeypatch):
    reset_journal_memo()
    old = "%d old" % int(NOW - 60)
    new = "%d new" % int(NOW + 10)
    j = _Journal((0, old + "\n", ""), (0, new + "\n", ""))
    _run(monkeypatch, j, now=NOW)
    assert _run(monkeypatch, j, now=NOW + 30)[1] == new


# ── the honesty guards ───────────────────────────────────────────────

def test_unobservable_does_NOT_advance_the_position(monkeypatch):
    """A failed scan must never move the cursor: the skipped stretch would be
    journal that nothing ever read, a blind spot made by the optimisation."""
    reset_journal_memo()
    j = _Journal((0, "", ""), (2, "", "boom"), (0, "", ""))
    _run(monkeypatch, j, now=NOW)
    assert _run(monkeypatch, j, now=NOW + 30)[0] == "unobservable"
    _run(monkeypatch, j, now=NOW + 60)
    # third scan resumes from the last SUCCESSFUL look (NOW), not the failed one
    assert j.since_of(2) == "@%d" % int(NOW)


def test_timeout_does_NOT_advance_the_position(monkeypatch):
    reset_journal_memo()

    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 10)

    j = _Journal((0, "", ""))
    _run(monkeypatch, j, now=NOW)
    monkeypatch.setattr(subprocess, "run", boom)
    assert _journal_newest_match_status("u", "pat", "6h", now=NOW + 30)[0] == "unobservable"
    j2 = _Journal((0, "", ""))
    _run(monkeypatch, j2, now=NOW + 60)
    assert j2.since_of(0) == "@%d" % int(NOW)


def test_malformed_since_is_unobservable_not_no_match(monkeypatch):
    """journalctl exits 1 on a bad timestamp with stderr 'Failed to parse
    timestamp' — measured live 2026-08-12. rc 1 alone means 'nothing matched',
    so without the stderr check this optimisation could fail silently and
    permanently: every tick dark, reported clean."""
    reset_journal_memo()
    j = _Journal((1, "", "Failed to parse timestamp: @notatime"))
    assert _run(monkeypatch, j, now=NOW)[0] == "unobservable"


def test_plain_no_match_still_reads_ok(monkeypatch):
    """rc 1 with EMPTY stderr is the real 'no entries matched' — it must not
    get swept up by the guard above."""
    reset_journal_memo()
    j = _Journal((1, "", ""))
    assert _run(monkeypatch, j, now=NOW) == ("ok", None)


def test_clock_going_backwards_forces_a_full_rescan(monkeypatch):
    """RTC-less Pis and NTP steps move the clock back. A remembered position in
    the FUTURE would suppress scanning of real journal."""
    reset_journal_memo()
    j = _Journal((0, "", ""), (0, "", ""))
    _run(monkeypatch, j, now=NOW)
    _run(monkeypatch, j, now=NOW - 3600)
    assert j.since_of(1) == "-6h"


def test_unparseable_lookback_full_scans_and_never_memoizes(monkeypatch):
    """A window we cannot measure must not be guessed at — guessing short
    silently narrows what every probe sharing this helper can see."""
    reset_journal_memo()
    j = _Journal((0, "", ""), (0, "", ""))
    monkeypatch.setattr(subprocess, "run", j)
    _journal_newest_match_status("u", "pat", "yesterday", now=NOW)
    _journal_newest_match_status("u", "pat", "yesterday", now=NOW + 30)
    assert j.since_of(0) == "-yesterday" and j.since_of(1) == "-yesterday"


def test_memo_is_keyed_per_unit_and_pattern(monkeypatch):
    """Two probes sharing this helper must not inherit each other's position."""
    reset_journal_memo()
    j = _Journal((0, "", ""), (0, "", ""))
    monkeypatch.setattr(subprocess, "run", j)
    _journal_newest_match_status("unitA", "pat", "6h", now=NOW)
    _journal_newest_match_status("unitB", "pat", "6h", now=NOW + 30)
    assert j.since_of(1) == "-6h"


def test_lookback_parser():
    assert _lookback_seconds("6h") == 21600.0
    assert _lookback_seconds("90m") == 5400.0
    assert _lookback_seconds("45s") == 45.0
    assert _lookback_seconds("2d") == 172800.0
    for bad in ("yesterday", "", None, "6 hours", "h"):
        assert _lookback_seconds(bad) is None


def test_reset_is_what_the_conftest_fixture_calls():
    """The autouse fixture in conftest depends on this symbol existing; pin it
    so a rename cannot silently reintroduce cross-test memo leakage."""
    from utils import watchdog_probe_core as core
    core._JOURNAL_MEMO[("x", "y", "z")] = {"scanned_through": 1.0,
                                           "match_ts": None, "line": None}
    core.reset_journal_memo()
    assert core._JOURNAL_MEMO == {}


def test_real_journalctl_accepts_the_epoch_since_we_emit():
    """The @<epoch> form is only useful if the REAL binary honours it. Asserted
    against the actual journalctl, because a silently-ignored --since would
    make this whole change inert while every unit test still passed."""
    import shutil
    if not shutil.which("journalctl"):
        import pytest
        pytest.skip("no journalctl on this box")
    now = int(time.time())
    p = subprocess.run(["journalctl", "--since", "@%d" % (now - 600),
                        "-n", "1", "-q", "--no-pager"],
                       capture_output=True, text=True, timeout=20)
    assert p.returncode in (0, 1)
    assert "Failed to parse" not in (p.stderr or "")

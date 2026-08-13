"""utils.timer_cadence — how often does a systemd .timer fire?

``systemd-analyze`` is mocked in every test. That is not squeamishness about
subprocesses: this module's whole job is to answer a question about the BOX,
so a test that shelled out for real would return a different verdict on a Pi,
in CI's container, and on a box where the binary is missing — pinning nothing
(2026-07-28, the probe tests that read the runner's real crontab).

The live behaviour is verified separately, against real unit files, and
recorded in the commit that introduced this module.
"""
import subprocess
from unittest.mock import patch

import pytest


TRACER = "[Timer]\nOnCalendar=*-*-* *:00/10:00\nPersistent=true\n"
DAILY = "[Timer]\nOnCalendar=*-*-* 03:17:00\nRandomizedDelaySec=600\n"
TWICE_DAILY = "[Timer]\nOnCalendar=*-*-* 08,18:00:00\n"
MONOTONIC = "[Timer]\nOnBootSec=1min\nOnUnitActiveSec=5min\n"
BOOT_ONLY = "[Timer]\nOnBootSec=1min\n"


def _calendar_out(*utc_stamps):
    """A ``systemd-analyze calendar`` transcript with the given UTC elapses."""
    lines = ["Normalized form: whatever"]
    for i, s in enumerate(utc_stamps):
        lines.append(f"    {'Next elapse' if i == 0 else f'Iteration #{i+1}'}: "
                     f"Thu {s} HST")
        lines.append(f"       (in UTC): Thu {s} UTC")
    return "\n".join(lines) + "\n"


def _timespan_out(micros):
    return f"Original: x\n      μs: {micros}\n   Human: x\n"


class _Fake:
    """Stand-in for ``systemd-analyze``, driven by a {argv-kind: output} map."""

    def __init__(self, calendar=None, timespan=None, rc=0, exc=None):
        self.calendar = calendar
        self.timespan = timespan
        self.rc = rc
        self.exc = exc
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        assert kw.get("timeout"), "MF004: every subprocess needs a timeout"
        assert "shell" not in kw, "MF002: never shell=True"
        if self.exc is not None:
            raise self.exc

        class R:
            pass
        r = R()
        r.returncode = self.rc
        r.stdout = (self.calendar if argv[1] == "calendar"
                    else self.timespan) or ""
        return r


@pytest.fixture(autouse=True)
def _clear_memo():
    """The memo is process-lifetime by design; tests must not inherit it."""
    from utils import timer_cadence
    timer_cadence._CADENCE_MEMO.clear()
    timer_cadence._analyze_missing_warned = False
    yield
    timer_cadence._CADENCE_MEMO.clear()


def _cadence(text, fake):
    from utils.timer_cadence import cadence_from_spec_text
    with patch("utils.timer_cadence.subprocess.run", side_effect=fake):
        return cadence_from_spec_text(text)


class TestCalendarSchedules:

    def test_ten_minute_timer(self):
        fake = _Fake(calendar=_calendar_out(
            "2026-08-13 16:30:00", "2026-08-13 16:40:00",
            "2026-08-13 16:50:00", "2026-08-13 17:00:00"))
        assert _cadence(TRACER, fake) == 600.0

    def test_daily_timer_adds_the_randomized_delay(self):
        """Jitter can push a firing later, so it widens the expected gap —
        a window sized without it clips the tail of its own schedule."""
        fake = _Fake(calendar=_calendar_out(
            "2026-08-14 13:17:00", "2026-08-15 13:17:00",
            "2026-08-16 13:17:00"), timespan=_timespan_out(600_000_000))
        assert _cadence(DAILY, fake) == 86400.0 + 600.0

    def test_uneven_schedule_returns_the_LONGEST_gap(self):
        """``08,18:00:00`` has a 10h gap and a 14h one. Taking the mean or the
        first would size a window that misses the long leg every night —
        under-wide is the direction that manufactures a false clean."""
        fake = _Fake(calendar=_calendar_out(
            "2026-08-13 18:00:00", "2026-08-14 04:00:00",
            "2026-08-14 18:00:00", "2026-08-15 04:00:00"))
        assert _cadence(TWICE_DAILY, fake) == 14 * 3600.0

    def test_several_oncalendar_lines_take_the_widest(self):
        fake = _Fake(calendar=_calendar_out(
            "2026-08-13 16:00:00", "2026-08-13 17:00:00"))
        text = "[Timer]\nOnCalendar=hourly\nOnCalendar=daily\n"
        assert _cadence(text, fake) == 3600.0
        assert len(fake.calls) == 2, "each spec must be asked about"


class TestMonotonicSchedules:

    def test_on_unit_active_sec(self):
        fake = _Fake(timespan=_timespan_out(300_000_000))
        assert _cadence(MONOTONIC, fake) == 300.0

    def test_boot_only_timer_has_no_cadence(self):
        """``OnBootSec=`` alone fires ONCE per boot. Calling that a period
        would invent a bar out of a schedule that has none."""
        fake = _Fake()
        assert _cadence(BOOT_ONLY, fake) is None
        assert fake.calls == [], "nothing to ask systemd about"


class TestUnobservableIsNeverAGuess:
    """Every one of these must be None so the caller falls back to its own
    documented default. A wrong number here silently resizes a detector's
    window, which is worse than losing the feature (honest_failure_modes #1)."""

    def test_missing_binary(self):
        fake = _Fake(exc=FileNotFoundError("no systemd-analyze"))
        assert _cadence(DAILY, fake) is None

    def test_timeout(self):
        fake = _Fake(exc=subprocess.TimeoutExpired("systemd-analyze", 10))
        assert _cadence(DAILY, fake) is None

    def test_unparseable_spec_is_none_not_zero(self):
        """systemd rejects a bad spec with rc 1; zero would read as 'fires
        constantly' and collapse every window to the floor."""
        fake = _Fake(calendar="", rc=1)
        assert _cadence("[Timer]\nOnCalendar=not-a-spec\n", fake) is None

    def test_never_elapsing_spec(self):
        """One elapse (or none) gives no gap to measure."""
        fake = _Fake(calendar=_calendar_out("2026-08-13 16:30:00"))
        assert _cadence(DAILY, fake) is None

    def test_no_schedule_at_all(self):
        assert _cadence("[Timer]\n", _Fake()) is None

    def test_missing_binary_warns_once(self):
        """Losing the cadence math regresses every slow timer back to
        unjudgeable — the first failure must be loud, and only the first
        (a per-tick per-timer warning would drown the journal it protects)."""
        from utils import timer_cadence
        fake = _Fake(exc=FileNotFoundError("nope"))
        with patch("utils.timer_cadence.subprocess.run", side_effect=fake), \
                patch.object(timer_cadence.logger, "warning") as warn:
            timer_cadence.cadence_from_spec_text(DAILY)
            timer_cadence.cadence_from_spec_text(TWICE_DAILY)
        assert warn.call_count == 1, warn.call_args_list


class TestMemo:

    def test_repeated_lookups_do_not_re_shell(self):
        fake = _Fake(calendar=_calendar_out(
            "2026-08-13 16:30:00", "2026-08-13 16:40:00"))
        from utils.timer_cadence import cadence_from_spec_text
        with patch("utils.timer_cadence.subprocess.run", side_effect=fake):
            for _ in range(5):
                assert cadence_from_spec_text(TRACER) == 600.0
        assert len(fake.calls) == 1

    def test_an_edited_unit_is_not_answered_from_the_memo(self):
        """The key is the spec TEXT, so changing the schedule changes the key.
        Keying on the unit NAME would serve a stale cadence forever."""
        fake = _Fake(calendar=_calendar_out(
            "2026-08-13 16:30:00", "2026-08-13 16:40:00"))
        from utils.timer_cadence import cadence_from_spec_text
        with patch("utils.timer_cadence.subprocess.run", side_effect=fake):
            cadence_from_spec_text(TRACER)
            cadence_from_spec_text(TRACER + "# edited\n")
        assert len(fake.calls) == 2


class TestUtcTimezoneBox:
    """2026-08-13 review: when the SYSTEM timezone is UTC, ``systemd-analyze
    calendar`` prints NO ``(in UTC):`` line — the elapse lines ARE the UTC
    form (verified live: ``TZ=UTC0 systemd-analyze calendar daily``). The
    original regex required that prefix, so a UTC-configured box silently
    lost every OnCalendar cadence: rc 0, nothing parsed, None memoised, no
    warning. Latent on this all-HST fleet; real for the standalone offering.
    """

    def test_utc_shaped_output_without_in_utc_lines_still_parses(self):
        out = ("  Original form: daily\n"
               "Normalized form: *-*-* 00:00:00\n"
               "    Next elapse: Fri 2026-08-14 00:00:00 UTC\n"
               "       From now: 2h 16min left\n"
               "   Iteration #2: Sat 2026-08-15 00:00:00 UTC\n"
               "       From now: 1 day 2h left\n")
        fake = _Fake(calendar=out)
        assert _cadence("[Timer]\nOnCalendar=daily\n", fake) == 86400.0

    def test_analyze_is_forced_to_utc(self):
        """The parse depends on UTC-stamped output, so the subprocess must
        not inherit the box's timezone — otherwise the fix only works where
        it isn't needed."""
        envs = []

        def spy(argv, **kw):
            envs.append(kw.get("env"))
            return _Fake(calendar=_calendar_out(
                "2026-08-13 16:30:00", "2026-08-13 16:40:00"))(argv, **kw)

        from utils.timer_cadence import cadence_from_spec_text
        with patch("utils.timer_cadence.subprocess.run", side_effect=spy):
            assert cadence_from_spec_text(TRACER) == 600.0
        assert envs and all(e is not None and e.get("TZ") == "UTC"
                            for e in envs)

    def test_mixed_shape_output_does_not_double_count(self):
        """Local-TZ output carries BOTH a local elapse line and an (in UTC)
        line per elapse; only the UTC one may be counted, or the gap list
        grows zero-width entries from the duplicates."""
        fake = _Fake(calendar=_calendar_out(
            "2026-08-13 16:30:00", "2026-08-13 16:40:00"))
        assert _cadence(TRACER, fake) == 600.0


class TestTransientFailureIsNotMemoised:
    """2026-08-13 review: the memo's contract is "an edited unit cannot be
    answered from a stale entry" — but a TRANSIENT analyze failure (timeout,
    OSError, missing binary) used to be memoised as the spec's answer, pinning
    that spec to the flat floor for the process lifetime. Transient means the
    BOX could not answer; that says nothing about the spec."""

    def test_timeout_then_recovery_re_asks_and_heals(self):
        from utils.timer_cadence import cadence_from_spec_text
        good = _Fake(calendar=_calendar_out(
            "2026-08-13 16:30:00", "2026-08-13 16:40:00"))
        calls = []

        def flaky(argv, **kw):
            calls.append(argv)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired("systemd-analyze", 10)
            return good(argv, **kw)

        with patch("utils.timer_cadence.subprocess.run", side_effect=flaky):
            assert cadence_from_spec_text(TRACER) is None   # honest fallback
            assert cadence_from_spec_text(TRACER) == 600.0  # healed next ask

    def test_deterministic_rejection_IS_memoised(self):
        """rc != 0 is systemd rejecting the spec — the same text gets the
        same answer forever, so re-shelling per tick would be pure cost."""
        from utils.timer_cadence import cadence_from_spec_text
        fake = _Fake(calendar="", rc=1)
        bad = "[Timer]\nOnCalendar=not-a-spec\n"
        with patch("utils.timer_cadence.subprocess.run", side_effect=fake):
            assert cadence_from_spec_text(bad) is None
            assert cadence_from_spec_text(bad) is None
        assert len(fake.calls) == 1


class TestRandomizedDelayLastAssignmentWins:

    def test_last_randomized_delay_line_is_the_one_systemd_honours(self):
        """RandomizedDelaySec is single-valued: systemd takes the LAST
        assignment. Honouring the first would add a jitter systemd ignores."""
        text = ("[Timer]\nOnCalendar=daily\n"
                "RandomizedDelaySec=300\nRandomizedDelaySec=600\n")

        def by_spec(argv, **kw):
            if argv[1] == "calendar":
                return _Fake(calendar=_calendar_out(
                    "2026-08-14 13:17:00", "2026-08-15 13:17:00"))(argv, **kw)
            micros = {"300": 300_000_000, "600": 600_000_000}[argv[-1]]
            return _Fake(timespan=_timespan_out(micros))(argv, **kw)

        from utils.timer_cadence import cadence_from_spec_text
        with patch("utils.timer_cadence.subprocess.run", side_effect=by_spec):
            assert cadence_from_spec_text(text) == 86400.0 + 600.0


class TestFileReaders:

    def test_unreadable_timer_file_is_none(self, tmp_path):
        from utils.timer_cadence import timer_cadence_s
        assert timer_cadence_s(str(tmp_path / "nope.timer")) is None

    def test_timer_cadences_scans_every_wants_dir(self, tmp_path):
        """Must agree with user_units.enabled_user_timers on WHICH timers
        exist, including the first-dir-wins precedence — a cadence map that
        misses a timer silently leaves it on the flat floor."""
        from utils.timer_cadence import timer_cadences
        a = tmp_path / "timers.target.wants"
        b = tmp_path / "default.target.wants"
        a.mkdir()
        b.mkdir()
        (a / "one.timer").write_text(MONOTONIC)
        (b / "two.timer").write_text(BOOT_ONLY)
        (b / "one.timer").write_text(BOOT_ONLY)     # loses to a/
        fake = _Fake(timespan=_timespan_out(300_000_000))
        with patch("utils.timer_cadence.subprocess.run", side_effect=fake):
            got = timer_cadences([str(a), str(b)])
        assert got == {"one.timer": 300.0, "two.timer": None}

    def test_missing_wants_dir_is_skipped_not_fatal(self, tmp_path):
        from utils.timer_cadence import timer_cadences
        assert timer_cadences([str(tmp_path / "gone")]) == {}

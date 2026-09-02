"""Absence, blindness and silence are three different claims.

Regression cover for the 2026-09-02 defect in scripts/nomadnet_silence_watch.py:
the remote probe ended `|| echo 0`, so a box with NO NomadNet logfile reported
`now - 0` — seconds since the epoch. Four fleet boxes read as
"quiet (29,806,174 min)" — 56.7 years — and latched into the alarm state
permanently, which also meant a REAL silence on those boxes could never fire a
transition again, because the state never changed away from "quiet".

honest_failure_modes #1 (a degraded value mapped into the healthy domain) and
the inert-vs-indeterminate rule: an organ absent BY DESIGN must never be
reported as an observation that failed, or real failures have nowhere to stand
out.
"""
import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "nomadnet_silence_watch.py"


def _load():
    spec = importlib.util.spec_from_file_location("nomadnet_silence_watch", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nsw = _load()
QUIET_S = 60 * 60  # the script's default --quiet-min 60


class TestClassify:
    """The four states must stay distinguishable."""

    def test_absent_logfile_is_not_quiet(self):
        """THE regression. Absence is inert, not silence."""
        assert nsw.classify(nsw.NO_LOGFILE, QUIET_S) == "no-logfile"

    def test_probe_failure_is_not_quiet_and_not_ok(self):
        """Unobservable is never healthy and never silence."""
        assert nsw.classify(None, QUIET_S) == "ssh-fail"

    def test_real_silence_is_quiet(self):
        assert nsw.classify(QUIET_S + 1, QUIET_S) == "quiet"

    def test_recent_activity_is_ok(self):
        assert nsw.classify(60, QUIET_S) == "ok"

    def test_boundary_is_not_quiet(self):
        assert nsw.classify(QUIET_S, QUIET_S) == "ok"

    def test_future_mtime_is_clock_skew_not_ok(self):
        """RTC-less Pis restore stale time; a future mtime must not read healthy."""
        assert nsw.classify(-500, QUIET_S) == "clock-skew"

    def test_epoch_zero_age_would_have_read_as_quiet(self):
        """Pin the SHAPE of the old bug: had the sentinel stayed numeric, the
        age was ~1.79e9 s and classify() would call it real silence."""
        assert nsw.classify(1_788_000_000, QUIET_S) == "quiet"
        # ...which is exactly why the sentinel must not be a number.
        assert not isinstance(nsw.NO_LOGFILE, int)


class TestProbe:
    """probe() must return three distinct answers, not two."""

    def _run(self, stdout):
        cp = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
        with patch.object(nsw.subprocess, "run", return_value=cp):
            return nsw.probe("box")

    def test_nolog_token_becomes_sentinel(self):
        assert self._run("NOLOG\n") == nsw.NO_LOGFILE

    def test_numeric_becomes_int(self):
        assert self._run("4242\n") == 4242

    def test_negative_age_survives_as_int(self):
        """Do not clamp — clock skew must reach classify() to be named."""
        assert self._run("-90\n") == -90

    def test_timeout_is_none(self):
        with patch.object(nsw.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("ssh", 15)):
            assert nsw.probe("box") is None

    def test_unparseable_output_is_none(self):
        assert self._run("") is None


class TestRemoteCommandContract:
    """Guard the wire text itself — the defect lived there, not in the logic."""

    def _cmd(self):
        cp = subprocess.CompletedProcess(args=[], returncode=0, stdout="1\n", stderr="")
        with patch.object(nsw.subprocess, "run", return_value=cp) as m:
            nsw.probe("box")
        return m.call_args[0][0][-1]

    def test_no_numeric_fallback_for_missing_file(self):
        cmd = self._cmd()
        assert "|| echo 0" not in cmd, (
            "the absent-logfile fallback must not be a number — that is the "
            "2026-09-02 epoch-0 defect"
        )

    def test_emits_the_nolog_token(self):
        assert nsw._NOLOG_TOKEN in self._cmd()

    def test_guards_existence_before_stat(self):
        assert "-e" in self._cmd()


class TestHonestyOfLabels:
    """A sentinel is not a duration."""

    @pytest.mark.parametrize("age", [nsw.NO_LOGFILE, None])
    def test_sentinels_are_not_integer_ages(self, age):
        assert not (isinstance(age, int) and not isinstance(age, bool))

"""probe_claw_watched_node_silent — the watch list's consumer.

The behaviours pinned here are the JUDGMENT calls, not the plumbing:

* it fires on the gated ``silent`` verdict and NEVER on raw ``never`` (the
  reboot-page defect the uptime gate exists to prevent, one layer up)
* HEARD on any claw outranks SILENT on another (positive physical evidence)
* one blind claw must NOT mask a qualified finding from another (the
  silence-manufacturing direction, which is the one that must never happen)
* ``unobservable`` never reads as healthy AND never reads as a finding — and
  an all-blind tick resolves ``indeterminate``, so the tracker HOLDS instead of
  clearing a real signal when a claw reboots (honest_failure_modes #2)
* an unrecognised verdict string is blindness, not an answer (closed-enum, #7)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.watchdog_probe_core import (  # noqa: E402
    SIGNAL_CLASSES,
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes_claw_watch import (  # noqa: E402
    _fold_watch_verdicts,
    probe_claw_watched_node_silent,
)

WINDOW = 3 * 3 * 3600.0


def _verdict(kind, *, age_s=None, held=None, need=WINDOW, reason="test"):
    return {"verdict": kind, "age_s": age_s, "silent_for_at_least_s": held,
            "required_window_s": need, "reason": reason}


def _tick(device="dudeclaw-01", verdicts=None, reachable=True):
    return {"device": device, "reachable": reachable,
            "watch_verdicts": verdicts}


class _Base:
    @pytest.fixture(autouse=True)
    def _state(self, tmp_path):
        self.sp = str(tmp_path / "watch_debounce.json")
        reset_dispositions()

    def _fire(self, ticks, *, debounce=1):
        """Run the probe past its debounce and return the Signal (or None)."""
        sig = None
        for _ in range(debounce):
            sig = probe_claw_watched_node_silent(
                ticks=ticks, now=1.0, state_path=self.sp,
                debounce_ticks=debounce)
        return sig

    def _disp(self):
        return collect_dispositions()["claw_watched_node_silent"]


class TestRegistration(_Base):
    def test_class_is_registered(self):
        assert "claw_watched_node_silent" in SIGNAL_CLASSES


class TestNeverIsNotSilent(_Base):
    """THE point of the probe: `never` inside the window is not a finding."""

    def test_unobservable_never_fires(self):
        """A freshly-rebooted claw reports `never` for every watched node. That
        must produce NO signal — the defect that would page on every flash."""
        ticks = [_tick(verdicts={
            "!32962f10": _verdict("unobservable", held=842.0),
            "!a1b2c3d4": _verdict("unobservable", held=842.0),
        })]
        assert self._fire(ticks, debounce=1) is None
        assert self._fire(ticks, debounce=2) is None

    def test_all_blind_is_indeterminate_not_clean(self):
        """Held, not cleared: the tracker may only clear on a positive
        observation, so an all-blind tick must not heal a real finding."""
        self._fire([_tick(verdicts={"!32962f10": _verdict("unobservable")})])
        d = self._disp()
        assert d["disp"] == "indeterminate"
        assert "listening window" in d["reason"]

    def test_qualified_silent_does_fire(self):
        """Same node, same `never` underneath — but the window elapsed."""
        ticks = [_tick(verdicts={"!32962f10": _verdict("silent", held=40000.0)})]
        sig = self._fire(ticks, debounce=1)
        assert sig is not None
        assert sig.cls == "claw_watched_node_silent"
        assert sig.subject == "!32962f10"
        assert sig.severity == "degraded"

    def test_signal_states_the_threshold_is_provisional(self):
        """Escalate-only discipline has to travel WITH the claim, or the next
        reader promotes an unmeasured threshold to a pager."""
        sig = self._fire(
            [_tick(verdicts={"!32962f10": _verdict("silent", held=40000.0)})])
        assert sig.extra["threshold_provisional"] is True
        assert "PROVISIONAL" in sig.detail


class TestCrossClawArbitration(_Base):
    def test_heard_on_any_claw_outranks_silent_on_another(self):
        """One deaf receiver is that receiver's problem. Hearing is positive
        physical evidence about the transmitter and settles the question."""
        ticks = [
            _tick("dudeclaw-01", {"!32962f10": _verdict("silent", held=40000.0)}),
            _tick("dudeclaw-02", {"!32962f10": _verdict("heard", age_s=12.0)}),
        ]
        assert self._fire(ticks, debounce=1) is None
        assert self._disp()["disp"] == "clean"

    def test_one_blind_claw_does_not_mask_a_qualified_finding(self):
        """The silence-manufacturing direction. Requiring unanimity would let a
        single rebooted claw suppress a finding the other has fully qualified."""
        ticks = [
            _tick("dudeclaw-01", {"!32962f10": _verdict("silent", held=40000.0)}),
            _tick("dudeclaw-02", {"!32962f10": _verdict("unobservable", held=60.0)}),
        ]
        sig = self._fire(ticks, debounce=1)
        assert sig is not None
        assert sig.subject == "!32962f10"

    def test_longest_silence_is_the_reported_lower_bound(self):
        ticks = [
            _tick("dudeclaw-01", {"!aa": _verdict("silent", held=40000.0)}),
            _tick("dudeclaw-02", {"!aa": _verdict("silent", held=95000.0)}),
        ]
        sig = self._fire(ticks, debounce=1)
        assert sig.extra["silent_nodes"]["!aa"]["silent_for_at_least_s"] == 95000.0

    def test_widest_window_wins_when_claws_disagree(self):
        """Two claws disagreeing on a node's interval must clear the STRICTER
        bar — the shared-constant drift shape (honest_failure_modes #5)."""
        nodes, _ = _fold_watch_verdicts([
            _tick("dudeclaw-01", {"!aa": _verdict("silent", held=40000.0,
                                                  need=32400.0)}),
            _tick("dudeclaw-02", {"!aa": _verdict("silent", held=40000.0,
                                                  need=86400.0)}),
        ])
        assert nodes["!aa"]["required_window_s"] == 86400.0

    def test_dark_claw_says_nothing_about_a_transmitter(self):
        """An unreachable claw is claw_device_dark's call; its stale verdicts
        must not be counted as a receiver that listened."""
        ticks = [_tick("dudeclaw-02", {"!aa": _verdict("silent", held=40000.0)},
                       reachable=False)]
        assert self._fire(ticks, debounce=1) is None
        assert self._disp()["disp"] == "indeterminate"


class TestBlindStaysInItsOwnColumn(_Base):
    def test_clean_reason_reports_blind_separately(self):
        """The gate's whole purpose: 'we have not looked long enough' is never
        averaged into a reassuring number."""
        self._fire([_tick(verdicts={
            "!heardone": _verdict("heard", age_s=5.0),
            "!blindone": _verdict("unobservable", held=100.0),
        })])
        d = self._disp()
        assert d["disp"] == "clean"
        assert "1/2" in d["reason"]
        assert "NOT counted as healthy" in d["reason"]
        assert "!blindone" in d["reason"]

    def test_signal_carries_all_three_columns(self):
        sig = self._fire([_tick(verdicts={
            "!silentone": _verdict("silent", held=40000.0),
            "!heardone": _verdict("heard", age_s=5.0),
            "!blindone": _verdict("unobservable", held=100.0),
        })])
        assert list(sig.extra["silent_nodes"]) == ["!silentone"]
        assert sig.extra["heard_nodes"] == ["!heardone"]
        assert sig.extra["unobservable_nodes"] == ["!blindone"]

    def test_unrecognised_verdict_is_blindness_not_an_answer(self):
        """Closed-enum consumer: a vocabulary that grew must not be read as
        healthy (and must not fire either)."""
        assert self._fire([_tick(verdicts={
            "!aa": _verdict("some_future_verdict")})], debounce=1) is None
        assert self._disp()["disp"] == "indeterminate"


class TestSelfGuards(_Base):
    def test_no_tick_files_is_inert(self):
        assert probe_claw_watched_node_silent(
            ticks=[], now=1.0, state_path=self.sp) is None
        assert self._disp()["disp"] == "inert"

    def test_no_watch_verdicts_is_indeterminate_not_clean(self):
        """Old firmware / no ids configured. 'Nobody is watching' must never
        read as 'nothing is wrong'."""
        assert self._fire([_tick(verdicts=None)], debounce=1) is None
        d = self._disp()
        assert d["disp"] == "indeterminate"
        assert "nobody watching is not nothing wrong" in d["reason"]

    def test_debounce_holds_one_tick(self):
        ticks = [_tick(verdicts={"!aa": _verdict("silent", held=40000.0)})]
        assert probe_claw_watched_node_silent(
            ticks=ticks, now=1.0, state_path=self.sp, debounce_ticks=2) is None
        assert "debounce 1/2" in self._disp()["reason"]
        sig = probe_claw_watched_node_silent(
            ticks=ticks, now=2.0, state_path=self.sp, debounce_ticks=2)
        assert sig is not None

    def test_recovery_resets_the_streak(self):
        silent = [_tick(verdicts={"!aa": _verdict("silent", held=40000.0)})]
        heard = [_tick(verdicts={"!aa": _verdict("heard", age_s=3.0)})]
        assert probe_claw_watched_node_silent(
            ticks=silent, now=1.0, state_path=self.sp, debounce_ticks=2) is None
        assert probe_claw_watched_node_silent(
            ticks=heard, now=2.0, state_path=self.sp, debounce_ticks=2) is None
        # streak was reset by the clean tick, so this is 1/2 again, not 2/2
        assert probe_claw_watched_node_silent(
            ticks=silent, now=3.0, state_path=self.sp, debounce_ticks=2) is None

    def test_malformed_verdict_entry_does_not_raise(self):
        assert probe_claw_watched_node_silent(
            ticks=[_tick(verdicts={"!aa": "not-a-dict"})], now=1.0,
            state_path=self.sp) is None

    def test_subject_names_the_count_when_several_are_silent(self):
        sig = self._fire([_tick(verdicts={
            "!aa": _verdict("silent", held=40000.0),
            "!bb": _verdict("silent", held=40000.0),
        })])
        assert sig.subject == "2 watched nodes"

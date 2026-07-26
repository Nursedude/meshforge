"""Pri-2 (2026-07-26): an unobserved class must HOLD its signals, never CLEAR them.

The defect this pins, answered by reading `SignalTracker.update`: the tracker
diffed purely on signal presence

    current_keys = {s.key() for s in current}
    newly_cleared = list(previous_keys - current_keys)

and never saw the disposition map, so a probe that returned ``None`` because it
COULD NOT OBSERVE was byte-identical to one that returned ``None`` because
everything was fine. Every probe carrying an ``indeterminate`` self-guard (~50
of them share that documented contract) could therefore emit a false CLEARED
the moment its observation channel failed.

Live instance that queued this: `rules_seed_drift` logged `signal CLEARED` at
18:22:24 on boot c9e16aae — six seconds after systemd-oomd killed 49 processes
— on a box where the drift was real, uninterrupted, and re-fired next boot.

The cure is honest_failure_modes #2 (never emit a recovery from data missing
because the observation channel failed) applied at the layer that owns the
transition: only a POSITIVE observation may clear. This is the same shape as
the 2026-07-25 rtun watchdog fix (unobservable HOLDS instead of counting as a
confirmed death) — the CHANNEL field of the second-brain taxonomy.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.watchdog_probe_core import Signal  # noqa: E402
from utils.watchdog_runner import SignalTracker, observed_only  # noqa: E402


CLS = "main_thread_wedge"
SUBJ = "x"


def _sig(cls: str = CLS, subject: str = SUBJ) -> Signal:
    return Signal(cls=cls, subject=subject, severity="wedge", detail="d")


def _cov(disp: str, cls: str = CLS) -> dict:
    return {cls: {"disp": disp}}


class TestUnobservedHoldsInsteadOfClearing:
    """The core contract: only a positive observation may clear a signal."""

    @pytest.mark.parametrize("disp", ["indeterminate", "unknown"])
    def test_unobserved_disposition_holds_signal(self, disp):
        tracker = SignalTracker()
        tracker.update([_sig()], now=100.0, coverage=_cov("active"))

        active, cleared, held = tracker.update(
            [], now=130.0, coverage=_cov(disp),
        )

        assert cleared == [], f"{disp} must not clear a still-true signal"
        assert held == [(CLS, SUBJ)]
        # Held, therefore still emitted — see
        # test_held_signal_is_re_emitted_so_it_survives_into_the_state_file.
        assert [s.key() for s, _ in active] == [(CLS, SUBJ)]
        assert active[0][0].extra.get("unobserved_hold") is True

    @pytest.mark.parametrize("disp", ["clean", "inert", "active"])
    def test_observed_disposition_still_clears(self, disp):
        """The fix must not break real recoveries — a probe that RAN and saw
        nothing is exactly what CLEARED is for."""
        tracker = SignalTracker()
        tracker.update([_sig()], now=100.0, coverage=_cov("active"))

        active, cleared, held = tracker.update(
            [], now=130.0, coverage=_cov(disp),
        )

        assert cleared == [(CLS, SUBJ)]
        assert held == []

    def test_class_missing_from_coverage_map_holds(self):
        """A class absent from the map is unobserved, not healthy.

        build_coverage emits an entry for every member of the closed enum, so
        this should be unreachable in production — which is exactly why it must
        fail safe rather than silently clear if the map ever goes partial.
        """
        tracker = SignalTracker()
        tracker.update([_sig()], now=100.0, coverage=_cov("active"))

        _active, cleared, held = tracker.update([], now=130.0, coverage={})

        assert cleared == []
        assert held == [(CLS, SUBJ)]

    def test_coverage_none_holds_everything(self):
        """Probe dispatch crashed mid-tick.

        The runner sets `signals = []` and `coverage = None` in that branch, so
        before this fix a single probe-dispatch exception cleared EVERY active
        signal on the box at once — the same defect at its widest blast radius.
        """
        tracker = SignalTracker()
        s1, s2 = _sig(), _sig(cls="service_inactive", subject="rnsd.service")
        tracker.update([s1, s2], now=100.0, coverage=None)

        _active, cleared, held = tracker.update([], now=130.0, coverage=None)

        assert cleared == []
        assert sorted(held) == sorted([(CLS, SUBJ), ("service_inactive", "rnsd.service")])


class TestHoldIsNotAmnesty:
    """A held signal stays real: it must not leak, duplicate, or forget."""

    def test_held_signal_clears_once_observation_recovers(self):
        tracker = SignalTracker()
        tracker.update([_sig()], now=100.0, coverage=_cov("active"))
        tracker.update([], now=130.0, coverage=_cov("indeterminate"))

        _active, cleared, held = tracker.update(
            [], now=160.0, coverage=_cov("clean"),
        )

        assert cleared == [(CLS, SUBJ)], "recovery must still be reportable"
        assert held == []

    def test_held_signal_preserves_first_seen_when_it_reappears(self):
        """Blindness must not reset the age of an ongoing condition — a wedge
        that has been true for an hour must not read as brand new because the
        channel blinked."""
        tracker = SignalTracker()
        tracker.update([_sig()], now=100.0, coverage=_cov("active"))
        tracker.update([], now=130.0, coverage=_cov("indeterminate"))

        active, cleared, _held = tracker.update(
            [_sig()], now=160.0, coverage=_cov("active"),
        )

        assert cleared == []
        assert len(active) == 1
        assert active[0][1] == 100.0, "first_seen must survive the blind window"

    def test_held_signal_is_re_emitted_so_it_survives_into_the_state_file(self):
        """THE test — the one the first cut of this fix got wrong.

        `write_state` serialises `signals` straight from the returned active
        list. A held key that is only kept in the tracker's internal table
        still DISAPPEARS from watchdog.json — and mini, fleet_truth and the
        /fleet page all read that disappearance as recovery. Suppressing the
        watchdog's own CLEARED log while letting the state file drop the
        signal fixes the symptom in the one place nobody consumes.
        """
        tracker = SignalTracker()
        tracker.update([_sig()], now=100.0, coverage=_cov("active"))

        active, _cleared, _held = tracker.update(
            [], now=130.0, coverage=_cov("indeterminate"),
        )

        assert len(active) == 1, "a held signal must still be written out"
        sig, first_seen = active[0]
        assert sig.key() == (CLS, SUBJ)
        assert first_seen == 100.0, "not re-announced as new"
        assert sig.extra.get("unobserved_hold") is True, (
            "a consumer must be able to tell 'still true' from "
            "'last known, currently blind'"
        )

    def test_hold_marker_does_not_leak_into_the_live_signal(self):
        """The marker is added to a COPY; a probe's own Signal is untouched,
        and the marker disappears the moment observation resumes."""
        live = _sig()
        tracker = SignalTracker()
        tracker.update([live], now=100.0, coverage=_cov("active"))
        tracker.update([], now=130.0, coverage=_cov("indeterminate"))

        assert "unobserved_hold" not in (live.extra or {})

        active, _c, _h = tracker.update(
            [_sig()], now=160.0, coverage=_cov("active"))
        assert active[0][0].extra.get("unobserved_hold") is None

    def test_held_wedge_keeps_the_box_not_ok(self):
        """`write_state` derives `ok` from the emitted signals, so a held wedge
        must keep ok=False — blindness is not a recovery at that layer either."""
        tracker = SignalTracker()
        tracker.update([_sig()], now=100.0, coverage=_cov("active"))
        active, _c, _h = tracker.update(
            [], now=130.0, coverage=_cov("indeterminate"))

        assert any(s.severity == "wedge" for s, _ in active)

    def test_held_list_is_per_tick_not_cumulative(self):
        tracker = SignalTracker()
        tracker.update([_sig()], now=100.0, coverage=_cov("active"))
        tracker.update([], now=130.0, coverage=_cov("indeterminate"))

        _active, _cleared, held = tracker.update(
            [_sig()], now=160.0, coverage=_cov("active"),
        )

        assert held == [], "a re-observed signal is not held"

    def test_sustained_blindness_reports_the_hold_once_but_keeps_holding(self):
        """A permanently-blind probe must not log a WARNING every tick forever.

        The hold is an EDGE like the other two transitions; the standing state
        stays visible as the class's `indeterminate` coverage entry. Without
        this the fix would trade a false CLEARED for a per-tick alert storm —
        the exact volume problem the 2026-07-26 alerting arc was cutting.
        """
        tracker = SignalTracker()
        tracker.update([_sig()], now=100.0, coverage=_cov("active"))

        _a, cleared1, held1 = tracker.update(
            [], now=130.0, coverage=_cov("indeterminate"))
        _a, cleared2, held2 = tracker.update(
            [], now=160.0, coverage=_cov("indeterminate"))
        _a, cleared3, held3 = tracker.update(
            [], now=190.0, coverage=_cov("indeterminate"))

        assert held1 == [(CLS, SUBJ)], "the hold announces itself once"
        assert held2 == [] and held3 == [], "and then stays quiet"
        # ...but it is STILL held: blindness never clears the signal.
        assert cleared1 == [] and cleared2 == [] and cleared3 == []

        _a, cleared4, _h = tracker.update([], now=220.0, coverage=_cov("clean"))
        assert cleared4 == [(CLS, SUBJ)], "and clears when sight returns"

    def test_hold_re_announces_after_an_intervening_observation(self):
        """Blind -> seen -> blind is two distinct incidents, not one."""
        tracker = SignalTracker()
        tracker.update([_sig()], now=100.0, coverage=_cov("active"))
        _a, _c, held1 = tracker.update(
            [], now=130.0, coverage=_cov("indeterminate"))
        # Signal observed again (still failing), then the channel drops again.
        tracker.update([_sig()], now=160.0, coverage=_cov("active"))
        _a, _c, held2 = tracker.update(
            [], now=190.0, coverage=_cov("indeterminate"))

        assert held1 == [(CLS, SUBJ)]
        assert held2 == [(CLS, SUBJ)]

    def test_unrelated_class_blindness_does_not_hold_a_real_recovery(self):
        """Dispositions are per-class: one blind probe must not freeze the
        whole board."""
        tracker = SignalTracker()
        s1 = _sig()
        s2 = _sig(cls="service_inactive", subject="rnsd.service")
        tracker.update([s1, s2], now=100.0, coverage={
            CLS: {"disp": "active"}, "service_inactive": {"disp": "active"},
        })

        _active, cleared, held = tracker.update([], now=130.0, coverage={
            CLS: {"disp": "indeterminate"}, "service_inactive": {"disp": "clean"},
        })

        assert cleared == [("service_inactive", "rnsd.service")]
        assert held == [(CLS, SUBJ)]


class TestReportButDoNotAct:
    """Phase-2 auto-restart must not fire on a signal we cannot currently see.

    Phase 2 ships disabled, so this is latent today — which is precisely why it
    is pinned now: the hold made held signals reach the decider for the first
    time, and a latent footgun armed by a fix is still armed.
    """

    def test_held_signals_are_excluded_from_restart_decisions(self):
        tracker = SignalTracker()
        live = _sig(cls="service_inactive", subject="rnsd.service")
        tracker.update([_sig(), live], now=100.0, coverage={
            CLS: {"disp": "active"}, "service_inactive": {"disp": "active"},
        })
        active, _c, _h = tracker.update([live], now=130.0, coverage={
            CLS: {"disp": "indeterminate"}, "service_inactive": {"disp": "active"},
        })

        assert len(active) == 2, "both are reported"
        actionable = observed_only(active)
        assert [s.key() for s, _ in actionable] == [("service_inactive", "rnsd.service")]

    def test_observed_only_passes_through_normal_signals(self):
        pairs = [(_sig(), 100.0)]
        assert observed_only(pairs) == pairs

    def test_observed_only_tolerates_empty_extra(self):
        sig = Signal(cls=CLS, subject=SUBJ, severity="wedge", detail="d")
        assert observed_only([(sig, 1.0)]) == [(sig, 1.0)]


class TestCoverageIsRequired:
    """honest_failure_modes #7 — a closed contract needs closed consumers.

    `coverage` is a REQUIRED keyword so that every call site must answer the
    question rather than silently inherit the old clear-on-absence semantics.
    """

    def test_update_requires_coverage_keyword(self):
        tracker = SignalTracker()
        with pytest.raises(TypeError):
            tracker.update([_sig()], now=100.0)

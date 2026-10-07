"""A blind PEER leg must not hold a LOCAL cron verdict that re-ran OK.

Measured 2026-10-06 (review_provenance QUEUED 10-06): alaula moved →
``router_scout`` FAIL 12:07/12:37 → ``router_scout`` OK 13:07 → peers'
``fleet_hosts_drift`` went unconfirmed (peer leg ``indeterminate``) → the
class read ``indeterminate`` by worst-wins → the tracker HELD the local
``(cron_verdict_stale, cron)`` signal at its last verdict → mini paged
"1 failing: router_scout(FAIL(1))" at 13:24, 17 min after it passed.

Dispositions are per CLASS; one class here has two legs (local subject
``cron``, peer subjects ``<alias>``). A positive observation of one subject
is an observation, whatever its sibling leg could see.
"""
from __future__ import annotations

import json

from utils.watchdog_probe_core import (
    Signal, note_disposition, note_subject_observed, reset_dispositions,
)
from utils.watchdog_probes_cron import probe_cron_verdict_stale
from utils.watchdog_probes_peer_cron import probe_peer_cron_verdict_stale
from utils.watchdog_runner import build_coverage
from utils.watchdog_tracker import SignalTracker

CLS = "cron_verdict_stale"
NOW = 1_800_000_000.0
CRONTAB = "7,37 * * * * /home/op/bin/cron_verdict.sh router_scout -- true\n"


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _verdicts(*rows) -> str:
    return "".join(f"{_iso(ts)} router_scout {st} x\n" for ts, st in rows)


def _local(verdicts: str, tmp_path, **kw):
    return probe_cron_verdict_stale(
        operator=(1000, "op"), crontab_text=CRONTAB, verdicts_text=verdicts,
        now=NOW, state_path=str(tmp_path / "streak.json"), **kw)


def _blind_peer_spool(tmp_path):
    d = tmp_path / "spool"
    d.mkdir(exist_ok=True)
    (d / "lehua.json").write_text(json.dumps({"fetched_at": 0}))
    return d


def _held_local_signal() -> Signal:
    return Signal(cls=CLS, subject="cron", severity="degraded",
                  detail="Cron(s) unhealthy — 1 failing: router_scout(FAIL(1))",
                  extra={"failed": ["router_scout(FAIL(1))"]})


class TestTrackerHonoursObservedSubjects:
    def test_observed_subject_clears_while_class_indeterminate(self):
        tracker = SignalTracker()
        local = _held_local_signal()
        peer = Signal(cls=CLS, subject="lehua", severity="degraded", detail="x")
        tracker.update([local, peer], now=100.0,
                       coverage={CLS: {"disp": "active"}})
        cov = {CLS: {"disp": "indeterminate", "reason": "peer blind",
                     "observed_subjects": ["cron"]}}
        active, cleared, held = tracker.update([], now=200.0, coverage=cov)
        assert (CLS, "cron") in cleared
        assert (CLS, "lehua") in held          # unseen peer still holds
        assert [s.subject for s, _ in active] == ["lehua"]

    def test_malformed_observed_subjects_is_not_an_observation(self):
        tracker = SignalTracker()
        tracker.update([_held_local_signal()], now=100.0,
                       coverage={CLS: {"disp": "active"}})
        cov = {CLS: {"disp": "indeterminate", "observed_subjects": "cron"}}
        _a, cleared, held = tracker.update([], now=200.0, coverage=cov)
        assert cleared == [] and (CLS, "cron") in held


class TestBuildCoverageCarriesObservedSubjects:
    def test_attached_only_when_class_otherwise_blind(self):
        reset_dispositions()
        note_disposition(CLS, "indeterminate", reason="peer blind")
        note_subject_observed(CLS, "cron")
        assert build_coverage([])[CLS]["observed_subjects"] == ["cron"]

    def test_not_attached_to_a_clean_class(self):
        reset_dispositions()
        note_disposition(CLS, "clean")
        note_subject_observed(CLS, "cron")
        assert build_coverage([])[CLS] == {"disp": "clean"}

    def test_reset_clears_observed_subjects(self):
        reset_dispositions()
        note_subject_observed(CLS, "cron")
        reset_dispositions()
        note_disposition(CLS, "indeterminate")
        assert "observed_subjects" not in build_coverage([])[CLS]


class TestTheOctober6Chain:
    def test_recovered_local_verdict_clears_despite_blind_peer(self, tmp_path):
        tracker = SignalTracker()
        spool = _blind_peer_spool(tmp_path)
        failing = _verdicts((NOW - 3600, "FAIL(1)"), (NOW - 1800, "FAIL(1)"))

        # Tick 1+2: local FAIL confirmed (2-tick debounce) → signal active.
        for t in (1, 2):
            reset_dispositions()
            sigs = [s for s in [_local(failing, tmp_path)] if s]
            sigs += probe_peer_cron_verdict_stale(now=NOW, spool_dir=spool)
            tracker.update(sigs, now=NOW + t, coverage=build_coverage(sigs))
        assert sigs and sigs[0].subject == "cron"

        # Tick 3: router_scout re-ran OK; the peer leg is still blind.
        recovered = failing + _verdicts((NOW - 60, "OK"))
        reset_dispositions()
        sigs = [s for s in [_local(recovered, tmp_path)] if s]
        sigs += probe_peer_cron_verdict_stale(now=NOW, spool_dir=spool)
        assert sigs == []
        cov = build_coverage(sigs)
        assert cov[CLS]["disp"] == "indeterminate"   # the peer IS blind
        active, cleared, _held = tracker.update(sigs, now=NOW + 3,
                                                coverage=cov)
        assert (CLS, "cron") in cleared
        assert active == []   # nothing left in watchdog.json for mini to page


class TestLocalProbeNotesOnlyRealObservations:
    def _observed_after(self, verdicts, tmp_path, **kw):
        reset_dispositions()
        note_disposition(CLS, "indeterminate", reason="sibling blind")
        _local(verdicts, tmp_path, **kw)
        return build_coverage([])[CLS].get("observed_subjects", [])

    def test_clean_local_run_is_observed(self, tmp_path):
        assert self._observed_after(_verdicts((NOW - 60, "OK")),
                                    tmp_path) == ["cron"]

    def test_unconfirmed_first_failure_is_not_observed(self, tmp_path):
        v = _verdicts((NOW - 3600, "OK"), (NOW - 60, "FAIL(1)"))
        assert self._observed_after(v, tmp_path) == []

    def test_unreadable_log_is_not_observed(self, tmp_path):
        reset_dispositions()
        note_disposition(CLS, "indeterminate", reason="sibling blind")
        probe_cron_verdict_stale(
            operator=None, crontab_text=CRONTAB, verdicts_text=None,
            now=NOW, state_path=str(tmp_path / "s.json"))
        assert "observed_subjects" not in build_coverage([])[CLS]

    def test_disposition_sink_does_not_touch_global_recorder(self, tmp_path):
        sink: list = []
        assert self._observed_after(_verdicts((NOW - 60, "OK")), tmp_path,
                                    disposition_sink=sink) == []


class TestPeerLegNotesJudgedHealthyPeers:
    """The same hold, peer to peer: one blind spool held every OTHER peer's
    recovered subject. A peer judged healthy this tick was observed."""

    def test_healthy_peer_observed_blind_peer_not(self, tmp_path, monkeypatch):
        import base64
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
        d = _blind_peer_spool(tmp_path)          # lehua: stale → blind
        crontab = ("41 * * * * /x/cron_verdict.sh fleet_hosts_drift -- true\n")
        verdicts = (f"{_iso(NOW - 120)} fleet_hosts_drift OK fine\n")
        (d / "moc4.json").write_text(json.dumps({
            "fetched_at": NOW - 10,
            "schedules": {
                "crontab_b64": base64.b64encode(crontab.encode()).decode(),
                "verdicts_b64": base64.b64encode(verdicts.encode()).decode(),
            }}))
        reset_dispositions()
        assert probe_peer_cron_verdict_stale(now=NOW, spool_dir=d) == []
        cov = build_coverage([])[CLS]
        assert cov["disp"] == "indeterminate"
        assert cov["observed_subjects"] == ["moc4"]

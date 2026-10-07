"""The 10-06 cron hold had SIBLINGS — every class that merges more than one
subject through ONE worst-wins disposition.

Frontier sibling hunt (review_provenance, QUEUED 2026-10-06). The question
asked of every multi-subject class: *can a disposition about subject A
decide subject B?* Each sibling below was confirmed by this drill against
the pre-fix code (the recovered subject was HELD and re-emitted with
``unobserved_hold`` — the signal mini paged 17 minutes after it passed on
10-06) and cured with the same primitive the cron fix introduced:
``note_subject_observed`` at the probe's positive-observation site.

  service_inactive        the runner loops ``probe_service_inactive(unit)``;
                          one unit's ``systemctl`` timeout noted the CLASS
                          indeterminate and held every sibling unit that had
                          recovered.
  main_thread_wedge       the unit leg and the lxmf process leg share the
                          class; one pid's unreadable task stack — or a DOWN
                          wedge-checked unit, which the presence gate notes
                          indeterminate on EVERY tick — held a wedge already
                          cured by restart.
  tracer_peer_unreachable the 10-06 ``returned`` branch (5c5986a2) notes
                          indeterminate while a returned box awaits its first
                          fire, holding every peer reachable right now.
  cron_verdict_stale      reviewer PLAUSIBLE (b): a truth-spool alias
                          literally ``cron`` observed the LOCAL subject on a
                          peer's behalf.

Every drill runs the REAL probes through the REAL ``build_coverage`` and
``SignalTracker`` — the consumer path to its end state, not the function
that changed.
"""
from __future__ import annotations

import base64
import json
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from utils import fleet_posture as fp
from utils.watchdog_probe_core import (
    Signal, note_disposition, reset_dispositions,
)
from utils.watchdog_probes_peer_cron import probe_peer_cron_verdict_stale
from utils.watchdog_probes_rns import (
    probe_lxmf_process_wedge, probe_main_thread_wedge,
)
from utils.watchdog_probes_service import probe_service_inactive
from utils.watchdog_probes_tracer import probe_tracer_peer_unreachable
from utils.watchdog_runner import build_coverage
from utils.watchdog_tracker import SignalTracker

_HEALTHY_STACK = (
    "[<0>] do_sys_poll+0x3b8/0x540\n"
    "[<0>] __arm64_sys_ppoll+0xb4/0x148\n"
    "[<0>] el0_svc+0x30/0xf8\n"
)


def _tracker_with(*signals: Signal) -> SignalTracker:
    """A tracker that saw ``signals`` active on the previous tick."""
    tracker = SignalTracker()
    cov = {s.cls: {"disp": "active"} for s in signals}
    tracker.update(list(signals), now=1.0, coverage=cov)
    return tracker


def _settle(tracker: SignalTracker, current):
    """One tick through the real consumer path: coverage → tracker."""
    cov = build_coverage(current)
    active, cleared, held = tracker.update(current, now=2.0, coverage=cov)
    return cov, active, cleared, held


# ── service_inactive ───────────────────────────────────────────────────

def _systemctl(timeout_units=(), state="active"):
    def _run(args, **kwargs):
        if any(u in args for u in timeout_units):
            raise subprocess.TimeoutExpired(args, 3)

        class _R:
            stdout = f"{state}\n"
            returncode = 0
        return _R()
    return _run


class TestServiceInactiveSiblingHold:
    CLS = "service_inactive"

    def test_recovered_unit_clears_while_a_sibling_unit_is_unobservable(self):
        tracker = _tracker_with(Signal(
            cls=self.CLS, subject="meshforge-map.service",
            severity="degraded", detail="inactive"))
        reset_dispositions()
        with patch("utils.watchdog_probes_service.subprocess.run",
                   side_effect=_systemctl(timeout_units=("rnsd.service",))):
            current = [s for u in ("rnsd.service", "meshforge-map.service")
                       for s in [probe_service_inactive(u)] if s]
        assert current == []
        cov, active, cleared, _held = _settle(tracker, current)
        assert cov[self.CLS]["disp"] == "indeterminate"   # rnsd IS blind
        assert (self.CLS, "meshforge-map.service") in cleared
        assert active == []   # nothing left in watchdog.json for mini to page

    def test_the_unit_whose_own_systemctl_timed_out_stays_held(self):
        """Control: the hold still protects the subject that was NOT seen."""
        tracker = _tracker_with(Signal(
            cls=self.CLS, subject="rnsd.service",
            severity="degraded", detail="inactive"))
        reset_dispositions()
        with patch("utils.watchdog_probes_service.subprocess.run",
                   side_effect=_systemctl(timeout_units=("rnsd.service",))):
            current = [s for u in ("rnsd.service", "meshforge-map.service")
                       for s in [probe_service_inactive(u)] if s]
        _cov, active, cleared, held = _settle(tracker, current)
        assert cleared == []
        assert (self.CLS, "rnsd.service") in held
        assert [(s.subject, s.extra.get("unobserved_hold"))
                for s, _ in active] == [("rnsd.service", True)]

    def test_a_unit_in_the_wrong_state_is_not_observed_healthy(self):
        """Emitting is not observing-healthy: the key stays via ``current``."""
        reset_dispositions()
        with patch("utils.watchdog_probes_service.subprocess.run",
                   side_effect=_systemctl(state="failed")):
            sig = probe_service_inactive("meshforge-map.service")
        assert sig is not None
        note_disposition(self.CLS, "indeterminate", reason="sibling blind")
        cov = build_coverage([sig])
        assert "observed_subjects" not in cov[self.CLS]


# ── main_thread_wedge ──────────────────────────────────────────────────

def _proc_tree(root: Path, pid: int, cmdline: str = "") -> None:
    task = root / str(pid) / "task" / str(pid)
    task.mkdir(parents=True)
    (task / "stack").write_text(_HEALTHY_STACK)
    if cmdline:
        (root / str(pid) / "cmdline").write_bytes(
            cmdline.replace(" ", "\x00").encode())


class TestMainThreadWedgeSiblingHold:
    CLS = "main_thread_wedge"

    def test_cured_wedge_clears_while_a_sibling_pid_is_unscannable(self, tmp_path):
        _proc_tree(tmp_path, 4242)              # gateway: healthy now
        tracker = _tracker_with(Signal(
            cls=self.CLS, subject="meshforge-gateway.service",
            severity="wedge", detail="wedged"))
        reset_dispositions()
        current = [s for s in (
            probe_main_thread_wedge("meshforge-gateway.service", pid=4242,
                                    proc_root=str(tmp_path)),
            probe_main_thread_wedge("meshforge-map.service", pid=5555,
                                    proc_root=str(tmp_path)),   # no such pid
        ) if s]
        assert current == []
        cov, active, cleared, _held = _settle(tracker, current)
        assert cov[self.CLS]["disp"] == "indeterminate"
        assert (self.CLS, "meshforge-gateway.service") in cleared
        assert active == []

    def test_down_wedge_checked_unit_does_not_hold_a_cured_sibling(self, tmp_path):
        """moc3's shape: a wedge-checked unit that is OFF by design makes the
        presence gate note indeterminate on every tick — forever."""
        _proc_tree(tmp_path, 4242)
        tracker = _tracker_with(Signal(
            cls=self.CLS, subject="meshforge-gateway.service",
            severity="wedge", detail="wedged"))
        reset_dispositions()

        def _show(args, **kwargs):
            class _R:
                stdout = "MainPID=0\nLoadState=loaded\n"
                returncode = 0
            return _R()
        with patch("utils.watchdog_probe_core.subprocess.run",
                   side_effect=_show):
            down = probe_main_thread_wedge("meshforge-map.service",
                                           proc_root=str(tmp_path))
        cured = probe_main_thread_wedge("meshforge-gateway.service", pid=4242,
                                        proc_root=str(tmp_path))
        assert down is None and cured is None
        cov, active, cleared, _held = _settle(tracker, [])
        assert cov[self.CLS]["disp"] == "indeterminate"
        assert (self.CLS, "meshforge-gateway.service") in cleared
        assert active == []

    def test_cured_process_wedge_clears_while_the_unit_leg_is_blind(self, tmp_path):
        """The lxmf leg's subjects are cmdline patterns, not units."""
        _proc_tree(tmp_path, 777, "python3 -m lab.lxmf_echo --x")
        tracker = _tracker_with(Signal(
            cls=self.CLS, subject="lab.lxmf_echo",
            severity="wedge", detail="wedged"))
        reset_dispositions()
        current = list(probe_lxmf_process_wedge(proc_root=str(tmp_path)))
        unit = probe_main_thread_wedge("meshforge-map.service", pid=5555,
                                       proc_root=str(tmp_path))
        assert current == [] and unit is None
        cov, active, cleared, _held = _settle(tracker, current)
        assert cov[self.CLS]["disp"] == "indeterminate"
        assert (self.CLS, "lab.lxmf_echo") in cleared
        assert active == []

    def test_unscannable_pid_is_not_observed(self, tmp_path):
        """Control: the subject whose stack could not be read stays held."""
        tracker = _tracker_with(Signal(
            cls=self.CLS, subject="meshforge-map.service",
            severity="wedge", detail="wedged"))
        reset_dispositions()
        assert probe_main_thread_wedge("meshforge-map.service", pid=5555,
                                       proc_root=str(tmp_path)) is None
        _cov, active, cleared, held = _settle(tracker, [])
        assert cleared == [] and (self.CLS, "meshforge-map.service") in held
        assert [s.extra.get("unobserved_hold") for s, _ in active] == [True]


# ── tracer_peer_unreachable ────────────────────────────────────────────

def _fire(tracer_dir: Path, at: float, results: list) -> None:
    (tracer_dir / f"tracer-{int(at)}.json").write_text(json.dumps({
        "schema_version": 1, "fire_at_unix": at,
        "fire_at_iso": "x", "self_short": "moc1", "results": results}))


def _row(peer: str, result: str) -> dict:
    return {"peer": peer, "seq": 1, "result": result, "rtt_ms": 0}


def _returned_posture(tmp_path: Path, boxes, returned_at: float):
    """A real Posture written the way Resume writes it: declare, then clear."""
    doc = {"boxes": {}}
    for box in boxes:
        doc = fp.declare(doc, box, fp.STATE_DORMANT, returned_at + 3600,
                         now=returned_at - 7200)
        doc, _ = fp.clear(doc, box, now=returned_at)
    p = tmp_path / "posture-returned.json"
    p.write_text(json.dumps(doc))
    return fp.read_posture(str(p), now=returned_at + 60, clock_confident=True)


class TestTracerSiblingHold:
    CLS = "tracer_peer_unreachable"

    def test_reachable_peer_clears_while_a_returned_peer_awaits(self, tmp_path):
        tracer_dir = tmp_path / "tracer"
        tracer_dir.mkdir()
        now = time.time()
        returned = now - 30
        # kiai: only fires from its declared window → returned, awaiting.
        # alpha: a transient blip earlier, reachable on the newest fire.
        for i in range(3):
            _fire(tracer_dir, returned - 600 * (i + 1) + 5,
                  [_row("meshforge-kiai", "no-route"),
                   _row("meshforge-alpha", "timeout" if i == 1 else "ok")])
        _fire(tracer_dir, now - 100, [_row("meshforge-alpha", "ok")])
        tracker = _tracker_with(Signal(
            cls=self.CLS, subject="meshforge-alpha", severity="info",
            detail="1 recent failed fire(s)"))
        reset_dispositions()
        current = probe_tracer_peer_unreachable(
            tracer_dir=tracer_dir, persistent_cycles=3, now=now,
            lookback_s=7200.0,
            posture=_returned_posture(tmp_path, ["kiai"], returned))
        assert current == []
        cov, active, cleared, _held = _settle(tracker, current)
        assert cov[self.CLS]["disp"] == "indeterminate"      # kiai IS unseen
        assert "kiai" in cov[self.CLS]["reason"]
        assert (self.CLS, "meshforge-alpha") in cleared
        assert active == []

    def test_returned_peer_seen_ok_since_return_is_observed(self, tmp_path):
        """The returned branch's own ok path is an observation too."""
        tracer_dir = tmp_path / "tracer"
        tracer_dir.mkdir()
        now = time.time()
        returned = now - 900
        for i in range(3):
            _fire(tracer_dir, returned - 600 * (i + 1) + 5,
                  [_row("meshforge-kiai", "no-route"),
                   _row("meshforge-beta", "no-route")])
        _fire(tracer_dir, now - 100, [_row("meshforge-kiai", "ok")])
        reset_dispositions()
        current = probe_tracer_peer_unreachable(
            tracer_dir=tracer_dir, persistent_cycles=3, now=now,
            lookback_s=7200.0,
            posture=_returned_posture(tmp_path, ["kiai", "beta"], returned))
        assert current == []
        cov = build_coverage(current)[self.CLS]
        assert cov["disp"] == "indeterminate" and "beta" in cov["reason"]
        assert cov["observed_subjects"] == ["meshforge-kiai"]

    def test_awaiting_peer_itself_stays_held(self, tmp_path):
        """Control: kiai's own pre-return signal is NOT cleared by the fix —
        nothing has observed kiai since it returned."""
        tracer_dir = tmp_path / "tracer"
        tracer_dir.mkdir()
        now = time.time()
        returned = now - 30
        for i in range(3):
            _fire(tracer_dir, returned - 600 * (i + 1) + 5,
                  [_row("meshforge-kiai", "no-route")])
        tracker = _tracker_with(Signal(
            cls=self.CLS, subject="meshforge-kiai", severity="degraded",
            detail="absent"))
        reset_dispositions()
        current = probe_tracer_peer_unreachable(
            tracer_dir=tracer_dir, persistent_cycles=3, now=now,
            lookback_s=7200.0,
            posture=_returned_posture(tmp_path, ["kiai"], returned))
        _cov, active, cleared, held = _settle(tracker, current)
        assert cleared == [] and (self.CLS, "meshforge-kiai") in held
        assert [s.extra.get("unobserved_hold") for s, _ in active] == [True]


# ── cron_verdict_stale: the reserved local subject ─────────────────────

class TestPeerCronAliasGuard:
    CLS = "cron_verdict_stale"
    NOW = 1_800_000_000.0

    def _healthy_spool(self, d: Path, alias: str) -> None:
        from datetime import datetime, timezone
        iso = datetime.fromtimestamp(self.NOW - 120, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        crontab = "41 * * * * /x/cron_verdict.sh fleet_hosts_drift -- true\n"
        verdicts = f"{iso} fleet_hosts_drift OK fine\n"
        (d / f"{alias}.json").write_text(json.dumps({
            "fetched_at": self.NOW - 10,
            "schedules": {
                "crontab_b64": base64.b64encode(crontab.encode()).decode(),
                "verdicts_b64": base64.b64encode(verdicts.encode()).decode(),
            }}))

    def test_spool_alias_cron_cannot_observe_the_local_subject(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))
        d = tmp_path / "spool"
        d.mkdir()
        self._healthy_spool(d, "cron")      # a peer named like the local leg
        self._healthy_spool(d, "peer4")
        # The LOCAL leg did not run this tick (its verdict log was unreadable
        # — it noted nothing observed) and a FAIL for subject cron is active.
        tracker = _tracker_with(Signal(
            cls=self.CLS, subject="cron", severity="degraded",
            detail="1 failing: router_scout(FAIL(1))"))
        reset_dispositions()
        current = probe_peer_cron_verdict_stale(now=self.NOW, spool_dir=d)
        assert current == []
        cov, active, cleared, held = _settle(tracker, current)
        assert cov[self.CLS]["observed_subjects"] == ["peer4"]
        assert "cron" in cov[self.CLS]["reason"]     # the collision is witnessed
        assert cleared == [] and (self.CLS, "cron") in held
        assert [s.subject for s, _ in active] == ["cron"]


# ── the rule the reviewer asked to pin (c) ─────────────────────────────

class TestObservedAndEmittedNeverCollide:
    def test_a_subject_both_emitted_and_observed_stays_active(self):
        """An emitted key is in ``current`` and never reaches the vanished
        set, so ``observed_subjects`` cannot clear it whatever the order of
        the checks in ``_class_was_observed``."""
        tracker = SignalTracker()
        sig = Signal(cls="c", subject="x", severity="degraded", detail="d")
        cov = {"c": {"disp": "active", "partial": True,
                     "observed_subjects": ["x"]}}
        active, cleared, held = tracker.update([sig], now=5.0, coverage=cov)
        assert cleared == [] and held == []
        assert [(s.subject, s.extra.get("unobserved_hold"))
                for s, _ in active] == [("x", None)]

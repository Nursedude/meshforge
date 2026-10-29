"""#69 repair order: stop RNS clients → restart rnsd → rnsd OWNS @rns → start clients.

These tests RECORD the order of every stop/start/ownership call. Nothing
here touches a real unit, socket or file: every systemctl-reaching seam is
patched to a recorder.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from utils import rnsd_restart_order as ro

ACTIVE = {("meshforge-map", False), ("nomadnet", True)}


class Recorder:
    """One ordered log for stop/start/ownership across both modules."""

    def __init__(self, owns_after_start=True, owns_value=None):
        self.log = []
        self.rnsd_up = False
        # rnsd's systemd view: running as 100 before the restart
        self.rnsd_pid, self.rnsd_nrestarts = 100, 0
        self.stop_rnsd_ok = True      # False = the stop fails, the old rnsd stays
        self.crash_in_wait = False    # True = systemd Restart= during ownership wait
        self.start_dies = False       # True = `start` returns ok but rnsd is not running
        self.show_value = None        # {} = systemctl show unobservable
        self.rpc_late = 0             # polls before rnsd's RPC listener exists
        self.rpc_value = "follow"     # "follow" rnsd_up, or a fixed True/False/None
        self.owns_after_start = owns_after_start
        self.owns_value = owns_value  # override (e.g. None = unobservable)
        self.activating = 0           # ownership polls rnsd spends in ExecStartPre
        self.in_start_pre = False     # set by `start` when activating > 0
        self.owns_names = []          # instance name every ownership check asked about
        self.stop_timeouts = {}       # unit -> the timeout its stop was given

    def system_active(self, unit, timeout=5):
        return (unit, False) in ACTIVE

    def user_active(self, unit, timeout=5):
        return (unit, True) in ACTIVE

    def stop(self, unit, timeout=30, user=False):
        self.log.append(("stop", unit))
        self.stop_timeouts[unit] = timeout
        if unit == "rnsd":
            if not self.stop_rnsd_ok:
                return False, "Job for rnsd.service failed"
            self.rnsd_up = False
            self.rnsd_pid = 0
        return True, "ok"

    def start(self, unit, timeout=30, user=False):
        self.log.append(("start", unit))
        if unit == "rnsd":
            if self.start_dies:
                return True, "ok"
            if self.activating:       # start returns while ExecStartPre still runs
                self.in_start_pre = True
                return False, "Timeout while starting rnsd"
            self.rnsd_up = True
            if self.rnsd_pid == 0:   # starting an ACTIVE unit is a no-op success
                self.rnsd_pid = 200
        return True, "ok"

    def show(self, unit, user=False):
        self.log.append(("show", unit))
        if self.show_value is not None:
            return dict(self.show_value)
        assert unit == "rnsd", unit
        pid = self.rnsd_pid
        if self.in_start_pre:
            return {"ActiveState": "activating", "SubState": "start-pre",
                    "MainPID": "0", "NRestarts": str(self.rnsd_nrestarts)}
        return {"ActiveState": "active" if pid else "inactive",
                "SubState": "running" if pid else "dead",
                "MainPID": str(pid), "NRestarts": str(self.rnsd_nrestarts)}

    def rpc(self, name=None):
        self.log.append(("rpc?", None))
        if self.rpc_value != "follow":
            return self.rpc_value
        if self.rpc_late > 0:
            self.rpc_late -= 1
            return False
        return self.rnsd_up

    def owns(self, name=None):
        self.log.append(("owns?", None))
        self.owns_names.append(name)
        if self.in_start_pre:
            self.activating -= 1
            if self.activating:
                return False
            self.in_start_pre = False                  # ExecStartPre done, rnsd runs
            self.rnsd_up, self.rnsd_pid = True, 200
        if self.crash_in_wait and self.rnsd_pid == 200:
            self.rnsd_pid, self.rnsd_nrestarts = 201, self.rnsd_nrestarts + 1
        if self.owns_value is not None or not self.owns_after_start:
            return self.owns_value if self.owns_value is not None else False
        return self.rnsd_up


_REAL_UNIT_STOP_TIMEOUT = ro.unit_stop_timeout_s  # the autouse pin below replaces the attribute


@pytest.fixture(autouse=True)
def _pinned_stop_timeout():
    # Ambient-state pin: the hold now reads each unit's TimeoutStopUSec from
    # the host's systemd. Tests that care patch it themselves.
    with patch.object(ro, "unit_stop_timeout_s", lambda unit, user: 90.0):
        yield


def _patched(rec):
    return [
        patch.object(ro, "is_system_unit_active", rec.system_active),
        patch.object(ro, "is_user_unit_active", rec.user_active),
        patch.object(ro, "stop_service", rec.stop),
        patch.object(ro, "start_service", rec.start),
        patch.object(ro, "rnsd_owns_listener", rec.owns),
        patch.object(ro, "listener_owners", lambda name=None: []),
        patch.object(ro, "instance_name", lambda: "volcano"),
        patch.object(ro, "show_unit", rec.show),
        patch.object(ro, "rpc_listener_up", rec.rpc),
    ]


def _run(rec, fn):
    ps = _patched(rec)
    for p in ps:
        p.start()
    try:
        return fn()
    finally:
        for p in ps:
            p.stop()


def _idx(log, event):
    return log.index(event)


class TestOrderedRestart:
    def test_clients_stop_before_rnsd_and_start_after_ownership(self):
        rec = Recorder()
        started, release, hold = _run(
            rec, lambda: ro.ordered_restart_rnsd(wait_s=1, ))
        log = rec.log
        assert started and release.ok
        for client in ("meshforge-map", "nomadnet"):
            assert _idx(log, ("stop", client)) < _idx(log, ("stop", "rnsd"))
            assert _idx(log, ("start", "rnsd")) < _idx(log, ("start", client))
        # ownership was PROVEN between rnsd start and the first client start
        first_client = min(_idx(log, ("start", c)) for c in ("meshforge-map", "nomadnet"))
        owns_before = [i for i, e in enumerate(log) if e[0] == "owns?"
                       and _idx(log, ("start", "rnsd")) < i < first_client]
        assert owns_before, log

    def test_inactive_clients_are_never_touched(self):
        rec = Recorder()
        _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=1))
        touched = {u for _, u in rec.log if u}
        assert touched == {"meshforge-map", "nomadnet", "rnsd"}

    def test_rnsd_never_owns_clients_left_stopped_and_named(self):
        rec = Recorder(owns_after_start=False)
        started, release, _ = _run(
            rec, lambda: ro.ordered_restart_rnsd(wait_s=0.01))
        assert ("start", "meshforge-map") not in rec.log
        assert ("start", "nomadnet") not in rec.log
        assert release.rnsd_owns is False and not release.ok
        assert set(release.left_stopped) == {"meshforge-map (system)",
                                             "nomadnet (user)"}
        assert "left STOPPED" in release.summary()

    def test_unobservable_ownership_is_not_ownership(self):
        # ss unavailable → None; unobservable ≠ owned → clients stay down
        rec = Recorder(owns_value=None)
        rec.owns_value = None
        rec.owns_after_start = False

        def owns(name=None):
            rec.log.append(("owns?", None))
            return None
        rec.owns = owns
        _, release, _ = _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=0.01))
        assert release.rnsd_owns is None and not release.ok
        assert not any(e == ("start", "nomadnet") for e in rec.log)
        assert "could not be observed" in release.summary()


class TestRnsdActuallyRestarted:
    """rnsd-ordered-restart-residue (1)+(2): the shared restart MEASURES that
    rnsd restarted. `systemctl start` on a still-active unit is a no-op
    success, so a failed stop used to read as a repair in the TUI and
    `commands.rns` (only the CLI compared MainPIDs)."""

    def test_failed_stop_is_not_a_restart(self):
        rec = Recorder()
        rec.stop_rnsd_ok = False
        _, release, _ = _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=1))
        assert release.rnsd_owns is True        # the OLD rnsd still owns it
        assert not release.ok
        assert "did not restart" in release.summary()
        assert "MainPID unchanged (100)" in release.summary()
        # F2 (operator, 2026-10-27): a PROVEN failed restart leaves the
        # clients stopped and named — never back on the process the repair
        # was for, even though the old rnsd still owns the socket.
        assert ("start", "nomadnet") not in rec.log
        assert ("start", "meshforge-map") not in rec.log
        assert "nomadnet (user)" in release.left_stopped
        assert "left STOPPED" in release.summary()

    def test_dead_after_start_does_not_wait_for_ownership(self):
        # decidable the moment `start` returns: MainPID 0 — waiting the full
        # ownership window could only end the same way
        rec = Recorder()
        rec.start_dies = True
        waits = []
        _, release, _ = _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=30))
        assert ("start", "nomadnet") not in rec.log
        assert release.verdict is False and "not running" in release.summary()
        assert sum(1 for e in rec.log if e == ("owns?", None)) <= 1

    def test_crash_during_ownership_wait_is_not_a_clean_restart(self):
        rec = Recorder()
        rec.crash_in_wait = True
        _, release, _ = _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=1))
        assert not release.ok
        assert "crashed" in release.summary()
        assert "NRestarts 0 -> 1" in release.summary()
        assert ("start", "nomadnet") not in rec.log   # F2: proven → held

    def test_unobservable_mainpid_is_unverified_never_ok(self):
        rec = Recorder()
        rec.show_value = {}
        _, release, _ = _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=1))
        assert not release.ok and release.verdict is None
        assert "UNVERIFIED" in release.summary()
        # operator rule: unverified → release + UNKNOWN (rnsd owns + RPC up)
        assert ("start", "nomadnet") in rec.log

    def test_rnsd_not_running_before_is_still_a_restart(self):
        rec = Recorder()
        rec.rnsd_pid = 0
        started, release, _ = _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=1))
        assert started and release.ok, release.summary()

    def test_rnsd_stop_waits_its_own_stop_timeout(self):
        # review of the MA port, SHOULD 2: a plain stop_service('rnsd') (30 s)
        # under a longer TimeoutStopSec races the start against a queued stop
        rec = Recorder()
        _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=1))
        assert rec.stop_timeouts["rnsd"] == ro.stop_deadline_s("rnsd", False)

    def test_baseline_is_read_before_rnsd_is_stopped(self):
        # NIT 3: read after the stop, the baseline is MainPID 0, and a stop
        # that did nothing can never read "MainPID unchanged"
        rec = Recorder()
        _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=1))
        assert _idx(rec.log, ("show", "rnsd")) < _idx(rec.log, ("stop", "rnsd")), rec.log

    def test_judge_is_pure(self):
        up = {"ActiveState": "active", "SubState": "running"}
        j = ro.judge_rnsd_restart(dict(up, MainPID="1", NRestarts="0"),
                                  dict(up, MainPID="2", NRestarts="0"),
                                  dict(up, MainPID="2", NRestarts="0"))
        assert (j.state, j.why) == ("ok", "") and "1 -> 2" in j.trace
        assert "not running" in ro.judge_rnsd_restart(
            dict(up, MainPID="1", NRestarts="0"),
            {"ActiveState": "failed", "SubState": "failed", "MainPID": "0", "NRestarts": "0"},
            {"ActiveState": "failed", "SubState": "failed", "MainPID": "0", "NRestarts": "0"})[1]


class TestRnsdActivating:
    """Review of 2cf548ae, blocker #2: rnsd's ExecStartPre (wait_for_ipv6_ll.sh,
    up to 30 s) runs with ActiveState=activating and MainPID 0. start_service
    gives up after 30 s, so the release can begin while rnsd is still in its
    pre-start. MainPID 0 there means "not started YET", not "dead"."""

    def test_slow_execstartpre_is_waited_for_not_judged_dead(self):
        rec = Recorder()
        rec.activating = 3
        _, release, _ = _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=5))
        assert release.ok, release.summary()
        assert ("start", "nomadnet") in rec.log
        assert release.rnsd_restart == "ok"
        assert "100 -> 0 -> 200" in release.restart.trace

    def test_still_activating_after_the_wait_is_unconfirmed_not_dead(self):
        rec = Recorder()
        rec.activating = 10_000
        _, release, _ = _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=0.01))
        assert ("start", "nomadnet") not in rec.log   # never owned: held
        assert release.verdict is False
        assert release.rnsd_restart == "unverified"
        assert "still starting" in release.summary()
        assert "not running" not in release.summary()

    def _st(self, active, pid, n="0", sub=None):
        sub = sub or {"activating": "start-pre", "active": "running"}.get(active, "dead")
        return {"ActiveState": active, "SubState": sub, "MainPID": pid, "NRestarts": n}

    def test_judge_activating_then_running_is_ok(self):
        j = ro.judge_rnsd_restart(self._st("active", "100"), self._st("activating", "0"),
                                  self._st("active", "200"))
        assert j.state == "ok", j

    def test_judge_activating_then_systemd_restart_is_a_crash(self):
        # ExecStartPre or the first start failed and Restart= fired: NRestarts moved
        j = ro.judge_rnsd_restart(self._st("active", "100"), self._st("activating", "0"),
                                  self._st("active", "300", n="1"))
        assert j.state == "failed" and "crashed" in j.why

    def test_judge_activating_then_old_pid_is_not_a_restart(self):
        j = ro.judge_rnsd_restart(self._st("active", "100"), self._st("activating", "0"),
                                  self._st("active", "100"))
        assert j.state == "failed" and "unchanged" in j.why

    def test_judge_auto_restart_after_the_wait_is_a_crash_not_still_starting(self):
        # review SHOULD 1: activating/auto-restart is BETWEEN Restart= cycles
        # (the Errno-99 status=255 exit this drop-in exists for) — proven crash
        j = ro.judge_rnsd_restart(self._st("active", "100"), self._st("active", "200"),
                                  self._st("activating", "0", n="1", sub="auto-restart"))
        assert j.state == "failed" and "crashed" in j.why, j

    def test_judge_auto_restart_queued_at_start_is_a_crash(self):
        # review NIT 4: systemd >=254 has already counted it in NRestarts
        j = ro.judge_rnsd_restart(self._st("active", "100"),
                                  self._st("activating", "0", n="1", sub="auto-restart-queued"),
                                  self._st("active", "300", n="1"))
        assert j.state == "failed" and "crashed" in j.why, j

    def test_judge_inactive_at_start_then_running_is_not_a_clean_restart(self):
        # review NIT 3: only ACTIVATING excuses MainPID 0 at start (mutation pin)
        j = ro.judge_rnsd_restart(self._st("active", "100"), self._st("inactive", "0"),
                                  self._st("active", "300"))
        assert j.state == "failed" and "not running" in j.why, j
        assert "(inactive/dead)" in j.why, j   # NIT 4: names the state that WAS 0

    def test_judge_dead_after_start_is_still_failed(self):
        # the fix must not soften the real "start returned, nothing running" case
        j = ro.judge_rnsd_restart(self._st("active", "100"), self._st("failed", "0"),
                                  self._st("failed", "0"))
        assert j.state == "failed" and "not running" in j.why


class TestRpcReadiness:
    """rnsd-ordered-restart-residue (8), root-caused 2026-10-27: RNS binds
    @rns/<name> in __start_local_interface, brings up every interface (the
    RNode serial open, 2-5 s), and only THEN creates @rns/<name>/rpc. A
    client released on ownership alone hit ECONNREFUSED on its first RPC and
    died (NomadNet, 09:00 + 18:34 on the RNode desktop box; both survivors started later)."""

    def test_clients_wait_for_the_rpc_listener(self):
        rec = Recorder()
        rec.rnsd_up, rec.rpc_late = True, 3
        hold = ro.ClientHold(stopped=[("nomadnet", True)])
        release = _run(rec, lambda: ro.release_rns_clients(
            hold, wait_s=5, _wait=lambda s: None,
            rnsd_before={"MainPID": "99", "NRestarts": "0"}))   # the caller restarted it
        assert release.ok, release.summary()
        last_rpc_false = max(i for i, e in enumerate(rec.log) if e == ("rpc?", None))
        assert last_rpc_false < _idx(rec.log, ("start", "nomadnet"))
        assert sum(1 for e in rec.log if e == ("rpc?", None)) >= 4

    def test_rpc_never_up_leaves_clients_stopped_and_says_why(self):
        rec = Recorder()
        rec.rpc_value = False
        _, release, _ = _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=0.01))
        assert ("start", "nomadnet") not in rec.log
        assert not release.ok
        assert "RPC" in release.summary() and "left STOPPED" in release.summary()

    def test_rpc_unobservable_releases_and_reads_unknown(self):
        # R3 (review 2026-10-28): holding on an unreadable /proc/net/unix
        # turned "cannot see" into a service outage we caused, reported as
        # UNKNOWN. Operator rule: unverified → release + UNKNOWN.
        rec = Recorder()
        rec.rpc_value = None
        _, release, _ = _run(rec, lambda: ro.ordered_restart_rnsd(wait_s=0.01))
        assert ("start", "nomadnet") in rec.log
        assert release.verdict is None and not release.ok
        assert "could not be observed" in release.summary()
        assert "left STOPPED" not in release.summary()

    def _proc(self, tmp_path, *paths_flags):
        f = tmp_path / "unix"
        rows = ["Num       RefCount Protocol Flags    Type St Inode Path"]
        for i, (flags, path) in enumerate(paths_flags):
            rows.append(f"0000{i:012x}: 00000002 00000000 {flags} 0001 01 {1000 + i} {path}")
        f.write_text("\n".join(rows) + "\n")
        return str(f)

    def test_parser_spaced_name_listening(self, tmp_path):
        # ss truncates "volcano ai rns" to "@rns/volcano" for BOTH sockets;
        # /proc/net/unix keeps the full path — the reason to read it here
        f = self._proc(tmp_path, ("00010000", "@rns/volcano ai rns"),
                       ("00010000", "@rns/volcano ai rns/rpc"))
        assert ro.rpc_listener_up("volcano ai rns", proc_net_unix=f) is True

    def test_parser_data_socket_only_is_not_ready(self, tmp_path):
        f = self._proc(tmp_path, ("00010000", "@rns/volcano ai rns"))
        assert ro.rpc_listener_up("volcano ai rns", proc_net_unix=f) is False

    def test_parser_connected_rpc_socket_is_not_a_listener(self, tmp_path):
        f = self._proc(tmp_path, ("00000000", "@rns/x/rpc"))
        assert ro.rpc_listener_up("x", proc_net_unix=f) is False

    def test_parser_other_instance_does_not_count(self, tmp_path):
        f = self._proc(tmp_path, ("00010000", "@rns/x-2/rpc"))
        assert ro.rpc_listener_up("x", proc_net_unix=f) is False

    def test_parser_unreadable_is_none(self, tmp_path):
        assert ro.rpc_listener_up("x", proc_net_unix=str(tmp_path / "nope")) is None


class TestVerdict:
    """Review of f9b5e0eb: ONE tri-state the CLI's exit code and `ok` both
    read — an unobservable state is UNKNOWN (rc 2), never a proven FAIL."""

    def _rel(self, **k):
        base = dict(rnsd_owns=True, rpc_ready=True, restart=ro.RestartJudgement("ok", ""))
        base.update(k)
        return ro.ReleaseResult(**base)

    def test_clean_is_true(self):
        assert self._rel().verdict is True and self._rel().ok

    def test_unobservable_states_are_none(self):
        assert self._rel(rpc_ready=None).verdict is None
        assert self._rel(rnsd_owns=None).verdict is None
        assert self._rel(restart=ro.RestartJudgement("unverified", "x")).verdict is None

    def test_a_proven_failure_beats_an_unknown(self):
        assert self._rel(rpc_ready=None,
                         restart=ro.RestartJudgement("failed", "x")).verdict is False
        assert self._rel(rnsd_owns=False).verdict is False
        assert self._rel(start_failed=["x (user)"], rpc_ready=None).verdict is False

    def test_unjudged_release_is_unknown_never_a_pass(self):
        # R6/F3: a release with no rnsd baseline cannot say rnsd restarted;
        # it used to read True and the diagnostics dialogs said "Fixed"
        assert ro.ReleaseResult(rnsd_owns=True, rpc_ready=True).verdict is None
        with patch.object(ro, "rnsd_owns_listener", return_value=True), \
             patch.object(ro, "rpc_listener_up", return_value=True), \
             patch.object(ro, "start_service", return_value=(True, "ok")):
            rel = ro.release_rns_clients(ro.ClientHold(), name="x", wait_s=0)
        assert rel.restart.state == "not_judged" and rel.verdict is None

    def test_problem_text_cannot_drift_from_the_state(self):
        # R4: rnsd_problem and rnsd_restart were two hand-synced fields
        with pytest.raises(TypeError):
            ro.ReleaseResult(rnsd_owns=True, rnsd_problem="x")
        rel = self._rel(restart=ro.RestartJudgement("failed", "why."))
        assert rel.rnsd_problem == "why." and rel.rnsd_restart == "failed"

    def test_judge_returns_the_state(self):
        up = {"ActiveState": "active", "SubState": "running", "NRestarts": "0"}
        assert ro.judge_rnsd_restart(dict(up, MainPID="1"), dict(up, MainPID="2"),
                                     dict(up, MainPID="2")).state == "ok"
        assert ro.judge_rnsd_restart({}, {}, {}).state == "unverified"
        assert ro.judge_rnsd_restart(dict(up, MainPID="1"), dict(up, MainPID="1"),
                                     dict(up, MainPID="1")).state == "failed"

    def test_held_after_a_failed_restart_is_named_as_such(self):
        # finding 10's successor: F2 no longer puts them back, so say why
        rel = self._rel(restart=ro.RestartJudgement(
            "failed", "rnsd did not restart: MainPID unchanged (100)."),
            left_stopped=["nomadnet (user)"])
        assert "Restarted clients" not in rel.summary()
        assert "left STOPPED" in rel.summary() and "nomadnet (user)" in rel.summary()

    def test_unreadable_rpc_does_not_wait_the_full_window(self):
        # finding 6: polling a read that can never answer cost 30 s
        # R10: every seam pinned — listener_owners ran ss + /proc on the host
        waits = []
        with patch.object(ro, "rnsd_owns_listener", return_value=True), \
             patch.object(ro, "rpc_listener_up", return_value=None), \
             patch.object(ro, "listener_owners", return_value=[]), \
             patch.object(ro, "show_unit", return_value={"MainPID": "2", "NRestarts": "0"}), \
             patch.object(ro, "start_service", return_value=(True, "ok")) as start:
            rel = ro.release_rns_clients(ro.ClientHold(stopped=[("nomadnet", True)]),
                                         name="x", wait_s=30, _wait=waits.append,
                                         rnsd_before={"MainPID": "1", "NRestarts": "0"})
        assert waits == [] and rel.rpc_ready is None and rel.verdict is None
        assert start.called   # R3: released, not held


class TestOwnership:
    def _owns(self, owners, classified):
        with patch("utils.watchdog_probes_rns._scan_rns_listener_owners",
                   return_value=None if owners is None else (owners, None)), \
             patch("utils.watchdog_probes_rns._classify_listener_owners",
                   return_value=classified):
            return ro.rnsd_owns_listener("volcano")

    def test_rnsd_alone_owns(self):
        assert self._owns({101: "rnsd"}, ([], [], [])) is True

    def test_nomadnet_squat_is_not_ownership(self):
        # the #69 inversion: RNS-family, but not rnsd
        assert self._owns({202: "python3"},
                          ([], [(202, "python3 nomadnet")], [])) is False

    def test_absent_listener_is_not_ownership(self):
        assert self._owns({}, ([], [], [])) is False

    def test_owner_mid_teardown_is_not_ownership(self):
        assert self._owns({303: "python3"}, ([], [], [303])) is False

    def test_unobservable_is_none(self):
        assert self._owns(None, None) is None


class TestUserUnitTriState:
    """Back-port of MA e1fa3732: an unreachable user manager is UNKNOWN,
    never "inactive" — check_service(user=True) folds it into NOT_RUNNING."""

    def _state(self, rc, out):
        from utils import service_check as sc
        with patch.object(sc.subprocess, "run",
                          return_value=SimpleNamespace(returncode=rc, stdout=out)):
            return sc.is_user_unit_active("nomadnet")

    def test_active(self):
        assert self._state(0, "active\n") is True

    def test_activating_counts_as_active(self):
        assert self._state(3, "activating\n") is True

    def test_deactivating_counts_as_active(self):
        # Re-review R1 (2026-09-29, drilled on a throwaway unit): a CRASHED
        # client reads `deactivating` for its whole TimeoutStopSec and comes
        # back by itself under Restart=; an explicit stop now cancels that.
        # Skipping it hands the rnsd window to a squatter-in-waiting.
        assert self._state(3, "deactivating\n") is True

    def test_inactive(self):
        assert self._state(3, "inactive\n") is False

    def test_unreachable_manager_is_unknown_not_inactive(self):
        assert self._state(1, "") is None

    def test_unobservable_client_is_not_stopped_and_is_recorded(self):
        with patch.object(ro, "is_user_unit_active", return_value=None), \
             patch.object(ro, "is_system_unit_active", return_value=False), \
             patch.object(ro, "stop_service") as stop:
            hold = ro.hold_rns_clients()
        stop.assert_not_called()
        assert ("nomadnet", True) in hold.unobservable


class TestSystemUnitTriState:
    """Review S2 (2026-09-28): the hold was tri-state for USER units only.
    SYSTEM units read `check_service(unit).available` — a bool — so
    `activating` and a systemctl TIMEOUT both folded into "not active → skip"
    and a gateway in auto-restart when the repair began was neither held nor
    reported (probe: activating → NOT_RUNNING available=False; TimeoutExpired
    → UNKNOWN available=False; hold stopped=[] with no witness)."""

    def _state(self, rc=None, out="", exc=None):
        import subprocess
        from utils import service_check as sc
        kw = ({"side_effect": exc} if exc else
              {"return_value": SimpleNamespace(returncode=rc, stdout=out)})
        with patch.object(sc.subprocess, "run", **kw) as run:
            r = sc.is_system_unit_active("meshforge-gateway")
        if not exc:
            assert "--user" not in run.call_args.args[0]
        return r

    def test_active(self):
        assert self._state(0, "active\n") is True

    def test_activating_counts_as_active(self):
        assert self._state(3, "activating\n") is True

    def test_deactivating_counts_as_active(self):
        # Re-review R1 (2026-09-29, drilled on a throwaway unit): a CRASHED
        # client reads `deactivating` for its whole TimeoutStopSec and comes
        # back by itself under Restart=; an explicit stop now cancels that.
        # Skipping it hands the rnsd window to a squatter-in-waiting.
        assert self._state(3, "deactivating\n") is True

    def test_inactive(self):
        assert self._state(3, "inactive\n") is False

    def test_failed_is_inactive(self):
        assert self._state(3, "failed\n") is False

    def test_timeout_is_unknown_not_inactive(self):
        import subprocess
        assert self._state(exc=subprocess.TimeoutExpired("systemctl", 5)) is None

    def test_unrecognised_answer_is_unknown(self):
        assert self._state(1, "") is None

    def test_activating_system_client_is_held(self):
        # The squatter-in-waiting: a gateway mid-start must be STOPPED before
        # rnsd restarts, exactly as an active one is.
        with patch.object(ro, "is_user_unit_active", return_value=False), \
             patch.object(ro, "is_system_unit_active",
                          side_effect=lambda u, timeout=5: u == "meshforge-gateway"), \
             patch.object(ro, "stop_service", return_value=(True, "ok")) as stop:
            hold = ro.hold_rns_clients()
        assert ("meshforge-gateway", False) in hold.stopped
        assert [c.args[0] for c in stop.call_args_list] == ["meshforge-gateway"]

    def test_unobservable_system_client_is_recorded_not_skipped(self):
        with patch.object(ro, "is_user_unit_active", return_value=False), \
             patch.object(ro, "is_system_unit_active",
                          side_effect=lambda u, timeout=5: None if u == "meshforge-gateway" else False), \
             patch.object(ro, "stop_service") as stop:
            hold = ro.hold_rns_clients()
        stop.assert_not_called()
        assert ("meshforge-gateway", False) in hold.unobservable
        assert "meshforge-gateway" in " ".join(hold.names()) or hold.unobservable

    def test_hold_never_consults_the_bool_check_service(self):
        import inspect
        src = (inspect.getsource(ro.hold_rns_clients)
               + inspect.getsource(ro._client_active))
        assert "check_service(" not in src
        assert "is_system_unit_active" in src


class TestRepairWizardOrder:
    """repair_rns_shared_instance must hold clients across the rnsd restart."""

    def _run_repair(self, rec, instance_up=True, blocking=(), disabled=()):
        from handlers import _rns_repair as rr
        from handler_test_utils import make_handler_context

        handler = MagicMock()
        handler.ctx = make_handler_context()

        def release(hold, name=None, **kw):
            return ro.release_rns_clients(hold, name, wait_s=0.01, **kw)

        ps = _patched(rec) + [
            patch.object(rr, "stop_service", rec.stop),
            patch.object(rr, "start_service", rec.start),
            patch.object(rr, "hold_rns_clients", ro.hold_rns_clients),
            patch.object(rr, "release_rns_clients", release),
            patch.object(rr, "listener_owners", lambda name=None: []),
            patch.object(rr, "instance_name", lambda: "volcano"),
            patch.object(rr, "show_unit", rec.show),
            patch.object(rr, "ReticulumPaths"),
            patch.object(rr, "_ensure_rnsd_rpc_key", return_value=False),
            patch.object(rr, "validate_rnsd_service_file", return_value=False),
            patch.object(rr, "_clear_stale_auth_files", return_value=0),
            patch.object(rr, "_preflight_share_instance"),
            patch.object(rr, "detect_rnsd_config_drift",
                         return_value=SimpleNamespace(drifted=False)),
            patch.object(rr, "find_blocking_interfaces", return_value=list(blocking)),
            patch.object(rr, "_confirm_disable_interfaces",
                         return_value=(True, list(disabled))),
            patch.object(rr, "check_rns_shared_instance",
                         side_effect=lambda: rec.rnsd_up and instance_up),
            patch.object(rr, "get_rns_shared_instance_info",
                         return_value={"detail": "@rns/volcano"}),
            patch.object(rr, "get_udp_port_owner", return_value=None),
            patch.object(rr.subprocess, "run",
                         return_value=SimpleNamespace(stdout="activating\n",
                                                      returncode=0)),
            patch.object(rr.time, "sleep"),
            patch.object(rr, "_diagnose_timeout", return_value=False),
        ]
        for p in ps:
            p.start()
        try:
            return rr.repair_rns_shared_instance(handler)
        finally:
            for p in ps:
                p.stop()

    def test_repair_holds_clients_across_rnsd_restart(self, capsys):
        rec = Recorder()
        assert self._run_repair(rec) is True
        log = rec.log
        for client in ("meshforge-map", "nomadnet"):
            assert _idx(log, ("stop", client)) < _idx(log, ("stop", "rnsd")), log
            assert _idx(log, ("start", "rnsd")) < _idx(log, ("start", client)), log

    def test_repair_failure_leaves_clients_stopped_and_names_them(self, capsys):
        rec = Recorder()
        assert self._run_repair(rec, instance_up=False) is False
        assert ("start", "nomadnet") not in rec.log
        assert ("start", "meshforge-map") not in rec.log
        out = capsys.readouterr().out
        assert "LEFT STOPPED" in out and "nomadnet (user)" in out

    def test_disabling_interfaces_does_not_change_the_instance_checked(self, capsys):
        # `for name in disabled:` reused the instance-name variable, so after
        # disabling an interface the release asked whether rnsd owned
        # @rns/<interface name>, never true, and left every client stopped
        rec = Recorder()
        blocking = [("RNode LoRa", "port missing", "plug it in")]
        assert self._run_repair(rec, blocking=blocking, disabled=["RNode LoRa"]) is True
        assert rec.owns_names and set(rec.owns_names) == {"volcano"}, rec.owns_names
        assert ("start", "nomadnet") in rec.log

    def test_repair_reads_the_baseline_before_stopping_rnsd(self, capsys):
        # NIT 3 (review of the MA port), the wizard's own site
        rec = Recorder()
        self._run_repair(rec)
        assert _idx(rec.log, ("show", "rnsd")) < _idx(rec.log, ("stop", "rnsd")), rec.log

    def test_repair_with_a_failed_rnsd_stop_keeps_clients_stopped(self, capsys):
        # F3: the wizard judged nothing — a failed stop + no-op start read as
        # "RNS shared instance is now available!" with clients back on it
        rec = Recorder()
        rec.stop_rnsd_ok = False
        rec.rnsd_up = True            # the OLD rnsd keeps answering
        assert self._run_repair(rec) is False
        assert ("start", "nomadnet") not in rec.log
        out = capsys.readouterr().out
        assert "did not restart" in out and "nomadnet (user)" in out

    def test_no_pattern_kill_remains(self):
        import inspect
        from handlers import _rns_repair as rr
        from handlers import rns_diagnostics as rd
        for mod in (rr, rd):
            src = inspect.getsource(mod)
            assert "'pkill', '-f'" not in src, mod.__name__


class TestOperatorUserScope:
    """Under sudo, `systemctl --user` must reach the OPERATOR's manager."""

    def test_sudo_routes_user_scope_to_operator(self):
        from utils import service_check as sc
        pw = SimpleNamespace(pw_uid=1000)
        with patch("os.geteuid", return_value=0), \
             patch.dict(os.environ, {"SUDO_USER": "op"}), \
             patch("pwd.getpwnam", return_value=pw):
            argv = sc._systemctl_argv(["stop", "nomadnet"], user=True)
            q = sc._systemctl_query_argv(["is-active", "nomadnet"], user=True)
        for a in (argv, q):
            assert a[:3] == ["sudo", "-u", "op"]
            assert "XDG_RUNTIME_DIR=/run/user/1000" in a
            assert a[-4:-2] == ["systemctl", "--user"]

    def test_root_daemon_without_sudo_user_unchanged(self):
        from utils import service_check as sc
        env = {k: v for k, v in os.environ.items() if k != "SUDO_USER"}
        with patch("os.geteuid", return_value=0), \
             patch.dict(os.environ, env, clear=True):
            assert sc._systemctl_argv(["stop", "x"], user=True) == \
                ["systemctl", "--user", "stop", "x"]


class TestEveryRestartSiteIsOrdered:
    """The four sites that restarted rnsd bare after 71705049 (2026-09-27)."""

    def test_commands_rns_restart_is_ordered(self):
        from commands import rns
        rel = MagicMock(ok=True, verdict=True, summary=lambda: "rnsd owns the shared instance.")
        with patch("utils.rnsd_restart_order.ordered_restart_rnsd",
                   return_value=(True, rel, ro.ClientHold())) as ordered, \
             patch.object(rns, "stop_service") as bare_stop:
            result = rns.restart_rnsd()
        ordered.assert_called_once()
        bare_stop.assert_not_called()
        assert result.success

    def test_commands_rns_restart_reports_left_stopped(self):
        from commands import rns
        rel = ro.ReleaseResult(rnsd_owns=False, left_stopped=["nomadnet (user)"])
        with patch("utils.rnsd_restart_order.ordered_restart_rnsd",
                   return_value=(True, rel, ro.ClientHold())):
            result = rns.restart_rnsd()
        assert not result.success and "nomadnet (user)" in result.message

    def test_commands_rns_restart_unobservable_is_unknown_not_fail(self):
        # R2: the CLI said rc 2 UNKNOWN while this surface said FAIL
        from commands import rns
        from commands.base import ResultStatus
        rel = ro.ReleaseResult(rnsd_owns=True, rpc_ready=None,
                               restart=ro.RestartJudgement("ok", ""))
        with patch("utils.rnsd_restart_order.ordered_restart_rnsd",
                   return_value=(True, rel, ro.ClientHold())):
            result = rns.restart_rnsd()
        assert not result.success                       # never a pass claim
        assert result.status is ResultStatus.WARNING    # and never ERROR
        assert result.message.startswith("UNKNOWN")

    def test_tui_restart_reports_the_tri_state(self):
        from handlers import _rns_repair as rr
        for verdict_rel, want in (
                (ro.ReleaseResult(rnsd_owns=True, rpc_ready=None,
                                  restart=ro.RestartJudgement("ok", "")), None),
                (ro.ReleaseResult(rnsd_owns=True, rpc_ready=True,
                                  restart=ro.RestartJudgement("ok", "")), True),
                (ro.ReleaseResult(rnsd_owns=False), False)):
            with patch.object(rr, "ordered_restart_rnsd",
                              return_value=(True, verdict_rel, ro.ClientHold())):
                assert rr.restart_rnsd_reported()[0] is want

    def test_start_timeout_in_execstartpre_follows_the_measured_release(self):
        # review SHOULD 2: start_service's client wait (30 s) can expire inside
        # ExecStartPre; the release then MEASURES rnsd restarted and released
        # the clients. Neither surface may report that as "did not start".
        from commands import rns
        from handlers import _rns_repair as rr
        rel = ro.ReleaseResult(rnsd_owns=True, rpc_ready=True,
                               restart=ro.RestartJudgement("ok", ""))
        with patch("utils.rnsd_restart_order.ordered_restart_rnsd",
                   return_value=(False, rel, ro.ClientHold())):
            result = rns.restart_rnsd()
        assert result.success, result.message
        assert "start command" in result.message
        with patch.object(rr, "ordered_restart_rnsd",
                          return_value=(False, rel, ro.ClientHold())):
            verdict, text = rr.restart_rnsd_reported()
        assert verdict is True and "start command" in text

    def test_start_failed_and_release_failed_is_still_a_failure(self):
        from commands import rns
        from handlers import _rns_repair as rr
        rel = ro.ReleaseResult(rnsd_owns=False,
                               restart=ro.RestartJudgement("failed", "rnsd is not running"))
        with patch("utils.rnsd_restart_order.ordered_restart_rnsd",
                   return_value=(False, rel, ro.ClientHold())):
            assert not rns.restart_rnsd().success
        with patch.object(rr, "ordered_restart_rnsd",
                          return_value=(False, rel, ro.ClientHold())):
            assert rr.restart_rnsd_reported()[0] is False

    def _tui_site(self, handler_cls_path, method):
        import importlib
        mod_name, cls_name = handler_cls_path.rsplit(".", 1)
        mod = importlib.import_module(mod_name)
        h = getattr(mod, cls_name).__new__(getattr(mod, cls_name))
        h.ctx = MagicMock()
        h._capture_command = MagicMock(return_value="status")
        h._has_systemd_unit = MagicMock(return_value=True)
        with patch("handlers._rns_repair.restart_rnsd_reported",
                   return_value=(False, "left STOPPED: nomadnet (user)")) as rr:
            getattr(h, method)()
        rr.assert_called_once()
        shown = h.ctx.dialog.textbox.call_args[0][1]
        assert "left STOPPED: nomadnet (user)" in shown

    def test_quick_actions_restart_is_ordered(self):
        self._tui_site("handlers.quick_actions.QuickActionsHandler", "_qa_restart_rnsd")

    def test_service_menu_restart_is_ordered(self):
        self._tui_site("handlers.service_menu.ServiceMenuHandler", "_restart_rnsd_service")

    def test_drift_fix_holds_clients_across_rnsd_restart(self):
        import inspect
        from handlers.rns_diagnostics import RNSDiagnosticsHandler
        src = inspect.getsource(RNSDiagnosticsHandler._offer_drift_fix)
        hold = src.index("hold_rns_clients()")
        before = src.index("show_unit('rnsd')")
        stop = src.index("stop_service('rnsd')")
        start = src.index("start_service('rnsd')")
        release = src.index("release_rns_clients(hold, rnsd_before=")
        assert hold < before < stop < start < release

    def test_every_release_site_is_judged(self):
        # F3 as a closed-consumer gate: a release with no rnsd baseline
        # reads UNKNOWN forever, so every call site must pass one
        import re
        from pathlib import Path
        src = Path(__file__).parent.parent / "src"
        sites = 0
        for f in src.rglob("*.py"):
            text = f.read_text()
            for m in re.finditer(r"(?<![\w.])(?:ro\.)?(release_rns_clients|_release_and_report)\(", text):
                line = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
                if line.lstrip().startswith(("def ", "#")) or "import" in line:
                    continue
                depth, i = 1, m.end()
                while depth:
                    depth += {"(": 1, ")": -1}.get(text[i], 0)
                    i += 1
                sites += 1
                assert "rnsd_before=" in text[m.end():i], f"{f.name}: {text[m.start():i]}"
        assert sites >= 6, sites   # 2 diagnostics + 3 wizard + ordered_restart_rnsd


class TestStopFollowsTheUnitsOwnTimeout:
    """Re-review F1 (2026-09-29, drilled): stop_service's 30 s default is
    shorter than every RNS client's TimeoutStopSec, so a slow-to-stop held
    unit timed out the CLIENT, landed in stop_failed, was stopped by the
    still-queued job anyway, and release never restarted it — left down."""

    @pytest.mark.parametrize("text,expect", [
        ("1min 30s", 90.0), ("5min", 300.0), ("45s", 45.0), ("1h 2min 3s", 3723.0),
        ("infinity", float("inf")), ("", None), ("garbage", None), ("1min x", None),
    ])
    def test_parse_systemd_timespan(self, text, expect):
        assert ro.parse_systemd_timespan(text) == expect

    def test_deadline_is_the_units_timeout_plus_margin(self):
        with patch.object(ro, "unit_stop_timeout_s", return_value=90.0):
            assert ro.stop_deadline_s("x", False) == 90 + ro.STOP_TIMEOUT_MARGIN_S

    def test_deadline_unreadable_waits_the_cap(self):
        # F-B: the map's TimeoutStopSec is 5 min; a 90 s guess reproduces F1.
        with patch.object(ro, "unit_stop_timeout_s", return_value=None):
            assert ro.stop_deadline_s("x", True) == ro.STOP_TIMEOUT_CAP_S

    def test_deadline_infinity_is_capped(self):
        with patch.object(ro, "unit_stop_timeout_s", return_value=float("inf")):
            assert ro.stop_deadline_s("x", False) == ro.STOP_TIMEOUT_CAP_S

    def test_unit_stop_timeout_reads_the_units_property(self):
        with patch.object(ro.subprocess, "run",
                          return_value=SimpleNamespace(returncode=0, stdout="1min 30s\n")) as run:
            assert _REAL_UNIT_STOP_TIMEOUT("meshforge-gateway", False) == 90.0
        argv = run.call_args.args[0]
        assert argv[-3:] == ["-p", "TimeoutStopUSec", "--value"] and "meshforge-gateway" in argv

    def _hold_with_client_timeout(self, state_after):
        # hold asks: True (held); stop_service's client times out; re-ask -> state_after
        with patch.object(ro, "is_user_unit_active", return_value=False), \
             patch.object(ro, "is_system_unit_active",
                          side_effect=[True, state_after]), \
             patch.object(ro, "unit_stop_timeout_s", return_value=90.0), \
             patch.object(ro, "stop_service",
                          return_value=(False, "Timeout while stopping u")) as stop:
            hold = ro.hold_rns_clients(units=(("meshforge-gateway", False),))
        return hold, stop

    def test_stop_waits_as_long_as_the_unit_may_take(self):
        _, stop = self._hold_with_client_timeout(False)
        assert stop.call_args.kwargs["timeout"] == 90 + ro.STOP_TIMEOUT_MARGIN_S

    def test_client_timeout_but_unit_did_stop_counts_as_stopped(self):
        # The exact drilled sequence: client gave up, job landed, unit inactive.
        hold, _ = self._hold_with_client_timeout(False)
        assert hold.stopped == [("meshforge-gateway", False)] and hold.stop_failed == []

    def test_client_timeout_and_unit_still_up_is_stop_failed(self):
        hold, _ = self._hold_with_client_timeout(True)
        assert hold.stopped == [] and [u for u, _, _ in hold.stop_failed] == ["meshforge-gateway"]

    def test_client_timeout_and_unit_unobservable_says_unknown(self):
        hold, _ = self._hold_with_client_timeout(None)
        assert hold.stopped == [] and "UNKNOWN" in hold.stop_failed[0][2]

    def test_release_is_not_ok_while_a_client_could_not_be_stopped(self):
        # F-A: the run-as-user site shows "rnsd Fixed" on `ok` alone.
        hold = ro.ClientHold(stop_failed=[("meshforge-map", False, "Timeout while stopping")])
        with patch.object(ro, "rnsd_owns_listener", return_value=True), \
             patch.object(ro, "rpc_listener_up", return_value=True):  # never the host's
            rel = ro.release_rns_clients(hold, name="default", wait_s=0)
        assert rel.ok is False and rel.not_stopped

    def test_release_reports_what_could_not_be_stopped(self):
        hold = ro.ClientHold(stop_failed=[("meshforge-gateway", False, "Timeout while stopping")])
        with patch.object(ro, "rnsd_owns_listener", return_value=True), \
             patch.object(ro, "rpc_listener_up", return_value=True):
            rel = ro.release_rns_clients(hold, name="default", wait_s=0)
        assert rel.not_stopped and "meshforge-gateway" in rel.not_stopped[0]
        assert "Could NOT be stopped" in rel.summary()
        with patch.object(ro, "rnsd_owns_listener", return_value=False), \
             patch.object(ro, "listener_owners", return_value=[]):
            rel = ro.release_rns_clients(hold, name="default", wait_s=0)
        assert "NOT stopped (may be the squatter)" in rel.summary()

    def test_both_diagnostics_sites_print_stop_failed(self):
        # F2: one site printed nothing from the hold at all. Since 2026-10-02
        # every site prints through ClientHold.report_lines (one formatter),
        # so pin BOTH halves: each site calls it, and it renders stop_failed.
        from pathlib import Path
        handlers = Path(__file__).parent.parent / "src" / "launcher_tui" / "handlers"
        diag = (handlers / "rns_diagnostics.py").read_text()
        repair = (handlers / "_rns_repair.py").read_text()
        assert diag.count("hold.report_lines()") >= 2
        assert repair.count("hold.report_lines()") >= 1
        assert "hold_rns_clients()\n            stop_service('rnsd')" not in diag
        hold = ro.ClientHold(stop_failed=[("meshforge-gateway", False, "Timeout")])
        assert any("could NOT stop RNS client meshforge-gateway" in ln
                   for ln in hold.report_lines())

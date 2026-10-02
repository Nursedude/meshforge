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
        self.owns_after_start = owns_after_start
        self.owns_value = owns_value  # override (e.g. None = unobservable)

    def system_active(self, unit, timeout=5):
        return (unit, False) in ACTIVE

    def user_active(self, unit, timeout=5):
        return (unit, True) in ACTIVE

    def stop(self, unit, timeout=30, user=False):
        self.log.append(("stop", unit))
        if unit == "rnsd":
            self.rnsd_up = False
        return True, "ok"

    def start(self, unit, timeout=30, user=False):
        self.log.append(("start", unit))
        if unit == "rnsd":
            self.rnsd_up = True
        return True, "ok"

    def owns(self, name=None):
        self.log.append(("owns?", None))
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

    def _run_repair(self, rec, instance_up=True):
        from handlers import _rns_repair as rr
        from handler_test_utils import make_handler_context

        handler = MagicMock()
        handler.ctx = make_handler_context()

        def release(hold, name=None, **kw):
            return ro.release_rns_clients(hold, name, wait_s=0.01)

        ps = _patched(rec) + [
            patch.object(rr, "stop_service", rec.stop),
            patch.object(rr, "start_service", rec.start),
            patch.object(rr, "hold_rns_clients", ro.hold_rns_clients),
            patch.object(rr, "release_rns_clients", release),
            patch.object(rr, "listener_owners", lambda name=None: []),
            patch.object(rr, "instance_name", lambda: "volcano"),
            patch.object(rr, "ReticulumPaths"),
            patch.object(rr, "_ensure_rnsd_rpc_key", return_value=False),
            patch.object(rr, "validate_rnsd_service_file", return_value=False),
            patch.object(rr, "_clear_stale_auth_files", return_value=0),
            patch.object(rr, "_preflight_share_instance"),
            patch.object(rr, "detect_rnsd_config_drift",
                         return_value=SimpleNamespace(drifted=False)),
            patch.object(rr, "find_blocking_interfaces", return_value=[]),
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
        rel = MagicMock(ok=True, summary=lambda: "rnsd owns the shared instance.")
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
        stop = src.index("stop_service('rnsd')")
        start = src.index("start_service('rnsd')")
        release = src.index("release_rns_clients(hold)")
        assert hold < stop < start < release


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
        with patch.object(ro, "rnsd_owns_listener", return_value=True):
            rel = ro.release_rns_clients(hold, name="default", wait_s=0)
        assert rel.ok is False and rel.not_stopped

    def test_release_reports_what_could_not_be_stopped(self):
        hold = ro.ClientHold(stop_failed=[("meshforge-gateway", False, "Timeout while stopping")])
        with patch.object(ro, "rnsd_owns_listener", return_value=True):
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

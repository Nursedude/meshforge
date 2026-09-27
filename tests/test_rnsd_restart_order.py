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

    def check_service(self, unit, user=False):
        return SimpleNamespace(available=(unit, user) in ACTIVE)

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


def _patched(rec):
    return [
        patch.object(ro, "check_service", rec.check_service),
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

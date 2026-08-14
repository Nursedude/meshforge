"""Unit tests for Config Doctor.

Covers each pure check function in isolation using tmp_path + monkeypatch
to simulate per-box drift scenarios, plus handler dispatch tests using
FakeDialog + make_handler_context.

No live rnsd / NomadNet / systemctl calls — all externals are mocked.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context

from handlers import _config_doctor_checks as checks
from handlers._config_doctor_checks import (
    CheckResult,
    OK,
    WARN,
    FAIL,
    SKIP,
    run_all_checks,
)
from handlers.config_doctor import ConfigDoctorHandler


# Canonical valid 64-hex rpc_key and 32-hex LXMF destination for tests.
VALID_KEY = "a" * 64
DIFFERENT_KEY = "b" * 64
VALID_LXMF = "0123456789abcdef" * 2  # 32 hex chars


# ---------------------------------------------------------------------------
# CheckResult dataclass
# ---------------------------------------------------------------------------

class TestCheckResult:
    def test_rejects_unknown_status(self):
        with pytest.raises(ValueError):
            CheckResult(name="x", status="bogus", message="")

    def test_accepts_valid_statuses(self):
        for s in (OK, WARN, FAIL, SKIP):
            r = CheckResult(name="x", status=s, message="ok")
            assert r.status == s

    def test_defaults(self):
        r = CheckResult(name="x", status=OK, message="ok")
        assert r.fix_hint == ""
        assert r.route_hint == ""
        assert r.details == []


# ---------------------------------------------------------------------------
# rpc_key checks
# ---------------------------------------------------------------------------

class TestCheckRpcKeyPinned:
    def test_skip_when_no_config(self, tmp_path):
        missing = tmp_path / "config"  # does not exist
        with patch("utils.paths.ReticulumPaths.get_config_file",
                   return_value=missing):
            r = checks.check_rpc_key_pinned()
        assert r.status == SKIP
        assert "not found" in r.message.lower()

    def test_fail_when_key_absent(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\nshare_instance = Yes\n")
        with patch("utils.paths.ReticulumPaths.get_config_file",
                   return_value=cfg), \
             patch("utils.paths.ReticulumPaths.get_shared_rpc_key",
                   return_value=None):
            r = checks.check_rpc_key_pinned()
        assert r.status == FAIL
        assert "rpc_key" in r.message.lower()
        # The fix is now in-app: route to the RNS Repair wizard (which pins a
        # fresh rpc_key, restarts rnsd, and offers client restarts) — not a
        # shell openssl/systemctl runbook (In-Domain Principle).
        assert "Repair RNS" in r.fix_hint
        assert "openssl" not in r.fix_hint and "systemctl" not in r.fix_hint
        assert r.route_hint == "RNS > Repair RNS"

    def test_ok_when_key_pinned(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(f"[reticulum]\nrpc_key = {VALID_KEY}\n")
        with patch("utils.paths.ReticulumPaths.get_config_file",
                   return_value=cfg), \
             patch("utils.paths.ReticulumPaths.get_shared_rpc_key",
                   return_value=VALID_KEY):
            r = checks.check_rpc_key_pinned()
        assert r.status == OK
        assert VALID_KEY[:8] in " ".join(r.details)


class TestCheckRpcKeyClientMatch:
    def test_skip_when_rnsd_key_unpinned(self):
        with patch("utils.paths.ReticulumPaths.get_shared_rpc_key",
                   return_value=None):
            r = checks.check_rpc_key_client_match()
        assert r.status == SKIP

    def test_skip_when_no_client_configs(self, tmp_path):
        # Point _CLIENT_CONFIGS at tmp paths that don't exist.
        empty = ((tmp_path / "nope" / "config", "test"),)
        with patch("utils.paths.ReticulumPaths.get_shared_rpc_key",
                   return_value=VALID_KEY), \
             patch.object(checks, "_CLIENT_CONFIGS", empty):
            r = checks.check_rpc_key_client_match()
        assert r.status == SKIP

    def test_ok_when_client_matches(self, tmp_path):
        client_cfg = tmp_path / "client_config"
        client_cfg.write_text(f"rpc_key = {VALID_KEY}\n")
        with patch("utils.paths.ReticulumPaths.get_shared_rpc_key",
                   return_value=VALID_KEY), \
             patch.object(checks, "_CLIENT_CONFIGS",
                          ((client_cfg, "test-client"),)):
            r = checks.check_rpc_key_client_match()
        assert r.status == OK

    def test_fail_when_client_mismatches(self, tmp_path):
        client_cfg = tmp_path / "client_config"
        client_cfg.write_text(f"rpc_key = {DIFFERENT_KEY}\n")
        with patch("utils.paths.ReticulumPaths.get_shared_rpc_key",
                   return_value=VALID_KEY), \
             patch.object(checks, "_CLIENT_CONFIGS",
                          ((client_cfg, "test-client"),)):
            r = checks.check_rpc_key_client_match()
        assert r.status == FAIL
        assert "test-client" in " ".join(r.details)
        assert "meshforge-map" in r.fix_hint or "meshforge-gateway" in r.fix_hint

    def test_fail_when_client_has_no_key_line(self, tmp_path):
        client_cfg = tmp_path / "client_config"
        client_cfg.write_text("[reticulum]\nshare_instance = Yes\n")
        with patch("utils.paths.ReticulumPaths.get_shared_rpc_key",
                   return_value=VALID_KEY), \
             patch.object(checks, "_CLIENT_CONFIGS",
                          ((client_cfg, "test-client"),)):
            r = checks.check_rpc_key_client_match()
        assert r.status == FAIL
        assert "no rpc_key" in " ".join(r.details).lower()


# ---------------------------------------------------------------------------
# rnsd reachable + config drift
# ---------------------------------------------------------------------------

class TestCheckRnsdReachable:
    def test_ok_when_shared_instance_detected(self):
        with patch("utils._port_detection.check_rns_shared_instance",
                   return_value=True), \
             patch("utils._port_detection.get_rns_shared_instance_info",
                   return_value={"available": True,
                                  "detection_method": "abstract_unix"}):
            r = checks.check_rnsd_reachable()
        assert r.status == OK
        assert "abstract_unix" in r.message

    def test_fail_when_not_reachable(self):
        with patch("utils._port_detection.check_rns_shared_instance",
                   return_value=False), \
             patch("utils._port_detection.get_rns_shared_instance_info",
                   return_value={"available": False, "tcp": False,
                                  "unix_socket": False}):
            r = checks.check_rnsd_reachable()
        assert r.status == FAIL
        # MF018 Q3 sweep: fix_hints point in-app now, never at a shell.
        assert "rnsd" in r.fix_hint and "in-app" in r.fix_hint


class TestCheckRnsdConfigDrift:
    def _drift_result(self, **kwargs):
        from types import SimpleNamespace
        defaults = dict(drifted=False,
                        gateway_config_dir=Path("/etc/reticulum"),
                        rnsd_config_dir=Path("/etc/reticulum"),
                        message="aligned",
                        fix_hint="",
                        severity="info")
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_ok_when_aligned(self):
        with patch("utils.config_drift.detect_rnsd_config_drift",
                   return_value=self._drift_result()):
            r = checks.check_rnsd_config_drift()
        assert r.status == OK

    def test_fail_when_drift_error(self):
        with patch("utils.config_drift.detect_rnsd_config_drift",
                   return_value=self._drift_result(
                       drifted=True,
                       rnsd_config_dir=Path("/root/.reticulum"),
                       message="drift",
                       fix_hint="pin rpc_key",
                       severity="error")):
            r = checks.check_rnsd_config_drift()
        assert r.status == FAIL
        assert "pin rpc_key" in r.fix_hint

    def test_warn_when_drift_info(self):
        with patch("utils.config_drift.detect_rnsd_config_drift",
                   return_value=self._drift_result(
                       drifted=True,
                       rnsd_config_dir=Path("/root/.reticulum"),
                       severity="warning")):
            r = checks.check_rnsd_config_drift()
        assert r.status == WARN


# ---------------------------------------------------------------------------
# RNS interface devices
# ---------------------------------------------------------------------------

class TestCheckRnsInterfaceDevices:
    def test_ok_when_no_blocking(self):
        with patch("handlers._rns_interface_mgr."
                   "find_blocking_interfaces", return_value=[]):
            r = checks.check_rns_interface_devices()
        assert r.status == OK

    def test_fail_surfaces_blocking(self):
        blocking = [
            ("MyRNode", "serial /dev/ttyUSB0 not found", "connect device"),
            ("MyTCP", "host 10.1.1.1:4403 unreachable", "check host"),
        ]
        with patch("handlers._rns_interface_mgr."
                   "find_blocking_interfaces", return_value=blocking):
            r = checks.check_rns_interface_devices()
        assert r.status == FAIL
        assert "2 blocking" in r.message
        assert "MyRNode" in " ".join(r.details)
        assert "connect device" in r.fix_hint


# ---------------------------------------------------------------------------
# Gateway default_lxmf_destination
# ---------------------------------------------------------------------------

class TestCheckGatewayDefaultLxmfDestination:
    def _with_home(self, tmp_path):
        return patch("utils.paths.get_real_user_home", return_value=tmp_path)

    def test_skip_when_no_gateway_json(self, tmp_path):
        with self._with_home(tmp_path):
            r = checks.check_gateway_default_lxmf_destination()
        assert r.status == SKIP

    def test_warn_when_empty(self, tmp_path):
        cfg_dir = tmp_path / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "gateway.json").write_text(
            '{"rns": {"default_lxmf_destination": ""}}')
        with self._with_home(tmp_path):
            r = checks.check_gateway_default_lxmf_destination()
        assert r.status == WARN
        assert "empty" in r.message.lower()

    def test_fail_when_malformed(self, tmp_path):
        cfg_dir = tmp_path / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "gateway.json").write_text(
            '{"rns": {"default_lxmf_destination": "not-hex-at-all!"}}')
        with self._with_home(tmp_path):
            r = checks.check_gateway_default_lxmf_destination()
        assert r.status == FAIL
        assert "not 32-hex" in r.message

    def test_ok_when_valid_32_hex(self, tmp_path):
        cfg_dir = tmp_path / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "gateway.json").write_text(
            '{"rns": {"default_lxmf_destination": "' + VALID_LXMF + '"}}')
        with self._with_home(tmp_path):
            r = checks.check_gateway_default_lxmf_destination()
        assert r.status == OK

    def test_rejects_wrong_length_hex(self, tmp_path):
        """64-hex is rpc_key length, not LXMF destination length."""
        cfg_dir = tmp_path / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "gateway.json").write_text(
            '{"rns": {"default_lxmf_destination": "' + ("a" * 64) + '"}}')
        with self._with_home(tmp_path):
            r = checks.check_gateway_default_lxmf_destination()
        assert r.status == FAIL

    def test_fail_when_unreadable_json(self, tmp_path):
        cfg_dir = tmp_path / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "gateway.json").write_text('{not valid json')
        with self._with_home(tmp_path):
            r = checks.check_gateway_default_lxmf_destination()
        assert r.status == FAIL

    def test_ok_when_list_of_valid_32_hex(self, tmp_path):
        """Multi-recipient list form: every entry must be 32-hex."""
        cfg_dir = tmp_path / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        body = '{"rns": {"default_lxmf_destination": ["' + VALID_LXMF + '", "' + ("b" * 32) + '"]}}'
        (cfg_dir / "gateway.json").write_text(body)
        with self._with_home(tmp_path):
            r = checks.check_gateway_default_lxmf_destination()
        assert r.status == OK
        assert "2 recipients" in r.message

    def test_warn_when_empty_list(self, tmp_path):
        """Empty list normalizes the same as empty string."""
        cfg_dir = tmp_path / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "gateway.json").write_text(
            '{"rns": {"default_lxmf_destination": []}}')
        with self._with_home(tmp_path):
            r = checks.check_gateway_default_lxmf_destination()
        assert r.status == WARN

    def test_fail_when_list_contains_bad_entry(self, tmp_path):
        """One bad entry in the list fails the whole check."""
        cfg_dir = tmp_path / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        body = '{"rns": {"default_lxmf_destination": ["' + VALID_LXMF + '", "not-hex"]}}'
        (cfg_dir / "gateway.json").write_text(body)
        with self._with_home(tmp_path):
            r = checks.check_gateway_default_lxmf_destination()
        assert r.status == FAIL
        assert "not 32-hex" in r.message

    def test_fail_when_wrong_type(self, tmp_path):
        """Integer/dict types are explicitly rejected."""
        cfg_dir = tmp_path / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "gateway.json").write_text(
            '{"rns": {"default_lxmf_destination": 42}}')
        with self._with_home(tmp_path):
            r = checks.check_gateway_default_lxmf_destination()
        assert r.status == FAIL
        assert "wrong type" in r.message


# ---------------------------------------------------------------------------
# NomadNet unit tmux binary
# ---------------------------------------------------------------------------

class TestCheckNomadnetUnitTmux:
    def _with_home(self, tmp_path):
        return patch("utils.paths.get_real_user_home", return_value=tmp_path)

    def test_skip_when_unit_missing(self, tmp_path):
        with self._with_home(tmp_path):
            r = checks.check_nomadnet_unit_tmux()
        assert r.status == SKIP

    def test_ok_when_unit_has_no_tmux_ref(self, tmp_path):
        unit = tmp_path / ".config" / "systemd" / "user" / "nomadnet.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nExecStart=/usr/local/bin/nomadnet\n")
        with self._with_home(tmp_path):
            r = checks.check_nomadnet_unit_tmux()
        assert r.status == OK

    def test_fail_when_tmux_binary_missing(self, tmp_path):
        unit = tmp_path / ".config" / "systemd" / "user" / "nomadnet.service"
        unit.parent.mkdir(parents=True)
        unit.write_text(
            "[Service]\n"
            "ExecStart=/definitely/not/here/tmux new-session -d -s nomadnet\n"
        )
        with self._with_home(tmp_path):
            r = checks.check_nomadnet_unit_tmux()
        assert r.status == FAIL
        # MF018 Q3 sweep: fix_hints point in-app now, never at a shell.
        assert "tmux" in r.fix_hint and "in-app" in r.fix_hint

    def test_ok_when_tmux_binary_exists(self, tmp_path):
        """Unit references a tmux path that DOES exist on disk."""
        fake_tmux = tmp_path / "fake_tmux"
        fake_tmux.write_text("#!/bin/sh\n")
        unit = tmp_path / ".config" / "systemd" / "user" / "nomadnet.service"
        unit.parent.mkdir(parents=True)
        unit.write_text(
            f"[Service]\nExecStart={fake_tmux} new-session -d -s nomadnet\n"
        )
        with self._with_home(tmp_path), \
             patch("handlers._config_doctor_checks.shutil.which",
                   return_value=str(fake_tmux)):
            r = checks.check_nomadnet_unit_tmux()
        assert r.status == OK


# ---------------------------------------------------------------------------
# NomadNet service state (consumes SSOT dict)
# ---------------------------------------------------------------------------

class TestCheckNomadnetServiceState:
    def test_skip_when_state_none(self):
        r = checks.check_nomadnet_service_state(None)
        assert r.status == SKIP

    def test_skip_when_unit_not_installed(self):
        state = {"unit_installed": False, "active": False, "enabled": False,
                 "sub_state": "", "main_pid": 0, "n_restarts": 0,
                 "tmux_session": False, "error": None}
        r = checks.check_nomadnet_service_state(state)
        assert r.status == SKIP

    def test_warn_when_inactive(self):
        state = {"unit_installed": True, "active": False, "enabled": True,
                 "sub_state": "dead", "main_pid": 0, "n_restarts": 0,
                 "tmux_session": False, "error": None}
        r = checks.check_nomadnet_service_state(state)
        assert r.status == WARN
        assert "inactive" in r.message.lower()

    def test_warn_when_restart_count_positive(self):
        state = {"unit_installed": True, "active": True, "enabled": True,
                 "sub_state": "running", "main_pid": 1234, "n_restarts": 3,
                 "tmux_session": True, "error": None}
        r = checks.check_nomadnet_service_state(state)
        assert r.status == WARN
        assert "3" in r.message

    def test_warn_when_error_present(self):
        state = {"unit_installed": True, "active": False, "enabled": False,
                 "sub_state": "", "main_pid": 0, "n_restarts": 0,
                 "tmux_session": False,
                 "error": "systemctl --user unreachable"}
        r = checks.check_nomadnet_service_state(state)
        assert r.status == WARN
        assert "unreachable" in r.message

    def test_ok_when_active_no_restarts(self):
        state = {"unit_installed": True, "active": True, "enabled": True,
                 "sub_state": "running", "main_pid": 1234, "n_restarts": 0,
                 "tmux_session": True, "error": None}
        r = checks.check_nomadnet_service_state(state)
        assert r.status == OK


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class TestRunAllChecks:
    def test_returns_expected_check_names_in_order(self, tmp_path, monkeypatch):
        # Monkey-patch all primitives to return skip/ok — we only care about
        # the shape of the result list.
        with patch("utils.paths.ReticulumPaths.get_config_file",
                   return_value=tmp_path / "nope"), \
             patch("utils.paths.ReticulumPaths.get_shared_rpc_key",
                   return_value=None), \
             patch("utils._port_detection.check_rns_shared_instance",
                   return_value=True), \
             patch("utils._port_detection.get_rns_shared_instance_info",
                   return_value={"available": True,
                                  "detection_method": "test"}), \
             patch("utils.config_drift.detect_rnsd_config_drift") as drift, \
             patch("handlers._rns_interface_mgr."
                   "find_blocking_interfaces", return_value=[]), \
             patch("utils.paths.get_real_user_home", return_value=tmp_path):
            from types import SimpleNamespace
            drift.return_value = SimpleNamespace(
                drifted=False, gateway_config_dir=tmp_path,
                rnsd_config_dir=tmp_path, message="aligned",
                fix_hint="", severity="info")
            results = run_all_checks(service_state=None)
        names = [r.name for r in results]
        assert names == [
            "rpc_key_pinned",
            "rpc_key_client_match",
            "rnsd_reachable",
            "rnsd_config_drift",
            "rns_interface_devices",
            "gateway_default_lxmf_destination",
            "nomadnet_unit_tmux",
            "nomadnet_service_state",
        ]


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------

class TestConfigDoctorHandler:
    def _handler(self, ctx):
        h = ConfigDoctorHandler()
        h.set_context(ctx)
        return h

    def test_menu_items(self):
        h = ConfigDoctorHandler()
        items = h.menu_items()
        tags = [t for t, _, _ in items]
        assert "run" in tags
        assert "details" in tags

    def test_handler_registered_in_system_section(self):
        assert ConfigDoctorHandler.menu_section == "system"
        assert ConfigDoctorHandler.handler_id == "config_doctor"

    def test_run_invokes_checks_and_waits(self, capsys):
        ctx = make_handler_context()
        h = self._handler(ctx)
        fake_results = [
            CheckResult(name="t1", status=OK, message="fine"),
            CheckResult(name="t2", status=FAIL, message="bad",
                        fix_hint="do thing", route_hint="Menu > Fix"),
        ]
        with patch.object(h, "_collect_results", return_value=fake_results), \
             patch.object(ctx, "wait_for_enter"):
            h.execute("run")
        out = capsys.readouterr().out
        assert "Config Doctor" in out
        assert "t1" in out and "t2" in out
        assert "do thing" in out  # failing-row fix_hint inlined
        assert "Menu > Fix" in out
        assert h._last_results == fake_results

    def test_details_offers_drill_down(self, capsys):
        ctx = make_handler_context()
        ctx.dialog._menu_returns = ["t2", None]  # pick t2 then back out
        h = self._handler(ctx)
        fake_results = [
            CheckResult(name="t1", status=OK, message="fine"),
            CheckResult(name="t2", status=FAIL, message="bad",
                        fix_hint="do thing", route_hint="Menu > Fix",
                        details=["context line"]),
        ]
        h._last_results = fake_results
        with patch.object(ctx, "wait_for_enter"):
            h.execute("details")
        # Menu was shown, then drilled-down render produced fix_hint + route.
        out = capsys.readouterr().out
        assert "t2" in out
        assert "context line" in out
        assert "Menu > Fix" in out

    def test_details_runs_if_no_cached_results(self):
        ctx = make_handler_context()
        ctx.dialog._menu_returns = [None]
        h = self._handler(ctx)
        with patch.object(h, "_collect_results",
                          return_value=[CheckResult(name="t1", status=OK,
                                                     message="fine")]) as m:
            h.execute("details")
        assert m.called

    def test_nomadnet_service_state_missing_handler_is_none(self):
        """If the NomadNet handler isn't registered, service_state returns None."""
        ctx = make_handler_context()

        class FakeRegistry:
            def get_handler(self, _id):
                return None

        ctx.registry = FakeRegistry()
        h = self._handler(ctx)
        assert h._nomadnet_service_state() is None

    def test_nomadnet_service_state_registry_missing_is_none(self):
        ctx = make_handler_context()
        ctx.registry = None
        h = self._handler(ctx)
        assert h._nomadnet_service_state() is None

    def test_nomadnet_service_state_pulls_from_handler(self):
        ctx = make_handler_context()
        sample = {"unit_installed": True, "active": True, "enabled": True,
                  "sub_state": "running", "main_pid": 99, "n_restarts": 0,
                  "tmux_session": True, "error": None}

        class FakeNomad:
            def _nomadnet_service_state(self):
                return sample

        class FakeRegistry:
            def get_handler(self, _id):
                return FakeNomad()

        ctx.registry = FakeRegistry()
        h = self._handler(ctx)
        assert h._nomadnet_service_state() == sample

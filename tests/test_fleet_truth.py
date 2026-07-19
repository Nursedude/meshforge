"""Tests for the honest fleet-truth SSOT (utils/fleet_truth.py).

The contract under test: NO missing / stale / absent / indeterminate input may
ever produce a ``healthy`` cell — "no data" can never read green. Plus the
fleet-verdict worst-of roll-up and the default-dark coverage map.

Run: python3 -m pytest tests/test_fleet_truth.py -v
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import fleet_truth as ft  # noqa: E402
from utils.watchdog_probe_core import SIGNAL_CLASSES  # noqa: E402

NOW = 1_000_000.0


# ── cell / worst_of primitives ──────────────────────────────────────────
class TestCellPrimitives:
    def test_invalid_state_raises(self):
        # a bug must not silently become a healthy-looking cell
        with pytest.raises(ValueError):
            ft.cell("green")

    def test_worst_of_precedence(self):
        assert ft.worst_of([ft.HEALTHY, ft.DARK, ft.FAILED]) == ft.FAILED
        assert ft.worst_of([ft.HEALTHY, ft.DARK]) == ft.DARK
        assert ft.worst_of([ft.HEALTHY]) == ft.HEALTHY

    def test_worst_of_empty_is_dark(self):
        # observed nothing => cannot claim health
        assert ft.worst_of([]) == ft.DARK


# ── classify_block: the default-dark heart ──────────────────────────────
class TestClassifyBlock:
    def test_none_block_is_dark(self):
        assert ft.classify_block(None, source="x")["state"] == ft.DARK

    def test_not_installed_is_dark(self):
        c = ft.classify_block({"installed": False}, source="x")
        assert c["state"] == ft.DARK

    def test_stale_reason_is_dark_not_healthy(self):
        # frozen watchdog serving old-but-ok JSON must read DARK
        c = ft.classify_block(
            {"installed": True, "ok": False,
             "reason": "stale: last write 900s ago"}, source="x")
        assert c["state"] == ft.DARK

    def test_malformed_is_dark(self):
        c = ft.classify_block(
            {"installed": True, "ok": False, "reason": "malformed_json: x"},
            source="x")
        assert c["state"] == ft.DARK

    def test_ok_true_is_the_only_green(self):
        c = ft.classify_block({"installed": True, "ok": True, "ts": NOW, "age_s": 5},
                              source="x")
        assert c["state"] == ft.HEALTHY

    def test_ok_false_real_fault_is_failed(self):
        c = ft.classify_block(
            {"installed": True, "ok": False, "reason": "watchdog wedge signals: rns"},
            source="x")
        assert c["state"] == ft.FAILED

    def test_missing_ok_flag_is_dark(self):
        # present but no positive observation => cannot claim health
        c = ft.classify_block({"installed": True}, source="x")
        assert c["state"] == ft.DARK

    @pytest.mark.parametrize("block", [
        None, {}, {"installed": False}, {"installed": True},
        {"installed": True, "ok": False, "reason": "stale"},
        {"installed": True, "reason": "read_error: boom"},
    ])
    def test_no_bad_input_yields_healthy(self, block):
        """THE money invariant — nothing but an explicit ok:True is healthy."""
        assert ft.classify_block(block, source="x")["state"] != ft.HEALTHY


# ── coverage map ─────────────────────────────────────────────────────────
class TestCoverage:
    def test_active_signal_is_red(self):
        wd = {"installed": True, "ok": False,
              "signals": [{"class": "role_drift", "severity": "degraded",
                           "subject": "collector", "detail": "d"}]}
        cov = ft.merge_coverage(wd, ["role_drift", "service_inactive"])
        assert cov["classes"]["role_drift"]["disp"] == "active"
        assert cov["red"] == 1

    def test_pre_phase0_unknown_is_dark_not_green(self):
        # watchdog up, no per-class disposition reported => honest dark
        wd = {"installed": True, "ok": True, "signals": []}
        cov = ft.merge_coverage(wd, ["role_drift", "service_inactive"])
        assert cov["green"] == 0
        assert cov["dark"] == 2
        assert cov["classes"]["role_drift"]["disp"] == "unknown"

    def test_reported_clean_is_green(self):
        # Phase-0 enrichment: producer says clean
        wd = {"installed": True, "ok": True, "signals": [],
              "coverage": {"role_drift": "clean", "service_inactive": "inert"}}
        cov = ft.merge_coverage(wd, ["role_drift", "service_inactive"])
        assert cov["green"] == 1  # role_drift clean
        assert cov["dark"] == 1   # service_inactive inert
        assert cov["classes"]["service_inactive"]["disp"] == "inert"

    def test_unobservable_watchdog_all_dark(self):
        cov = ft.merge_coverage({"installed": False}, ["a", "b", "c"])
        assert cov["dark"] == 3 and cov["green"] == 0

    def test_every_signal_class_appears(self):
        """seed-coverage pin: the whole enum is represented, none dropped."""
        wd = {"installed": True, "ok": True, "signals": []}
        cov = ft.merge_coverage(wd, list(SIGNAL_CLASSES))
        assert set(cov["classes"].keys()) == set(SIGNAL_CLASSES)
        assert cov["total"] == len(SIGNAL_CLASSES)


# ── build_box_truth ──────────────────────────────────────────────────────
class TestBoxTruth:
    def _snap(self, **kw):
        base = {"alias": "moc3", "resolution_method": "dns",
                "status": None, "slo": None, "error": None, "answered_at": NOW}
        base.update(kw)
        return base

    def test_unreachable_box_is_dark(self):
        b = ft.build_box_truth(self._snap(status=None, slo=None, error="timeout"),
                               now=NOW, signal_classes=["role_drift"])
        assert b["reachable"]["state"] == ft.DARK
        assert "timeout" in (b["reachable"]["reason"] or "")

    def test_healthy_box(self):
        snap = self._snap(
            status={"app": {"name": "meshforge", "role": "gateway"},
                    "watchdog": {"installed": True, "ok": True, "signals": []},
                    "mini_dudeai": {"installed": True, "ok": True}},
            slo={"overall_status": "ready", "cascade": {"pre_fail": 0, "wedged": 0},
                 "ci_status": {"repos": [{"name": "mf", "state": "success"}]},
                 "radio": {"connected": True}, "schedules": {}, "path_table": {}})
        b = ft.build_box_truth(snap, now=NOW, signal_classes=["role_drift"])
        assert b["reachable"]["state"] == ft.HEALTHY
        assert b["subsystems"]["watchdog"]["state"] == ft.HEALTHY
        assert b["subsystems"]["services"]["state"] == ft.HEALTHY
        assert b["reachable"]["resolution_method"] == "dns"

    def test_gateway_less_box_delivery_stays_dark_not_zero(self):
        # a box with no claw => claw cell dark, never a fake healthy
        snap = self._snap(status={"claw": {"installed": False}},
                          slo={"overall_status": "ready"})
        b = ft.build_box_truth(snap, now=NOW, signal_classes=["role_drift"])
        assert b["subsystems"]["claw"]["state"] == ft.DARK

    def test_failed_service_is_failed(self):
        snap = self._snap(status={"watchdog": {"installed": True, "ok": True}},
                          slo={"overall_status": "degraded"})
        b = ft.build_box_truth(snap, now=NOW, signal_classes=["role_drift"])
        assert b["subsystems"]["services"]["state"] == ft.FAILED


# ── build_fleet_truth: verdict + fan-out honesty ────────────────────────
class TestFleetTruth:
    def _healthy_snap(self, alias):
        return {"alias": alias, "resolution_method": "dns", "answered_at": NOW,
                "status": {"app": {"name": "meshforge"},
                           "watchdog": {"installed": True, "ok": True},
                           "mini_dudeai": {"installed": True, "ok": True}},
                "slo": {"overall_status": "ready", "cascade": {"pre_fail": 0, "wedged": 0},
                        "ci_status": {"repos": [{"name": "mf", "state": "success"}]},
                        "radio": {"connected": True}, "schedules": {}, "path_table": {}}}

    def test_all_healthy_verdict_healthy(self):
        snaps = [self._healthy_snap("moc"), self._healthy_snap("moc1")]
        t = ft.build_fleet_truth(snaps, now=NOW, signal_classes=[], noc_host="moc")
        assert t["fleet_state"] == ft.HEALTHY
        assert t["counts"]["healthy"] == 2
        assert t["fanout"]["stale"] is False

    def test_incomplete_fanout_forces_non_green(self):
        snaps = [self._healthy_snap("moc")]  # 1 answered
        t = ft.build_fleet_truth(snaps, now=NOW, signal_classes=[],
                                 noc_host="moc", hosts_declared=3)
        assert t["fanout"]["stale"] is True
        assert t["fleet_state"] != ft.HEALTHY  # dark fan-out can't read green

    def test_dark_box_taints_verdict(self):
        dark = {"alias": "moc9", "resolution_method": "unresolved",
                "status": None, "slo": None, "error": "no route", "answered_at": None}
        t = ft.build_fleet_truth([self._healthy_snap("moc"), dark],
                                 now=NOW, signal_classes=[], noc_host="moc")
        assert t["counts"]["dark"] == 1
        assert t["fleet_state"] != ft.HEALTHY

    def test_failed_box_makes_verdict_failed(self):
        bad = self._healthy_snap("moc2")
        bad["slo"]["overall_status"] = "degraded"
        t = ft.build_fleet_truth([self._healthy_snap("moc"), bad],
                                 now=NOW, signal_classes=[], noc_host="moc")
        assert t["fleet_state"] == ft.FAILED

    def test_structural_dark_always_present(self):
        t = ft.build_fleet_truth([], now=NOW, signal_classes=[], noc_host="moc")
        assert len(t["structural_dark"]) >= 5
        ids = {d["id"] for d in t["structural_dark"]}
        assert "mesh_rf_ota_leg_unwatched" in ids
        assert "oracle_rns_send_blind" in ids

    def test_empty_fleet_is_dark_not_healthy(self):
        t = ft.build_fleet_truth([], now=NOW, signal_classes=[], noc_host="moc",
                                 hosts_declared=2)
        assert t["fleet_state"] != ft.HEALTHY

    def test_schema_tag(self):
        t = ft.build_fleet_truth([], now=NOW, signal_classes=[], noc_host="moc")
        assert t["schema"] == "fleet_truth/v1"

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

    def test_held_signal_carries_unobserved_hold_marker(self):
        """B4 (2026-07-26): a HELD signal (tracker kept it because its
        observation channel went dark) is last-observed data — the cell must
        carry the marker so /fleet can dim it rather than render stale
        detail (voltage, restarts, ...) as a fresh observation."""
        wd = {"installed": True, "ok": False,
              "signals": [{"class": "claw_battery_low", "severity": "degraded",
                           "subject": "dudeclaw-01", "detail": "3.4V",
                           "extra": {"unobserved_hold": True}}]}
        cov = ft.merge_coverage(wd, ["claw_battery_low", "role_drift"])
        cell = cov["classes"]["claw_battery_low"]
        assert cell["disp"] == "active"
        assert cell["unobserved_hold"] is True

    def test_live_signal_has_no_hold_marker(self):
        wd = {"installed": True, "ok": False,
              "signals": [{"class": "claw_battery_low", "severity": "degraded",
                           "subject": "dudeclaw-01", "detail": "3.4V",
                           "extra": {}}]}
        cov = ft.merge_coverage(wd, ["claw_battery_low"])
        assert "unobserved_hold" not in cov["classes"]["claw_battery_low"]

    def test_every_signal_class_appears(self):
        """seed-coverage pin: the whole enum is represented, none dropped."""
        wd = {"installed": True, "ok": True, "signals": []}
        cov = ft.merge_coverage(wd, list(SIGNAL_CLASSES))
        assert set(cov["classes"].keys()) == set(SIGNAL_CLASSES)
        assert cov["total"] == len(SIGNAL_CLASSES)

    def test_stale_block_signals_not_presented_as_active(self):
        """2026-07-19 adversarial review: a FROZEN watchdog serving a stale
        block still carries its last signals[] — those are a stale
        observation and must read dark, never currently-active red."""
        wd = {"installed": True, "ok": False,
              "reason": "stale: last write 900s ago",
              "signals": [{"class": "role_drift", "severity": "degraded",
                           "subject": "s", "detail": "d"}]}
        cov = ft.merge_coverage(wd, ["role_drift", "service_inactive"])
        assert cov["red"] == 0
        assert cov["classes"]["role_drift"]["disp"] != "active"
        assert cov["dark"] == 2

    def test_stale_block_coverage_not_presented_as_current(self):
        """2026-07-21 re-review of the 07-19 fix: the staleness gate was
        applied to signals[] but NOT to coverage{} — a frozen watchdog's last
        coverage map still rendered as current green for exactly the classes
        that were firing when it froze (the stale signal was suppressed, then
        fell through to its stale 'clean' disposition). A stale block's
        reported dispositions are as unobservable as its signals."""
        wd = {"installed": True, "ok": False,
              "reason": "stale (age 7200s)",
              "signals": [{"class": "cpu_hot", "severity": "degraded",
                           "subject": "s", "detail": "d"}],
              "coverage": {"cpu_hot": "clean", "disk_full": "clean"}}
        cov = ft.merge_coverage(wd, ["cpu_hot", "disk_full"])
        assert cov["green"] == 0
        assert cov["red"] == 0
        assert cov["dark"] == 2
        assert cov["classes"]["cpu_hot"]["disp"] == "unknown"
        assert cov["classes"]["disk_full"]["disp"] == "unknown"

    # ── server-vs-fleet code skew (the #79 deploy-restart gap, truth-API skin)
    def test_class_reported_by_box_is_never_dropped(self):
        """2026-07-20 live incident: meshforge-map imports SIGNAL_CLASSES at
        process start, so after a fleet_pull that adds a class, every box
        reports it while the un-restarted server still holds the old list.
        Iterating the server's list ALONE silently dropped those classes and
        still published `total` as if the map were complete. The box's own
        report must win."""
        wd = {"installed": True, "ok": True, "signals": [],
              "coverage": {"role_drift": "clean",
                           "brand_new_probe": {"disp": "inert",
                                               "reason": "organ absent here"}}}
        cov = ft.merge_coverage(wd, ["role_drift"])          # server: 1 class
        assert "brand_new_probe" in cov["classes"]           # NOT dropped
        assert cov["classes"]["brand_new_probe"]["disp"] == "inert"
        assert cov["classes"]["brand_new_probe"]["reason"] == "organ absent here"
        assert cov["unknown_to_server"] == ["brand_new_probe"]
        assert cov["total"] == 2                             # counts what rendered
        assert cov["green"] == 1 and cov["dark"] == 1

    def test_active_signal_for_unknown_class_still_reads_red(self):
        """The dangerous direction: a FIRING probe the server doesn't know
        must not vanish into a green-looking map."""
        wd = {"installed": True, "ok": True,
              "signals": [{"class": "brand_new_probe", "severity": "degraded",
                           "subject": "s", "detail": "d"}]}
        cov = ft.merge_coverage(wd, ["role_drift"])
        assert cov["classes"]["brand_new_probe"]["disp"] == "active"
        assert cov["red"] == 1
        assert cov["unknown_to_server"] == ["brand_new_probe"]

    def test_empty_enum_is_not_reported_as_skew(self):
        """MeshAnchor passes an EMPTY class list when its blackout-kind import
        fails (its documented honest-zero fallback). Ungated, every reported
        class would read 'unknown to server', pin the verdict DARK, and blame a
        stale deploy — one degraded state wearing another's diagnosis, the very
        defect this fix removes. You cannot be behind a list you don't have."""
        wd = {"installed": True, "ok": True, "signals": [],
              "coverage": {"a": "clean", "b": "inert"}}
        cov = ft.merge_coverage(wd, [])
        assert cov["unknown_to_server"] == []
        assert cov["total"] == 0
        assert cov["green"] == cov["red"] == cov["dark"] == 0

    def test_no_skew_is_the_silent_steady_state(self):
        """In-sync server: the key exists but is empty, so consumers can tell
        'no skew' from 'this build never reported skew'."""
        wd = {"installed": True, "ok": True, "signals": [],
              "coverage": {"role_drift": "clean"}}
        cov = ft.merge_coverage(wd, ["role_drift", "service_inactive"])
        assert cov["unknown_to_server"] == []
        assert cov["total"] == 2


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

    def test_stale_mini_rules_not_presented_as_live_escalations(self):
        """2026-07-19 adversarial review: a frozen mini's last active_rules
        are a stale observation — the escalations feed must not show them
        as live (the dark mini cell surfaces the blindness instead)."""
        snap = self._snap(status={
            "mini_dudeai": {"installed": True, "ok": False,
                            "reason": "stale: last tick 900s ago",
                            "active_rules": [{"rule_id": "r1", "subject": "s",
                                              "detail": "old fire"}]}})
        b = ft.build_box_truth(snap, now=NOW, signal_classes=[])
        assert b["escalations"] == []
        assert b["subsystems"]["mini"]["state"] == ft.DARK

    def test_fresh_mini_rules_do_flow_to_escalations(self):
        snap = self._snap(status={
            "mini_dudeai": {"installed": True, "ok": True,
                            "active_rules": [{"rule_id": "r1", "subject": "s",
                                              "detail": "live fire"}]}})
        b = ft.build_box_truth(snap, now=NOW, signal_classes=[])
        assert len(b["escalations"]) == 1
        assert b["escalations"][0]["rule_id"] == "r1"


class TestClawCell:
    """The claw subsystem cell must account for EVERY dude-claw on the box, not
    just the primary. Before this, a dead/degraded second claw was invisible to
    the fleet verdict — a degraded state mapped to a valid-looking value
    (honest_failure_modes). ``_claw_cell`` rolls up ``worst_of`` primary +
    secondaries and carries a per-claw roster for the monitor's claw panel."""

    def _primary(self, **over):
        base = {"installed": True, "ok": True, "device": "dudeclaw-01",
                "age_s": 5, "battery": {"volts": 4.1}}
        base.update(over)
        return base

    def test_single_claw_unchanged_backward_compatible(self):
        # No secondaries → exact classify_block behaviour, no roster attached.
        c = ft._claw_cell(self._primary())
        assert c["state"] == ft.HEALTHY
        assert "claws" not in c and "claw_count" not in c
        assert c["claw_present"] is True

    def test_claw_less_box_stays_absent_dark(self):
        c = ft._claw_cell({"installed": False})
        assert c["state"] == ft.DARK and c["absent"] is True
        assert c["claw_present"] is False

    def test_unobservable_box_never_reads_as_a_claw_host(self):
        # 07-24 audit: an unreachable box (no /api/status at all) yields a dark,
        # NON-absent claw cell — the NOC's claw panel used to render that as
        # "1 claw", inventing an edge node on every claw-less box. The cell must
        # carry POSITIVE evidence, and there is none here.
        for block in (None, {}, {"installed": None}):
            c = ft._claw_cell(block)
            assert c["state"] == ft.DARK
            assert c["claw_present"] is False, block

    def test_hard_fault_secondary_makes_cell_failed_and_taints(self):
        # A secondary reporting an OBSERVED fault (ok:False, non-unobservable
        # reason) → the aggregate cell is FAILED and taints the box roll-up.
        # This pins the worst_of wiring: a hard-faulted second claw can never
        # hide under a healthy primary.
        block = self._primary()
        block["secondaries"] = [{"installed": True, "ok": False,
                                 "device": "dudeclaw-02",
                                 "reason": "hard_fault: watchdog reset loop"}]
        c = ft._claw_cell(block)
        assert c["state"] == ft.FAILED           # healthy primary + faulted secondary
        assert c["claw_count"] == 2
        devices = {cw["device"]: cw["state"] for cw in c["claws"]}
        assert devices == {"dudeclaw-01": ft.HEALTHY, "dudeclaw-02": ft.FAILED}
        assert "dudeclaw-02" in c["reason"]

    def test_hard_fault_secondary_taints_box_state(self):
        snap = {"alias": "moc2", "resolution_method": "dns", "error": None,
                "answered_at": NOW,
                "status": {"watchdog": {"installed": True, "ok": True},
                           "claw": {"installed": True, "ok": True,
                                    "device": "dudeclaw-01",
                                    "secondaries": [{"installed": True, "ok": False,
                                                     "device": "dudeclaw-02",
                                                     "reason": "hard_fault: reset loop"}]}},
                "slo": {"overall_status": "ready"}}
        b = ft.build_box_truth(snap, now=NOW, signal_classes=[])
        assert b["subsystems"]["claw"]["state"] == ft.FAILED
        assert b["box_state"] == ft.FAILED

    def test_unreachable_secondary_is_dark_visible_not_hidden(self):
        # The REALISTIC producer case: a dead/stale second claw is DARK
        # (a blind spot, not a hard fault — 'unreachable'/'stale' are
        # unobservable markers), informational and does NOT taint the verdict
        # (claw is optional, not core-observability). But the roster still
        # lists it — it must not vanish under the healthy primary.
        block = self._primary()
        block["secondaries"] = [{"installed": True, "ok": False,
                                 "device": "dudeclaw-02", "age_s": 9999,
                                 "reason": "stale: last capture 9999s ago"}]
        c = ft._claw_cell(block)
        assert c["state"] == ft.DARK
        assert c["claw_count"] == 2
        sec = [cw for cw in c["claws"] if cw["device"] == "dudeclaw-02"][0]
        assert sec["state"] == ft.DARK

    def test_dark_secondary_does_not_taint_box_state(self):
        # Consistency guard: a stale/unreachable secondary keeps the box tile
        # healthy (claw death is informational by design), while still visible.
        snap = {"alias": "moc2", "resolution_method": "dns", "error": None,
                "answered_at": NOW,
                "status": {"watchdog": {"installed": True, "ok": True},
                           "mini_dudeai": {"installed": True, "ok": True},
                           "claw": {"installed": True, "ok": True,
                                    "device": "dudeclaw-01",
                                    "secondaries": [{"installed": True, "ok": False,
                                                     "device": "dudeclaw-02",
                                                     "reason": "stale: 9999s ago"}]}},
                "slo": {"overall_status": "ready", "cascade": {"pre_fail": 0, "wedged": 0}}}
        b = ft.build_box_truth(snap, now=NOW, signal_classes=[])
        assert b["subsystems"]["claw"]["state"] == ft.DARK
        assert b["box_state"] == ft.HEALTHY

    def test_absent_primary_does_not_drag_real_secondary(self):
        # Primary tick missing but a real secondary present → the secondary's
        # health drives the cell; the absent primary stays visible but inert.
        block = {"installed": False, "reason": "no_state_file",
                 "secondaries": [{"installed": True, "ok": True,
                                  "device": "dudeclaw-02", "age_s": 5}]}
        c = ft._claw_cell(block)
        assert c["state"] == ft.HEALTHY
        # 07-24 audit: the count is the claws we HAVE, not the roster length —
        # "2 claws healthy" for one claw + a missing primary tick displayed a
        # missing observation as a healthy device.
        assert c["claw_count"] == 1
        assert c["claw_absent_count"] == 1
        assert "1 claw healthy" in c["reason"]
        assert "not present" in c["reason"]      # the absent one is not hidden
        assert len(c["claws"]) == 2              # still visible in the roster
        assert c["absent"] is False              # a real claw IS present

    def test_all_claws_absent_is_benign_absence_not_a_blind_spot(self):
        # Both tick files vanished between the glob and the read → nothing is
        # present. That is role-absence (absent=True), the same disposition the
        # single-claw path gives it — not an indeterminate blind spot.
        block = {"installed": False, "reason": "no_state_file",
                 "secondaries": [{"installed": False, "reason": "read_error: gone"}]}
        c = ft._claw_cell(block)
        assert c["state"] == ft.DARK
        assert c["absent"] is True
        assert c["claw_count"] == 0 and c["claw_absent_count"] == 2
        assert c["claw_present"] is False


class TestCiCell:
    """2026-07-19 adversarial review: FAILED only on an OBSERVED failure;
    in-progress/unknown/None states are DARK (cannot judge), never healthy
    and never an invented fault."""

    def _slo(self, repos):
        return {"ci_status": {"repos": repos}}

    def test_in_progress_is_dark_not_failed(self):
        c = ft._ci_cell(self._slo([{"name": "mf", "state": "in_progress"}]))
        assert c["state"] == ft.DARK
        assert "in_progress" in c["reason"]

    def test_none_state_is_dark_not_healthy(self):
        c = ft._ci_cell(self._slo([{"name": "mf", "state": None}]))
        assert c["state"] == ft.DARK

    def test_unknown_state_is_dark_not_failed(self):
        c = ft._ci_cell(self._slo([{"name": "mf", "state": "queued"}]))
        assert c["state"] == ft.DARK

    def test_failure_is_failed(self):
        c = ft._ci_cell(self._slo([{"name": "mf", "state": "failure"},
                                   {"name": "ma", "state": "success"}]))
        assert c["state"] == ft.FAILED

    def test_completed_failure_conclusions_all_read_failed(self):
        """2026-07-21 re-review: gh also concludes timed_out / startup_failure
        / action_required — observed terminal failures. The old set parked
        them in 'not judgeable yet' DARK forever (non-tainting)."""
        for state in ("timed_out", "startup_failure", "action_required"):
            c = ft._ci_cell(self._slo([{"name": "mf", "state": state}]))
            assert c["state"] == ft.FAILED, state
            assert state in c["reason"]

    def test_failure_beats_in_progress(self):
        c = ft._ci_cell(self._slo([{"name": "mf", "state": "failure"},
                                   {"name": "ma", "state": "in_progress"}]))
        assert c["state"] == ft.FAILED

    def test_all_success_is_healthy(self):
        c = ft._ci_cell(self._slo([{"name": "mf", "state": "success"}]))
        assert c["state"] == ft.HEALTHY


class TestCiCellStaleness:
    """2026-09-01: the cell read only ``repos`` and dropped the producer's
    ``stale``/``age_s``, so a FROZEN snapshot rendered as a live verdict.

    Observed: the poller box asserted ci=FAILED for 5h20m off an 18:05 poll of a
    run that went green at 18:26 (the timer fires twice daily, so nothing
    re-derived it). Both polarities are pinned here — the frozen-GREEN one is
    the dangerous direction (a dead timer + a red CI reading healthy), and it
    is the one no incident had yet forced.
    """

    def _slo(self, repos, **block):
        b = {"repos": repos}
        b.update(block)
        return {"ci_status": b}

    # ── the polarity today's incident produced ───────────────────────────
    def test_stale_failure_is_dark_not_failed(self):
        c = ft._ci_cell(self._slo([{"name": "mf", "state": "failure"}],
                                  stale=True, age_s=19200.0))
        assert c["state"] == ft.DARK
        assert "frozen" in c["reason"]
        assert "5.3h old" in c["reason"]

    # ── the polarity that had never fired, and is worse ──────────────────
    def test_stale_success_is_dark_not_healthy(self):
        """A frozen green is a live health claim off an unobserved source."""
        c = ft._ci_cell(self._slo([{"name": "mf", "state": "success"}],
                                  stale=True, age_s=54000.0))
        assert c["state"] == ft.DARK, "frozen snapshot must never read healthy"
        assert "frozen" in c["reason"]

    def test_stale_beats_every_repo_state(self):
        for state in ("success", "failure", "in_progress", None,
                      "timed_out", "startup_failure"):
            c = ft._ci_cell(self._slo([{"name": "mf", "state": state}],
                                      stale=True, age_s=60000.0))
            assert c["state"] == ft.DARK, state

    def test_truthy_non_bool_stale_falls_to_dark(self):
        """A producer emitting a non-bool truthy marker must land on the SAFE
        side. ``is True`` would have let it through as fresh."""
        c = ft._ci_cell(self._slo([{"name": "mf", "state": "success"}],
                                  stale=1, age_s=99999.0))
        assert c["state"] == ft.DARK

    # ── no freshness claim: judge as before, never invent a fault ────────
    def test_absent_stale_key_still_judged(self):
        """MeshAnchor's block (and older peers) may carry no ``stale`` key.
        Absence of a freshness claim is not a fault — the pre-existing
        verdicts must survive untouched."""
        assert ft._ci_cell(self._slo(
            [{"name": "mf", "state": "success"}]))["state"] == ft.HEALTHY
        assert ft._ci_cell(self._slo(
            [{"name": "mf", "state": "failure"}]))["state"] == ft.FAILED

    def test_stale_false_is_judged_normally(self):
        assert ft._ci_cell(self._slo([{"name": "mf", "state": "success"}],
                                     stale=False))["state"] == ft.HEALTHY
        assert ft._ci_cell(self._slo([{"name": "mf", "state": "failure"}],
                                     stale=False))["state"] == ft.FAILED

    # ── age rides EVERY cell, so the human sees what we acted on ─────────
    def test_age_and_observed_at_ride_every_state(self):
        for repos, want in (([{"name": "mf", "state": "success"}], ft.HEALTHY),
                            ([{"name": "mf", "state": "failure"}], ft.FAILED),
                            ([{"name": "mf", "state": "queued"}], ft.DARK),
                            ([], ft.DARK)):
            c = ft._ci_cell(self._slo(repos, age_s=120.0,
                                      generated_unix=1788300000.0))
            assert c["state"] == want
            assert c["age_s"] == 120.0
            assert c["observed_at"] == 1788300000.0

    def test_bogus_age_and_observed_at_are_dropped_not_coerced(self):
        c = ft._ci_cell(self._slo([{"name": "mf", "state": "success"}],
                                  age_s="soon", generated_unix=True))
        assert c["age_s"] is None
        assert c["observed_at"] is None

    def test_missing_ci_status_still_dark(self):
        assert ft._ci_cell({})["state"] == ft.DARK
        assert ft._ci_cell(None)["state"] == ft.DARK


class TestAgeSuffix:
    def test_minutes_under_an_hour(self):
        assert ft._age_suffix(720.0) == " (12m old)"

    def test_hours_at_one_decimal(self):
        assert ft._age_suffix(19200.0) == " (5.3h old)"

    def test_unknown_age_renders_nothing_not_zero(self):
        """'frozen (0m old)' would read as FRESH — the exact inversion this
        leg exists to prevent."""
        assert ft._age_suffix(None) == ""
        assert ft._age_suffix("later") == ""
        assert ft._age_suffix(True) == ""

    def test_negative_age_is_named_skew_not_quoted(self):
        """RTC-less Pi / NTP step: a backwards clock is not a duration
        (honest_failure_modes #6)."""
        out = ft._age_suffix(-500.0)
        assert "skew" in out
        assert "-" not in out.replace("—", "")


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

    def _witness_snap(self, collector, targets):
        # a claw-brain box whose host_frozen signal carries per-target verdicts
        snap = self._healthy_snap(collector)
        snap["status"]["watchdog"] = {
            "installed": True, "ok": True,
            "signals": [{"class": "host_frozen", "severity": "wedge",
                         "subject": "claw-witness", "detail": "witness detail",
                         "extra": {"targets": targets}}]}
        return snap

    def test_host_frozen_witness_fans_out_to_witnessed_box(self):
        # the claw witness runs on moc2 but reports on moc5 — moc5's verdict
        # must land on moc5's OWN row, not only under moc2's coverage chip.
        moc2 = self._witness_snap("moc2", [
            {"name": "moc5", "verdict": "HOST_FROZEN",
             "witness_device": "dudeclaw-02", "failover": False},
            {"name": "bot32", "verdict": "UNREACHABLE",
             "witness_device": "dudeclaw-01", "failover": False}])
        t = ft.build_fleet_truth([moc2, self._healthy_snap("moc5")],
                                 now=NOW, signal_classes=["host_frozen"],
                                 noc_host="moc2")
        by = {b["alias"]: b for b in t["boxes"]}
        w = by["moc5"]["witnessed"]
        assert len(w) == 1 and w[0]["verdict"] == "HOST_FROZEN"
        assert w[0]["witness_device"] == "dudeclaw-02"
        assert w[0]["source_box"] == "moc2"
        # bot32 is not a fleet box → no phantom row; stays in moc2's detail
        assert "bot32" not in by
        # the collector is never witnessed by itself
        assert by["moc2"]["witnessed"] == []

    def test_failover_witness_provenance_carries_through(self):
        moc2 = self._witness_snap("moc2", [
            {"name": "moc5", "verdict": "UNKNOWN",
             "witness_device": "dudeclaw-01", "failover": True}])
        t = ft.build_fleet_truth([moc2, self._healthy_snap("moc5")],
                                 now=NOW, signal_classes=["host_frozen"],
                                 noc_host="moc2")
        w = {b["alias"]: b for b in t["boxes"]}["moc5"]["witnessed"][0]
        assert w["verdict"] == "UNKNOWN" and w["failover"] is True

    def test_witness_is_display_only_not_a_verdict_taint(self):
        # moc5 self-reports healthy; an out-of-band HOST_FROZEN (which may be the
        # witness's own blind spot) SURFACES on its row but must NOT flip its
        # verdict — annotate, never override (honest_failure_modes: don't let one
        # observation channel's degraded state redefine another's).
        moc2 = self._witness_snap("moc2", [
            {"name": "moc5", "verdict": "HOST_FROZEN",
             "witness_device": "dudeclaw-02", "failover": False}])
        t = ft.build_fleet_truth([moc2, self._healthy_snap("moc5")],
                                 now=NOW, signal_classes=["host_frozen"],
                                 noc_host="moc2")
        by = {b["alias"]: b for b in t["boxes"]}
        assert by["moc5"]["box_state"] == ft.HEALTHY        # not tainted
        assert by["moc5"]["witnessed"][0]["verdict"] == "HOST_FROZEN"  # but surfaced

    def test_server_class_skew_rolls_up_and_forces_non_green(self):
        """2026-07-20: a per-box unknown_to_server list is true-but-buried.
        If any box reports a class this server doesn't know, the server is
        running older code than the fleet and EVERY coverage map it publishes
        is partial — so it rolls up top-level and taints the verdict DARK.
        DARK, not FAILED: nothing out there is broken, we just cannot see all
        of it from here, and unobservable must never read healthy."""
        snap = self._healthy_snap("moc")
        snap["status"]["watchdog"] = {
            "installed": True, "ok": True, "signals": [],
            "coverage": {"brand_new_probe": "inert"}}
        t = ft.build_fleet_truth([snap], now=NOW, signal_classes=["role_drift"],
                                 noc_host="moc")
        assert t["server_class_skew"] == {"brand_new_probe": ["moc"]}
        assert t["fleet_state"] == ft.DARK

    def test_foreign_app_peer_is_not_a_stale_code_accusation(self):
        """CAUGHT LIVE on first deploy, 2026-07-20. meshanchor-server instantly
        reported no_data/http_dead/frozen/daemon_dead — MeshAnchor's OWN
        blackout-kind vocabulary, not classes this NOC is behind on. Ungated,
        a heterogeneous fleet pins itself DARK forever on a false 'your code is
        stale' diagnosis. The classes must still RENDER (never drop what a box
        observed) while making no accusation about this server's version."""
        snap = self._healthy_snap("meshanchor-server")
        snap["status"]["app"] = {"name": "meshanchor"}
        snap["status"]["watchdog"] = {
            "installed": True, "ok": True, "signals": [],
            "coverage": {"no_data": "clean", "frozen": "clean"}}
        t = ft.build_fleet_truth([snap], now=NOW, signal_classes=["role_drift"],
                                 noc_host="moc")
        assert t["server_class_skew"] == {}          # no accusation
        assert t["fleet_state"] == ft.HEALTHY        # verdict untainted
        cov = t["boxes"][0]["coverage"]
        assert "no_data" in cov["classes"]           # but still rendered
        assert cov["unknown_to_server"] == ["frozen", "no_data"]

    def test_unidentifiable_peer_makes_no_accusation(self):
        """A peer whose app can't be read is a blind spot; blind spots must
        not manufacture a confident 'restart your server' claim."""
        snap = self._healthy_snap("mystery")
        snap["status"]["app"] = {}                   # no name
        snap["status"]["watchdog"] = {
            "installed": True, "ok": True, "signals": [],
            "coverage": {"brand_new_probe": "inert"}}
        t = ft.build_fleet_truth([snap], now=NOW, signal_classes=["role_drift"],
                                 noc_host="moc")
        assert t["server_class_skew"] == {}
        assert t["fleet_state"] == ft.HEALTHY

    # ── accepted blind spots: a role-declared missing map (moc3, 2026-07-20)
    def _maples_snap(self, alias="moc3", *, expected=False):
        """A gateway-only box as the spool actually reports it: watchdog from
        the raw file (healthy), and NOTHING else, because it runs no map."""
        return {"alias": alias, "resolution_method": "ssh_spool",
                "answered_at": NOW, "error": None,
                "http_surface_expected": expected,
                "status": {"app": {"name": "meshforge", "role": "gateway-only"},
                           "watchdog": {"installed": True, "ok": True, "signals": []}},
                "slo": None}

    def test_role_declared_maples_box_does_not_darken_the_fleet(self):
        """moc3 declares `meshforge-map: disabled` (too heavy for a ~1GB
        board), which removes the only window onto mini + services. A box
        configured exactly as designed must not pin the fleet DARK forever."""
        t = ft.build_fleet_truth(
            [self._healthy_snap("moc"), self._maples_snap()],
            now=NOW, signal_classes=[], noc_host="moc")
        assert t["fleet_state"] == ft.HEALTHY
        assert t["boxes"][1]["box_state"] == ft.HEALTHY

    def test_accepted_blind_spots_are_disclosed_not_hidden(self):
        """We stopped ALARMING on these; we do not get to stop DISCLOSING
        them. The cells stay DARK for the human and are listed at the top."""
        t = ft.build_fleet_truth([self._maples_snap()], now=NOW,
                                 signal_classes=[], noc_host="moc")
        subs = t["boxes"][0]["subsystems"]
        assert subs["mini"]["state"] == ft.DARK        # still dark, not green
        assert subs["services"]["state"] == ft.DARK
        assert subs["mini"]["accepted_blind"] is True
        assert "declared role runs no map server" in subs["mini"]["reason"]
        assert t["accepted_blind_spots"]["moc3"] == [
            "cascade", "ci", "claw", "mini", "radio", "rns_paths",
            "schedules", "services"]

    def test_undeclared_dark_box_still_taints(self):
        """THE anti-silence guard. Without a declaration (None — undeclared,
        unknown role, or unreadable catalog) nothing changes: the box is just
        as dark and just as tainting as before. This must never become a
        switch that quiets genuinely broken boxes."""
        snap = self._maples_snap(expected=None)
        t = ft.build_fleet_truth([snap], now=NOW, signal_classes=[], noc_host="moc")
        assert t["fleet_state"] == ft.DARK
        assert t["accepted_blind_spots"] == {}
        assert "accepted_blind" not in t["boxes"][0]["subsystems"]["mini"]

    def test_watchdog_going_dark_still_taints_on_a_maples_box(self):
        """The one core signal a gateway-only box still owes us. Its
        watchdog.json is a RAW FILE the spool reads, so it stays genuinely
        observable — if it goes dark, that is a real blind spot, not an
        accepted one, and it must still alarm."""
        snap = self._maples_snap()
        snap["status"] = {"app": {"name": "meshforge", "role": "gateway-only"}}
        t = ft.build_fleet_truth([snap], now=NOW, signal_classes=[], noc_host="moc")
        assert t["boxes"][0]["subsystems"]["watchdog"]["state"] == ft.DARK
        assert not t["boxes"][0]["subsystems"]["watchdog"].get("accepted_blind")
        assert t["fleet_state"] == ft.DARK

    def test_accepted_blind_never_masks_an_observed_fault(self):
        """accepted_blind only ever applies to DARK cells. A cell that is
        FAILED is an observation, and observations always taint."""
        snap = self._maples_snap()
        snap["slo"] = {"overall_status": "degraded", "cascade": {},
                       "ci_status": {}, "radio": {}, "schedules": {},
                       "path_table": {}}
        t = ft.build_fleet_truth([snap], now=NOW, signal_classes=[], noc_host="moc")
        svc = t["boxes"][0]["subsystems"]["services"]
        assert svc["state"] == ft.FAILED
        assert not svc.get("accepted_blind")
        assert t["fleet_state"] == ft.FAILED

    def test_no_skew_leaves_the_verdict_alone(self):
        snaps = [self._healthy_snap("moc")]
        t = ft.build_fleet_truth(snaps, now=NOW, signal_classes=[], noc_host="moc")
        assert t["server_class_skew"] == {}
        assert t["fleet_state"] == ft.HEALTHY

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
        """The Known Blind Spots registry is always emitted, non-trivial, and
        every row is fully formed.

        Pin the SHAPE, not a catalogue of ids: the registry is a work-list, so
        narrowing a row RENAMES it by design (2026-07-19 renamed four in one
        day — mesh_rf_ota_leg_unwatched -> mesh_rf_ota_egress_unproven among
        them). An id-list assertion turns every legitimate closure into a red
        test that says nothing about correctness. One still-open row stays
        pinned so this cannot pass vacuously; when THAT row closes, update it
        here deliberately."""
        t = ft.build_fleet_truth([], now=NOW, signal_classes=[], noc_host="moc")
        assert len(t["structural_dark"]) >= 5
        for row in t["structural_dark"]:
            assert row.get("id") and row.get("detail") and row.get("ref"), (
                f"malformed structural-dark row: {row}")
        ids = {d["id"] for d in t["structural_dark"]}
        assert "oracle_rns_send_blind" in ids   # still open (row 2, post-roll)

    def test_empty_fleet_is_dark_not_healthy(self):
        t = ft.build_fleet_truth([], now=NOW, signal_classes=[], noc_host="moc",
                                 hosts_declared=2)
        assert t["fleet_state"] != ft.HEALTHY

    def test_schema_tag(self):
        t = ft.build_fleet_truth([], now=NOW, signal_classes=[], noc_host="moc")
        assert t["schema"] == "fleet_truth/v1"

    def test_faulted_box_counts_failed_not_healthy(self):
        """2026-07-19 adversarial review: counts/tiles were reachability-only,
        so a reachable box with an observed fault summarized green. box_state
        is worst-of(reach + tainting subsystems) and drives counts."""
        bad = self._healthy_snap("moc2")
        bad["slo"]["overall_status"] = "degraded"
        t = ft.build_fleet_truth([self._healthy_snap("moc"), bad],
                                 now=NOW, signal_classes=[], noc_host="moc")
        assert t["counts"]["failed"] == 1
        assert t["counts"]["healthy"] == 1
        bad_box = next(b for b in t["boxes"] if b["alias"] == "moc2")
        assert bad_box["box_state"] == ft.FAILED

    def test_dead_slo_leg_taints_verdict(self):
        """2026-07-19 adversarial review: a box whose /api/status answers but
        whose /fleet/slo leg is dead was reachable-healthy with a dark
        services cell that tainted NOTHING — a fleet-wide dead slo leg read
        green. services is now core-observability."""
        snap = self._healthy_snap("moc")
        snap["slo"] = None
        t = ft.build_fleet_truth([snap], now=NOW, signal_classes=[], noc_host="moc")
        assert t["fleet_state"] != ft.HEALTHY
        assert t["boxes"][0]["box_state"] == ft.DARK


class TestSpoolServicesTransientTolerance:
    """07-23 audit E-F7: the 2-min spool cron landing during a deploy restart
    caught units `activating` and rendered FAILED for a cycle (no debounce
    anywhere on this path). Mid-start = not-yet-determined = DARK with the
    mid-restart reason; hard-down stays FAILED."""

    def test_activating_is_dark_not_failed(self):
        c = ft._services_cell_from_spool({
            "meshforge-gateway.service": {"active": "activating",
                                          "enabled": "enabled"}})
        assert c["state"] == ft.DARK

    def test_activating_reason_names_both_readings(self):
        """B5 (2026-07-26): a crashlooping unit spends most of its life in
        `activating` (auto-restart backoff, the #82 shape) — the reason must
        not label the state as benign-only. Wording, not a debounce: the
        spool is point-in-time; probe-side detectors own crashloop paging."""
        c = ft._services_cell_from_spool({
            "meshforge-gateway.service": {"active": "activating",
                                          "enabled": "enabled"}})
        assert "crash-loop" in c["reason"]
        assert "transient" in c["reason"]
        assert "meshforge-gateway.service" in c["reason"]

    def test_hard_down_still_failed_even_with_a_transient_sibling(self):
        c = ft._services_cell_from_spool({
            "a.service": {"active": "failed", "enabled": "enabled"},
            "b.service": {"active": "activating", "enabled": "enabled"}})
        assert c["state"] == ft.FAILED
        assert "a.service" in c["reason"]

    def test_all_active_still_healthy(self):
        c = ft._services_cell_from_spool({
            "a.service": {"active": "active", "enabled": "enabled"}})
        assert c["state"] == ft.HEALTHY


class TestRoleDeclaredNoWatchdog:
    """2026-08-31. `watchdog` is core-observability and is deliberately NOT
    part of the HTTP-surface exemption — a map-less gateway still writes
    watchdog.json, which the spool reads as a raw file, so it still owes us
    that signal.

    But a `field-node` (zero-class board carrying only a radio) runs no
    watchdog at all: ~46 MB RSS does not fit a 512MB-class board already
    running meshtasticd and a bot. Before this, such a box darkened the
    FLEET verdict forever. A permanently-dark verdict is not a safe default
    — it trains the reader to ignore `dark`, which costs more than the gap
    it was flagging.

    The distinction restored is the domain's oldest: `inert` (the organ is
    not here, by declaration) is not `indeterminate` (we should see it and
    cannot). Only the second is a finding.
    """

    def _field_snap(self, alias="lehua", *, watchdog_expected=False,
                    watchdog_block=None):
        """A field-node as the spool actually reports it: mini + radio +
        services from raw non-HTTP reads, and NO watchdog block at all."""
        status = {"app": {"name": "meshforge", "role": "field-node"},
                  "mini_dudeai": {"installed": True, "ok": True}}
        if watchdog_block is not None:
            status["watchdog"] = watchdog_block
        return {"alias": alias, "resolution_method": "ssh_spool",
                "answered_at": NOW, "error": None,
                "http_surface_expected": False,
                "watchdog_expected": watchdog_expected,
                "spool_services": {"meshtasticd": {"active": "active",
                                                   "enabled": "enabled"}},
                "status": status,
                "slo": {"radio": {"connected": True, "mode": "tcp"}}}

    def test_declared_no_watchdog_does_not_darken_the_fleet(self):
        t = ft.build_fleet_truth([self._field_snap()], now=NOW,
                                 signal_classes=[], noc_host="moc")
        assert t["boxes"][0]["box_state"] == ft.HEALTHY
        assert t["fleet_state"] == ft.HEALTHY

    def test_the_cell_is_absent_not_accepted_blind(self):
        """`accepted_blind` claims "it may well be running, we gave up SEEING
        it". Here the unit is not installed, so that would be a lie. Both stop
        tainting; only `absent` is true."""
        t = ft.build_fleet_truth([self._field_snap()], now=NOW,
                                 signal_classes=[], noc_host="moc")
        wd = t["boxes"][0]["subsystems"]["watchdog"]
        assert wd["state"] == ft.DARK, "must still render dark for the human"
        assert wd["absent"] is True
        assert not wd.get("accepted_blind")
        assert "declared role runs no watchdog" in wd["reason"]

    def test_undeclared_box_still_taints(self):
        """THE anti-silence guard, same as the map exemption's. None must
        never collapse to False."""
        t = ft.build_fleet_truth([self._field_snap(watchdog_expected=None)],
                                 now=NOW, signal_classes=[], noc_host="moc")
        assert t["fleet_state"] == ft.DARK
        assert not t["boxes"][0]["subsystems"]["watchdog"].get("absent")

    def test_a_stale_watchdog_still_taints_even_when_declared_absent(self):
        """The case the first draft of this guard would have swallowed. A box
        that HAS a watchdog whose file went STALE produces a present-but-dark
        block. Declaring the unit absent must not suppress that — a stale
        watchdog is a real wedge signal, not a role-appropriate gap."""
        stale = {"installed": True, "ok": False,
                 "reason": "stale: last write 9000s ago"}
        t = ft.build_fleet_truth(
            [self._field_snap(watchdog_block=stale)],
            now=NOW, signal_classes=[], noc_host="moc")
        wd = t["boxes"][0]["subsystems"]["watchdog"]
        assert wd["state"] == ft.DARK
        assert not wd.get("absent"), (
            "a STALE watchdog was marked absent — the guard keyed on the role "
            "instead of on whether a block actually arrived")
        assert t["fleet_state"] == ft.DARK

    def test_a_healthy_watchdog_is_untouched(self):
        """If the box runs one anyway, it reports normally."""
        ok = {"installed": True, "ok": True, "signals": []}
        t = ft.build_fleet_truth([self._field_snap(watchdog_block=ok)],
                                 now=NOW, signal_classes=[], noc_host="moc")
        wd = t["boxes"][0]["subsystems"]["watchdog"]
        assert wd["state"] == ft.HEALTHY
        assert not wd.get("absent")


class TestDeclaredPosture:
    """2026-09-01 DORMANT arc batch 2: a box declared dormant/detached (the
    collector stamps snap['posture'] from utils.fleet_posture) is a FOURTH
    state — dark for the human, disclosed under `declared_posture`, counted
    apart, and NOT tainting the fleet verdict. A declared box that answers is
    posture drift: healthy reach + posture_drift flag + disclosure. Never
    inferred from silence: no stamp = exactly the old document."""

    def _dark(self, alias, posture=None):
        s = {"alias": alias, "resolution_method": "dns", "status": None, "slo": None,
             "error": "no response", "answered_at": None}
        if posture:
            s["posture"] = posture
        return s

    def _healthy(self, alias, posture=None):
        s = TestFleetTruth()._healthy_snap(alias)
        if posture:
            s["posture"] = posture
        return s

    def test_dark_without_declaration_still_taints(self):
        t = ft.build_fleet_truth([self._healthy("moc"), self._dark("moc4")],
                                 now=NOW, signal_classes=[], noc_host="moc")
        assert t["fleet_state"] == ft.DARK
        assert t["counts"]["dark"] == 1 and t["counts"]["dormant"] == 0
        assert t["declared_posture"] == {}

    def test_declared_dormant_dark_box_does_not_taint_and_is_disclosed(self):
        t = ft.build_fleet_truth(
            [self._healthy("moc"),
             self._dark("moc4", {"state": "dormant", "note": "declared dormant until X (storm)"})],
            now=NOW, signal_classes=[], noc_host="moc")
        assert t["fleet_state"] == ft.HEALTHY
        assert t["counts"] == {"healthy": 1, "failed": 0, "dark": 0, "dormant": 1}
        assert list(t["declared_posture"]) == ["moc4"]
        assert "storm" in t["declared_posture"]["moc4"]
        by = {b["alias"]: b for b in t["boxes"]}
        r = by["moc4"]["reachable"]
        assert r["state"] == ft.DARK and r["dormant"] is True     # still dark for the human
        assert "declared dormant" in r["reason"]

    def test_declared_box_that_answers_is_posture_drift(self):
        t = ft.build_fleet_truth(
            [self._healthy("moc4", {"state": "dormant", "note": "declared dormant until X"})],
            now=NOW, signal_classes=[], noc_host="moc")
        r = t["boxes"][0]["reachable"]
        assert r["state"] == ft.HEALTHY and r.get("posture_drift") is True
        assert "moc4" in t["posture_drift"] and "ANSWERED" in t["posture_drift"]["moc4"]
        assert t["declared_posture"] == {}
        assert t["counts"]["healthy"] == 1

    def test_detached_is_treated_like_dormant(self):
        t = ft.build_fleet_truth(
            [self._healthy("moc"), self._dark("kit", {"state": "detached", "note": "field"})],
            now=NOW, signal_classes=[], noc_host="moc")
        assert t["fleet_state"] == ft.HEALTHY and t["counts"]["dormant"] == 1

    def test_active_stamp_changes_nothing(self):
        t = ft.build_fleet_truth([self._dark("moc4", {"state": "active", "note": "x"})],
                                 now=NOW, signal_classes=[], noc_host="moc")
        assert t["fleet_state"] == ft.DARK and t["counts"]["dark"] == 1

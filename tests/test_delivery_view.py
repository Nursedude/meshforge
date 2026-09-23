"""utils.delivery_view — the TUI Delivery screen's reader (2026-09-23).

Every leg is tri-state (ok / inert / unknown). The properties pinned here:
an unknown leg prints no number; absent-by-design reads inert, never unknown;
the headline is never better than its worst present leg; a thin window reads
QUIET, not healthy; and the windowed count is the stall probe's OWN function.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import delivery_view as dv  # noqa: E402
from utils import watchdog_probes_gateway as wpg  # noqa: E402

NOW = 1_800_000_000.0


def _gw(status):
    return lambda unit: (status, 1234 if status == "ok" else None)


def _enrolled(mapping):
    return lambda unit, home: mapping.get(unit, False)


def _snapshot(confirmed=200, failed=0, ts_age=10.0, **extra):
    ring = ([{"ts": NOW - 60 - i, "state": "confirmed", "protocol": "rns"}
             for i in range(confirmed)]
            + [{"ts": NOW - 60, "state": "dropped", "protocol": "rns",
                "drop_reason": "rns_delivery_failed"} for _ in range(failed)])
    snap = {
        "state_totals": {"queued": 5, "sent": 7, "confirmed": 900, "dropped": 3},
        "drop_reasons": {"dedup": 2, "rns_delivery_failed": 1},
        "state_by_protocol": {"confirmed": {"rns": 900}},
        "confirmation_rate": 0.99,
        "unconfirmable_sent": 7,
        "recent_terminal": ring,
        "first_event_ts": NOW - 86400 * 30,
        "last_event_ts": NOW - 30,
        "health": {"preflight_ok": True, "consecutive_write_errors": 0},
    }
    snap.update(extra)
    return {"schema": 1, "ts": NOW - ts_age, "snapshot": snap}


def _write(home, sub, doc):
    p = os.path.join(home, sub)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(doc if isinstance(doc, str) else json.dumps(doc))
    return p


def _queue(ts_age=10.0, **kw):
    stats = {"delivered": 203, "failed": 0, "retried": 0, "shed": 0,
             "pending": 0, "in_progress": 0, "queue_depth": 0,
             "max_queue_size": 1000, "dead_letter": 0}
    stats.update(kw)
    return {"schema": 1, "ts": NOW - ts_age, "stats": stats}


def _soak(home, leaf, name, body, age=600.0):
    p = _write(home, os.path.join(".local", "state", "meshforge", leaf, name),
               body)
    os.utime(p, (NOW - age, NOW - age))
    return p


def _gather(home, gw="ok", enrolled=None, unit_enabled=True):
    return dv.gather(home=str(home), now=NOW, gateway_resolver=_gw(gw),
                     enrolled_fn=_enrolled(enrolled or {}),
                     unit_enabled_fn=lambda unit: unit_enabled)


def _gateway_box(home, snap=None, queue=None):
    _write(str(home), dv._DELIVERY_SNAPSHOT_SUBPATH, snap or _snapshot())
    _write(str(home), dv._QUEUE_STATS_SUBPATH, queue or _queue())


def _leg(view, prefix):
    return next(leg for leg in view.legs if leg.title.startswith(prefix))


class TestSharedDefinitions:
    def test_window_is_the_probes_own_function(self):
        # One implementation (honest_failure_modes #5): the screen must show
        # the number the stall probe judges, not a lookalike.
        assert dv.confirmation_window is wpg.confirmation_window
        assert dv.DELIVERY_STALL_MIN_TERMINAL is wpg.DELIVERY_STALL_MIN_TERMINAL

    def test_probe_default_floor_is_the_named_constant(self):
        import inspect
        sig = inspect.signature(wpg.probe_delivery_confirmation_stall)
        assert sig.parameters["min_terminal"].default == wpg.DELIVERY_STALL_MIN_TERMINAL

    def test_window_falls_back_to_legacy_ring(self):
        payload = {"state_by_protocol": {"confirmed": {"rns": 1}},
                   "recent": [{"state": "confirmed", "protocol": "rns"},
                              {"state": "dropped", "protocol": "rns",
                               "drop_reason": "dedup"},
                              {"state": "sent", "protocol": "meshtastic"}]}
        w = wpg.confirmation_window(payload)
        assert w["ring_source"] == "recent"
        # dedup is not a delivery failure; meshtastic is not confirmable.
        assert (w["confirmed"], w["failed"], w["terminal"]) == (1, 0, 1)

    def test_window_with_no_ring_says_so(self):
        w = wpg.confirmation_window({"state_by_protocol": {"confirmed": {"rns": 3}}})
        assert w["ring_source"] is None and w["terminal"] == 0

    def test_window_tolerates_misshaped_protocol_block(self):
        w = wpg.confirmation_window({"state_by_protocol": "garbage"})
        assert w["confirmable"] == []


class TestInertVsUnknown:
    def test_no_gateway_no_timers_is_inert_everywhere(self, tmp_path):
        v = _gather(tmp_path, gw="absent")
        assert {leg.status for leg in v.legs} == {dv.INERT}
        assert v.headline().startswith("NO DELIVERY ORGAN")

    @staticmethod
    def _units(mapping):
        # per-unit resolver: MF gateway + the sister app's writer unit
        return lambda unit: (mapping.get(unit, "absent"), None)

    def test_sister_app_record_is_not_called_absent(self, tmp_path):
        # meshanchor-server 2026-09-23: MA's daemon keeps its own record.
        _write(str(tmp_path), dv.PEER_DELIVERY_DBS[0][1], "")
        v = dv.gather(home=str(tmp_path), now=NOW,
                      gateway_resolver=self._units({dv.PEER_DELIVERY_DBS[0][2]: "ok"}),
                      enrolled_fn=_enrolled({}), unit_enabled_fn=lambda u: True)
        assert v.headline().startswith("NOT READ HERE")
        assert "[not read] MeshAnchor" in dv.render(v)

    def test_sister_file_without_writer_unit_is_a_leftover(self, tmp_path):
        # manager box 2026-09-23: an Aug-5 leftover DB, no MeshAnchor unit.
        _write(str(tmp_path), dv.PEER_DELIVERY_DBS[0][1], "")
        v = dv.gather(home=str(tmp_path), now=NOW,
                      gateway_resolver=self._units({}),
                      enrolled_fn=_enrolled({}), unit_enabled_fn=lambda u: True)
        assert v.headline().startswith("NO DELIVERY ORGAN")
        text = dv.render(v)
        assert "[leftover] MeshAnchor" in text and "NOT READ HERE" not in text

    def test_sister_writer_masked_is_a_leftover(self, tmp_path):
        # The actual manager-box shape: the daemon unit is MASKED ("down").
        _write(str(tmp_path), dv.PEER_DELIVERY_DBS[0][1], "")
        v = dv.gather(home=str(tmp_path), now=NOW,
                      gateway_resolver=self._units({dv.PEER_DELIVERY_DBS[0][2]: "down"}),
                      enrolled_fn=_enrolled({}), unit_enabled_fn=lambda u: False)
        assert v.peer_leftovers and not v.peer_records

    def test_sister_writer_stopped_but_enabled_is_not_a_leftover(self, tmp_path):
        _write(str(tmp_path), dv.PEER_DELIVERY_DBS[0][1], "")
        v = dv.gather(home=str(tmp_path), now=NOW,
                      gateway_resolver=self._units({dv.PEER_DELIVERY_DBS[0][2]: "down"}),
                      enrolled_fn=_enrolled({}), unit_enabled_fn=lambda u: True)
        assert v.peer_records and not v.peer_leftovers

    def test_sister_writer_unobservable_is_not_a_leftover(self, tmp_path):
        _write(str(tmp_path), dv.PEER_DELIVERY_DBS[0][1], "")
        v = dv.gather(home=str(tmp_path), now=NOW,
                      gateway_resolver=lambda unit: ("absent", None)
                      if unit == dv.GATEWAY_UNIT else ("unknown", None),
                      enrolled_fn=_enrolled({}), unit_enabled_fn=lambda u: True)
        assert v.peer_records and not v.peer_leftovers

    def test_sister_record_does_not_mask_our_own_unknown(self, tmp_path):
        _write(str(tmp_path), dv.PEER_DELIVERY_DBS[0][1], "")
        v = _gather(tmp_path, gw="ok")
        assert v.headline().startswith("UNKNOWN")

    def test_gateway_running_but_no_record_is_unknown(self, tmp_path):
        v = _gather(tmp_path, gw="ok")
        assert _leg(v, "Gateway delivery").status == dv.UNKNOWN
        assert v.headline().startswith("UNKNOWN")

    def test_gateway_installed_but_down_is_unknown(self, tmp_path):
        v = _gather(tmp_path, gw="down")
        assert _leg(v, "Gateway delivery").status == dv.UNKNOWN

    def test_installed_stopped_and_disabled_is_inert(self, tmp_path):
        # moc1/moc2 (2026-09-23): relays that do not bridge by decision.
        v = _gather(tmp_path, gw="down", unit_enabled=False)
        leg = _leg(v, "Gateway delivery")
        assert leg.status == dv.INERT and "disabled" in leg.why

    def test_stopped_with_unreadable_enablement_is_unknown(self, tmp_path):
        # Unobservable must never read as a decision.
        v = _gather(tmp_path, gw="down", unit_enabled=None)
        assert _leg(v, "Gateway delivery").status == dv.UNKNOWN

    def test_unit_state_unreadable_is_unknown_not_inert(self, tmp_path):
        v = _gather(tmp_path, gw="unknown")
        assert _leg(v, "Gateway delivery").status == dv.UNKNOWN

    def test_resolver_that_raises_is_unknown(self, tmp_path):
        def boom(unit):
            raise OSError("no systemctl")
        v = dv.gather(home=str(tmp_path), now=NOW, gateway_resolver=boom,
                      enrolled_fn=_enrolled({}), unit_enabled_fn=lambda u: False)
        assert _leg(v, "Gateway delivery").status == dv.UNKNOWN


class TestDeliveryLeg:
    def test_healthy_gateway_reads_arriving(self, tmp_path):
        _gateway_box(tmp_path)
        v = _gather(tmp_path)
        leg = _leg(v, "Gateway delivery")
        assert leg.status == dv.OK and not leg.failing and not leg.thin
        text = dv.render(v)
        assert "200 confirmed / 0 failed of 200 -> 100.0%" in text
        assert v.headline().startswith("Messages are arriving")

    @pytest.mark.parametrize("age", [dv._DELIVERY_SNAPSHOT_FRESH_S + 1,
                                     -(dv._DELIVERY_SNAPSHOT_FUTURE_SLOP_S + 1)])
    def test_stale_or_future_record_prints_no_number(self, tmp_path, age):
        _gateway_box(tmp_path, snap=_snapshot(ts_age=age))
        v = _gather(tmp_path)
        leg = _leg(v, "Gateway delivery")
        assert leg.status == dv.UNKNOWN and leg.lines == []
        section = dv.render(v).split("── ")[1]
        assert "confirmed" not in section and "27768" not in section

    def test_corrupt_record_is_unknown(self, tmp_path):
        _write(str(tmp_path), dv._DELIVERY_SNAPSHOT_SUBPATH, "{not json")
        v = _gather(tmp_path)
        assert _leg(v, "Gateway delivery").status == dv.UNKNOWN

    def test_db_unobservable_is_unknown(self, tmp_path):
        snap = _snapshot()
        snap["snapshot"]["health"] = {"db_unobservable": True, "preflight_ok": None}
        _gateway_box(tmp_path, snap=snap)
        assert _leg(_gather(tmp_path), "Gateway delivery").status == dv.UNKNOWN

    def test_thin_window_reads_quiet_not_healthy(self, tmp_path):
        _gateway_box(tmp_path, snap=_snapshot(confirmed=6))
        v = _gather(tmp_path)
        assert _leg(v, "Gateway delivery").thin
        assert v.headline().startswith("QUIET")
        assert "TOO FEW to judge (6 of 20 needed)" in dv.render(v)

    def test_collapsed_rate_reads_degraded(self, tmp_path):
        _gateway_box(tmp_path, snap=_snapshot(confirmed=10, failed=30))
        v = _gather(tmp_path)
        assert _leg(v, "Gateway delivery").failing
        assert v.headline().startswith("DEGRADED")

    def test_write_errors_flag_failing(self, tmp_path):
        snap = _snapshot()
        snap["snapshot"]["health"]["consecutive_write_errors"] = 4
        _gateway_box(tmp_path, snap=snap)
        assert _leg(_gather(tmp_path), "Gateway delivery").failing


class TestQueueLeg:
    def test_dead_letters_flag_failing(self, tmp_path):
        _gateway_box(tmp_path, queue=_queue(dead_letter=3))
        v = _gather(tmp_path)
        assert _leg(v, "Gateway queue").failing
        assert "DEAD LETTERS 3" in dv.render(v)
        assert v.headline().startswith("DEGRADED")


class TestQueueClocks:
    def test_db_counts_and_process_counters_are_labelled_apart(self, tmp_path):
        # delivered/dead_letter are rows held in the queue DB (pruned by
        # retention); failed/retried/shed reset with the process. One title
        # for both was wrong (2026-09-23: 203 -> 199 with no restart).
        _gateway_box(tmp_path)
        text = dv.render(_gather(tmp_path))
        assert "held in the queue DB: delivered 203" in text
        assert "since the gateway started: failed 0" in text
        assert "Gateway queue (since" not in text


class TestSoakLegs:
    SYN = dv.SYNTH_SOAK_TIMER_UNIT

    def _result(self, passed=True):
        return {"pass_envelope": passed, "total_samples": 10, "total_ok": 10 if passed else 5,
                "ok_ratio": 1.0 if passed else 0.5, "ok_ratio_threshold": 0.95,
                "pair_results": [{"user": "u0", "peer": "p", "samples": 10,
                                  "ok": 10 if passed else 5, "p95_ms": 40.0,
                                  "fail_pct": 0.0 if passed else 50.0}]}

    def test_enrolled_fresh_pass(self, tmp_path):
        _gateway_box(tmp_path)
        _soak(str(tmp_path), "synth_soak", "synth-1.json", self._result())
        v = _gather(tmp_path, enrolled={self.SYN: True})
        leg = _leg(v, "Synth soak")
        assert leg.status == dv.OK and not leg.failing
        assert "PASS — 10/10 round trips arrived" in dv.render(v)

    def test_enrolled_failed_envelope_is_degraded(self, tmp_path):
        _gateway_box(tmp_path)
        _soak(str(tmp_path), "synth_soak", "synth-1.json", self._result(False))
        v = _gather(tmp_path, enrolled={self.SYN: True})
        assert _leg(v, "Synth soak").failing
        assert v.headline().startswith("DEGRADED")
        assert "u0->p: 5/10 ok" in dv.render(v)

    def test_enrolled_but_no_result_is_unknown(self, tmp_path):
        v = _gather(tmp_path, gw="absent", enrolled={self.SYN: True})
        assert _leg(v, "Synth soak").status == dv.UNKNOWN

    def test_enrolled_but_stale_is_unknown(self, tmp_path):
        _soak(str(tmp_path), "synth_soak", "synth-1.json", self._result(),
              age=dv._SYNTH_SOAK_STALE_AFTER_S + 60)
        v = _gather(tmp_path, gw="absent", enrolled={self.SYN: True})
        leg = _leg(v, "Synth soak")
        assert leg.status == dv.UNKNOWN and "STALE" in leg.why

    def test_hand_run_artifact_without_timer_is_inert(self, tmp_path):
        # The 2026-08-09 lesson: artifacts are not a cadence.
        _soak(str(tmp_path), "synth_soak", "synth-1.json", self._result(False),
              age=86400 * 90)
        v = _gather(tmp_path, gw="absent", enrolled={})
        leg = _leg(v, "Synth soak")
        assert leg.status == dv.INERT and "hand-run" in leg.why

    def test_enrollment_unobservable_is_not_inert(self, tmp_path):
        v = dv.gather(home=str(tmp_path), now=NOW, gateway_resolver=_gw("absent"),
                      enrolled_fn=lambda unit, home: None)
        assert _leg(v, "Synth soak").status == dv.UNKNOWN

    def test_verdictless_result_is_unknown(self, tmp_path):
        _soak(str(tmp_path), "synth_soak", "synth-1.json", {"total_ok": 3})
        v = _gather(tmp_path, gw="absent", enrolled={self.SYN: True})
        assert _leg(v, "Synth soak").status == dv.UNKNOWN


class TestRender:
    def test_every_present_leg_names_source_and_age(self, tmp_path):
        _gateway_box(tmp_path)
        text = dv.render(_gather(tmp_path))
        assert "delivery_snapshot.json" in text and "(10s old)" in text
        assert "This screen never changes anything." in text

"""Tests for monitoring.traffic_pulse — the five-axis traffic heartbeat.

Focus: the honest-failure-mode contract. Every "unobservable" path must stay
unobservable (never collapse to a fabricated 0/healthy/recovered), and the QA
confirmation must judge ONLY confirmable protocols (#74) so a mesh-heavy
gateway with no ACK consumption never reads as "delivery failure".

RF/telemetry are sourced from node_observations (node_history.db) — the fleet
runs mqtt_bridge mode, so there is no capturable local-radio packet stream;
SNR/battery arrive over MQTT and land as per-node observations.
"""
import json
from datetime import datetime

import pytest

from monitoring import traffic_pulse as tp
# connect_tuned creates WAL DBs (as production does), so the aggregator's
# connect_tuned(mode=ro) open succeeds — a raw sqlite3.connect would leave the
# fixture in rollback-journal mode, which a read-only WAL open cannot convert.
from utils.db_helpers import connect_tuned


_OBS_DDL = (
    "CREATE TABLE node_observations (id INTEGER PRIMARY KEY, node_id TEXT, "
    "timestamp REAL, snr REAL, battery INTEGER)"
)


def _make_node_db(path, rows):
    """rows: iterable of (node_id, timestamp, snr, battery)."""
    conn = connect_tuned(str(path))
    conn.execute(_OBS_DDL)
    conn.executemany(
        "INSERT INTO node_observations (node_id, timestamp, snr, battery) "
        "VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


# ─────────────────────────────────────────────────────────────────────
# Shared-constant pinning (honest_failure_modes checklist item 5)
# ─────────────────────────────────────────────────────────────────────

class TestSharedConstantPinning:
    EXPECTED = frozenset({
        "rns_delivery_failed", "retries_exhausted", "destination_unreachable",
        "delivery_timeout", "non_retriable_error", "circuit_open", "wedged",
    })

    def test_aggregator_matches_expected(self):
        assert tp._DELIVERY_FAILURE_REASONS == self.EXPECTED

    def test_watchdog_probe_matches_expected(self):
        from utils.watchdog_probes_gateway import _DELIVERY_FAILURE_REASONS as wd
        assert wd == self.EXPECTED


# ─────────────────────────────────────────────────────────────────────
# Honest confirmation (#74) — the headline guarantee
# ─────────────────────────────────────────────────────────────────────

class TestHonestConfirmation:
    def test_no_confirmable_protocol_is_unobservable_not_failure(self):
        d = {"state_by_protocol": {"confirmed": {}},
             "recent": [{"protocol": "meshtastic", "state": "sent"}] * 30}
        r = tp._honest_confirmation(d)
        assert r["status"] == tp.UNOBSERVABLE
        assert r["reason"] == "no_confirmable_protocol"

    def test_high_rate_is_ok(self):
        recent = [{"protocol": "rns", "state": "confirmed"} for _ in range(25)]
        d = {"state_by_protocol": {"confirmed": {"rns": 25}}, "recent": recent}
        r = tp._honest_confirmation(d)
        assert r["status"] == tp.OK and r["rate"] == 1.0 and r["terminal"] == 25

    def test_collapse_is_alert(self):
        recent = ([{"protocol": "rns", "state": "confirmed"} for _ in range(2)]
                  + [{"protocol": "rns", "state": "dropped",
                      "drop_reason": "rns_delivery_failed"} for _ in range(20)])
        d = {"state_by_protocol": {"confirmed": {"rns": 2}}, "recent": recent}
        r = tp._honest_confirmation(d)
        assert r["status"] == tp.ALERT and r["confirmed"] == 2 and r["failed"] == 20

    def test_insufficient_sample_is_unobservable(self):
        recent = [{"protocol": "rns", "state": "confirmed"} for _ in range(5)]
        d = {"state_by_protocol": {"confirmed": {"rns": 5}}, "recent": recent}
        r = tp._honest_confirmation(d)
        assert r["status"] == tp.UNOBSERVABLE and r["reason"] == "insufficient_sample"

    def test_benign_dedup_drops_never_count_as_failure(self):
        recent = ([{"protocol": "rns", "state": "confirmed"} for _ in range(20)]
                  + [{"protocol": "rns", "state": "dropped",
                      "drop_reason": "dedup"} for _ in range(50)])
        d = {"state_by_protocol": {"confirmed": {"rns": 20}}, "recent": recent}
        r = tp._honest_confirmation(d)
        assert r["confirmed"] == 20 and r["failed"] == 0 and r["rate"] == 1.0
        assert r["status"] == tp.OK

    def test_meshtastic_sends_excluded_from_confirmable(self):
        recent = ([{"protocol": "rns", "state": "confirmed"} for _ in range(20)]
                  + [{"protocol": "meshtastic", "state": "sent"} for _ in range(100)])
        d = {"state_by_protocol": {"confirmed": {"rns": 20}}, "recent": recent}
        r = tp._honest_confirmation(d)
        assert r["confirmable"] == ["rns"] and r["terminal"] == 20


# ─────────────────────────────────────────────────────────────────────
# DUPs block
# ─────────────────────────────────────────────────────────────────────

class TestDupsBlock:
    def test_dedup_counted_and_pct(self):
        d = {"drop_reasons": {"dedup": 100}, "state_totals": {"queued": 900},
             "recent": []}
        r = tp._dups_block(d)
        assert r["dedup_total"] == 100 and r["dedup_pct"] == 10.0
        assert r["status"] == tp.OK

    def test_recent_dup_storm_alerts(self):
        recent = [{"state": "dropped", "drop_reason": "dedup"} for _ in range(25)]
        d = {"drop_reasons": {"dedup": 25}, "state_totals": {"queued": 5},
             "recent": recent}
        r = tp._dups_block(d)
        assert r["window_dedup"] == 25 and r["status"] == tp.ALERT

    def test_high_lifetime_dedup_is_not_an_alarm(self):
        # The live moc case: 1070 lifetime dedups but none recent. Dedup is
        # benign by design — this must NOT alert.
        d = {"drop_reasons": {"dedup": 1070}, "state_totals": {"queued": 200},
             "recent": []}
        r = tp._dups_block(d)
        assert r["dedup_total"] == 1070 and r["window_dedup"] == 0
        assert r["status"] == tp.OK

    def test_delivery_down_is_unobservable(self):
        r = tp._dups_block(None)
        assert r["status"] == tp.UNOBSERVABLE and r["reason"] == "delivery_source_down"


# ─────────────────────────────────────────────────────────────────────
# Diag block
# ─────────────────────────────────────────────────────────────────────

class TestDiagBlock:
    def test_status_source_down_unobservable(self):
        assert tp._diag_block(None)["status"] == tp.UNOBSERVABLE

    def test_watchdog_absent_unobservable(self):
        r = tp._diag_block({"watchdog": {"installed": False}})
        assert r["status"] == tp.UNOBSERVABLE and r["reason"] == "watchdog_absent"

    def test_traffic_relevant_degraded_alerts(self):
        sb = {"watchdog": {"installed": True, "signals": [
            {"class": "channel_feed_dark", "severity": "degraded", "subject": "ch0"}]}}
        r = tp._diag_block(sb)
        assert r["status"] == tp.ALERT and r["traffic_relevant"] == 1

    def test_nonrelevant_signal_not_alert(self):
        sb = {"watchdog": {"installed": True, "signals": [
            {"class": "cron_verdict_stale", "severity": "degraded"}]}}
        r = tp._diag_block(sb)
        assert r["status"] == tp.OK and r["traffic_relevant"] == 0

    def test_reads_class_key_not_cls(self):
        # /api/status serializes Signal.cls as "class" — the bug we fixed.
        sb = {"watchdog": {"installed": True, "signals": [
            {"class": "mqtt_root_drift", "severity": "info"}]}}
        r = tp._diag_block(sb)
        assert r["signals"][0]["cls"] == "mqtt_root_drift"


# ─────────────────────────────────────────────────────────────────────
# QA block — verdict precedence
# ─────────────────────────────────────────────────────────────────────

class TestQABlock:
    def _delivery(self, now, **over):
        ts = now.timestamp()
        d = {"state_totals": {"sent": 100, "dropped": 0, "confirmed": 0},
             "drop_reasons": {}, "state_by_protocol": {"confirmed": {}},
             "recent": [], "last_event_ts": ts,
             "health": {"preflight_ok": True, "consecutive_write_errors": 0,
                        "last_successful_write_ts": ts}}
        d.update(over)
        return d

    @pytest.fixture
    def no_queue(self, monkeypatch):
        monkeypatch.setattr(tp, "_queue_facts", lambda: {
            "status": tp.OK, "by_status": {}, "backlog": 0, "dead_letter": 0})

    def test_delivery_down_unobservable(self):
        assert tp._qa_block(None, datetime.now())["status"] == tp.UNOBSERVABLE

    def test_healthy_fresh_unconfirmable_is_ok_but_not_claimed_reliable(self, no_queue):
        now = datetime.now()
        r = tp._qa_block(self._delivery(now), now)
        assert r["status"] == tp.OK
        assert "not observable" in r["verdict"]
        assert "reliably moving" not in r["verdict"]

    def test_healthy_with_confirmation_claims_reliable(self, no_queue):
        now = datetime.now()
        recent = [{"protocol": "rns", "state": "confirmed"} for _ in range(25)]
        d = self._delivery(now, state_by_protocol={"confirmed": {"rns": 25}},
                           recent=recent)
        r = tp._qa_block(d, now)
        assert r["status"] == tp.OK and "reliably moving" in r["verdict"]

    def test_write_canary_fail_is_alert(self, no_queue):
        now = datetime.now()
        d = self._delivery(now, health={"preflight_ok": True,
                           "consecutive_write_errors": 3,
                           "last_successful_write_ts": now.timestamp()})
        r = tp._qa_block(d, now)
        assert r["status"] == tp.ALERT and "write canary" in r["verdict"]

    def test_stale_is_stale_not_alert_dead_letter_is_concern(self, monkeypatch):
        monkeypatch.setattr(tp, "_queue_facts", lambda: {
            "status": tp.OK, "backlog": 0, "dead_letter": 6})
        now = datetime.now()
        old = now.timestamp() - 99999
        d = self._delivery(now, last_event_ts=old,
                           health={"preflight_ok": True,
                                   "consecutive_write_errors": 0,
                                   "last_successful_write_ts": old})
        r = tp._qa_block(d, now)
        assert r["status"] == tp.STALE
        assert "idle" in r["verdict"] and "6 dead-letter" in r["verdict"]
        assert r["concerns"] == ["6 dead-letter messages"]

    def test_confirmation_collapse_is_alert(self, no_queue):
        now = datetime.now()
        recent = ([{"protocol": "rns", "state": "confirmed"} for _ in range(2)]
                  + [{"protocol": "rns", "state": "dropped",
                      "drop_reason": "rns_delivery_failed"} for _ in range(20)])
        d = self._delivery(now, state_by_protocol={"confirmed": {"rns": 2}},
                           recent=recent)
        r = tp._qa_block(d, now)
        assert r["status"] == tp.ALERT and "confirmation collapsed" in r["verdict"]

    def test_circuit_open_is_alert(self, no_queue):
        now = datetime.now()
        d = self._delivery(now, drop_reasons={"circuit_open": 4})
        r = tp._qa_block(d, now)
        assert r["status"] == tp.ALERT and "circuit-open" in r["verdict"]

    def test_cross_population_rate_only_labelled_never_headline(self, no_queue):
        now = datetime.now()
        d = self._delivery(now, confirmation_rate=0.0)
        r = tp._qa_block(d, now)
        assert r["status"] == tp.OK
        assert r["cumulative_confirmation_rate_cross_population"] == 0.0


# ─────────────────────────────────────────────────────────────────────
# Node facts — RF/telemetry source (collector-stale / quiet / ok / absent)
# ─────────────────────────────────────────────────────────────────────

class TestNodeFacts:
    NOW = datetime(2026, 6, 12, 12, 0, 0)

    def _patch_db(self, monkeypatch, path):
        monkeypatch.setattr(tp, "_db_path",
                            lambda n: path if n == "node_history" else None)

    def test_db_absent_is_unobservable(self, monkeypatch):
        monkeypatch.setattr(tp, "_db_path", lambda n: None)
        nf = tp._node_facts(3600, self.NOW)
        assert nf.status == tp.UNOBSERVABLE and nf.reason == "db_absent"

    def test_recent_obs_is_ok(self, tmp_path, monkeypatch):
        ts = self.NOW.timestamp() - 120
        rows = [("!a", ts, 5.0, 90), ("!b", ts, -2.0, 80), ("!c", ts, None, None)]
        self._patch_db(monkeypatch, _make_node_db(tmp_path / "n.db", rows))
        nf = tp._node_facts(3600, self.NOW)
        assert nf.status == tp.OK and nf.obs == 3 and nf.nodes == 3
        assert nf.snr_nodes == 2 and nf.battery_nodes == 2

    def test_stale_collector_is_unobservable_not_quiet(self, tmp_path, monkeypatch):
        # Newest observation 3h old (> NODE_STALE_AFTER_S) → collector stopped.
        ts = self.NOW.timestamp() - 3 * 3600
        self._patch_db(monkeypatch, _make_node_db(tmp_path / "n.db", [("!a", ts, 5.0, 90)]))
        nf = tp._node_facts(3600, self.NOW)
        assert nf.status == tp.UNOBSERVABLE and nf.reason == "collector_stale"

    def test_recently_active_outside_window_is_quiet(self, tmp_path, monkeypatch):
        # 90m old: outside the 60m window but inside the 2h stale bound.
        ts = self.NOW.timestamp() - 5400
        self._patch_db(monkeypatch, _make_node_db(tmp_path / "n.db", [("!a", ts, 5.0, 90)]))
        nf = tp._node_facts(3600, self.NOW)
        assert nf.status == tp.QUIET

    def test_empty_db_is_unobservable_no_observations(self, tmp_path, monkeypatch):
        self._patch_db(monkeypatch, _make_node_db(tmp_path / "n.db", []))
        nf = tp._node_facts(3600, self.NOW)
        assert nf.status == tp.UNOBSERVABLE and nf.reason == "no_observations"


# ─────────────────────────────────────────────────────────────────────
# Telemetry / RF blocks over node facts
# ─────────────────────────────────────────────────────────────────────

class TestTelemetryRFBlocks:
    def _nf(self, **kw):
        base = dict(status=tp.OK, window_s=3600, obs=10, nodes=8,
                    snr_values=[], snr_nodes=0, battery_values=[], battery_nodes=0)
        base.update(kw)
        return tp._NodeFacts(**base)

    def test_telemetry_ok_with_battery(self):
        nf = self._nf(battery_values=[90, 80, 100], battery_nodes=3)
        tb = tp._telemetry_block(nf)
        assert tb["status"] == tp.OK and tb["telemetry_nodes"] == 3
        assert tb["avg_battery"] == 90

    def test_telemetry_no_battery_is_ok_not_alert(self):
        nf = self._nf(battery_values=[], battery_nodes=0, nodes=8)
        tb = tp._telemetry_block(nf)
        assert tb["status"] == tp.OK and tb["telemetry_nodes"] == 0

    def test_telemetry_quiet(self):
        nf = self._nf(status=tp.QUIET, obs=0, nodes=0)
        tb = tp._telemetry_block(nf)
        assert tb["status"] == tp.QUIET

    def test_telemetry_unobservable_propagates(self):
        nf = self._nf(status=tp.UNOBSERVABLE, reason="collector_stale",
                      latest_age_s=20000)
        tb = tp._telemetry_block(nf)
        assert tb["status"] == tp.UNOBSERVABLE and "collector appears stopped" in tb["detail"]

    def test_rf_ok_with_snr(self):
        nf = self._nf(snr_values=[5.0, -2.0, 8.0], snr_nodes=3)
        rb = tp._rf_block(nf)
        assert rb["status"] == tp.OK and rb["snr_samples"] == 3
        assert rb["quality_band"] == "excellent"

    def test_rf_bad_snr_alerts(self):
        nf = self._nf(snr_values=[-18.0, -20.0], snr_nodes=2)
        rb = tp._rf_block(nf)
        assert rb["status"] == tp.ALERT and rb["quality_band"] == "bad"

    def test_rf_no_snr_samples_is_unobservable(self):
        # Nodes heard but none carried SNR (relayed/RNS) — not a signal problem.
        nf = self._nf(snr_values=[], snr_nodes=0, nodes=5)
        rb = tp._rf_block(nf)
        assert rb["status"] == tp.UNOBSERVABLE and rb["reason"] == "no_snr_samples"

    def test_rf_quiet(self):
        nf = self._nf(status=tp.QUIET)
        rb = tp._rf_block(nf)
        assert rb["status"] == tp.QUIET


# ─────────────────────────────────────────────────────────────────────
# Delivery DB fallback parse (read-only key-scheme round-trip)
# ─────────────────────────────────────────────────────────────────────

class TestParseDeliveryDB:
    def test_round_trips_counters_and_events(self, tmp_path, monkeypatch):
        p = tmp_path / "delivery_counters.db"
        conn = connect_tuned(str(p))
        conn.execute("CREATE TABLE counters (key TEXT PRIMARY KEY, value INTEGER)")
        conn.execute("CREATE TABLE events (ts REAL, id TEXT, state TEXT, "
                     "protocol TEXT, drop_reason TEXT, note TEXT)")
        conn.executemany("INSERT INTO counters VALUES (?,?)", [
            ("state.sent", 10), ("state.confirmed", 4),
            ("state_proto.confirmed.rns", 4), ("drop.dedup", 2),
            ("meta.last_event_ts", 1781000000000),
            ("meta.preflight_ok", 1), ("meta.consecutive_write_errors", 0)])
        conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                     (1781000000.0, "m1", "confirmed", "rns", None, None))
        conn.commit()
        conn.close()
        monkeypatch.setattr(tp, "_db_path",
                            lambda n: p if n == "delivery_counters" else None)
        d = tp._parse_delivery_db()
        assert d["state_totals"]["sent"] == 10
        assert d["state_by_protocol"]["confirmed"]["rns"] == 4
        assert d["drop_reasons"]["dedup"] == 2
        assert d["confirmation_rate"] == 0.4
        assert d["recent"][-1]["state"] == "confirmed"

    def test_absent_db_returns_none(self, monkeypatch):
        monkeypatch.setattr(tp, "_db_path", lambda n: None)
        assert tp._parse_delivery_db() is None


# ─────────────────────────────────────────────────────────────────────
# End-to-end pulse_snapshot
# ─────────────────────────────────────────────────────────────────────

class TestPulseSnapshot:
    def test_shape_json_serializable_and_unobservable_when_sources_down(self, monkeypatch):
        monkeypatch.setattr(tp, "_load_delivery", lambda b, t: (None, "none"))
        monkeypatch.setattr(tp, "_http_json", lambda u, t: None)
        monkeypatch.setattr(tp, "_node_facts",
                            lambda w, n: tp._NodeFacts(status=tp.UNOBSERVABLE,
                                                       reason="db_absent", window_s=w))
        snap = tp.pulse_snapshot()
        json.dumps(snap)  # must not raise
        assert set(snap) == {"meta", "telemetry", "rf", "dups", "diag", "qa"}
        assert snap["qa"]["status"] == tp.UNOBSERVABLE
        assert snap["dups"]["status"] == tp.UNOBSERVABLE
        assert snap["diag"]["status"] == tp.UNOBSERVABLE
        assert snap["telemetry"]["status"] == tp.UNOBSERVABLE
        assert snap["rf"]["status"] == tp.UNOBSERVABLE

    def test_never_raises_on_garbage_sources(self, monkeypatch):
        monkeypatch.setattr(tp, "_load_delivery", lambda b, t: ({"junk": 1}, "http"))
        monkeypatch.setattr(tp, "_http_json", lambda u, t: {"nonsense": True})
        monkeypatch.setattr(tp, "_node_facts",
                            lambda w, n: tp._NodeFacts(status=tp.OK, window_s=w,
                                                       obs=5, nodes=3, snr_values=[],
                                                       snr_nodes=0, battery_values=[],
                                                       battery_nodes=0))
        monkeypatch.setattr(tp, "_queue_facts",
                            lambda: {"status": tp.UNOBSERVABLE, "reason": "x"})
        snap = tp.pulse_snapshot()  # must not raise
        json.dumps(snap)
        assert set(snap) == {"meta", "telemetry", "rf", "dups", "diag", "qa"}

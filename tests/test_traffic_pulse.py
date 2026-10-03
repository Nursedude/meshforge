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
from utils.observation import Seen, Unobservable
# connect_tuned creates WAL DBs (as production does), so the aggregator's
# connect_tuned(mode=ro) open succeeds — a raw sqlite3.connect would leave the
# fixture in rollback-journal mode, which a read-only WAL open cannot convert.
from utils.db_helpers import connect_tuned


_OBS_DDL = (
    "CREATE TABLE node_observations (id INTEGER PRIMARY KEY, node_id TEXT, "
    "timestamp REAL, snr REAL, rssi INTEGER, battery INTEGER)"
)
_OBS_DDL_LEGACY = (  # pre-rssi schema (an un-migrated DB read restart-free)
    "CREATE TABLE node_observations (id INTEGER PRIMARY KEY, node_id TEXT, "
    "timestamp REAL, snr REAL, battery INTEGER)"
)


def _make_node_db(path, rows, legacy=False):
    """rows: iterable of (node_id, timestamp, snr, rssi, battery).

    legacy=True writes the pre-rssi schema (drops the rssi column) to exercise
    the reader's graceful degradation on an un-migrated DB.
    """
    conn = connect_tuned(str(path))
    if legacy:
        conn.execute(_OBS_DDL_LEGACY)
        conn.executemany(
            "INSERT INTO node_observations (node_id, timestamp, snr, battery) "
            "VALUES (?,?,?,?)", [(r[0], r[1], r[2], r[4]) for r in rows])
    else:
        conn.execute(_OBS_DDL)
        conn.executemany(
            "INSERT INTO node_observations (node_id, timestamp, snr, rssi, "
            "battery) VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def _make_mqtt_cache(path, props_list):
    """Write a GeoJSON FeatureCollection like the MQTT subscriber's cache."""
    feats = [{"type": "Feature", "properties": p} for p in props_list]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
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
        r = tp._dups_block(Seen(d))
        assert r["dedup_total"] == 100 and r["dedup_pct"] == 10.0
        assert r["status"] == tp.OK

    def test_recent_dup_storm_alerts(self):
        recent = [{"state": "dropped", "drop_reason": "dedup"} for _ in range(25)]
        d = {"drop_reasons": {"dedup": 25}, "state_totals": {"queued": 5},
             "recent": recent}
        r = tp._dups_block(Seen(d))
        assert r["window_dedup"] == 25 and r["status"] == tp.ALERT

    def test_high_lifetime_dedup_is_not_an_alarm(self):
        # The live moc case: 1070 lifetime dedups but none recent. Dedup is
        # benign by design — this must NOT alert.
        d = {"drop_reasons": {"dedup": 1070}, "state_totals": {"queued": 200},
             "recent": []}
        r = tp._dups_block(Seen(d))
        assert r["dedup_total"] == 1070 and r["window_dedup"] == 0
        assert r["status"] == tp.OK

    def test_delivery_down_is_unobservable(self):
        r = tp._dups_block(Unobservable("down"))
        assert r["status"] == tp.UNOBSERVABLE and r["reason"] == "delivery_source_down"


# ─────────────────────────────────────────────────────────────────────
# Diag block
# ─────────────────────────────────────────────────────────────────────

class TestDiagBlock:
    def test_status_source_down_unobservable(self):
        assert tp._diag_block(Unobservable("down"))["status"] == tp.UNOBSERVABLE

    def test_watchdog_absent_unobservable(self):
        r = tp._diag_block(Seen({"watchdog": {"installed": False}}))
        assert r["status"] == tp.UNOBSERVABLE and r["reason"] == "watchdog_absent"

    def test_traffic_relevant_degraded_alerts(self):
        sb = {"watchdog": {"installed": True, "signals": [
            {"class": "channel_feed_dark", "severity": "degraded", "subject": "ch0"}]}}
        r = tp._diag_block(Seen(sb))
        assert r["status"] == tp.ALERT and r["traffic_relevant"] == 1

    def test_nonrelevant_signal_not_alert(self):
        sb = {"watchdog": {"installed": True, "signals": [
            {"class": "cron_verdict_stale", "severity": "degraded"}]}}
        r = tp._diag_block(Seen(sb))
        assert r["status"] == tp.OK and r["traffic_relevant"] == 0

    def test_reads_class_key_not_cls(self):
        # /api/status serializes Signal.cls as "class" — the bug we fixed.
        sb = {"watchdog": {"installed": True, "signals": [
            {"class": "mqtt_root_drift", "severity": "info"}]}}
        r = tp._diag_block(Seen(sb))
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
        assert tp._qa_block(Unobservable("down"), datetime.now())["status"] == tp.UNOBSERVABLE

    def test_healthy_fresh_unconfirmable_is_ok_but_not_claimed_reliable(self, no_queue):
        now = datetime.now()
        r = tp._qa_block(Seen(self._delivery(now)), now)
        assert r["status"] == tp.OK
        assert "not observable" in r["verdict"]
        assert "reliably moving" not in r["verdict"]

    def test_healthy_with_confirmation_claims_reliable(self, no_queue):
        now = datetime.now()
        recent = [{"protocol": "rns", "state": "confirmed"} for _ in range(25)]
        d = self._delivery(now, state_by_protocol={"confirmed": {"rns": 25}},
                           recent=recent)
        r = tp._qa_block(Seen(d), now)
        assert r["status"] == tp.OK and "reliably moving" in r["verdict"]

    def test_write_canary_fail_is_alert(self, no_queue):
        now = datetime.now()
        d = self._delivery(now, health={"preflight_ok": True,
                           "consecutive_write_errors": 3,
                           "last_successful_write_ts": now.timestamp()})
        r = tp._qa_block(Seen(d), now)
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
        r = tp._qa_block(Seen(d), now)
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
        r = tp._qa_block(Seen(d), now)
        assert r["status"] == tp.ALERT and "confirmation collapsed" in r["verdict"]

    def test_circuit_open_is_alert(self, no_queue):
        now = datetime.now()
        d = self._delivery(now, drop_reasons={"circuit_open": 4})
        r = tp._qa_block(Seen(d), now)
        assert r["status"] == tp.ALERT and "circuit-open" in r["verdict"]

    def test_cumulative_rate_only_labelled_never_headline(self, no_queue):
        now = datetime.now()
        d = self._delivery(now, confirmation_rate=0.0)
        r = tp._qa_block(Seen(d), now)
        assert r["status"] == tp.OK
        # Issue #74 display fix: the cumulative field is the honest
        # confirmable-population rate now, surfaced labelled — never the
        # headline (which is the windowed ring computation).
        assert r["cumulative_confirmable_confirmation_rate"] == 0.0
        assert "unconfirmable_sent" in r


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
        rows = [("!a", ts, 5.0, -100, 90), ("!b", ts, -2.0, -110, 80),
                ("!c", ts, None, None, None)]
        self._patch_db(monkeypatch, _make_node_db(tmp_path / "n.db", rows))
        nf = tp._node_facts(3600, self.NOW)
        assert nf.status == tp.OK and nf.obs == 3 and nf.nodes == 3
        assert nf.snr_nodes == 2 and nf.battery_nodes == 2 and nf.rssi_nodes == 2

    def test_legacy_db_without_rssi_column_degrades_gracefully(self, tmp_path, monkeypatch):
        # An un-migrated (pre-rssi) DB, read restart-free, must not crash —
        # SNR still works, RSSI is simply empty.
        ts = self.NOW.timestamp() - 120
        rows = [("!a", ts, 5.0, -100, 90)]
        self._patch_db(monkeypatch,
                       _make_node_db(tmp_path / "n.db", rows, legacy=True))
        nf = tp._node_facts(3600, self.NOW)
        assert nf.status == tp.OK and nf.snr_nodes == 1 and nf.rssi_nodes == 0
        assert nf.rssi_values == []

    def test_stale_collector_is_unobservable_not_quiet(self, tmp_path, monkeypatch):
        # Newest observation 3h old (> NODE_STALE_AFTER_S) → collector stopped.
        ts = self.NOW.timestamp() - 3 * 3600
        self._patch_db(monkeypatch,
                       _make_node_db(tmp_path / "n.db", [("!a", ts, 5.0, -100, 90)]))
        nf = tp._node_facts(3600, self.NOW)
        assert nf.status == tp.UNOBSERVABLE and nf.reason == "collector_stale"

    def test_recently_active_outside_window_is_quiet(self, tmp_path, monkeypatch):
        # 90m old: outside the 60m window but inside the 2h stale bound.
        ts = self.NOW.timestamp() - 5400
        self._patch_db(monkeypatch,
                       _make_node_db(tmp_path / "n.db", [("!a", ts, 5.0, -100, 90)]))
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
                    snr_values=[], snr_nodes=0, rssi_values=[], rssi_nodes=0,
                    battery_values=[], battery_nodes=0)
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

    def test_telemetry_enriched_with_env(self):
        nf = self._nf(battery_values=[90, 80], battery_nodes=2)
        env = {"status": tp.OK, "temp_nodes": 2, "avg_temp_c": 25.0,
               "humidity_nodes": 1, "avg_humidity": 55.0,
               "pressure_nodes": 0, "avg_pressure_hpa": None}
        tb = tp._telemetry_block(nf, env)
        assert tb["env"]["avg_temp_c"] == 25.0
        assert "env:" in tb["detail"] and "25.0°C" in tb["detail"]

    def test_no_env_sensors_leaves_detail_clean(self):
        # Cache readable but no env metrics → no detail clutter.
        nf = self._nf(battery_values=[90], battery_nodes=1)
        tb = tp._telemetry_block(nf, {"status": tp.OK, "temp_nodes": 0,
                                      "avg_temp_c": None})
        assert tb["env"]["status"] == tp.OK and "env:" not in tb["detail"]

    def test_absent_env_does_not_degrade_battery_status(self):
        nf = self._nf(battery_values=[90], battery_nodes=1)
        tb = tp._telemetry_block(nf, {"status": tp.UNOBSERVABLE, "reason": "cache_absent"})
        assert tb["status"] == tp.OK and tb["env"]["status"] == tp.UNOBSERVABLE

    def test_rf_enriched_with_rssi(self):
        nf = self._nf(snr_values=[5.0, -2.0], snr_nodes=2,
                      rssi_values=[-100, -116], rssi_nodes=2)
        rb = tp._rf_block(nf)
        assert rb["avg_rssi_dbm"] == -108 and rb["rssi_nodes"] == 2
        assert "RSSI ~-108 dBm (2 nodes)" in rb["detail"]

    def test_rf_no_rssi_leaves_detail_clean(self):
        # SNR present but no RSSI samples (relayed/MQTT nodes) — detail clean.
        nf = self._nf(snr_values=[5.0], snr_nodes=1, rssi_values=[], rssi_nodes=0)
        rb = tp._rf_block(nf)
        assert "avg_rssi_dbm" not in rb and "RSSI" not in rb["detail"]
        assert rb["status"] == tp.OK


# ─────────────────────────────────────────────────────────────────────
# MQTT environment-sensor enrichment (temp/humidity/pressure)
# ─────────────────────────────────────────────────────────────────────

class TestMqttNodeFacts:
    def test_cache_absent_is_unobservable(self, monkeypatch):
        monkeypatch.setattr(tp, "_mqtt_nodes_path", lambda: None)
        e = tp._mqtt_node_facts()
        assert e["status"] == tp.UNOBSERVABLE and e["reason"] == "cache_absent"

    def test_aggregates_temp_humidity_pressure(self, tmp_path, monkeypatch):
        p = _make_mqtt_cache(tmp_path / "m.json", [
            {"temperature": 24.0, "humidity": 55.0, "pressure": 1013.0},
            {"temperature": 26.0, "humidity": 45.0},
            {"battery": 90}])  # node with no env metrics
        monkeypatch.setattr(tp, "_mqtt_nodes_path", lambda: p)
        e = tp._mqtt_node_facts()
        assert e["status"] == tp.OK
        assert e["temp_nodes"] == 2 and e["avg_temp_c"] == 25.0
        assert e["humidity_nodes"] == 2 and e["avg_humidity"] == 50.0
        assert e["pressure_nodes"] == 1 and e["avg_pressure_hpa"] == 1013.0

    def test_no_metrics_is_ok_with_zero_counts(self, tmp_path, monkeypatch):
        # Cache readable but nodes carry no env → OK (readable), counts 0.
        p = _make_mqtt_cache(tmp_path / "m.json", [{"battery": 90}, {"snr": 5.0}])
        monkeypatch.setattr(tp, "_mqtt_nodes_path", lambda: p)
        e = tp._mqtt_node_facts()
        assert e["status"] == tp.OK and e["temp_nodes"] == 0

    def test_bogus_values_are_filtered(self, tmp_path, monkeypatch):
        p = _make_mqtt_cache(tmp_path / "m.json", [
            {"temperature": 999.0, "humidity": 150.0, "pressure": 5.0}])
        monkeypatch.setattr(tp, "_mqtt_nodes_path", lambda: p)
        e = tp._mqtt_node_facts()
        assert e["temp_nodes"] == 0 and e["humidity_nodes"] == 0
        assert e["pressure_nodes"] == 0 and e["status"] == tp.OK

    def test_unreadable_cache_is_unobservable(self, tmp_path, monkeypatch):
        p = tmp_path / "m.json"
        p.write_text("{not valid json")
        monkeypatch.setattr(tp, "_mqtt_nodes_path", lambda: p)
        e = tp._mqtt_node_facts()
        assert e["status"] == tp.UNOBSERVABLE and e["reason"] == "cache_unreadable"


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
            ("state_proto.confirmed.rns", 4),
            ("state_proto.sent.meshtastic", 8), ("drop.dedup", 2),
            ("meta.last_event_ts", 1781000000000),
            ("meta.preflight_ok", 1), ("meta.consecutive_write_errors", 0)])
        conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                     (1781000000.0, "m1", "confirmed", "rns", None, None))
        conn.commit()
        conn.close()
        monkeypatch.setattr(tp, "_db_path",
                            lambda n: p if n == "delivery_counters" else None)
        d = tp._parse_delivery_db().value
        assert d["state_totals"]["sent"] == 10
        assert d["state_by_protocol"]["confirmed"]["rns"] == 4
        assert d["drop_reasons"]["dedup"] == 2
        # Issue #74 display fix in the fallback path too: honest confirmable
        # rate (4 confirmed, 0 failures -> 1.0; dedup is benign), and the
        # mesh sends surface as the blind spot, not inflating the rate.
        assert d["confirmation_rate"] == 1.0
        assert d["unconfirmable_sent"] == 8
        assert d["confirmable_protocols"] == ["rns"]
        assert d["recent"][-1]["state"] == "confirmed"

    def test_absent_db_returns_none(self, monkeypatch):
        monkeypatch.setattr(tp, "_db_path", lambda n: None)
        assert isinstance(tp._parse_delivery_db(), Unobservable)


# ─────────────────────────────────────────────────────────────────────
# End-to-end pulse_snapshot
# ─────────────────────────────────────────────────────────────────────

class TestPulseSnapshot:
    def test_shape_json_serializable_and_unobservable_when_sources_down(self, monkeypatch):
        monkeypatch.setattr(tp, "_load_delivery",
                            lambda b, t: (Unobservable("map down; db absent"), "none"))
        monkeypatch.setattr(tp, "_http_json", lambda u, t: Unobservable("map down"))
        monkeypatch.setattr(tp, "_node_facts",
                            lambda w, n: tp._NodeFacts(status=tp.UNOBSERVABLE,
                                                       reason="db_absent", window_s=w))
        monkeypatch.setattr(tp, "_mqtt_node_facts",
                            lambda: {"status": tp.UNOBSERVABLE, "reason": "cache_absent"})
        snap = tp.pulse_snapshot()
        json.dumps(snap)  # must not raise
        assert set(snap) == {"meta", "telemetry", "rf", "dups", "diag", "qa"}
        assert snap["qa"]["status"] == tp.UNOBSERVABLE
        assert snap["dups"]["status"] == tp.UNOBSERVABLE
        assert snap["diag"]["status"] == tp.UNOBSERVABLE
        assert snap["telemetry"]["status"] == tp.UNOBSERVABLE
        assert snap["rf"]["status"] == tp.UNOBSERVABLE

    def test_never_raises_on_garbage_sources(self, monkeypatch):
        # Garbage INSIDE a Seen (the type guarantees the envelope, not the
        # payload) must still never raise.
        monkeypatch.setattr(tp, "_load_delivery", lambda b, t: (Seen({"junk": 1}), "http"))
        monkeypatch.setattr(tp, "_http_json", lambda u, t: Seen({"nonsense": True}))
        monkeypatch.setattr(tp, "_node_facts",
                            lambda w, n: tp._NodeFacts(status=tp.OK, window_s=w,
                                                       obs=5, nodes=3, snr_values=[],
                                                       snr_nodes=0, rssi_values=[],
                                                       rssi_nodes=0, battery_values=[],
                                                       battery_nodes=0))
        monkeypatch.setattr(tp, "_queue_facts",
                            lambda: {"status": tp.UNOBSERVABLE, "reason": "x"})
        monkeypatch.setattr(tp, "_mqtt_node_facts", lambda: {"garbage": True})
        snap = tp.pulse_snapshot()  # must not raise
        json.dumps(snap)
        assert set(snap) == {"meta", "telemetry", "rf", "dups", "diag", "qa"}


class TestQueueAge:
    """A seven-week-old test residue must not read as current traffic (2026-09-25)."""

    def _queue(self, tmp_path, monkeypatch, updated):
        from gateway.message_queue import PersistentMessageQueue
        path = tmp_path / "message_queue.db"
        PersistentMessageQueue(db_path=str(path))  # the real schema
        c = connect_tuned(path)
        cols = [r[1] for r in c.execute("PRAGMA table_info(messages)")]
        for i, st in enumerate(["dead_letter", "dead_letter", "pending"]):
            row = {k: "" for k in cols}
            row.update(id=f"m{i}", status=st, created_at=updated, updated_at=updated,
                       priority=0, retry_count=0, max_retries=3)
            keys = [k for k in cols if k in row]
            c.execute(f"INSERT INTO messages ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})",
                      [row[k] for k in keys])
        c.commit()
        c.close()
        monkeypatch.setattr(tp, "_db_path", lambda n: path if n == "message_queue" else None)

    def test_old_queue_carries_its_age(self, tmp_path, monkeypatch):
        self._queue(tmp_path, monkeypatch, "2026-08-05T14:55:26.448403")
        q = tp._queue_facts()
        assert (q["backlog"], q["dead_letter"]) == (1, 2)
        assert q["last_activity_age_s"] > 30 * 86400

    def test_unparseable_stamp_is_unknown_not_now(self, tmp_path, monkeypatch):
        self._queue(tmp_path, monkeypatch, "not-a-date")
        assert tp._queue_facts()["last_activity_age_s"] is None

    def test_never_any_delivery_is_quiet_not_sending(self, monkeypatch):
        monkeypatch.setattr(tp, "_queue_facts", lambda: {
            "status": tp.OK, "backlog": 0, "dead_letter": 0})
        empty = {"state_totals": {"queued": 0, "sent": 0, "confirmed": 0, "dropped": 0},
                 "drop_reasons": {}, "last_event_ts": None, "health": {}}
        qa = tp._qa_block(Seen(empty), datetime.now())
        assert qa["status"] == tp.QUIET
        assert "Sending" not in qa["verdict"]


class TestServedBlindnessIsNotAnEmptyHistory:
    """2026-10-02 (tri-state adopter #3): the map's delivery snapshot marks a
    DB it could not read THIS call with ``health.db_unobservable`` — and then
    still serves ALL-ZERO totals, an empty ring and ``last_event_ts=None``
    (gateway.delivery_counters G3). The watchdog canary and delivery_view
    honour the marker; traffic_pulse did not, so the TUI heartbeat read a
    blind DB as "0 duplicates suppressed" + "No delivery activity ever
    recorded on this box". Driven through the real HTTP layer so the test is
    independent of the loader's internal types."""

    BLIND = {
        "state_totals": {"queued": 0, "sent": 0, "confirmed": 0, "dropped": 0},
        "drop_reasons": {"dedup": 0}, "state_by_protocol": {},
        "confirmation_rate": None, "recent": [], "last_event_ts": None,
        "health": {"preflight_ok": None, "consecutive_write_errors": 0,
                   "db_unobservable": True},
    }

    def _serve(self, monkeypatch, delivery, status=None):
        import io

        def fake_urlopen(url, timeout=None):
            if url.endswith("/api/gateway/delivery"):
                body = delivery
            elif url.endswith("/api/status") and status is not None:
                body = status
            else:
                raise tp.URLError("down")
            return io.BytesIO(json.dumps(body).encode())

        monkeypatch.setattr(tp, "urlopen", fake_urlopen)
        monkeypatch.setattr(tp, "_queue_facts", lambda: {
            "status": tp.OK, "backlog": 0, "dead_letter": 0})
        monkeypatch.setattr(tp, "_db_path", lambda n: None)  # no DB fallback

    def test_blind_snapshot_is_unobservable_in_dups_and_qa(self, monkeypatch):
        self._serve(monkeypatch, self.BLIND)
        snap = tp.pulse_snapshot()
        assert snap["dups"]["status"] == tp.UNOBSERVABLE, snap["dups"]
        assert snap["qa"]["status"] == tp.UNOBSERVABLE, snap["qa"]
        assert "ever recorded" not in snap["qa"].get("verdict", "")
        assert "0 duplicates" not in snap["dups"].get("detail", "")

    def test_misshaped_payload_is_unobservable_not_quiet(self, monkeypatch):
        self._serve(monkeypatch, {"ok": True})  # 200, parses, no state_totals
        snap = tp.pulse_snapshot()
        assert snap["dups"]["status"] == tp.UNOBSERVABLE, snap["dups"]
        assert snap["qa"]["status"] == tp.UNOBSERVABLE, snap["qa"]

    def test_control_healthy_empty_snapshot_still_reads_quiet(self, monkeypatch):
        """The control that CAN fail the other way: a READABLE DB with no
        traffic is a real observation and must stay QUIET, not blind."""
        healthy = dict(self.BLIND, health={"preflight_ok": True,
                                           "consecutive_write_errors": 0})
        self._serve(monkeypatch, healthy)
        snap = tp.pulse_snapshot()
        assert snap["qa"]["status"] == tp.QUIET, snap["qa"]
        assert snap["dups"]["status"] == tp.OK, snap["dups"]

    def test_watchdog_installed_with_no_signal_list_is_not_all_clear(self, monkeypatch):
        """Same class on the diag axis: ``signals`` missing/null is not 'none'."""
        self._serve(monkeypatch, self.BLIND,
                    status={"watchdog": {"installed": True, "signals": None}})
        snap = tp.pulse_snapshot()
        assert snap["diag"]["status"] == tp.UNOBSERVABLE, snap["diag"]

    def test_stale_watchdog_block_is_unobservable_not_current(self, monkeypatch):
        """_map_status_endpoints marks a stale watchdog.json ok=False with a
        `stale:` reason but still passes its OLD signal list through."""
        old = [{"class": "queue_backlog", "severity": "degraded"}]
        self._serve(monkeypatch, self.BLIND, status={"watchdog": {
            "installed": True, "ok": False, "signals": old,
            "reason": "stale: last write 900s ago (threshold 300s)"}})
        assert tp.pulse_snapshot()["diag"]["status"] == tp.UNOBSERVABLE

    def test_control_wedge_ok_false_without_reason_is_an_observation(self, monkeypatch):
        """ok=False ALSO means 'a wedge-severity signal is live' — that is a
        reading, and must still render (ALERT), never blind."""
        live = [{"class": "queue_backlog", "severity": "wedge"}]
        self._serve(monkeypatch, self.BLIND, status={"watchdog": {
            "installed": True, "ok": False, "signals": live}})
        assert tp.pulse_snapshot()["diag"]["status"] == tp.ALERT

    def test_blind_served_answer_never_opens_the_db(self, monkeypatch):
        """Review A (2026-10-02): a mode=ro open still creates -wal/-shm, and
        'the map cannot read its DB' is the moment a root TUI must not touch
        it. The fallback keeps its OLD trigger: map did not answer."""
        self._serve(monkeypatch, self.BLIND)
        opened = []
        monkeypatch.setattr(tp, "_parse_delivery_db",
                            lambda: opened.append(1) or Unobservable("x"))
        snap = tp.pulse_snapshot()
        assert opened == []
        assert snap["meta"]["delivery_source"] == "http"
        assert snap["dups"]["detail"].startswith("map could not read")

    def test_control_map_down_still_uses_the_db_fallback(self, monkeypatch):
        self._serve(monkeypatch, None)  # body unused: delivery URL raises below
        monkeypatch.setattr(tp, "urlopen",
                            lambda url, timeout=None: (_ for _ in ()).throw(tp.URLError("down")))
        good = dict(self.BLIND, health={"preflight_ok": True,
                                        "consecutive_write_errors": 0})
        monkeypatch.setattr(tp, "_parse_delivery_db", lambda: Seen(good))
        snap = tp.pulse_snapshot()
        assert snap["meta"]["delivery_source"] == "db"
        assert snap["qa"]["status"] == tp.QUIET


class TestCollectorSnapshotIsNotReception:
    """2026-10-02, review B, re-measured on the manager box's live node_history.db:
    a window row is a collector SNAPSHOT row (written ~once per heartbeat per
    unmoved node, with its is_online flag at that moment), so a 60-min window
    held 882 rows at only 2 distinct timestamps — 838 of them offline nodes
    carrying their LAST-KNOWN SNR. is_online can itself be ~a heartbeat stale
    (review C), so the pane says "online at last snapshot", never "heard". The pane said 433 SNR nodes avg -0.9 dB (excellent); the online
    truth was 25 nodes at -7.3 dB (fair). And Meshtastic's battery=101 means
    "external power", which node_history_analytics already excludes
    (BATTERY_MAX_REAL) — two consumers, two rules (hfm #5)."""

    NOW = datetime(2026, 10, 2, 14, 0, 0)
    DDL = ("CREATE TABLE node_observations (id INTEGER PRIMARY KEY, node_id TEXT, "
           "timestamp REAL, snr REAL, rssi INTEGER, battery INTEGER, is_online INTEGER, "
           "voltage REAL)")

    def _db(self, tmp_path, monkeypatch, rows):
        path = tmp_path / "n.db"
        conn = connect_tuned(str(path))
        conn.execute(self.DDL)
        rows = [r if len(r) == 7 else tuple(r) + (None,) for r in rows]
        conn.executemany("INSERT INTO node_observations (node_id, timestamp, snr, "
                         "rssi, battery, is_online, voltage) VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()
        monkeypatch.setattr(tp, "_db_path",
                            lambda n: path if n == "node_history" else None)

    def test_offline_last_known_snr_is_not_counted(self, tmp_path, monkeypatch):
        ts = self.NOW.timestamp() - 60
        rows = [(f"!off{i}", ts, 8.0, -90, 50, 0) for i in range(20)]
        rows += [("!on1", ts, -10.0, -120, 60, 1), ("!on2", ts, -12.0, -121, 70, 1)]
        self._db(tmp_path, monkeypatch, rows)
        nf = tp._node_facts(3600, self.NOW)
        rf = tp._rf_block(nf)
        assert nf.nodes == 2 and nf.snr_nodes == 2
        assert rf["avg_snr"] == -11.0 and rf["quality_band"] == "fair"

    def test_external_power_101_is_not_a_percentage(self, tmp_path, monkeypatch):
        ts = self.NOW.timestamp() - 60
        rows = [("!a", ts, 1.0, -90, 101, 1), ("!b", ts, 1.0, -90, 101, 1),
                ("!c", ts, 1.0, -90, 40, 1), ("!d", ts, 1.0, -90, 0, 1)]
        self._db(tmp_path, monkeypatch, rows)
        tel = tp._telemetry_block(tp._node_facts(3600, self.NOW))
        assert tel["avg_battery"] == 40
        assert tel["telemetry_nodes"] == 1
        assert tel.get("external_power_nodes") == 2

    def test_control_legacy_db_without_is_online_still_reads(self, tmp_path, monkeypatch):
        """No is_online column (pre-migration) must not drop every row."""
        ts = self.NOW.timestamp() - 60
        path = _make_node_db(tmp_path / "legacy.db", [("!a", ts, -5.0, -100, 80)])
        monkeypatch.setattr(tp, "_db_path",
                            lambda n: path if n == "node_history" else None)
        nf = tp._node_facts(3600, self.NOW)
        assert nf.nodes == 1 and nf.status == tp.OK

    def test_zero_with_voltage_is_depleted_not_absent(self, tmp_path, monkeypatch):
        """Review C: live 1S cells at 2.76-3.0 V report battery 0 — a reading.
        0 with NO voltage is 'no monitor' and stays excluded."""
        ts = self.NOW.timestamp() - 60
        rows = [("!dead", ts, 1.0, -90, 0, 1, 2.9), ("!nomon", ts, 1.0, -90, 0, 1, None),
                ("!ok", ts, 1.0, -90, 80, 1, 4.0)]
        self._db(tmp_path, monkeypatch, rows)
        tel = tp._telemetry_block(tp._node_facts(3600, self.NOW))
        assert tel["avg_battery"] == 40 and tel["telemetry_nodes"] == 2
        assert tel["depleted_nodes"] == 1 and "DEPLETED" in tel["detail"]

    def test_all_offline_at_snapshot_is_not_claimed_silent(self, tmp_path, monkeypatch):
        """Review C: is_online can be a heartbeat stale, so zero online rows
        beside offline ones must not read 'No node activity — mesh quiet'."""
        ts = self.NOW.timestamp() - 60
        self._db(tmp_path, monkeypatch, [("!x", ts, 1.0, -90, 50, 0),
                                         ("!y", ts, 2.0, -91, 60, 0)])
        nf = tp._node_facts(3600, self.NOW)
        for blk in (tp._telemetry_block(nf), tp._rf_block(nf)):
            assert blk["status"] == tp.QUIET
            assert "mesh quiet" not in blk["detail"] and "No node activity" not in blk["detail"]
            assert "0 of 2" in blk["detail"]

    def test_offline_count_excludes_nodes_that_also_had_an_online_row(self, tmp_path, monkeypatch):
        ts = self.NOW.timestamp()
        self._db(tmp_path, monkeypatch, [("!flip", ts - 1800, 1.0, -90, 50, 0),
                                         ("!flip", ts - 60, 1.0, -90, 50, 1),
                                         ("!gone", ts - 60, 1.0, -90, 50, 0)])
        assert tp._node_facts(3600, self.NOW).offline_nodes == 1

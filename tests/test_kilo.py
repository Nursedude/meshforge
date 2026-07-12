"""Kilo K0 tests — registry honesty, store idempotence, ingest snapshot
purity, tri-state status. The honest-failure pins mirror the #80 class:
an unreadable registry is (None, errors) — never an empty registry; an
unobservable node is UNKNOWN — never OK, never DARK."""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kilo import registry as kreg  # noqa: E402
from kilo import store as kstore  # noqa: E402
from kilo.ingest import collect_mqtt, snapshot_readings  # noqa: E402
from kilo.__main__ import build_status  # noqa: E402
from monitoring._mqtt_types import MQTTNode  # noqa: E402

NOW = 1_760_000_000.0


def _write_registry(tmp_path, nodes):
    p = tmp_path / "kilo_nodes.json"
    p.write_text(json.dumps({"nodes": nodes}))
    return str(p)


def _node_raw(kid="bench1-esp32-env", role="esp32-sensor",
              ids=None, **kw):
    d = {"kilo_id": kid, "role": role,
         "ids": ids if ids is not None else {"meshtastic": "!0a0b0c0d"},
         "expected_metrics": ["temperature"], "cadence_s": 900}
    d.update(kw)
    return d


class TestRegistryHonesty:
    def test_valid_registry_loads(self, tmp_path):
        nodes, errs = kreg.load_registry(
            _write_registry(tmp_path, [_node_raw()]))
        assert errs == []
        assert nodes[0].kilo_id == "bench1-esp32-env"
        assert nodes[0].observable()

    def test_missing_file_is_error_not_empty(self, tmp_path):
        nodes, errs = kreg.load_registry(str(tmp_path / "absent.json"))
        assert nodes is None
        assert any("not found" in e for e in errs)

    def test_invalid_json_is_error_not_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{nope")
        nodes, errs = kreg.load_registry(str(p))
        assert nodes is None and errs

    def test_nodes_null_is_error(self, tmp_path):
        p = tmp_path / "null.json"
        p.write_text('{"nodes": null}')
        nodes, errs = kreg.load_registry(str(p))
        assert nodes is None
        assert any("must be a list" in e for e in errs)

    def test_empty_nodes_is_valid(self, tmp_path):
        nodes, errs = kreg.load_registry(_write_registry(tmp_path, []))
        assert nodes == [] and errs == []

    def test_duplicate_kilo_id_refused(self, tmp_path):
        nodes, errs = kreg.load_registry(
            _write_registry(tmp_path, [_node_raw(), _node_raw()]))
        assert nodes is None
        assert any("duplicate" in e for e in errs)

    def test_unknown_role_refused(self, tmp_path):
        nodes, errs = kreg.load_registry(
            _write_registry(tmp_path, [_node_raw(role="esp32sensor")]))
        assert nodes is None and any("role" in e for e in errs)

    def test_ip_shaped_anchor_refused(self, tmp_path):
        nodes, errs = kreg.load_registry(_write_registry(
            tmp_path, [_node_raw(ids={"meshtastic": "10.0.0.42"})]))
        assert nodes is None
        assert any("IP address" in e for e in errs)

    def test_anchor_map_is_case_insensitive(self, tmp_path):
        nodes, _ = kreg.load_registry(_write_registry(
            tmp_path, [_node_raw(ids={"meshtastic": "!0A0B0C0D"})]))
        assert kreg.anchor_map(nodes)["!0a0b0c0d"] == "bench1-esp32-env"

    def test_rns_only_node_is_legal_but_unobservable(self, tmp_path):
        nodes, errs = kreg.load_registry(_write_registry(
            tmp_path, [_node_raw(role="rnode", ids={"rns": "00" * 16})]))
        assert errs == []
        assert not nodes[0].observable()


class TestStore:
    def _conn(self, tmp_path):
        return kstore.open_db(str(tmp_path / "kilo.db"))

    def test_reobservation_is_idempotent(self, tmp_path):
        conn = self._conn(tmp_path)
        row = (NOW, "mqtt", "!0a0b0c0d", "bench1", "temperature", 21.5)
        assert kstore.record_readings(conn, [row]) == 1
        assert kstore.record_readings(conn, [row]) == 0  # UNIQUE guard

    def test_prune_drops_old_keeps_new(self, tmp_path):
        conn = self._conn(tmp_path)
        old = (NOW - 40 * 86400, "mqtt", "!x", None, "temperature", 1.0)
        new = (NOW - 1 * 86400, "mqtt", "!x", None, "temperature", 2.0)
        kstore.record_readings(conn, [old, new])
        assert kstore.prune(conn, retention_days=30, now=NOW) == 1
        rows = conn.execute("SELECT value FROM readings").fetchall()
        assert rows == [(2.0,)]

    def test_latest_by_key_newest_wins_and_lowercases(self, tmp_path):
        conn = self._conn(tmp_path)
        kstore.record_readings(conn, [
            (NOW - 100, "mqtt", "!0A0B", "b1", "temperature", 20.0),
            (NOW - 10, "mqtt", "!0A0B", "b1", "temperature", 22.0),
        ])
        assert kstore.latest_by_key(conn)[("!0a0b", "temperature")] == \
            (NOW - 10, 22.0)

    def test_seen_keys_lists_every_sender(self, tmp_path):
        conn = self._conn(tmp_path)
        kstore.record_readings(conn, [
            (NOW, "mqtt", "!feed", None, "temperature", 20.0),
            (NOW, "mqtt", "!feed", None, "humidity", 40.0),
        ])
        seen = kstore.seen_keys(conn)
        assert len(seen) == 1 and seen[0]["node_key"] == "!feed"
        assert set(seen[0]["metrics"]) == {"temperature", "humidity"}

    def test_dbspec_pair_pinned(self):
        # honest_failure_modes #5: the DBSpec entry and kilo.store are two
        # consumers of one path+retention — they move together or fail here.
        from utils.db_inventory import INVENTORY
        spec = next(s for s in INVENTORY if s.name == "kilo_telemetry")
        assert spec.path_factory() == kstore.db_path()
        assert spec.retention_days == kstore.RETENTION_DAYS
        assert spec.creator_module == "kilo.store"


def _mqtt_node(node_id="!0a0b0c0d", **metrics):
    n = MQTTNode(node_id=node_id)
    n.last_seen = datetime.fromtimestamp(NOW)
    for k, v in metrics.items():
        setattr(n, k, v)
    return n


def _registry_one(tmp_path):
    nodes, errs = kreg.load_registry(_write_registry(tmp_path, [_node_raw(
        ids={"meshtastic": "!0A0B0C0D"},
        expected_metrics=["temperature", "humidity"])]))
    assert errs == []
    return nodes


class TestSnapshotReadings:
    def test_metrics_join_registry_and_skip_none(self, tmp_path):
        reg = _registry_one(tmp_path)
        node = _mqtt_node(temperature=21.5, humidity=None, voltage=3.9)
        rows = snapshot_readings([node], reg, seen={})
        got = {(r[4], r[3]) for r in rows}
        assert ("temperature", "bench1-esp32-env") in got
        assert ("voltage", "bench1-esp32-env") in got
        assert not any(r[4] == "humidity" for r in rows)  # None ≠ 0

    def test_unchanged_snapshot_writes_nothing(self, tmp_path):
        reg = _registry_one(tmp_path)
        node = _mqtt_node(temperature=21.5)
        seen = {}
        assert snapshot_readings([node], reg, seen)
        assert snapshot_readings([node], reg, seen) == []

    def test_unregistered_sender_recorded_with_null_kilo_id(self, tmp_path):
        reg = _registry_one(tmp_path)
        rows = snapshot_readings([_mqtt_node(node_id="!feedbeef",
                                             temperature=30.0)], reg, {})
        assert rows and rows[0][3] is None


class _FakeSubscriber:
    def __init__(self, nodes, start_ok=True):
        self._nodes = nodes
        self._start_ok = start_ok
        self.stopped = False

    def start(self):
        return self._start_ok

    def stop(self):
        self.stopped = True

    def get_nodes(self):
        return self._nodes


class TestCollectMQTT:
    def test_bounded_window_collects_and_borrowed_sub_not_stopped(
            self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        reg = _registry_one(tmp_path)
        sub = _FakeSubscriber([_mqtt_node(temperature=21.5)])
        summary = collect_mqtt(conn, reg, seconds=0.2, sample_every=0.05,
                               subscriber=sub)
        assert summary["ok"] is True
        assert summary["samples"] >= 1
        assert summary["readings_written"] == 1  # dedup across samples
        assert summary["registered_seen"] == ["bench1-esp32-env"]
        # a caller-passed subscriber is BORROWED — the window must never
        # stop the owner's live feed (#75 shared-resource class)
        assert not sub.stopped

    def test_connect_failure_is_ok_false_not_quiet_air(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        sub = _FakeSubscriber([], start_ok=False)
        summary = collect_mqtt(conn, [], seconds=0.1, subscriber=sub)
        assert summary["ok"] is False
        assert "connect" in summary["error"]
        assert summary["samples"] == 0

    def test_stop_event_ends_the_window_early(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        stop = threading.Event()
        stop.set()
        sub = _FakeSubscriber([_mqtt_node(temperature=21.5)])
        t0 = time.monotonic()
        summary = collect_mqtt(conn, [], seconds=30.0, sample_every=5.0,
                               stop_event=stop, subscriber=sub)
        assert time.monotonic() - t0 < 2.0
        assert summary["ok"] is True and summary["samples"] == 0


class TestBuildStatus:
    def _nodes(self, tmp_path, **kw):
        nodes, errs = kreg.load_registry(
            _write_registry(tmp_path, [_node_raw(
                expected_metrics=["temperature", "humidity"], **kw)]))
        assert errs == []
        return nodes

    def test_fresh_node_is_ok(self, tmp_path):
        nodes = self._nodes(tmp_path)
        latest = {("!0a0b0c0d", "temperature"): (NOW - 60, 21.0),
                  ("!0a0b0c0d", "humidity"): (NOW - 60, 40.0)}
        rows = build_status(nodes, latest, now=NOW)
        assert rows[0]["state"] == "OK"

    def test_stale_metric_degrades(self, tmp_path):
        nodes = self._nodes(tmp_path)
        latest = {("!0a0b0c0d", "temperature"): (NOW - 60, 21.0),
                  ("!0a0b0c0d", "humidity"): (NOW - 10_000, 40.0)}
        rows = build_status(nodes, latest, now=NOW)
        assert rows[0]["state"] == "DEGRADED"
        assert rows[0]["metrics"]["humidity"]["ok"] is False

    def test_pre_registration_history_joins_after_registration(
            self, tmp_path):
        """The moc live-proof lesson (2026-07-04): readings captured
        BEFORE the node was registered (kilo_id NULL on disk) must count
        the moment the anchor exists — the join re-derives from CURRENT
        anchors at read time, never trusts the ingest-time stamp."""
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        kstore.record_readings(conn, [
            (NOW - 60, "mqtt", "!0A0B0C0D", None, "temperature", 21.0),
            (NOW - 60, "mqtt", "!0A0B0C0D", None, "humidity", 40.0),
        ])
        rows = build_status(self._nodes(tmp_path),
                            kstore.latest_by_key(conn), now=NOW)
        assert rows[0]["state"] == "OK"

    def test_never_seen_is_red_not_silent(self, tmp_path):
        rows = build_status(self._nodes(tmp_path), {}, now=NOW)
        assert rows[0]["state"] == "NEVER"

    def test_unobservable_anchor_is_unknown_never_ok_or_dark(self, tmp_path):
        nodes, errs = kreg.load_registry(_write_registry(
            tmp_path, [_node_raw(role="rnode", ids={"rns": "00" * 16})]))
        assert errs == []
        rows = build_status(nodes, {}, now=NOW)
        assert rows[0]["state"] == "UNKNOWN"
        assert "unobservable" in rows[0]["detail"]


# ── K0.1: claw adapter + closed-consumer gates ──────────────────────────

from kilo.ingest import CLAW_METRICS, NODE_METRICS, collect_claw  # noqa: E402


def _claw_tick(tmp_path, **overrides):
    tick = {
        "captured_at": NOW, "captured_iso": "2025-10-09T00:00:00",
        "host": "testhost", "device": "dudeclaw-99", "ok": True,
        "device_info": {"heap_free_bytes": 17764, "heap_total_bytes": 210492,
                        "uptime_s": 109368, "wifi_connected": True,
                        "wifi_rssi_dbm": -37, "chip": "ESP32-S3", "ip": None},
        "ble": {"adv_age_s": 0, "advs": 767422, "uniq": "32+",
                "last_rssi_dbm": -59, "restarts": "0/0", "window": "48/320ms"},
        "errors": {}, "brain_tier": "F",
    }
    tick.update(overrides)
    p = tmp_path / "claw_last_tick.json"
    p.write_text(json.dumps(tick))
    return str(p)


def _claw_registry(tmp_path):
    nodes, errs = kreg.load_registry(_write_registry(tmp_path, [
        {"kilo_id": "bench4-claw-edge", "role": "claw",
         "ids": {"claw": "DUDECLAW-99"},
         "expected_metrics": ["heap_free_bytes", "uptime_s",
                              "wifi_rssi_dbm"],
         "cadence_s": 300}]))
    assert errs == []
    return nodes


class TestCollectClaw:
    def test_full_tick_lands_numeric_halves_with_join(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        reg = _claw_registry(tmp_path)
        leg = collect_claw(conn, reg, tick_path=_claw_tick(tmp_path))
        assert leg["ok"] and leg["state"] == "ok"
        assert leg["device"] == "dudeclaw-99"
        assert leg["readings_written"] == len(CLAW_METRICS)
        latest = kstore.latest_by_key(conn)
        assert latest[("dudeclaw-99", "heap_free_bytes")] == (NOW, 17764.0)

    def test_absent_tick_is_inert_not_failure(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        leg = collect_claw(conn, [], tick_path=str(tmp_path / "absent.json"))
        assert leg["ok"] is True and leg["state"] == "inert"
        assert leg["readings_written"] == 0

    def test_garbage_tick_is_error_witness(self, tmp_path):
        p = tmp_path / "claw_last_tick.json"
        p.write_text("{torn")
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        leg = collect_claw(conn, [], tick_path=str(p))
        assert leg["ok"] is False and leg["state"] == "error"
        assert "unreadable" in leg["error"]

    def test_unreachable_tick_writes_nothing_fabricated(self, tmp_path):
        # both halves None (total NATS failure capture): no rows — the
        # node ages toward DARK, which is the truth; None never becomes 0.
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        path = _claw_tick(tmp_path, ok=False, device_info=None, ble=None)
        leg = collect_claw(conn, [], tick_path=path)
        assert leg["ok"] and leg["state"] == "ok"
        assert leg["readings_written"] == 0

    def test_same_tick_twice_is_idempotent(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        path = _claw_tick(tmp_path)
        assert collect_claw(conn, [], tick_path=path)["readings_written"] > 0
        assert collect_claw(conn, [], tick_path=path)["readings_written"] == 0

    def test_tick_path_pinned_to_writer(self):
        # kilo's default path formula and claw_metrics_push._tick_path()
        # are two consumers of one artifact — pinned together here.
        import importlib.util
        from kilo.ingest import default_claw_tick_path
        script = Path(__file__).parent.parent / "scripts" / "claw_metrics_push.py"
        spec = importlib.util.spec_from_file_location("cmp_pin", str(script))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert default_claw_tick_path() == mod._tick_path()


class TestMetricVocabularyClosedConsumers:
    def test_every_ingest_metric_has_a_unit(self):
        # honest_failure_modes #7: the metric vocabulary grew a second
        # producer (claw) — every producer entry must exist in UNITS or
        # this fails at test time, not at display time.
        for metric in list(NODE_METRICS.values()) + list(CLAW_METRICS.values()):
            assert metric in kstore.UNITS, f"UNITS missing {metric!r}"


class TestClawStatusJoin:
    def test_claw_anchored_node_is_observable_and_ok(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        reg = _claw_registry(tmp_path)
        collect_claw(conn, reg, tick_path=_claw_tick(tmp_path))
        rows = build_status(reg, kstore.latest_by_key(conn), now=NOW + 60)
        assert rows[0]["state"] == "OK"
        assert rows[0]["metrics"]["heap_free_bytes"]["value"] == 17764.0

    def test_dual_anchor_node_merges_both_transports(self, tmp_path):
        nodes, errs = kreg.load_registry(_write_registry(tmp_path, [
            {"kilo_id": "hybrid", "role": "claw",
             "ids": {"claw": "dudeclaw-99", "meshtastic": "!0a0b0c0d"},
             "expected_metrics": ["heap_free_bytes", "snr"],
             "cadence_s": 900}]))
        assert errs == []
        latest = {("dudeclaw-99", "heap_free_bytes"): (NOW - 30, 17764.0),
                  ("!0a0b0c0d", "snr"): (NOW - 30, 6.0)}
        rows = build_status(nodes, latest, now=NOW)
        assert rows[0]["state"] == "OK"
        assert set(rows[0]["metrics"]) == {"heap_free_bytes", "snr"}


# ── K1: edges capture, drift tri-state, link matrix ─────────────────────

from kilo import edges as kedges  # noqa: E402
from kilo.edges import (  # noqa: E402
    DRIFT_MIN_BASELINE, DRIFT_MIN_RECENT, EdgeBuffer, build_matrix,
    classify_drift, parse_edge,
)

# The moc-live payload shape (sampled 2026-07-04): per-packet from/snr/
# rssi/hops_away/hop_start/id are real; relay_node may be absent.
LIVE_TOPIC = "msh/2/json/LongFast/!32962f10"
LIVE_DATA = {
    "channel": 1, "from": 3792937512, "hop_start": 7, "hops_away": 6,
    "id": 3202878515, "payload": {"latitude_i": 214171648},
    "rssi": -34, "sender": "!32962f10", "snr": 5.75,
    "timestamp": 1783198573, "to": 4294967295, "type": "position",
}


class TestParseEdge:
    def test_live_moc_payload_shape(self):
        row, disp = parse_edge(LIVE_TOPIC, LIVE_DATA, now=NOW)
        assert disp == "ok"
        (ts, receiver, sender, channel, snr, rssi, hops_away, hop_start,
         relay, packet_id) = row
        assert ts == NOW
        assert receiver == "!32962f10"      # topic SUFFIX, not payload
        assert sender == "!e213a228"        # from = ORIGINATOR, not sender
        assert channel == "LongFast"        # NAME from topic, never slot N
        assert snr == 5.75 and rssi == -34.0
        assert hops_away == 6 and hop_start == 7
        assert relay is None                # absent in this payload
        assert packet_id == "3202878515"

    def test_self_edge_skipped_with_witness(self):
        data = dict(LIVE_DATA, **{"from": 848703248})  # == !32962f10
        row, disp = parse_edge(LIVE_TOPIC, data)
        assert row is None and disp == "self"

    def test_missing_from_is_no_sender(self):
        row, disp = parse_edge(LIVE_TOPIC, {"snr": 1.0})
        assert row is None and disp == "no_sender"

    def test_topic_without_gateway_suffix_is_no_receiver(self):
        row, disp = parse_edge("msh/2/json/LongFast", LIVE_DATA)
        assert row is None and disp == "no_receiver"

    def test_snr_zero_is_a_reading_not_absent(self):
        row, _ = parse_edge(LIVE_TOPIC, dict(LIVE_DATA, snr=0.0), now=NOW)
        assert row[4] == 0.0  # None-vs-0 discipline

    def test_absent_snr_and_hops_stay_none(self):
        data = {k: v for k, v in LIVE_DATA.items()
                if k not in ("snr", "hops_away")}
        row, disp = parse_edge(LIVE_TOPIC, data, now=NOW)
        assert disp == "ok"
        assert row[4] is None    # snr: absent ≠ 0.0
        assert row[6] is None    # hops_away: unknown ≠ direct

    def test_relay_byte_captured_and_zero_means_none(self):
        row, _ = parse_edge(LIVE_TOPIC, dict(LIVE_DATA, relay_node=168))
        assert row[8] == 168
        row, _ = parse_edge(LIVE_TOPIC, dict(LIVE_DATA, relay_node=0))
        assert row[8] is None

    def test_identities_lowercased_at_parse(self):
        row, _ = parse_edge("msh/2/json/LongFast/!32962F10", LIVE_DATA)
        assert row[1] == "!32962f10"

    def test_non_dict_payload_is_unparseable(self):
        row, disp = parse_edge(LIVE_TOPIC, ["not", "a", "dict"])
        assert row is None and disp == "unparseable"


class TestEdgeStore:
    def _conn(self, tmp_path):
        return kstore.open_db(str(tmp_path / "kilo.db"))

    def _row(self, **kw):
        d = {"ts": NOW, "receiver": "!32962f10", "sender": "!e213a228",
             "channel": "LongFast", "snr": 5.75, "rssi": -34.0,
             "hops_away": 0, "hop_start": 7, "relay": None,
             "packet_id": "111"}
        d.update(kw)
        return (d["ts"], d["receiver"], d["sender"], d["channel"], d["snr"],
                d["rssi"], d["hops_away"], d["hop_start"], d["relay"],
                d["packet_id"])

    def test_same_packet_reobserved_dedups(self, tmp_path):
        conn = self._conn(tmp_path)
        assert kstore.record_edges(conn, [self._row()]) == 1
        assert kstore.record_edges(conn, [self._row(ts=NOW + 5)]) == 0

    def test_null_packet_id_rows_never_dedup(self, tmp_path):
        # documented: without an id we record everything rather than guess
        conn = self._conn(tmp_path)
        assert kstore.record_edges(conn, [self._row(packet_id=None)]) == 1
        assert kstore.record_edges(conn, [self._row(packet_id=None)]) == 1

    def test_prune_edges_seven_days(self, tmp_path):
        conn = self._conn(tmp_path)
        kstore.record_edges(conn, [
            self._row(ts=NOW - 8 * 86400, packet_id="old"),
            self._row(ts=NOW - 1 * 86400, packet_id="new")])
        assert kstore.prune_edges(conn, now=NOW) == 1
        assert conn.execute("SELECT packet_id FROM edges").fetchall() == \
            [("new",)]

    def test_edge_retention_never_exceeds_db_retention(self):
        # the DBSpec declares 30d for the DB; edges must live within it
        assert kstore.EDGE_RETENTION_DAYS <= kstore.RETENTION_DAYS

    def test_dbspec_notes_carry_the_edges_table(self):
        from utils.db_inventory import INVENTORY
        spec = next(s for s in INVENTORY if s.name == "kilo_telemetry")
        assert "edges" in spec.notes
        assert "EDGE_RETENTION_DAYS" in spec.notes


class TestEdgeBuffer:
    def test_ok_packet_buffers_and_drain_clears(self):
        buf = EdgeBuffer()
        buf.on_packet(LIVE_TOPIC, LIVE_DATA)
        rows = buf.drain()
        assert len(rows) == 1 and rows[0][1] == "!32962f10"
        assert buf.drain() == []
        assert buf.counts() == {"ok": 1}

    def test_every_packet_lands_in_exactly_one_witness_counter(self):
        buf = EdgeBuffer()
        buf.on_packet(LIVE_TOPIC, LIVE_DATA)                       # ok
        buf.on_packet(LIVE_TOPIC, dict(LIVE_DATA, **{"from": 848703248}))
        buf.on_packet(LIVE_TOPIC, {"type": "text"})                # no from
        buf.on_packet("msh/2/json/LongFast", LIVE_DATA)            # no rx
        buf.on_packet(LIVE_TOPIC, "garbage")                       # unparse
        counts = buf.counts()
        assert counts == {"ok": 1, "self": 1, "no_sender": 1,
                          "no_receiver": 1, "unparseable": 1}
        assert sum(counts.values()) == 5

    def test_never_raises_into_the_decoder(self):
        buf = EdgeBuffer()
        buf.on_packet(None, None)  # must not raise (paho thread safety)
        assert sum(buf.counts().values()) == 1


class TestClassifyDrift:
    # baseline: 10×5.5 + 10×6.5 → median 6.0, MAD 0.5 → band =
    # max(2·1.4826·0.5, 2.0) = 2.0 (the quantization floor wins)
    BASE = [5.5] * 10 + [6.5] * 10

    def test_sparse_baseline_is_unknown_not_fine(self):
        d = classify_drift([6.0] * (DRIFT_MIN_BASELINE - 1), [7.0] * 10)
        assert d["state"] == "SPARSE"
        assert d["band_db"] is None  # no band claimed without a baseline

    def test_sparse_recent_is_unknown(self):
        d = classify_drift(self.BASE, [7.0] * (DRIFT_MIN_RECENT - 1))
        assert d["state"] == "SPARSE"

    def test_within_band_is_ok(self):
        d = classify_drift(self.BASE, [7.5] * 5)   # dev +1.5 ≤ 2.0
        assert d["state"] == "OK"
        assert d["deviation_db"] == 1.5 and d["band_db"] == 2.0

    def test_beyond_band_is_drifting(self):
        d = classify_drift(self.BASE, [9.0] * 5)   # dev +3.0 ≤ 4.0
        assert d["state"] == "DRIFTING"

    def test_beyond_twice_band_is_shifted(self):
        d = classify_drift(self.BASE, [12.0] * 5)  # dev +6.0 > 4.0
        assert d["state"] == "SHIFTED"

    def test_negative_drift_detected_too(self):
        d = classify_drift(self.BASE, [0.0] * 5)   # dev -6.0
        assert d["state"] == "SHIFTED"

    def test_zero_mad_floor_absorbs_quantization(self):
        # ultra-stable edge: MAD 0 → raw band 0 would page on 0.25 dB
        # steps; the 2 dB floor keeps small wobble OK
        d = classify_drift([6.0] * 30, [6.25] * 6)
        assert d["state"] == "OK" and d["band_db"] == 2.0

    def test_wide_baseline_widens_the_band(self):
        base = [0.0] * 10 + [4.0] * 20 + [8.0] * 10  # median 4, MAD 2
        d = classify_drift(base, [9.0] * 5)  # dev 5 ≤ 2·1.4826·2 ≈ 5.93
        assert d["state"] == "OK"

    def test_median_is_outlier_robust(self):
        d = classify_drift(self.BASE, [6.0] * 9 + [50.0])  # one rogue
        assert d["state"] == "OK"


def _edge(ts, receiver="!32962f10", sender="!e213a228", snr=6.0,
          hops=0, pid=None):
    return (ts, receiver, sender, "LongFast", snr, -34.0, hops, 7, None,
            pid)


class TestBuildMatrix:
    def _conn_with(self, tmp_path, rows):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        assert kstore.record_edges(conn, rows) == len(rows)
        return conn

    def test_direct_only_excludes_relayed_and_unknown(self, tmp_path):
        conn = self._conn_with(tmp_path, [
            _edge(NOW - 60, hops=0, pid="a"),
            _edge(NOW - 61, hops=3, pid="b"),     # relayed: last-hop snr
            _edge(NOW - 62, hops=None, pid="c"),  # unknown ≠ direct
        ])
        m = build_matrix(conn, [], now=NOW)
        assert m["totals"] == {"edges_total": 3, "edges_direct": 1,
                               "edges_relayed": 1, "edges_unknown_hops": 1,
                               "edges_no_snr": 0}
        assert len(m["cells"]) == 1 and m["cells"][0]["n"] == 1

    def test_all_hops_includes_everything(self, tmp_path):
        conn = self._conn_with(tmp_path, [
            _edge(NOW - 60, hops=0, pid="a"),
            _edge(NOW - 61, hops=3, pid="b"),
        ])
        m = build_matrix(conn, [], now=NOW, direct_only=False)
        assert m["cells"][0]["n"] == 2

    def test_labels_join_current_anchors_at_read_time(self, tmp_path):
        conn = self._conn_with(tmp_path, [_edge(NOW - 60, pid="a")])
        nodes, errs = kreg.load_registry(_write_registry(tmp_path, [
            _node_raw(kid="moc-gw", role="gateway",
                      ids={"meshtastic": "!32962F10"}),
            _node_raw(kid="bench7", ids={"meshtastic": "!E213A228"})]))
        assert errs == []
        m = build_matrix(conn, nodes, now=NOW)
        cell = m["cells"][0]
        assert cell["receiver_label"] == "moc-gw"
        assert cell["sender_label"] == "bench7"

    def test_vanished_edge_keeps_a_cell(self, tmp_path):
        # heard in the baseline, silent in the window: n=0, SPARSE —
        # a dark link must not vanish from the view
        conn = self._conn_with(
            tmp_path, [_edge(NOW - 3 * 86400, pid="old")])
        m = build_matrix(conn, [], now=NOW)
        assert len(m["cells"]) == 1
        cell = m["cells"][0]
        assert cell["n"] == 0 and cell["median_snr"] is None
        assert cell["drift"]["state"] == "SPARSE"

    def test_null_snr_counts_presence_but_not_median(self, tmp_path):
        conn = self._conn_with(tmp_path, [
            _edge(NOW - 60, snr=4.0, pid="a"),
            _edge(NOW - 61, snr=None, pid="b"),
        ])
        m = build_matrix(conn, [], now=NOW)
        cell = m["cells"][0]
        assert cell["n"] == 2 and cell["median_snr"] == 4.0
        assert m["totals"]["edges_no_snr"] == 1

    def test_drift_flows_from_baseline_to_cell(self, tmp_path):
        rows = [_edge(NOW - 2 * 86400 - i * 60, snr=6.0, pid=f"b{i}")
                for i in range(DRIFT_MIN_BASELINE)]
        rows += [_edge(NOW - 60 - i, snr=20.0, pid=f"r{i}")
                 for i in range(DRIFT_MIN_RECENT)]
        conn = self._conn_with(tmp_path, rows)
        m = build_matrix(conn, [], now=NOW)
        assert m["cells"][0]["drift"]["state"] == "SHIFTED"


class _FakeEdgeSubscriber(_FakeSubscriber):
    """Fake with the K1 packet hook: queued packets are delivered on the
    first get_nodes() tick, as if they arrived on the paho thread."""

    def __init__(self, nodes, packets=(), **kw):
        super().__init__(nodes, **kw)
        self._packets = list(packets)
        self.packet_cb = None

    def add_packet_callback(self, cb):
        self.packet_cb = cb

    def get_nodes(self):
        while self._packets and self.packet_cb is not None:
            self.packet_cb(*self._packets.pop(0))
        return super().get_nodes()


class TestCollectEdgesWiring:
    def test_edges_captured_during_window(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        sub = _FakeEdgeSubscriber(
            [_mqtt_node(temperature=21.5)],
            packets=[(LIVE_TOPIC, LIVE_DATA),
                     (LIVE_TOPIC, dict(LIVE_DATA, **{"from": 848703248}))])
        summary = collect_mqtt(conn, [], seconds=0.2, sample_every=0.05,
                               subscriber=sub)
        leg = summary["edges"]
        assert leg["enabled"] is True
        assert leg["rows_written"] == 1  # self-edge skipped
        assert leg["packets"] == {"ok": 1, "self": 1}
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1

    def test_no_edges_flag_disables_capture(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        sub = _FakeEdgeSubscriber([], packets=[(LIVE_TOPIC, LIVE_DATA)])
        summary = collect_mqtt(conn, [], seconds=0.1, sample_every=0.05,
                               subscriber=sub, edges=False)
        assert summary["edges"]["enabled"] is False
        assert summary["edges"]["reason"] == "disabled by flag"
        assert sub.packet_cb is None

    def test_subscriber_without_hook_reads_honest_not_silent(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        sub = _FakeSubscriber([_mqtt_node(temperature=21.5)])
        summary = collect_mqtt(conn, [], seconds=0.1, sample_every=0.05,
                               subscriber=sub)
        assert summary["ok"] is True
        assert summary["edges"]["enabled"] is False
        assert "no packet hook" in summary["edges"]["reason"]


class TestSubscriberPacketHook:
    def _sub(self):
        from monitoring.mqtt_subscriber import MQTTNodelessSubscriber
        return MQTTNodelessSubscriber(config={"broker": "test.invalid"})

    def test_hook_fires_per_decoded_json_packet(self):
        sub = self._sub()
        got = []
        sub.add_packet_callback(lambda t, d: got.append((t, d)))
        sub._handle_json_message(LIVE_TOPIC,
                                 json.dumps(LIVE_DATA).encode())
        # observers see the ENTRY-canonicalized packet: numeric from/to
        # are already '!hex' (one identity vocabulary for every consumer)
        expected = dict(LIVE_DATA)
        expected["from"] = f"!{LIVE_DATA['from'] & 0xFFFFFFFF:08x}"
        expected["to"] = f"!{LIVE_DATA['to'] & 0xFFFFFFFF:08x}"
        assert got == [(LIVE_TOPIC, expected)]

    def test_undecodable_payload_never_fires(self):
        sub = self._sub()
        got = []
        sub.add_packet_callback(lambda t, d: got.append(1))
        sub._handle_json_message(LIVE_TOPIC, b"{torn")
        assert got == []

    def test_raising_observer_does_not_break_decode(self):
        sub = self._sub()

        def boom(t, d):
            raise RuntimeError("observer bug")

        sub.add_packet_callback(boom)
        sub._handle_json_message(LIVE_TOPIC,
                                 json.dumps(LIVE_DATA).encode())
        # node tracking (the packet's sender field) proceeded regardless
        assert "!32962f10" in sub._nodes


class TestMatrixCLI:
    def test_empty_store_exits_zero(self, tmp_path):
        from kilo.__main__ import main
        rc = main(["--db", str(tmp_path / "kilo.db"),
                   "--registry", _write_registry(tmp_path, []), "matrix"])
        assert rc == 0

    def test_shifted_edge_exits_one(self, tmp_path):
        from kilo.__main__ import main
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        now = time.time()
        rows = [_edge(now - 2 * 86400 - i * 60, snr=6.0, pid=f"b{i}")
                for i in range(DRIFT_MIN_BASELINE)]
        rows += [_edge(now - 60 - i, snr=20.0, pid=f"r{i}")
                 for i in range(DRIFT_MIN_RECENT)]
        kstore.record_edges(conn, rows)
        conn.close()
        rc = main(["--db", str(tmp_path / "kilo.db"),
                   "--registry", _write_registry(tmp_path, []), "matrix"])
        assert rc == 1


# ── W5.1: multi-claw tick ingestion (dudeclaw-02 enrollment) ─────────────

from kilo.ingest import claw_tick_paths, collect_claw_all  # noqa: E402


class TestMultiClawIngest:
    def _tick(self, tmp_path, basename, device, ts=NOW):
        tick = {"captured_at": ts, "captured_iso": "x", "host": "h",
                "device": device, "ok": True,
                "device_info": {"heap_free_bytes": 1000,
                                "heap_total_bytes": 2000, "uptime_s": 5,
                                "wifi_rssi_dbm": -40},
                "ble": None, "errors": {}}
        (tmp_path / basename).write_text(json.dumps(tick))

    def test_glob_matches_writer_secondary_formula(self, tmp_path):
        # pair pin: kilo's glob and the writer's secondary basename are
        # two consumers of one naming rule — they move together or fail here
        from mini_dudeai.claw_telemetry import (CLAW_TICK_BASENAME,
                                                secondary_tick_basename)
        self._tick(tmp_path, CLAW_TICK_BASENAME, "dudeclaw-01")
        self._tick(tmp_path, secondary_tick_basename("dudeclaw-02"),
                   "dudeclaw-02")
        paths = claw_tick_paths(home=tmp_path)
        assert len(paths) == 2
        assert paths[0].endswith(CLAW_TICK_BASENAME)  # primary first

    def test_primary_never_globbed_twice(self, tmp_path):
        from mini_dudeai.claw_telemetry import CLAW_TICK_BASENAME
        self._tick(tmp_path, CLAW_TICK_BASENAME, "dudeclaw-01")
        assert len(claw_tick_paths(home=tmp_path)) == 1

    def test_all_ticks_land_with_device_identity(self, tmp_path):
        from mini_dudeai.claw_telemetry import (CLAW_TICK_BASENAME,
                                                secondary_tick_basename)
        self._tick(tmp_path, CLAW_TICK_BASENAME, "dudeclaw-01")
        self._tick(tmp_path, secondary_tick_basename("dudeclaw-02"),
                   "dudeclaw-02", ts=NOW + 1)
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        summary = collect_claw_all(conn, [], home=tmp_path)
        assert summary["ok"] is True
        assert len(summary["legs"]) == 2
        latest = kstore.latest_by_key(conn)
        assert ("dudeclaw-01", "heap_free_bytes") in latest
        assert ("dudeclaw-02", "heap_free_bytes") in latest

    def test_no_ticks_at_all_is_inert_not_failure(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        summary = collect_claw_all(conn, [], home=tmp_path)
        assert summary["ok"] is True
        assert summary["readings_written"] == 0
        assert all(leg["state"] == "inert" for leg in summary["legs"])

    def test_torn_secondary_fails_loud_but_primary_lands(self, tmp_path):
        from mini_dudeai.claw_telemetry import (CLAW_TICK_BASENAME,
                                                secondary_tick_basename)
        self._tick(tmp_path, CLAW_TICK_BASENAME, "dudeclaw-01")
        (tmp_path / secondary_tick_basename("dudeclaw-02")).write_text("{torn")
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        summary = collect_claw_all(conn, [], home=tmp_path)
        assert summary["ok"] is False          # error leg pages (exit 2)
        assert summary["readings_written"] > 0  # good leg still landed


# ─────────────────────────────────────────────────────────────────────────
# QA review 2026-07-05 pins — every fix from the kilo-arc xhigh pass.


class TestMainErrorBoundary:
    """V7.1: a crash must exit 2 ('could not verify'), never 1 — exit 1
    is a MEASURED DARK/SHIFTED verdict to a cron_verdict wire."""

    def test_unhandled_exception_exits_2(self, monkeypatch, capsys):
        from kilo import __main__ as kmain

        def _boom(_args):
            raise RuntimeError("synthetic crash")
        monkeypatch.setitem(kmain.__dict__, "_cmd_status", _boom)
        # re-register the subparser default via a fresh parse: main wires
        # fn=_cmd_status at parser build time, so patch through argv
        monkeypatch.setattr(kmain, "_cmd_status", _boom)
        rc = kmain.main(["status"])
        assert rc == 2
        assert "synthetic crash" in capsys.readouterr().err


class TestStatusVacuousGreen:
    """V7.3: zero observable nodes = nothing verifiable = exit 2. all([])
    must never read as green."""

    def _run_status(self, tmp_path, nodes_raw):
        from kilo import __main__ as kmain
        reg = _write_registry(tmp_path, nodes_raw)
        return kmain.main(["--registry", reg,
                           "--db", str(tmp_path / "kilo.db"), "status"])

    def test_all_unknown_registry_exits_2(self, tmp_path):
        rc = self._run_status(tmp_path, [_node_raw(
            ids={"rns": "aa" * 16}, expected_metrics=[])])
        assert rc == 2

    def test_empty_registry_exits_2(self, tmp_path):
        rc = self._run_status(tmp_path, [])
        assert rc == 2

    def test_observable_dark_node_still_exits_1(self, tmp_path):
        rc = self._run_status(tmp_path, [_node_raw()])
        assert rc == 1  # observable, never seen → NEVER → 1, not 2


class TestPresenceOnlyWentDark:
    """V7.6: seen-then-silent is NOT 'never seen' — a went-dark node
    sends the operator to the antenna, not the registry."""

    def _nodes(self, tmp_path):
        nodes, errs = kreg.load_registry(_write_registry(
            tmp_path, [_node_raw(expected_metrics=[])]))
        assert errs == []
        return nodes

    def test_fresh_presence_only_is_ok(self, tmp_path):
        latest = {("!0a0b0c0d", "temperature"): (NOW - 60, 21.0)}
        rows = build_status(self._nodes(tmp_path), latest, now=NOW)
        assert rows[0]["state"] == "OK"

    def test_never_seen_presence_only_is_never(self, tmp_path):
        rows = build_status(self._nodes(tmp_path), {}, now=NOW)
        assert rows[0]["state"] == "NEVER"

    def test_went_dark_presence_only_is_degraded_not_never(self, tmp_path):
        latest = {("!0a0b0c0d", "temperature"): (NOW - 10_000, 21.0)}
        rows = build_status(self._nodes(tmp_path), latest, now=NOW)
        assert rows[0]["state"] == "DEGRADED"
        assert "went dark" in rows[0]["detail"]


class TestBaselineHorizonWitness:
    """V7.2: 100% SPARSE is GUARANTEED while the store is younger than
    the window (or the window swallows the retention) — that global fact
    must be a witness in the result, not a per-edge bug hunt."""

    def _edge(self, ts, packet_id):
        return (ts, "!aa000001", "!bb000002", "LongFast", 5.0, -60.0,
                0, 7, None, str(packet_id))

    def test_young_store_reads_empty_by_construction(self, tmp_path):
        from kilo.edges import build_matrix
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        kstore.record_edges(conn, [self._edge(NOW - 3600 * i, i)
                                   for i in range(5)])
        m = build_matrix(conn, [], window_s=24 * 3600.0, now=NOW)
        assert m["baseline_horizon"]["empty_by_construction"] is True
        assert "younger than" in m["baseline_horizon"]["why"]

    def test_window_swallowing_retention_reads_permanent(self, tmp_path):
        from kilo.edges import build_matrix
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        m = build_matrix(conn, [], window_s=200 * 3600.0, now=NOW)
        assert m["baseline_horizon"]["empty_by_construction"] is True
        assert "retention" in m["baseline_horizon"]["why"]

    def test_mature_store_reads_false(self, tmp_path):
        from kilo.edges import build_matrix
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        kstore.record_edges(conn, [
            self._edge(NOW - 6 * 86400.0, 1),   # baseline-age edge
            self._edge(NOW - 60, 2)])           # recent edge
        m = build_matrix(conn, [], window_s=24 * 3600.0, now=NOW)
        assert m["baseline_horizon"]["empty_by_construction"] is False


class TestRegistryDuplicateGuards:
    """V7.4 + sweep S3: authoring errors the author cannot have meant."""

    def test_duplicate_anchor_value_refused(self, tmp_path):
        nodes, errs = kreg.load_registry(_write_registry(tmp_path, [
            _node_raw(kid="bench1"),
            _node_raw(kid="bench2", ids={"meshtastic": "!0A0B0C0D"})]))
        assert nodes is None
        assert any("duplicate anchor" in e for e in errs)

    def test_duplicate_json_key_refused(self, tmp_path):
        p = tmp_path / "dup.json"
        p.write_text('{"nodes": [{"kilo_id": "a"}], "nodes": []}')
        nodes, errs = kreg.load_registry(str(p))
        assert nodes is None
        assert any("duplicate JSON key" in e for e in errs)


class TestLatestByKeyNewestWins:
    """V7.5: two case-variant groups for one identity must resolve
    newest-wins, never GROUP-BY-iteration-order-wins."""

    def test_older_lowercase_group_cannot_shadow_newer_upper(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        kstore.record_readings(conn, [
            (NOW - 5000, "mqtt", "!0a0b0c0d", None, "temperature", 11.0),
            (NOW - 10, "mqtt", "!0A0B0C0D", None, "temperature", 22.0)])
        latest = kstore.latest_by_key(conn)
        ts, value = latest[("!0a0b0c0d", "temperature")]
        assert value == 22.0 and ts == NOW - 10


class TestSnapshotFreshnessHonesty:
    """V1.1 + V1.2: wrong-subject snr/rssi are out of NODE_METRICS, and a
    retained value must not re-record with a fabricated fresher ts."""

    def test_snr_rssi_not_in_node_metrics(self):
        from kilo.ingest import NODE_METRICS
        assert "snr" not in NODE_METRICS
        assert "rssi" not in NODE_METRICS

    def test_unchanged_value_with_advanced_last_seen_not_rerecorded(
            self, tmp_path):
        reg = _registry_one(tmp_path)
        node = _mqtt_node(temperature=21.5)
        seen = {}
        assert snapshot_readings([node], reg, seen)
        node.last_seen = datetime.fromtimestamp(NOW + 30)  # any packet
        assert snapshot_readings([node], reg, seen) == []

    def test_changed_value_still_records(self, tmp_path):
        reg = _registry_one(tmp_path)
        node = _mqtt_node(temperature=21.5)
        seen = {}
        assert snapshot_readings([node], reg, seen)
        node.temperature = 25.0
        node.last_seen = datetime.fromtimestamp(NOW + 30)
        assert len(snapshot_readings([node], reg, seen)) == 1


class TestObservableAnchorsClosedGate:
    """V4.2: adding an anchor kind before its collector exists would flip
    honest UNKNOWN into red NEVER fleet-wide. This gate FAILS until the
    new kind ships with a collector and is added here deliberately."""

    def test_every_observable_kind_has_a_collector(self):
        from kilo.ingest import collect_claw, collect_mqtt
        from kilo.ingest import collect_scout as _collect_scout
        collectors = {"meshtastic": collect_mqtt, "claw": collect_claw,
                      "scout": _collect_scout}
        assert set(kreg.OBSERVABLE_ANCHORS) == set(collectors), (
            "OBSERVABLE_ANCHORS grew without a collector (or vice versa) —"
            " ship the ingest adapter and update this map in the same"
            " commit, or nodes flip UNKNOWN→NEVER with nothing observing"
            " them")


class TestDispositionsClosed:
    """Simplification finding: DISPOSITIONS was dead documentation — now
    it is the enforced closed vocabulary."""

    def test_parse_edge_dispositions_are_in_the_closed_set(self):
        from kilo.edges import DISPOSITIONS, parse_edge
        cases = [
            ("msh/2/json/LongFast/!aa000001", {"from": 2}, "ok"),
            ("msh/2/json/LongFast/!aa000001", {"from": 0xAA000001}, "self"),
            ("nope", {"from": 2}, "no_receiver"),
            ("msh/2/json/LongFast/!aa000001", {"from": None}, "no_sender"),
            ("msh/2/json/LongFast/!aa000001", "notadict", "unparseable"),
        ]
        for topic, data, expected in cases:
            _row, disp = parse_edge(topic, data)
            assert disp == expected
            assert disp in DISPOSITIONS

    def test_overflow_is_witnessed_not_silent(self, monkeypatch):
        from kilo import edges as kedges
        monkeypatch.setattr(kedges, "EDGE_BUFFER_MAX_ROWS", 2)
        buf = kedges.EdgeBuffer()
        for i in range(4):
            buf.on_packet("msh/2/json/LongFast/!aa000001",
                          {"from": 100 + i, "id": i})
        assert len(buf.drain()) == 2
        counts = buf.counts()
        assert counts["overflow"] == 2
        assert "overflow" in kedges.DISPOSITIONS


class TestCollectClawWitnesses:
    """V1.3 + sweep S5: the tick's own health verdict surfaces, and a
    bool captured_at cannot masquerade as an epoch."""

    def _tick(self, tmp_path, ok, errors, captured_at=NOW):
        p = tmp_path / "claw_last_tick.json"
        p.write_text(json.dumps({
            "device": "dudeclaw-01", "captured_at": captured_at,
            "ok": ok, "errors": errors,
            "device_info": {"heap_free_bytes": 100000}}))
        return str(p)

    def test_half_dead_tick_carries_its_verdict(self, tmp_path):
        from kilo.ingest import collect_claw
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        leg = collect_claw(conn, [], tick_path=self._tick(
            tmp_path, ok=False, errors=["ble_stats"]))
        assert leg["state"] == "ok"          # rows landed
        assert leg["tick_ok"] is False       # but the verdict is visible
        assert leg["tick_errors"] == 1

    def test_bool_captured_at_is_shape_drift_error(self, tmp_path):
        from kilo.ingest import collect_claw
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        leg = collect_claw(conn, [], tick_path=self._tick(
            tmp_path, ok=True, errors=[], captured_at=True))
        assert leg["state"] == "error"
        assert leg["ok"] is False


class TestBorrowedSubscriberContract:
    """Sweep S2 + V8.3: register/remove must pair on a borrowed
    subscriber, including the connect-failure early return."""

    class _HookedSub(_FakeSubscriber):
        def __init__(self, nodes, start_ok=True):
            super().__init__(nodes, start_ok)
            self.callbacks = []

        def add_packet_callback(self, cb):
            self.callbacks.append(cb)

        def remove_packet_callback(self, cb):
            self.callbacks.remove(cb)

    def test_window_end_detaches_callback_and_never_stops(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        sub = self._HookedSub([_mqtt_node(temperature=21.5)])
        summary = collect_mqtt(conn, [], seconds=0.1, sample_every=0.05,
                               subscriber=sub)
        assert summary["ok"] is True
        assert sub.callbacks == []      # detached
        assert not sub.stopped          # borrowed, never stopped

    def test_connect_failure_also_detaches(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        sub = self._HookedSub([], start_ok=False)
        summary = collect_mqtt(conn, [], seconds=0.1, subscriber=sub)
        assert summary["ok"] is False
        assert sub.callbacks == []


class TestCollectKnobGuards:
    """Sweep S1: a zero cadence must not busy-loop a write-per-iteration
    hot loop for the whole window."""

    def test_zero_sample_every_completes_with_bounded_samples(self,
                                                              tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        sub = _FakeSubscriber([_mqtt_node(temperature=21.5)])
        t0 = time.monotonic()
        summary = collect_mqtt(conn, [], seconds=0.2, sample_every=0,
                               subscriber=sub)
        assert time.monotonic() - t0 < 5.0
        assert summary["samples"] <= 2  # sanitized cadence, not a hot loop

    def test_non_finite_seconds_is_bounded(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        stop = threading.Event()
        stop.set()  # window exits immediately; the pin is the sanitize
        sub = _FakeSubscriber([])
        summary = collect_mqtt(conn, [], seconds=float("inf"),
                               sample_every=5.0, stop_event=stop,
                               subscriber=sub)
        assert summary["window_s"] != float("inf")


# ── 2026-07-11: scout adapter (router-agent tick mirror) ─────────────────

from kilo.ingest import SCOUT_METRICS, SCOUT_MIRROR_SUBDIR, \
    collect_scout, collect_scout_all  # noqa: E402


def _scout_tick(tmp_path, name="owrt-test_tick.json", **overrides):
    tick = {
        "schema": 1, "device": "owrt-test", "captured_at": NOW, "ok": True,
        "errors": [], "notes": [],
        "service": {"name": "meshtasticd", "running": True, "pid": 100},
        "meshtasticd": {"vsz_kb": 18300, "rss_kb": 9000, "maps": 110,
                        "age_s": 4000},
        "radio_tcp": "ok",
        "host": {"uptime_s": 5000, "load_1m": 0.15,
                 "mem_available_kb": 800000, "mem_total_kb": 1000000},
        "persistence": {"ok": True, "data_dir": "/etc/meshtasticd/data",
                        "fstype": "ext4"},
        "opkg_hold": True,
    }
    tick.update(overrides)
    p = tmp_path / name
    p.write_text(json.dumps(tick))
    return str(p)


def _scout_registry(tmp_path):
    nodes, errs = kreg.load_registry(_write_registry(tmp_path, [
        {"kilo_id": "closet-openwrt-router", "role": "router",
         "ids": {"scout": "OWRT-TEST"},
         "expected_metrics": ["vsz_kb", "mmap_regions", "uptime_s"],
         "cadence_s": 1800}]))
    assert errs == []
    return nodes


class TestCollectScout:
    def test_full_tick_lands_numeric_halves_with_join(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        reg = _scout_registry(tmp_path)
        leg = collect_scout(conn, reg, tick_path=_scout_tick(tmp_path))
        assert leg["ok"] and leg["state"] == "ok"
        assert leg["device"] == "owrt-test"
        assert leg["readings_written"] == len(SCOUT_METRICS)
        latest = kstore.latest_by_key(conn)
        assert latest[("owrt-test", "mmap_regions")] == (NOW, 110.0)
        assert latest[("owrt-test", "vsz_kb")] == (NOW, 18300.0)

    def test_absent_tick_is_inert_not_failure(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        leg = collect_scout(conn, [], tick_path=str(tmp_path / "absent.json"))
        assert leg["ok"] is True and leg["state"] == "inert"
        assert leg["readings_written"] == 0

    def test_garbage_tick_is_error_witness(self, tmp_path):
        p = tmp_path / "owrt_tick.json"
        p.write_text("{torn")
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        leg = collect_scout(conn, [], tick_path=str(p))
        assert leg["ok"] is False and leg["state"] == "error"
        assert "unreadable" in leg["error"]

    def test_null_halves_write_nothing_fabricated(self, tmp_path):
        # a degraded router read lands as null in the tick — None must
        # never become a fabricated 0 row (the claw_telemetry contract).
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        path = _scout_tick(
            tmp_path, ok=False,
            errors=["VmSize unreadable"], meshtasticd=None)
        leg = collect_scout(conn, [], tick_path=path)
        assert leg["ok"] and leg["state"] == "ok"
        assert leg["tick_ok"] is False and leg["tick_errors"] == 1
        latest = kstore.latest_by_key(conn)
        assert ("owrt-test", "vsz_kb") not in latest
        assert latest[("owrt-test", "uptime_s")] == (NOW, 5000.0)

    def test_missing_identity_is_error_witness(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        path = _scout_tick(tmp_path, device="")
        leg = collect_scout(conn, [], tick_path=path)
        assert leg["ok"] is False and "shape drift" in leg["error"]

    def test_same_tick_twice_is_idempotent(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        path = _scout_tick(tmp_path)
        assert collect_scout(conn, [], tick_path=path)["readings_written"] > 0
        assert collect_scout(conn, [], tick_path=path)["readings_written"] == 0

    def test_collect_scout_all_globs_mirror_dir(self, tmp_path):
        home = tmp_path / "home"
        mirror = home / SCOUT_MIRROR_SUBDIR
        mirror.mkdir(parents=True)
        _scout_tick(mirror, name="owrt-a_tick.json", device="owrt-a")
        _scout_tick(mirror, name="owrt-b_tick.json", device="owrt-b")
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        summary = collect_scout_all(conn, [], home=home)
        assert summary["ok"] is True
        assert len(summary["legs"]) == 2
        devices = {leg["device"] for leg in summary["legs"]}
        assert devices == {"owrt-a", "owrt-b"}

    def test_collect_scout_all_no_mirror_dir_is_inert(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        summary = collect_scout_all(conn, [], home=tmp_path / "empty-home")
        assert summary["ok"] is True and summary["legs"] == []

    def test_every_scout_metric_has_a_unit(self):
        # honest_failure_modes #7: third producer joins the vocabulary —
        # closed consumers stay closed.
        for metric in SCOUT_METRICS.values():
            assert metric in kstore.UNITS, f"UNITS missing {metric!r}"


class TestScoutRegistry:
    def test_router_role_and_scout_anchor_accepted(self, tmp_path):
        nodes = _scout_registry(tmp_path)
        assert nodes[0].observable()

    def test_ip_shaped_scout_anchor_still_refused(self, tmp_path):
        nodes, errs = kreg.load_registry(_write_registry(tmp_path, [
            {"kilo_id": "r1", "role": "router",
             "ids": {"scout": "192.0.2.10"}, "cadence_s": 1800}]))
        assert nodes is None
        assert any("IP address" in e for e in errs)

    def test_scout_join_is_case_insensitive(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        reg = _scout_registry(tmp_path)   # registry anchors "OWRT-TEST"
        leg = collect_scout(conn, reg, tick_path=_scout_tick(tmp_path))
        assert leg["readings_written"] == len(SCOUT_METRICS)
        rows = conn.execute(
            "SELECT DISTINCT kilo_id FROM readings").fetchall()
        assert rows == [("closet-openwrt-router",)]

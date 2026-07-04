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
    def test_bounded_window_collects_and_stops(self, tmp_path):
        conn = kstore.open_db(str(tmp_path / "kilo.db"))
        reg = _registry_one(tmp_path)
        sub = _FakeSubscriber([_mqtt_node(temperature=21.5)])
        summary = collect_mqtt(conn, reg, seconds=0.2, sample_every=0.05,
                               subscriber=sub)
        assert summary["ok"] is True
        assert summary["samples"] >= 1
        assert summary["readings_written"] == 1  # dedup across samples
        assert summary["registered_seen"] == ["bench1-esp32-env"]
        assert sub.stopped

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

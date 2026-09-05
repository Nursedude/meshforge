"""Voltage capture in node_history (2026-09-05).

WHY: a battery-discharge measurement on the fleet had been "running" for
1910 hours reporting a flat 100.0%, because `battery` is a 1/16-quantized SoC
percentage — one 6.25% step is ~3h on a 4000mAh pack — and the node in
question was emitting no telemetry at all. Voltage rides the same
DeviceMetrics packet at mV resolution (`voltage=4.122000`) and, unlike SoC%,
separates CHARGING (pinned near 4.2V) from discharging. It was being dropped
twice: the map collector never read it into the feature properties, and
node_observations had no column for it.

The load-bearing test here is the 0.0 one. `voltage=0.000000` is a REAL value
on this mesh, sent by USB-powered nodes that have no pack to measure. Storing
it verbatim puts an absent-value sentinel into the measurement domain and a
discharge analysis reads a dead battery.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from utils.node_history import _clean_voltage, NodeHistoryDB  # noqa: E402


class TestCleanVoltage:
    def test_real_reading_passes_through_at_full_precision(self):
        # Verbatim from the mesh: `voltage=4.122000`.
        assert _clean_voltage(4.122000) == pytest.approx(4.122)

    def test_zero_is_not_measured_not_a_dead_pack(self):
        """The whole reason this helper exists. Observed 2026-09-05 from
        nodes reporting battery_level=101 (USB) with voltage=0.000000."""
        assert _clean_voltage(0.0) is None
        assert _clean_voltage("0.000000") is None

    def test_negative_and_absurd_are_rejected(self):
        assert _clean_voltage(-1.0) is None
        assert _clean_voltage(9999.0) is None

    def test_none_and_junk_are_none_not_raises(self):
        assert _clean_voltage(None) is None
        assert _clean_voltage("") is None
        assert _clean_voltage("nope") is None
        assert _clean_voltage(float("nan")) is None

    def test_string_numerics_are_accepted(self):
        assert _clean_voltage("4.05") == pytest.approx(4.05)

    def test_plausible_lipo_range_survives(self):
        for v in (2.9, 3.0, 3.7, 4.2, 4.35):
            assert _clean_voltage(v) == pytest.approx(v), v


def _feature(node_id, **props):
    base = {"id": node_id, "name": node_id, "network": "meshtastic",
            "is_online": True}
    base.update(props)
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-155.2, 19.4]},
            "properties": base}


class TestVoltagePersists:
    """End-to-end through the real writer against a real SQLite file."""

    def _rows(self, hist):
        conn = hist._connect()
        try:
            return conn.execute(
                "SELECT node_id, battery, voltage FROM node_observations "
                "ORDER BY id").fetchall()
        finally:
            conn.close()

    def test_voltage_is_stored(self, tmp_path):
        h = NodeHistoryDB(db_path=tmp_path / "n.db")
        assert h.record_observations(
            [_feature("!aaa", battery=94, voltage=4.122)]) == 1
        rows = self._rows(h)
        assert rows[0][1] == 94
        assert rows[0][2] == pytest.approx(4.122)

    def test_zero_voltage_stores_null_beside_a_real_battery(self, tmp_path):
        """A USB node: battery_level=101, voltage=0.0. The battery value is
        real and kept; the voltage is absent and must be NULL, not 0.0."""
        h = NodeHistoryDB(db_path=tmp_path / "n.db")
        h.record_observations([_feature("!bbb", battery=101, voltage=0.0)])
        rows = self._rows(h)
        assert rows[0][1] == 101
        assert rows[0][2] is None, "0.0 must not be stored as a reading"

    def test_absent_voltage_is_null_not_zero(self, tmp_path):
        h = NodeHistoryDB(db_path=tmp_path / "n.db")
        h.record_observations([_feature("!ccc", battery=50)])
        assert self._rows(h)[0][2] is None


class TestMigrationOnAPreExistingDatabase:
    """The fleet's live DBs predate the column; the ALTER must be idempotent
    and must not destroy the rows already there (same shape as the rssi
    migration this follows)."""

    def test_old_table_gains_the_column_and_keeps_its_rows(self, tmp_path):
        import sqlite3
        db = tmp_path / "old.db"
        con = sqlite3.connect(str(db))
        con.execute("""CREATE TABLE node_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL,
            timestamp REAL NOT NULL, latitude REAL NOT NULL,
            longitude REAL NOT NULL, altitude REAL, snr REAL,
            battery INTEGER, is_online INTEGER DEFAULT 1,
            network TEXT DEFAULT 'meshtastic', hardware TEXT DEFAULT '',
            role TEXT DEFAULT '', via_mqtt INTEGER DEFAULT 0,
            name TEXT DEFAULT '')""")
        con.execute("INSERT INTO node_observations "
                    "(node_id, timestamp, latitude, longitude, battery) "
                    "VALUES ('!old', 1.0, 19.4, -155.2, 77)")
        con.commit(); con.close()

        h = NodeHistoryDB(db_path=db)          # __init__ runs the migration
        conn = h._connect()
        try:
            cols = {r[1] for r in
                    conn.execute("PRAGMA table_info(node_observations)")}
            assert "voltage" in cols
            assert "rssi" in cols
            old = conn.execute("SELECT battery, voltage FROM node_observations "
                               "WHERE node_id='!old'").fetchone()
            assert old == (77, None), "pre-existing row lost or defaulted"
        finally:
            conn.close()

        # And the migrated DB still takes new writes with voltage.
        assert h.record_observations(
            [_feature("!new", battery=88, voltage=3.98)]) == 1

    def test_migration_is_idempotent(self, tmp_path):
        db = tmp_path / "twice.db"
        NodeHistoryDB(db_path=db)
        NodeHistoryDB(db_path=db)          # must not raise "duplicate column"
        h = NodeHistoryDB(db_path=db)
        assert h.record_observations(
            [_feature("!x", battery=1, voltage=3.1)]) == 1


# ── The branch I missed on the first pass ────────────────────────────────────
# 2026-09-05: the column shipped, the collector was patched, the service was
# restarted — and 377 fresh rows landed with voltage=NULL. The patch had gone
# into `_extract_node_info_without_position`, and record_observations() REQUIRES
# lat/lon, so position-less features never reach the observation time-series at
# all. Two builders read the same deviceMetrics dict; only one feeds the table.
#
# Same shape as the 2026-08-09 lesson in persistent_issues: grep EVERY branch
# that reaches the same return, not just the one you came in through. These
# tests pin BOTH builders so a future fix cannot land in only one.

class TestBothFeatureBuildersCarryVoltage:
    def _collector(self):
        from utils.map_data_collector import MapDataCollector
        return MapDataCollector.__new__(MapDataCollector)

    def test_make_feature_emits_voltage(self):
        """_make_feature is the POSITIONED path — the one whose features
        actually become node_observations rows."""
        f = self._collector()._make_feature(
            node_id="!aaa", name="n", lat=19.4, lon=-155.2,
            battery=94, voltage=4.122)
        assert f["properties"]["voltage"] == pytest.approx(4.122)

    def test_make_feature_omits_voltage_when_absent(self):
        """Absent must stay absent — not a null that looks measured."""
        f = self._collector()._make_feature(
            node_id="!aaa", name="n", lat=19.4, lon=-155.2, battery=94)
        assert "voltage" not in f["properties"]

    def test_positioned_collector_reads_voltage_from_device_metrics(self):
        """The specific regression: the positioned extractor must pull
        'voltage' off the same deviceMetrics dict it takes batteryLevel from."""
        import inspect

        from utils import _map_collector_meshtastic as mc
        src = inspect.getsource(mc.MeshtasticDataCollectorMixin._parse_tcp_node)
        assert "device_metrics.get('batteryLevel')" in src
        assert "device_metrics.get('voltage')" in src, (
            "the POSITIONED builder must read voltage — patching only the "
            "position-less one populates nothing")

    def test_positionless_collector_also_reads_voltage(self):
        import inspect

        from utils import _map_collector_meshtastic as mc
        src = inspect.getsource(
            mc.MeshtasticDataCollectorMixin._extract_node_info_without_position)
        assert "device_metrics.get('voltage')" in src

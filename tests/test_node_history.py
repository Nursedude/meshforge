"""Tests for NodeHistoryDB — focused on T2.2 auto-prune behavior.

Issue #44 follow-up: a 14 GB WAL accumulated over 4 days on a fleet box
because record_observations had no automatic pruning. The hourly auto-prune
ensures the WAL can't grow unbounded between manual cleanup() calls.
"""

import time
from pathlib import Path

import pytest

from utils.node_history import NodeHistoryDB


@pytest.fixture
def hist(tmp_path: Path) -> NodeHistoryDB:
    db_path = tmp_path / "node_history.db"
    return NodeHistoryDB(db_path=db_path, retention_seconds=86400)


def _feature(node_id: str, ts_offset: float = 0.0,
             lat: float = 0.2, lon: float = 0.1,
             network: str = "meshtastic"):
    """Build a minimal GeoJSON feature for record_observations."""
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "id": node_id, "name": node_id, "is_online": True,
            "network": network,
        },
    }


class TestAutoPrune:
    def test_first_call_does_not_prune_old_rows_within_retention(self, hist):
        # Seed with rows inside retention window — should survive any prune.
        hist.record_observations([_feature("!a"), _feature("!b")])
        # Force the prune cadence by clearing the timer.
        hist._last_prune_ts = 0.0
        hist.record_observations([_feature("!c")])
        # All three nodes should be present (none aged out).
        traj_a = hist.get_trajectory("!a", hours=24)
        traj_c = hist.get_trajectory("!c", hours=24)
        assert len(traj_a) == 1
        assert len(traj_c) == 1

    def test_prune_removes_rows_older_than_retention(self, hist):
        # Insert one row, then forge its timestamp into the past beyond retention.
        hist.record_observations([_feature("!old")])
        old_cutoff = time.time() - hist.retention_seconds - 60
        import sqlite3
        conn = sqlite3.connect(str(hist.db_path))
        try:
            conn.execute(
                "UPDATE node_observations SET timestamp = ? WHERE node_id = ?",
                (old_cutoff, "!old"),
            )
            conn.commit()
        finally:
            conn.close()
        # Force prune cadence; new insert triggers the auto-prune check.
        hist._last_prune_ts = 0.0
        # Bypass MIN_RECORD_INTERVAL throttle for !new.
        hist.record_observations([_feature("!new")])
        traj_old = hist.get_trajectory("!old", hours=72)
        traj_new = hist.get_trajectory("!new", hours=72)
        assert traj_old == [], "old row was not pruned"
        assert len(traj_new) == 1

    def test_prune_skipped_within_cadence_window(self, hist):
        # Set a very recent prune so cadence check skips the actual DELETE.
        hist._last_prune_ts = time.time()
        # Force-insert an aged-out row (older than retention).
        hist.record_observations([_feature("!aged")])
        old_cutoff = time.time() - hist.retention_seconds - 60
        import sqlite3
        conn = sqlite3.connect(str(hist.db_path))
        try:
            conn.execute(
                "UPDATE node_observations SET timestamp = ? WHERE node_id = ?",
                (old_cutoff, "!aged"),
            )
            conn.commit()
        finally:
            conn.close()
        # New insert; prune should NOT fire (cadence not reached).
        # Bump _last_prune_ts to JUST inside the cadence window.
        hist._last_prune_ts = time.time() - 60  # 60s ago, well inside 1h cadence
        hist.record_observations([_feature("!fresh")])
        traj_aged = hist.get_trajectory("!aged", hours=72)
        # The aged row should still be present because cadence blocked the DELETE.
        assert len(traj_aged) == 1, "prune fired despite cadence window"

    def test_prune_disabled_when_interval_zero(self, hist):
        # Operators can opt out by setting interval to 0 (e.g., for tests).
        hist._prune_interval_seconds = 0
        hist._last_prune_ts = 0.0  # cadence would otherwise trigger
        hist.record_observations([_feature("!aged")])
        old_cutoff = time.time() - hist.retention_seconds - 60
        import sqlite3
        conn = sqlite3.connect(str(hist.db_path))
        try:
            conn.execute(
                "UPDATE node_observations SET timestamp = ? WHERE node_id = ?",
                (old_cutoff, "!aged"),
            )
            conn.commit()
        finally:
            conn.close()
        hist.record_observations([_feature("!fresh")])
        traj_aged = hist.get_trajectory("!aged", hours=72)
        assert len(traj_aged) == 1, "prune ran despite interval=0 disable"


class TestConnectionPragmas:
    """Lock in WAL + tuned pragmas — regression guard for the fleet-host
    2026-04-26 wedge where rollback-journal mode caused multi-minute
    fdatasync stalls that blocked /api/nodes/geojson responses."""

    def test_journal_mode_is_wal(self, hist):
        # WAL is persistent on the DB header — first connect converts it.
        conn = hist._connect()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal", f"expected WAL, got {mode!r}"
        finally:
            conn.close()

    def test_synchronous_is_normal(self, hist):
        conn = hist._connect()
        try:
            # synchronous values: 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            assert sync == 1, f"expected synchronous=NORMAL (1), got {sync}"
        finally:
            conn.close()

    def test_journal_size_limit_is_capped(self, hist):
        conn = hist._connect()
        try:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == 67108864, f"expected 64 MB cap, got {limit}"
        finally:
            conn.close()

    def test_wal_persists_across_connections(self, hist):
        # Once one connection sets WAL, subsequent connections inherit it
        # from the DB header — no re-conversion needed.
        c1 = hist._connect()
        c1.close()
        c2 = hist._connect()
        try:
            mode = c2.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            c2.close()


class TestValueDedup:
    """Heartbeat + value-change dedup. Stationary nodes must not flood the
    DB with identical-position rows after the time-throttle has elapsed.
    Mirrors the meshforge-maps Phase 1 fix (commit b264b60)."""

    def _hist(self, tmp_path: Path, **kwargs):
        return NodeHistoryDB(
            db_path=tmp_path / "value_dedup.db",
            retention_seconds=86400,
            **kwargs,
        )

    def test_skips_when_position_unchanged_within_heartbeat(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        # Bypass MIN_RECORD_INTERVAL by aging the first record's last_recorded.
        assert h.record_observations([_feature("!s")]) == 1
        h._last_recorded["!s"] -= 120  # past time-throttle, inside heartbeat
        assert h.record_observations([_feature("!s")]) == 0

    def test_records_when_position_changes(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        assert h.record_observations([_feature("!m", lat=0.2, lon=0.1)]) == 1
        h._last_recorded["!m"] -= 120
        assert h.record_observations([_feature("!m", lat=0.21, lon=0.1)]) == 1

    def test_records_when_heartbeat_elapses_even_unchanged(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=60)
        assert h.record_observations([_feature("!s")]) == 1
        # Within heartbeat — skipped.
        h._last_recorded["!s"] -= 30
        assert h.record_observations([_feature("!s")]) == 0
        # Past heartbeat — recorded.
        h._last_recorded["!s"] -= 90
        assert h.record_observations([_feature("!s")]) == 1

    def test_first_observation_always_records(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        assert h.record_observations([_feature("!fresh")]) == 1

    def test_round_trip_at_6_decimals_is_treated_as_unchanged(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        assert h.record_observations([
            _feature("!noise", lat=35.123456, lon=139.0)
        ]) == 1
        h._last_recorded["!noise"] -= 120
        # 1e-7 delta — below 6dp threshold — should NOT trigger.
        assert h.record_observations([
            _feature("!noise", lat=35.1234561, lon=139.0)
        ]) == 0

    def test_batch_path_applies_value_dedup(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        h.record_observations([_feature("!a"), _feature("!b")])
        h._last_recorded["!a"] -= 120
        h._last_recorded["!b"] -= 120
        # Same positions for !a and !b → dedup. !c is fresh → recorded.
        n = h.record_observations([
            _feature("!a"), _feature("!b"),
            _feature("!c", lat=0.5, lon=0.5),
        ])
        assert n == 1

    def test_heartbeat_zero_disables_value_dedup(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=0)
        h.record_observations([_feature("!s")])
        h._last_recorded["!s"] -= 120
        # Time-throttle satisfied; no value-dedup → write happens.
        assert h.record_observations([_feature("!s")]) == 1

    def test_network_change_triggers_record(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        h.record_observations([_feature("!multi", network="meshtastic")])
        h._last_recorded["!multi"] -= 120
        # Same position, different network → different observation.
        assert h.record_observations([
            _feature("!multi", network="aredn")
        ]) == 1

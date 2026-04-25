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


def _feature(node_id: str, ts_offset: float = 0.0):
    """Build a minimal GeoJSON feature for record_observations."""
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [0.1, 0.2]},
        "properties": {"id": node_id, "name": node_id, "is_online": True},
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

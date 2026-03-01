"""
Tests for NodeHistoryDB.get_time_range() and history API endpoints.

Covers: get_time_range with data, empty DB, timerange API, snapshot API format.
"""

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.node_history import NodeHistoryDB, NodeObservation


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def temp_db():
    """Create a temporary node history database."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = Path(f.name)
    db = NodeHistoryDB(db_path=db_path)
    yield db
    try:
        db_path.unlink()
    except OSError:
        pass


def _insert_observation(db, obs: NodeObservation):
    """Direct SQL insert for testing (bypasses MIN_RECORD_INTERVAL check)."""
    import sqlite3
    conn = sqlite3.connect(str(db.db_path))
    conn.execute("""
        INSERT INTO node_observations
        (node_id, timestamp, latitude, longitude, altitude,
         snr, battery, is_online, network, hardware, role,
         via_mqtt, name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        obs.node_id, obs.timestamp, obs.latitude, obs.longitude,
        obs.altitude, obs.snr, obs.battery, obs.is_online,
        obs.network, obs.hardware, obs.role, obs.via_mqtt, obs.name,
    ))
    conn.commit()
    conn.close()


@pytest.fixture
def populated_db(temp_db):
    """Create a DB with sample observations."""
    now = time.time()
    observations = [
        NodeObservation(
            node_id="!abc123", timestamp=now - 7200,
            latitude=21.3069, longitude=-157.8583,
            snr=-5.0, battery=85, name="Kailua-01",
        ),
        NodeObservation(
            node_id="!def456", timestamp=now - 3600,
            latitude=21.4389, longitude=-158.0001,
            snr=3.0, battery=42, name="Waianae-02",
        ),
        NodeObservation(
            node_id="!abc123", timestamp=now - 1800,
            latitude=21.3070, longitude=-157.8580,
            snr=-3.0, battery=80, name="Kailua-01",
        ),
        NodeObservation(
            node_id="!ghi789", timestamp=now - 600,
            latitude=21.2710, longitude=-157.8167,
            snr=8.0, battery=97, name="Diamond-03", network="rns",
        ),
    ]
    for obs in observations:
        _insert_observation(temp_db, obs)
    return temp_db


# ===========================================================================
# get_time_range()
# ===========================================================================

class TestGetTimeRange:
    """Test NodeHistoryDB.get_time_range()."""

    def test_empty_database(self, temp_db):
        result = temp_db.get_time_range()
        assert result["oldest_ts"] is None
        assert result["newest_ts"] is None
        assert result["total_observations"] == 0

    def test_with_data(self, populated_db):
        result = populated_db.get_time_range()
        assert result["oldest_ts"] is not None
        assert result["newest_ts"] is not None
        assert result["total_observations"] == 4
        assert result["oldest_ts"] < result["newest_ts"]

    def test_time_range_bounds(self, populated_db):
        result = populated_db.get_time_range()
        now = time.time()
        # Oldest should be roughly 2 hours ago
        assert now - 7300 < result["oldest_ts"] < now - 7100
        # Newest should be roughly 10 minutes ago
        assert now - 700 < result["newest_ts"] < now - 500

    def test_single_observation(self, temp_db):
        obs = NodeObservation(
            node_id="!single", timestamp=time.time(),
            latitude=0.0, longitude=0.0,
        )
        _insert_observation(temp_db, obs)
        result = temp_db.get_time_range()
        assert result["total_observations"] == 1
        assert result["oldest_ts"] == result["newest_ts"]


# ===========================================================================
# get_snapshot() format
# ===========================================================================

class TestGetSnapshot:
    """Test snapshot query returns correct data."""

    def test_snapshot_returns_latest_per_node(self, populated_db):
        now = time.time()
        observations = populated_db.get_snapshot(timestamp=now, window_seconds=7200)
        node_ids = [obs.node_id for obs in observations]
        # Should have 3 unique nodes (abc123 appears twice, latest wins)
        assert len(observations) == 3
        assert "!abc123" in node_ids
        assert "!def456" in node_ids
        assert "!ghi789" in node_ids

    def test_snapshot_old_timestamp_fewer_nodes(self, populated_db):
        now = time.time()
        # Snapshot from 5000 seconds ago with 300s window — should only get first obs
        observations = populated_db.get_snapshot(
            timestamp=now - 5000, window_seconds=3000
        )
        node_ids = [obs.node_id for obs in observations]
        assert "!abc123" in node_ids

    def test_snapshot_empty_window(self, populated_db):
        # Very old timestamp with tight window — no data
        observations = populated_db.get_snapshot(
            timestamp=1000.0, window_seconds=10
        )
        assert len(observations) == 0


# ===========================================================================
# get_trajectory_geojson() format
# ===========================================================================

class TestGetTrajectoryGeoJSON:
    """Test trajectory GeoJSON output."""

    def test_trajectory_returns_geojson(self, populated_db):
        geojson = populated_db.get_trajectory_geojson("!abc123", hours=24)
        assert geojson["type"] == "Feature"
        assert geojson["geometry"]["type"] == "LineString"
        coords = geojson["geometry"]["coordinates"]
        assert len(coords) == 2  # Two observations for abc123
        assert geojson["properties"]["point_count"] == 2

    def test_trajectory_unknown_node(self, populated_db):
        geojson = populated_db.get_trajectory_geojson("!unknown")
        assert geojson["type"] == "Feature"
        assert geojson["geometry"] is None

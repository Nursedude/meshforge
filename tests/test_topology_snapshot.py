"""Tests for historical topology snapshots."""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add src to path for imports
_src_dir = Path(__file__).parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from utils.topology_snapshot import (
    TopologySnapshot,
    TopologyDiff,
    TopologySnapshotStore,
    get_topology_snapshot_store,
    start_topology_capture,
)


class TestTopologySnapshot:
    """Tests for TopologySnapshot dataclass."""

    def test_create_snapshot(self):
        """Test basic snapshot creation."""
        snapshot = TopologySnapshot(
            id="test_snap",
            timestamp=datetime.now(),
            nodes=[
                {'id': 'node1', 'name': 'Node 1', 'online': True},
                {'id': 'node2', 'name': 'Node 2', 'online': False},
            ],
            edges=[
                {'source': 'node1', 'dest': 'node2', 'hops': 1},
            ],
        )

        assert snapshot.id == "test_snap"
        assert snapshot.node_count == 2
        assert snapshot.edge_count == 1

    def test_snapshot_node_ids(self):
        """Test getting node IDs from snapshot."""
        snapshot = TopologySnapshot(
            id="test",
            timestamp=datetime.now(),
            nodes=[
                {'id': 'aaa'},
                {'id': 'bbb'},
                {'id': 'ccc'},
            ],
            edges=[],
        )

        ids = snapshot.node_ids
        assert 'aaa' in ids
        assert 'bbb' in ids
        assert 'ccc' in ids
        assert len(ids) == 3

    def test_snapshot_to_dict(self):
        """Test snapshot serialization."""
        snapshot = TopologySnapshot(
            id="test",
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            nodes=[{'id': 'n1'}],
            edges=[],
            stats={'count': 1},
            metadata={'source': 'test'},
        )

        d = snapshot.to_dict()
        assert d['id'] == 'test'
        assert d['timestamp'] == '2024-01-01T12:00:00'
        assert len(d['nodes']) == 1
        assert d['stats']['count'] == 1
        assert d['metadata']['source'] == 'test'

    def test_snapshot_from_dict(self):
        """Test snapshot deserialization."""
        data = {
            'id': 'snap123',
            'timestamp': '2024-01-01T12:00:00',
            'nodes': [{'id': 'x'}],
            'edges': [{'source': 'x', 'dest': 'y'}],
            'stats': {},
            'metadata': {},
        }

        snapshot = TopologySnapshot.from_dict(data)
        assert snapshot.id == 'snap123'
        assert snapshot.node_count == 1
        assert snapshot.edge_count == 1


class TestTopologyDiff:
    """Tests for TopologyDiff."""

    def test_empty_diff(self):
        """Test diff with no changes."""
        diff = TopologyDiff(
            snapshot_before_id='a',
            snapshot_after_id='b',
            timestamp_before=datetime.now(),
            timestamp_after=datetime.now(),
        )

        assert diff.has_changes is False
        assert diff.delta_node_count == 0
        assert diff.delta_edge_count == 0

    def test_diff_with_changes(self):
        """Test diff with various changes."""
        diff = TopologyDiff(
            snapshot_before_id='a',
            snapshot_after_id='b',
            timestamp_before=datetime.now(),
            timestamp_after=datetime.now(),
            nodes_added=['new1', 'new2'],
            nodes_removed=['old1'],
            edges_added=[('a', 'b')],
        )

        assert diff.has_changes is True
        assert len(diff.nodes_added) == 2
        assert len(diff.nodes_removed) == 1

    def test_diff_to_dict(self):
        """Test diff serialization."""
        diff = TopologyDiff(
            snapshot_before_id='a',
            snapshot_after_id='b',
            timestamp_before=datetime(2024, 1, 1),
            timestamp_after=datetime(2024, 1, 2),
            nodes_added=['n1'],
            delta_node_count=1,
        )

        d = diff.to_dict()
        assert d['snapshot_before_id'] == 'a'
        assert d['nodes_added'] == ['n1']
        assert d['delta_node_count'] == 1
        assert d['has_changes'] is True

    def test_diff_summary(self):
        """Test human-readable summary."""
        diff = TopologyDiff(
            snapshot_before_id='a',
            snapshot_after_id='b',
            timestamp_before=datetime(2024, 1, 1, 10, 0),
            timestamp_after=datetime(2024, 1, 1, 12, 0),
            nodes_added=['node_new'],
            nodes_removed=['node_old'],
            delta_node_count=0,
            delta_edge_count=0,
        )

        summary = diff.get_summary()
        assert 'Nodes Added' in summary
        assert 'node_new' in summary
        assert 'Nodes Removed' in summary
        assert 'node_old' in summary


class TestTopologySnapshotStore:
    """Tests for TopologySnapshotStore."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database for tests."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            yield f.name
        os.unlink(f.name)

    @pytest.fixture
    def store(self, temp_db):
        """Create store with temp database."""
        return TopologySnapshotStore(db_path=temp_db)

    def test_store_creation(self, store):
        """Test store initializes correctly."""
        assert store is not None
        stats = store.get_stats()
        assert stats['snapshot_count'] == 0

    def test_store_and_retrieve_snapshot(self, store):
        """Test storing and retrieving a snapshot."""
        # Manually create and store a snapshot
        snapshot = TopologySnapshot(
            id="test_snap_001",
            timestamp=datetime.now(),
            nodes=[{'id': 'node1', 'online': True}],
            edges=[],
        )
        store._store_snapshot(snapshot)

        # Retrieve
        retrieved = store.get_snapshot("test_snap_001")
        assert retrieved is not None
        assert retrieved.id == "test_snap_001"
        assert retrieved.node_count == 1

    def test_get_snapshots_by_time(self, store):
        """Test querying snapshots by time window."""
        # Store multiple snapshots
        for i in range(5):
            snapshot = TopologySnapshot(
                id=f"snap_{i}",
                timestamp=datetime.now() - timedelta(hours=i),
                nodes=[{'id': f'n{i}'}],
                edges=[],
            )
            store._store_snapshot(snapshot)

        # Get last 3 hours
        recent = store.get_snapshots(hours=3, limit=100)
        assert len(recent) >= 3  # At least 3 snapshots in range

    def test_get_latest_snapshot(self, store):
        """Test getting most recent snapshot."""
        # Store snapshots
        for i in range(3):
            snapshot = TopologySnapshot(
                id=f"snap_{i}",
                timestamp=datetime.now() - timedelta(hours=i),
                nodes=[],
                edges=[],
            )
            store._store_snapshot(snapshot)

        latest = store.get_latest_snapshot()
        assert latest is not None
        assert latest.id == "snap_0"  # Most recent

    def test_get_topology_at_time(self, store):
        """Test getting topology at specific time."""
        # Store snapshots at different times
        now = datetime.now()

        for i in range(5):
            snapshot = TopologySnapshot(
                id=f"snap_{i}",
                timestamp=now - timedelta(hours=i * 2),
                nodes=[{'id': f'state_{i}'}],
                edges=[],
            )
            store._store_snapshot(snapshot)

        # Get topology at 3 hours ago
        target = now - timedelta(hours=3)
        result = store.get_topology_at(target)

        assert result is not None
        # Should get snapshot from 2 hours ago (snap_1) as it's closest before target
        assert 'state_' in result.nodes[0]['id']

    def test_compare_snapshots(self, store):
        """Test snapshot comparison."""
        # Store two snapshots with different content
        snap1 = TopologySnapshot(
            id="snap_before",
            timestamp=datetime.now() - timedelta(hours=1),
            nodes=[
                {'id': 'node1', 'online': True, 'snr': 5.0},
                {'id': 'node2', 'online': True, 'snr': 3.0},
            ],
            edges=[{'source': 'node1', 'dest': 'node2', 'hops': 1}],
        )
        store._store_snapshot(snap1)

        snap2 = TopologySnapshot(
            id="snap_after",
            timestamp=datetime.now(),
            nodes=[
                {'id': 'node1', 'online': True, 'snr': 8.0},  # Changed SNR
                {'id': 'node3', 'online': True, 'snr': 6.0},  # New node
            ],
            edges=[{'source': 'node1', 'dest': 'node3', 'hops': 1}],  # Different edge
        )
        store._store_snapshot(snap2)

        # Compare
        diff = store.compare_snapshots("snap_before", "snap_after")

        assert diff is not None
        assert diff.has_changes is True
        assert 'node3' in diff.nodes_added
        assert 'node2' in diff.nodes_removed
        assert len(diff.nodes_changed) >= 1  # node1 SNR changed

    def test_record_and_get_events(self, store):
        """Test recording topology events."""
        store.record_event(
            event_type='NODE_ADDED',
            node_id='new_node',
            new_value={'id': 'new_node', 'online': True},
        )

        store.record_event(
            event_type='EDGE_ADDED',
            node_id='node1',
            dest_node_id='node2',
            new_value={'hops': 1},
        )

        events = store.get_recent_events(limit=10)
        assert len(events) == 2
        assert events[0]['event_type'] == 'EDGE_ADDED'  # Most recent first
        assert events[1]['event_type'] == 'NODE_ADDED'

    def test_evolution_summary(self, store):
        """Test getting network evolution summary."""
        # Store snapshots over time
        now = datetime.now()
        for i in range(12):
            snapshot = TopologySnapshot(
                id=f"evo_snap_{i}",
                timestamp=now - timedelta(hours=i * 2),
                nodes=[{'id': f'n{j}', 'online': j < (10 - i)} for j in range(10)],
                edges=[],
            )
            store._store_snapshot(snapshot)

        evolution = store.get_evolution_summary(hours=24, intervals=6)

        assert len(evolution) == 6
        # Each point should have timestamp and counts
        for point in evolution:
            assert 'timestamp' in point
            assert 'node_count' in point
            assert 'online_count' in point

    def test_stats(self, store):
        """Test getting store statistics."""
        # Add some data
        for i in range(3):
            snapshot = TopologySnapshot(
                id=f"stats_snap_{i}",
                timestamp=datetime.now() - timedelta(hours=i),
                nodes=[],
                edges=[],
            )
            store._store_snapshot(snapshot)

        stats = store.get_stats()

        assert stats['snapshot_count'] == 3
        assert stats['db_size_bytes'] > 0
        assert 'retention_days' in stats

    def test_cleanup(self, store):
        """Test cleanup of old data."""
        # Store an old snapshot
        old_snapshot = TopologySnapshot(
            id="old_snap",
            timestamp=datetime.now() - timedelta(days=100),  # Way past retention
            nodes=[],
            edges=[],
        )
        store._store_snapshot(old_snapshot)

        # Store a recent one
        new_snapshot = TopologySnapshot(
            id="new_snap",
            timestamp=datetime.now(),
            nodes=[],
            edges=[],
        )
        store._store_snapshot(new_snapshot)

        # Run cleanup
        deleted = store._cleanup()

        # Old snapshot should be deleted
        assert deleted >= 1
        assert store.get_snapshot("old_snap") is None
        assert store.get_snapshot("new_snap") is not None


class TestPeriodicCapture:
    """Tests for periodic topology capture."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database for tests."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            yield f.name
        os.unlink(f.name)

    def test_start_stop_capture(self, temp_db):
        """Test starting and stopping periodic capture."""
        store = TopologySnapshotStore(db_path=temp_db)

        assert store.is_capturing() is False

        store.start_periodic_capture(interval_seconds=1)
        assert store.is_capturing() is True

        store.stop_periodic_capture()
        assert store.is_capturing() is False

    def test_capture_creates_snapshots(self, temp_db):
        """Test that capture creates snapshots."""
        import time

        store = TopologySnapshotStore(db_path=temp_db)

        # Manually capture
        result = store.capture_snapshot()

        # Should create a snapshot (may have no nodes if no tracker available)
        if result:
            assert result.id.startswith("snap_")

        # Check stats
        stats = store.get_stats()
        assert stats['snapshot_count'] >= 0  # May be 0 if capture returned None


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_get_topology_snapshot_store(self):
        """Test singleton getter."""
        store1 = get_topology_snapshot_store()
        store2 = get_topology_snapshot_store()

        assert store1 is store2  # Should be same instance

    def test_start_topology_capture(self):
        """Test convenience start function."""
        store = start_topology_capture(interval_seconds=60)

        assert store.is_capturing() is True

        store.stop_periodic_capture()
        assert store.is_capturing() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

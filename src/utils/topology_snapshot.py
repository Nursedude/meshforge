"""
Historical Topology Snapshots for Network Evolution Tracking.

Captures and stores periodic snapshots of the network topology to enable:
- Time-travel visualization of network state
- Network growth/evolution analysis
- Topology comparison between time periods
- Identification of network changes (nodes added/removed, links changed)

Usage:
    from utils.topology_snapshot import TopologySnapshotStore, get_topology_snapshot_store

    # Get singleton instance
    store = get_topology_snapshot_store()

    # Capture current topology
    store.capture_snapshot()

    # Get historical snapshots
    snapshots = store.get_snapshots(hours=24)

    # Compare two snapshots
    diff = store.compare_snapshots(snapshot_id_1, snapshot_id_2)

    # Get topology at specific time
    topology = store.get_topology_at(timestamp)
"""

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from utils.safe_import import safe_import
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Optional dependencies for topology capture
_get_global_node_tracker, _HAS_GLOBAL_TRACKER = safe_import(
    'gateway.node_tracker', 'get_global_node_tracker'
)
_MapDataCollector, _HAS_MAP_COLLECTOR = safe_import(
    'utils.map_data_collector', 'MapDataCollector'
)

@dataclass
class TopologySnapshot:
    """A point-in-time snapshot of the network topology."""

    id: str
    timestamp: datetime
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    stats: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def node_count(self) -> int:
        """Number of nodes in this snapshot."""
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        """Number of edges in this snapshot."""
        return len(self.edges)

    @property
    def node_ids(self) -> Set[str]:
        """Set of node IDs in this snapshot."""
        return {n.get('id', n.get('node_id', '')) for n in self.nodes}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'nodes': self.nodes,
            'edges': self.edges,
            'stats': self.stats,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TopologySnapshot':
        """Create snapshot from dictionary."""
        return cls(
            id=data['id'],
            timestamp=datetime.fromisoformat(data['timestamp']),
            nodes=data.get('nodes', []),
            edges=data.get('edges', []),
            stats=data.get('stats', {}),
            metadata=data.get('metadata', {}),
        )


@dataclass
class TopologyDiff:
    """Difference between two topology snapshots."""

    snapshot_before_id: str
    snapshot_after_id: str
    timestamp_before: datetime
    timestamp_after: datetime

    # Node changes
    nodes_added: List[str] = field(default_factory=list)
    nodes_removed: List[str] = field(default_factory=list)
    nodes_changed: List[Dict[str, Any]] = field(default_factory=list)

    # Edge changes
    edges_added: List[Tuple[str, str]] = field(default_factory=list)
    edges_removed: List[Tuple[str, str]] = field(default_factory=list)
    edges_changed: List[Dict[str, Any]] = field(default_factory=list)

    # Summary stats
    delta_node_count: int = 0
    delta_edge_count: int = 0

    @property
    def has_changes(self) -> bool:
        """Check if there are any changes between snapshots."""
        return bool(
            self.nodes_added or self.nodes_removed or self.nodes_changed or
            self.edges_added or self.edges_removed or self.edges_changed
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'snapshot_before_id': self.snapshot_before_id,
            'snapshot_after_id': self.snapshot_after_id,
            'timestamp_before': self.timestamp_before.isoformat(),
            'timestamp_after': self.timestamp_after.isoformat(),
            'nodes_added': self.nodes_added,
            'nodes_removed': self.nodes_removed,
            'nodes_changed': self.nodes_changed,
            'edges_added': [list(e) for e in self.edges_added],
            'edges_removed': [list(e) for e in self.edges_removed],
            'edges_changed': self.edges_changed,
            'delta_node_count': self.delta_node_count,
            'delta_edge_count': self.delta_edge_count,
            'has_changes': self.has_changes,
        }

    def get_summary(self) -> str:
        """Get human-readable summary of changes."""
        lines = []
        lines.append(f"Topology Changes: {self.timestamp_before.strftime('%Y-%m-%d %H:%M')} -> "
                     f"{self.timestamp_after.strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        if self.nodes_added:
            lines.append(f"Nodes Added ({len(self.nodes_added)}):")
            for node in self.nodes_added[:10]:
                lines.append(f"  + {node}")
            if len(self.nodes_added) > 10:
                lines.append(f"  ... and {len(self.nodes_added) - 10} more")

        if self.nodes_removed:
            lines.append(f"Nodes Removed ({len(self.nodes_removed)}):")
            for node in self.nodes_removed[:10]:
                lines.append(f"  - {node}")
            if len(self.nodes_removed) > 10:
                lines.append(f"  ... and {len(self.nodes_removed) - 10} more")

        if self.edges_added:
            lines.append(f"Links Added ({len(self.edges_added)}):")
            for src, dst in self.edges_added[:10]:
                lines.append(f"  + {src} -> {dst}")

        if self.edges_removed:
            lines.append(f"Links Removed ({len(self.edges_removed)}):")
            for src, dst in self.edges_removed[:10]:
                lines.append(f"  - {src} -> {dst}")

        if not self.has_changes:
            lines.append("No topology changes detected.")

        lines.append("")
        lines.append(f"Net change: {self.delta_node_count:+d} nodes, {self.delta_edge_count:+d} edges")

        return "\n".join(lines)


class TopologySnapshotStore:
    """
    Persistent store for historical topology snapshots.

    Features:
    - SQLite-backed storage with automatic cleanup
    - Configurable snapshot intervals
    - Efficient delta comparison
    - Time-based queries
    - Integration with existing topology system
    """

    DEFAULT_RETENTION_DAYS = 30
    DEFAULT_SNAPSHOT_INTERVAL = 300  # 5 minutes
    MAX_SNAPSHOTS = 10000

    def __init__(
        self,
        db_path: Optional[str] = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        """
        Initialize topology snapshot store.

        Args:
            db_path: Path to SQLite database (default: ~/.config/meshforge/topology_history.db)
            retention_days: How long to keep snapshots
        """
        if db_path is None:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(config_dir / "topology_history.db")

        self._db_path = db_path
        self._retention_days = retention_days
        self._lock = threading.Lock()
        self._last_cleanup = 0.0

        # Background capture thread
        self._capture_running = False
        self._capture_thread: Optional[threading.Thread] = None
        self._capture_interval = self.DEFAULT_SNAPSHOT_INTERVAL

        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Get database connection with context management."""
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            # Snapshots table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS topology_snapshots (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    node_count INTEGER,
                    edge_count INTEGER,
                    data TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
                ON topology_snapshots(timestamp DESC)
            """)

            # Events table (for granular change tracking)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS topology_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    node_id TEXT,
                    dest_node_id TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    details TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp
                ON topology_events(timestamp DESC)
            """)

    def capture_snapshot(self, metadata: Dict[str, Any] = None) -> Optional[TopologySnapshot]:
        """
        Capture current network topology as a snapshot.

        Args:
            metadata: Optional metadata to attach to snapshot

        Returns:
            The captured TopologySnapshot, or None if capture failed
        """
        try:
            nodes = []
            edges = []
            stats = {}

            # Try to get topology from UnifiedNodeTracker
            if _HAS_GLOBAL_TRACKER:
                try:
                    tracker = _get_global_node_tracker()

                    # Get all nodes
                    for node in tracker.get_all_nodes():
                        node_dict = node.to_dict()
                        nodes.append({
                            'id': node.id,
                            'name': node.name,
                            'network': node.network,
                            'online': node.online,
                            'last_heard': node.last_heard.isoformat() if node.last_heard else None,
                            'position': {
                                'lat': node.position.latitude if node.position else None,
                                'lon': node.position.longitude if node.position else None,
                            } if node.position else None,
                            'snr': node_dict.get('snr'),
                            'rssi': node_dict.get('rssi'),
                            'battery': node_dict.get('battery'),
                            'state': node.state_name if hasattr(node, 'state_name') else None,
                        })

                    # Get topology edges
                    topology = tracker.get_topology()
                    if topology:
                        for edge_key, edge in topology._edges.items():
                            edges.append({
                                'source': edge.source,
                                'dest': edge.dest,
                                'hops': edge.hops,
                                'snr': edge.snr,
                                'rssi': edge.rssi,
                                'first_seen': edge.first_seen.isoformat() if edge.first_seen else None,
                                'last_seen': edge.last_seen.isoformat() if edge.last_seen else None,
                                'announce_count': edge.announce_count,
                            })

                        stats = topology.get_topology_stats()

                except Exception as e:
                    logger.debug(f"Error getting topology from tracker: {e}")
            else:
                logger.debug("UnifiedNodeTracker not available")

            # Also try MapDataCollector as fallback
            if not nodes:
                if _HAS_MAP_COLLECTOR:
                    try:
                        collector = _MapDataCollector(enable_history=False)
                        geojson = collector.collect(max_age_seconds=300)

                        for feature in geojson.get('features', []):
                            props = feature.get('properties', {})
                            coords = feature.get('geometry', {}).get('coordinates', [0, 0])
                            nodes.append({
                                'id': props.get('id', ''),
                                'name': props.get('name', ''),
                                'network': props.get('network', 'unknown'),
                                'online': props.get('online', False),
                                'position': {
                                    'lat': coords[1] if len(coords) > 1 else None,
                                    'lon': coords[0] if len(coords) > 0 else None,
                                },
                                'snr': props.get('snr'),
                                'rssi': props.get('rssi'),
                                'battery': props.get('battery'),
                            })

                    except Exception as e:
                        logger.debug(f"Error getting nodes from MapDataCollector: {e}")
                else:
                    logger.debug("MapDataCollector not available")

            # Create snapshot
            snapshot_id = f"snap_{int(time.time() * 1000)}"
            timestamp = datetime.now()

            snapshot = TopologySnapshot(
                id=snapshot_id,
                timestamp=timestamp,
                nodes=nodes,
                edges=edges,
                stats=stats or {
                    'node_count': len(nodes),
                    'edge_count': len(edges),
                },
                metadata=metadata or {},
            )

            # Store in database
            self._store_snapshot(snapshot)

            # Periodic cleanup
            self._maybe_cleanup()

            logger.debug(f"Captured topology snapshot: {len(nodes)} nodes, {len(edges)} edges")
            return snapshot

        except Exception as e:
            logger.error(f"Failed to capture topology snapshot: {e}")
            return None

    def _store_snapshot(self, snapshot: TopologySnapshot) -> None:
        """Store snapshot in database."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO topology_snapshots (id, timestamp, node_count, edge_count, data)
                VALUES (?, ?, ?, ?, ?)
            """, (
                snapshot.id,
                snapshot.timestamp.isoformat(),
                snapshot.node_count,
                snapshot.edge_count,
                json.dumps(snapshot.to_dict()),
            ))

    def get_snapshot(self, snapshot_id: str) -> Optional[TopologySnapshot]:
        """Get a specific snapshot by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT data FROM topology_snapshots WHERE id = ?",
                (snapshot_id,)
            )
            row = cursor.fetchone()
            if row:
                data = json.loads(row['data'])
                return TopologySnapshot.from_dict(data)
        return None

    def get_snapshots(
        self,
        hours: int = 24,
        limit: int = 100,
    ) -> List[TopologySnapshot]:
        """
        Get snapshots from the last N hours.

        Args:
            hours: How many hours back to query
            limit: Maximum snapshots to return

        Returns:
            List of TopologySnapshot objects (newest first)
        """
        since = datetime.now() - timedelta(hours=hours)

        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT data FROM topology_snapshots
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (since.isoformat(), limit))

            snapshots = []
            for row in cursor.fetchall():
                data = json.loads(row['data'])
                snapshots.append(TopologySnapshot.from_dict(data))

            return snapshots

    def get_latest_snapshot(self) -> Optional[TopologySnapshot]:
        """Get the most recent snapshot."""
        snapshots = self.get_snapshots(hours=24 * 365, limit=1)
        return snapshots[0] if snapshots else None

    def get_topology_at(self, timestamp: datetime) -> Optional[TopologySnapshot]:
        """
        Get the topology snapshot closest to a given timestamp.

        Args:
            timestamp: Target timestamp

        Returns:
            Closest TopologySnapshot, or None if no snapshots exist
        """
        with self._get_connection() as conn:
            # Get snapshot just before or at the timestamp
            cursor = conn.execute("""
                SELECT data FROM topology_snapshots
                WHERE timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (timestamp.isoformat(),))

            row = cursor.fetchone()
            if row:
                data = json.loads(row['data'])
                return TopologySnapshot.from_dict(data)
        return None

    def compare_snapshots(
        self,
        snapshot_id_before: str,
        snapshot_id_after: str,
    ) -> Optional[TopologyDiff]:
        """
        Compare two snapshots to find differences.

        Args:
            snapshot_id_before: ID of earlier snapshot
            snapshot_id_after: ID of later snapshot

        Returns:
            TopologyDiff with changes, or None if snapshots not found
        """
        before = self.get_snapshot(snapshot_id_before)
        after = self.get_snapshot(snapshot_id_after)

        if not before or not after:
            return None

        return self._compute_diff(before, after)

    def compare_with_latest(self, snapshot_id: str) -> Optional[TopologyDiff]:
        """Compare a snapshot with the latest one."""
        latest = self.get_latest_snapshot()
        if not latest:
            return None
        return self.compare_snapshots(snapshot_id, latest.id)

    def _compute_diff(
        self,
        before: TopologySnapshot,
        after: TopologySnapshot,
    ) -> TopologyDiff:
        """Compute the difference between two snapshots."""
        diff = TopologyDiff(
            snapshot_before_id=before.id,
            snapshot_after_id=after.id,
            timestamp_before=before.timestamp,
            timestamp_after=after.timestamp,
        )

        # Node changes
        before_nodes = before.node_ids
        after_nodes = after.node_ids

        diff.nodes_added = list(after_nodes - before_nodes)
        diff.nodes_removed = list(before_nodes - after_nodes)

        # Check for changed nodes (same ID but different properties)
        for node_id in before_nodes & after_nodes:
            before_node = next((n for n in before.nodes if n.get('id') == node_id), None)
            after_node = next((n for n in after.nodes if n.get('id') == node_id), None)

            if before_node and after_node:
                changes = {}
                for key in ['online', 'snr', 'rssi', 'battery', 'state']:
                    before_val = before_node.get(key)
                    after_val = after_node.get(key)
                    if before_val != after_val:
                        changes[key] = {'before': before_val, 'after': after_val}

                if changes:
                    diff.nodes_changed.append({
                        'node_id': node_id,
                        'changes': changes,
                    })

        # Edge changes
        def edge_key(edge: Dict) -> Tuple[str, str]:
            return (edge.get('source', ''), edge.get('dest', ''))

        before_edges = {edge_key(e) for e in before.edges}
        after_edges = {edge_key(e) for e in after.edges}

        diff.edges_added = list(after_edges - before_edges)
        diff.edges_removed = list(before_edges - after_edges)

        # Check for changed edges
        for key in before_edges & after_edges:
            before_edge = next((e for e in before.edges if edge_key(e) == key), None)
            after_edge = next((e for e in after.edges if edge_key(e) == key), None)

            if before_edge and after_edge:
                changes = {}
                for prop in ['hops', 'snr', 'rssi']:
                    before_val = before_edge.get(prop)
                    after_val = after_edge.get(prop)
                    if before_val != after_val:
                        changes[prop] = {'before': before_val, 'after': after_val}

                if changes:
                    diff.edges_changed.append({
                        'source': key[0],
                        'dest': key[1],
                        'changes': changes,
                    })

        # Summary stats
        diff.delta_node_count = len(diff.nodes_added) - len(diff.nodes_removed)
        diff.delta_edge_count = len(diff.edges_added) - len(diff.edges_removed)

        return diff

    def get_evolution_summary(
        self,
        hours: int = 24,
        intervals: int = 12,
    ) -> List[Dict[str, Any]]:
        """
        Get summary of network evolution over time.

        Args:
            hours: Time window
            intervals: Number of intervals to sample

        Returns:
            List of evolution data points for charting
        """
        evolution = []
        interval_hours = hours / intervals

        for i in range(intervals):
            target_time = datetime.now() - timedelta(hours=hours - (i * interval_hours))
            snapshot = self.get_topology_at(target_time)

            if snapshot:
                evolution.append({
                    'timestamp': snapshot.timestamp.isoformat(),
                    'node_count': snapshot.node_count,
                    'edge_count': snapshot.edge_count,
                    'online_count': sum(1 for n in snapshot.nodes if n.get('online', False)),
                })
            else:
                evolution.append({
                    'timestamp': target_time.isoformat(),
                    'node_count': 0,
                    'edge_count': 0,
                    'online_count': 0,
                })

        return evolution

    def record_event(
        self,
        event_type: str,
        node_id: str = None,
        dest_node_id: str = None,
        old_value: Any = None,
        new_value: Any = None,
        details: Dict[str, Any] = None,
    ) -> None:
        """
        Record a topology change event.

        Args:
            event_type: Type of event (NODE_ADDED, NODE_REMOVED, EDGE_ADDED, etc.)
            node_id: Primary node involved
            dest_node_id: Destination node (for edge events)
            old_value: Previous value
            new_value: New value
            details: Additional details
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO topology_events
                (timestamp, event_type, node_id, dest_node_id, old_value, new_value, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                event_type,
                node_id,
                dest_node_id,
                json.dumps(old_value) if old_value is not None else None,
                json.dumps(new_value) if new_value is not None else None,
                json.dumps(details) if details else None,
            ))

    def get_recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent topology events."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM topology_events
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))

            events = []
            for row in cursor.fetchall():
                events.append({
                    'id': row['id'],
                    'timestamp': row['timestamp'],
                    'event_type': row['event_type'],
                    'node_id': row['node_id'],
                    'dest_node_id': row['dest_node_id'],
                    'old_value': json.loads(row['old_value']) if row['old_value'] else None,
                    'new_value': json.loads(row['new_value']) if row['new_value'] else None,
                    'details': json.loads(row['details']) if row['details'] else None,
                })

            return events

    def _maybe_cleanup(self) -> None:
        """Periodically clean up old data."""
        now = time.time()
        if now - self._last_cleanup < 3600:  # Once per hour
            return

        self._last_cleanup = now
        self._cleanup()

    def _cleanup(self) -> int:
        """Remove old snapshots beyond retention period."""
        cutoff = datetime.now() - timedelta(days=self._retention_days)

        with self._get_connection() as conn:
            # Delete old snapshots
            cursor = conn.execute(
                "DELETE FROM topology_snapshots WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            deleted_snapshots = cursor.rowcount

            # Delete old events
            cursor = conn.execute(
                "DELETE FROM topology_events WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            deleted_events = cursor.rowcount

            # Enforce max snapshots
            cursor = conn.execute("SELECT COUNT(*) FROM topology_snapshots")
            count = cursor.fetchone()[0]

            if count > self.MAX_SNAPSHOTS:
                to_delete = count - self.MAX_SNAPSHOTS
                conn.execute("""
                    DELETE FROM topology_snapshots WHERE id IN (
                        SELECT id FROM topology_snapshots
                        ORDER BY timestamp ASC
                        LIMIT ?
                    )
                """, (to_delete,))
                deleted_snapshots += to_delete

            if deleted_snapshots > 0 or deleted_events > 0:
                logger.debug(f"Cleaned up {deleted_snapshots} snapshots, {deleted_events} events")

            return deleted_snapshots

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about stored data."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM topology_snapshots")
            snapshot_count = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM topology_events")
            event_count = cursor.fetchone()[0]

            cursor = conn.execute("""
                SELECT MIN(timestamp), MAX(timestamp) FROM topology_snapshots
            """)
            row = cursor.fetchone()
            oldest = row[0]
            newest = row[1]

        db_size = Path(self._db_path).stat().st_size if Path(self._db_path).exists() else 0

        return {
            'snapshot_count': snapshot_count,
            'event_count': event_count,
            'oldest_snapshot': oldest,
            'newest_snapshot': newest,
            'db_size_bytes': db_size,
            'db_size_mb': round(db_size / (1024 * 1024), 2),
            'retention_days': self._retention_days,
        }

    def start_periodic_capture(self, interval_seconds: int = None) -> None:
        """
        Start background thread for periodic topology capture.

        Args:
            interval_seconds: Seconds between captures (default: 300 / 5 minutes)
        """
        if self._capture_running:
            return

        self._capture_interval = interval_seconds or self.DEFAULT_SNAPSHOT_INTERVAL
        self._capture_running = True

        def capture_loop():
            while self._capture_running:
                try:
                    self.capture_snapshot()
                except Exception as e:
                    logger.debug(f"Periodic capture error: {e}")
                time.sleep(self._capture_interval)

        self._capture_thread = threading.Thread(target=capture_loop, daemon=True)
        self._capture_thread.start()
        logger.info(f"Topology snapshot capture started (interval: {self._capture_interval}s)")

    def stop_periodic_capture(self) -> None:
        """Stop background capture thread."""
        self._capture_running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=5)
            self._capture_thread = None
        logger.info("Topology snapshot capture stopped")

    def is_capturing(self) -> bool:
        """Check if periodic capture is running."""
        return self._capture_running


# Singleton instance
_global_store: Optional[TopologySnapshotStore] = None


def get_topology_snapshot_store() -> TopologySnapshotStore:
    """Get or create the global topology snapshot store."""
    global _global_store
    if _global_store is None:
        _global_store = TopologySnapshotStore()
    return _global_store


def start_topology_capture(interval_seconds: int = 300) -> TopologySnapshotStore:
    """
    Convenience function to start topology capture.

    Args:
        interval_seconds: Seconds between captures

    Returns:
        Running TopologySnapshotStore instance
    """
    store = get_topology_snapshot_store()
    store.start_periodic_capture(interval_seconds)
    return store

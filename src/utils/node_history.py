"""
Node History - SQLite-based node position and state tracking over time.

Records node observations from the MapDataCollector, enabling:
- Position playback on the live map (node trajectory)
- Historical network topology views
- Online/offline patterns over time
- Network growth tracking

Usage:
    from utils.node_history import NodeHistoryDB

    db = NodeHistoryDB()  # Uses default path
    db.record_observations(geojson_features)

    # Get trajectory for a node
    trajectory = db.get_trajectory("!ba4bf9d0", hours=24)

    # Get network snapshot at a point in time
    snapshot = db.get_snapshot(timestamp=time.time() - 3600)
"""

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


# Default retention: 7 days
DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600

# Minimum interval between recording the same node (avoid flooding)
MIN_RECORD_INTERVAL = 60  # 1 minute


@dataclass
class NodeObservation:
    """A single node observation at a point in time."""
    node_id: str
    timestamp: float
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    snr: Optional[float] = None
    battery: Optional[int] = None
    is_online: bool = True
    network: str = "meshtastic"
    hardware: str = ""
    role: str = ""
    via_mqtt: bool = False
    name: str = ""


class NodeHistoryDB:
    """SQLite database for node position and state history.

    Thread-safe. Records node observations over time and provides
    query methods for playback, trajectories, and network snapshots.
    """

    def __init__(self, db_path: Optional[Path] = None,
                 retention_seconds: int = DEFAULT_RETENTION_SECONDS):
        """Initialize node history database.

        Args:
            db_path: Path to SQLite database file.
                     Defaults to ~/.local/share/meshforge/node_history.db
            retention_seconds: How long to keep observations (default 7 days).
        """
        if db_path is None:
            db_path = get_real_user_home() / ".local" / "share" / "meshforge" / "node_history.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self.retention_seconds = retention_seconds
        self._lock = threading.Lock()
        self._last_recorded: Dict[str, float] = {}  # node_id -> last record time
        self._init_db()

    def _init_db(self) -> None:
        """Create tables and indexes if they don't exist."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS node_observations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        node_id TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        latitude REAL NOT NULL,
                        longitude REAL NOT NULL,
                        altitude REAL,
                        snr REAL,
                        battery INTEGER,
                        is_online INTEGER DEFAULT 1,
                        network TEXT DEFAULT 'meshtastic',
                        hardware TEXT DEFAULT '',
                        role TEXT DEFAULT '',
                        via_mqtt INTEGER DEFAULT 0,
                        name TEXT DEFAULT ''
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_obs_node_id
                    ON node_observations(node_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_obs_timestamp
                    ON node_observations(timestamp)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_obs_node_time
                    ON node_observations(node_id, timestamp)
                """)
                conn.commit()
            finally:
                conn.close()

    def record_observations(self, features: List[Dict[str, Any]]) -> int:
        """Record a batch of node observations from GeoJSON features.

        Skips nodes that were recorded less than MIN_RECORD_INTERVAL ago
        to prevent database flooding from rapid collection cycles.

        Args:
            features: List of GeoJSON Feature dicts with node properties.

        Returns:
            Number of observations actually recorded.
        """
        now = time.time()
        to_insert = []

        for feature in features:
            props = feature.get("properties", {})
            geom = feature.get("geometry", {})
            coords = geom.get("coordinates", [])

            if len(coords) < 2:
                continue

            node_id = props.get("id", "")
            if not node_id:
                continue

            # Throttle: skip if recorded recently
            last = self._last_recorded.get(node_id, 0)
            if now - last < MIN_RECORD_INTERVAL:
                continue

            lon, lat = coords[0], coords[1]

            to_insert.append((
                node_id,
                now,
                lat,
                lon,
                None,  # altitude not in standard features
                props.get("snr"),
                props.get("battery"),
                1 if props.get("is_online", True) else 0,
                props.get("network", "meshtastic"),
                props.get("hardware", ""),
                props.get("role", ""),
                1 if props.get("via_mqtt", False) else 0,
                props.get("name", ""),
            ))
            self._last_recorded[node_id] = now

        # Prune stale entries to prevent unbounded memory growth
        if len(self._last_recorded) > 10000:
            cutoff = now - self.retention_seconds
            self._last_recorded = {
                k: v for k, v in self._last_recorded.items()
                if v > cutoff
            }

        if not to_insert:
            return 0

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.executemany("""
                    INSERT INTO node_observations
                    (node_id, timestamp, latitude, longitude, altitude,
                     snr, battery, is_online, network, hardware, role,
                     via_mqtt, name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, to_insert)
                conn.commit()
                return len(to_insert)
            except sqlite3.Error as e:
                logger.error(f"Failed to record observations: {e}")
                return 0
            finally:
                conn.close()

    def get_trajectory(self, node_id: str, hours: float = 24,
                       limit: int = 1000) -> List[NodeObservation]:
        """Get position history for a specific node.

        Args:
            node_id: The node identifier (e.g., "!ba4bf9d0").
            hours: How far back to look (default 24 hours).
            limit: Maximum observations to return.

        Returns:
            List of NodeObservation ordered by time (oldest first).
        """
        cutoff = time.time() - (hours * 3600)

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("""
                    SELECT * FROM node_observations
                    WHERE node_id = ? AND timestamp >= ?
                    ORDER BY timestamp ASC
                    LIMIT ?
                """, (node_id, cutoff, limit)).fetchall()
                return [self._row_to_observation(row) for row in rows]
            finally:
                conn.close()

    def get_snapshot(self, timestamp: Optional[float] = None,
                     window_seconds: int = 300) -> List[NodeObservation]:
        """Get the most recent observation for each node at a point in time.

        Args:
            timestamp: Unix timestamp for the snapshot (default: now).
            window_seconds: How far back from timestamp to search (default 5 min).

        Returns:
            List of the most recent observation per node within the window.
        """
        if timestamp is None:
            timestamp = time.time()

        window_start = timestamp - window_seconds

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            try:
                # Get latest observation per node within the window
                rows = conn.execute("""
                    SELECT o.* FROM node_observations o
                    INNER JOIN (
                        SELECT node_id, MAX(timestamp) as max_ts
                        FROM node_observations
                        WHERE timestamp BETWEEN ? AND ?
                        GROUP BY node_id
                    ) latest ON o.node_id = latest.node_id
                        AND o.timestamp = latest.max_ts
                    ORDER BY o.node_id
                """, (window_start, timestamp)).fetchall()
                return [self._row_to_observation(row) for row in rows]
            finally:
                conn.close()

    def get_unique_nodes(self, hours: float = 24) -> List[Dict[str, Any]]:
        """Get summary of unique nodes seen in a time window.

        Args:
            hours: How far back to look.

        Returns:
            List of dicts with node_id, name, observation_count, first_seen, last_seen.
        """
        cutoff = time.time() - (hours * 3600)

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("""
                    SELECT node_id,
                           MAX(name) as name,
                           COUNT(*) as observation_count,
                           MIN(timestamp) as first_seen,
                           MAX(timestamp) as last_seen,
                           MAX(network) as network
                    FROM node_observations
                    WHERE timestamp >= ?
                    GROUP BY node_id
                    ORDER BY last_seen DESC
                """, (cutoff,)).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def get_trajectory_geojson(self, node_id: str, hours: float = 24) -> Dict[str, Any]:
        """Get trajectory as GeoJSON LineString for map rendering.

        Args:
            node_id: The node identifier.
            hours: How far back to look.

        Returns:
            GeoJSON Feature with LineString geometry and time properties.
        """
        observations = self.get_trajectory(node_id, hours)
        if not observations:
            return {"type": "Feature", "geometry": None, "properties": {"node_id": node_id}}

        coordinates = [[obs.longitude, obs.latitude] for obs in observations]
        timestamps = [obs.timestamp for obs in observations]

        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coordinates,
            },
            "properties": {
                "node_id": node_id,
                "name": observations[-1].name,
                "point_count": len(observations),
                "start_time": timestamps[0],
                "end_time": timestamps[-1],
                "duration_hours": (timestamps[-1] - timestamps[0]) / 3600 if len(timestamps) > 1 else 0,
            }
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics.

        Returns:
            Dict with total_observations, unique_nodes, oldest_record, newest_record, db_size_kb.
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                total = conn.execute(
                    "SELECT COUNT(*) FROM node_observations"
                ).fetchone()[0]
                unique = conn.execute(
                    "SELECT COUNT(DISTINCT node_id) FROM node_observations"
                ).fetchone()[0]

                time_range = conn.execute(
                    "SELECT MIN(timestamp), MAX(timestamp) FROM node_observations"
                ).fetchone()

                oldest = time_range[0] if time_range[0] else None
                newest = time_range[1] if time_range[1] else None

                # DB file size
                db_size_kb = 0
                if self.db_path.exists():
                    db_size_kb = self.db_path.stat().st_size / 1024

                return {
                    "total_observations": total,
                    "unique_nodes": unique,
                    "oldest_record": oldest,
                    "newest_record": newest,
                    "db_size_kb": round(db_size_kb, 1),
                    "retention_days": self.retention_seconds / 86400,
                }
            finally:
                conn.close()

    def cleanup(self) -> int:
        """Remove observations older than retention period.

        Returns:
            Number of rows deleted.
        """
        cutoff = time.time() - self.retention_seconds

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cursor = conn.execute(
                    "DELETE FROM node_observations WHERE timestamp < ?",
                    (cutoff,)
                )
                conn.commit()
                deleted = cursor.rowcount
                if deleted > 0:
                    conn.execute("VACUUM")
                    logger.debug(f"Node history cleanup: deleted {deleted} old observations")
                return deleted
            except sqlite3.Error as e:
                logger.error(f"Cleanup failed: {e}")
                return 0
            finally:
                conn.close()

    def _row_to_observation(self, row: sqlite3.Row) -> NodeObservation:
        """Convert a database row to a NodeObservation."""
        return NodeObservation(
            node_id=row["node_id"],
            timestamp=row["timestamp"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            altitude=row["altitude"],
            snr=row["snr"],
            battery=row["battery"],
            is_online=bool(row["is_online"]),
            network=row["network"],
            hardware=row["hardware"],
            role=row["role"],
            via_mqtt=bool(row["via_mqtt"]),
            name=row["name"],
        )

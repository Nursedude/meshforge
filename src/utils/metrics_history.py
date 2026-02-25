"""
Historical Metrics Tracking for MeshForge.

Tracks and persists network metrics over time for trend analysis:
- SNR (Signal-to-Noise Ratio) trends
- Hop count changes
- Link quality evolution
- Node uptime patterns
- Path reliability statistics

Uses SQLite for persistent storage with automatic cleanup of old data.

Usage:
    from utils.metrics_history import MetricsHistory, MetricType

    history = MetricsHistory()

    # Record a metric
    history.record(MetricType.SNR, node_id="!abc123", value=8.5)

    # Get recent values
    recent = history.get_recent(MetricType.SNR, node_id="!abc123", hours=24)

    # Get trend analysis
    trend = history.get_trend(MetricType.SNR, node_id="!abc123", hours=24)

    # Export for analysis
    history.export_csv("metrics_export.csv")
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
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Iterator

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """Types of metrics that can be tracked."""
    SNR = "snr"                    # Signal-to-Noise Ratio (dB)
    RSSI = "rssi"                  # Received Signal Strength (dBm)
    HOPS = "hops"                  # Hop count to destination
    LATENCY = "latency"            # Round-trip latency (ms)
    PACKET_LOSS = "packet_loss"    # Packet loss percentage
    THROUGHPUT = "throughput"      # Data throughput (bytes/sec)
    BATTERY = "battery"            # Battery level (%)
    UPTIME = "uptime"              # Node uptime (seconds)
    ANNOUNCE_RATE = "announce_rate"  # Announces per hour
    LINK_QUALITY = "link_quality"  # Composite link quality (0-100)


@dataclass
class MetricPoint:
    """A single metric data point."""
    timestamp: datetime
    metric_type: MetricType
    value: float
    node_id: Optional[str] = None
    edge_id: Optional[str] = None  # "source->dest" format
    tags: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "metric_type": self.metric_type.value,
            "value": self.value,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
            "tags": self.tags,
        }


@dataclass
class TrendAnalysis:
    """Analysis of metric trends over time."""
    metric_type: MetricType
    node_id: Optional[str]
    edge_id: Optional[str]
    period_hours: float

    # Basic statistics
    count: int
    min_value: float
    max_value: float
    avg_value: float
    std_dev: float

    # Trend indicators
    first_value: float
    last_value: float
    change: float          # last - first
    change_percent: float  # percentage change
    trend: str            # "improving", "stable", "degrading"

    # Time range
    start_time: datetime
    end_time: datetime

    def to_dict(self) -> dict:
        return {
            "metric_type": self.metric_type.value,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
            "period_hours": self.period_hours,
            "count": self.count,
            "min": self.min_value,
            "max": self.max_value,
            "avg": round(self.avg_value, 2),
            "std_dev": round(self.std_dev, 2),
            "first": self.first_value,
            "last": self.last_value,
            "change": round(self.change, 2),
            "change_percent": round(self.change_percent, 2),
            "trend": self.trend,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
        }


@dataclass
class AggregatedMetric:
    """Aggregated metric for time buckets."""
    timestamp: datetime  # Start of bucket
    bucket_seconds: int
    count: int
    min_value: float
    max_value: float
    avg_value: float
    sum_value: float


class MetricsHistory:
    """
    Historical metrics storage and analysis.

    Uses SQLite for persistent storage with automatic retention management.
    Thread-safe for concurrent access from multiple components.
    """

    # Default retention periods
    DEFAULT_RETENTION_DAYS = 30
    DEFAULT_AGGREGATION_HOURS = 24  # Keep raw data for 24 hours

    # Database schema version for migrations
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str = None, retention_days: int = None):
        """
        Initialize metrics history.

        Args:
            db_path: Path to SQLite database (default: ~/.cache/meshforge/metrics.db)
            retention_days: How long to keep data (default: 30 days)
        """
        if db_path is None:
            cache_dir = get_real_user_home() / ".cache" / "meshforge"
            cache_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(cache_dir / "metrics.db")

        self._db_path = db_path
        self._retention_days = retention_days or self.DEFAULT_RETENTION_DAYS
        self._lock = threading.RLock()
        self._local = threading.local()

        # Initialize database
        self._init_db()

        # Start background cleanup
        self._cleanup_thread = None
        self._stop_cleanup = threading.Event()
        self._start_cleanup_thread()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a thread-local database connection."""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=30.0
            )
            self._local.connection.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
        return self._local.connection

    @contextmanager
    def _transaction(self):
        """Context manager for database transactions."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self):
        """Initialize database schema."""
        with self._transaction() as conn:
            # Main metrics table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    value REAL NOT NULL,
                    node_id TEXT,
                    edge_id TEXT,
                    tags TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Aggregated metrics (hourly rollups)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics_hourly (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hour_start TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    node_id TEXT,
                    edge_id TEXT,
                    count INTEGER NOT NULL,
                    min_value REAL NOT NULL,
                    max_value REAL NOT NULL,
                    avg_value REAL NOT NULL,
                    sum_value REAL NOT NULL,
                    UNIQUE(hour_start, metric_type, node_id, edge_id)
                )
            """)

            # Schema version tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            """)

            # Check/update schema version
            cursor = conn.execute("SELECT version FROM schema_version")
            row = cursor.fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)",
                             (self.SCHEMA_VERSION,))

            # Create indexes for performance
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_timestamp
                ON metrics(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_type_node
                ON metrics(metric_type, node_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_type_edge
                ON metrics(metric_type, edge_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hourly_hour
                ON metrics_hourly(hour_start)
            """)

    def _start_cleanup_thread(self):
        """Start background thread for cleanup and aggregation."""
        def cleanup_loop():
            while not self._stop_cleanup.is_set():
                try:
                    self._perform_cleanup()
                    self._aggregate_old_data()
                except Exception as e:
                    logger.error(f"Metrics cleanup error: {e}")

                # Run every hour
                self._stop_cleanup.wait(3600)

        self._cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def _perform_cleanup(self):
        """Remove old data beyond retention period and reclaim disk space."""
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        deleted_raw = 0
        deleted_hourly = 0

        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM metrics WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            deleted_raw = cursor.rowcount

            cursor = conn.execute(
                "DELETE FROM metrics_hourly WHERE hour_start < ?",
                (cutoff.isoformat(),)
            )
            deleted_hourly = cursor.rowcount

        # Run VACUUM to reclaim disk space (must be outside transaction)
        # Only vacuum if we actually deleted something significant
        if deleted_raw > 100 or deleted_hourly > 10:
            try:
                conn = self._get_connection()
                conn.execute("VACUUM")
                logger.debug(f"Vacuumed database after deleting {deleted_raw} raw, {deleted_hourly} hourly records")
            except Exception as e:
                # VACUUM can fail if another connection is active; not critical
                logger.debug(f"VACUUM skipped: {e}")

        logger.debug(f"Cleaned metrics older than {cutoff.isoformat()} (deleted {deleted_raw} raw, {deleted_hourly} hourly)")

    def _aggregate_old_data(self):
        """Aggregate raw data older than aggregation threshold into hourly buckets."""
        cutoff = datetime.now() - timedelta(hours=self.DEFAULT_AGGREGATION_HOURS)

        with self._transaction() as conn:
            # Find data to aggregate
            cursor = conn.execute("""
                SELECT
                    strftime('%Y-%m-%dT%H:00:00', timestamp) as hour_start,
                    metric_type,
                    node_id,
                    edge_id,
                    COUNT(*) as count,
                    MIN(value) as min_value,
                    MAX(value) as max_value,
                    AVG(value) as avg_value,
                    SUM(value) as sum_value
                FROM metrics
                WHERE timestamp < ?
                GROUP BY hour_start, metric_type, node_id, edge_id
            """, (cutoff.isoformat(),))

            # Insert aggregations
            for row in cursor:
                conn.execute("""
                    INSERT OR REPLACE INTO metrics_hourly
                    (hour_start, metric_type, node_id, edge_id, count,
                     min_value, max_value, avg_value, sum_value)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row['hour_start'],
                    row['metric_type'],
                    row['node_id'],
                    row['edge_id'],
                    row['count'],
                    row['min_value'],
                    row['max_value'],
                    row['avg_value'],
                    row['sum_value'],
                ))

            # Remove aggregated raw data
            conn.execute(
                "DELETE FROM metrics WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )

    def close(self):
        """Close database connections and stop background threads."""
        self._stop_cleanup.set()
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5)

        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None

    def record(self, metric_type: MetricType, value: float,
               node_id: str = None, edge_id: str = None,
               tags: Dict[str, str] = None, timestamp: datetime = None):
        """
        Record a metric value.

        Args:
            metric_type: Type of metric
            value: Numeric value
            node_id: Associated node ID (optional)
            edge_id: Associated edge ID in "source->dest" format (optional)
            tags: Additional key-value tags (optional)
            timestamp: Custom timestamp (default: now)
        """
        if timestamp is None:
            timestamp = datetime.now()

        tags_json = json.dumps(tags) if tags else None

        with self._transaction() as conn:
            conn.execute("""
                INSERT INTO metrics (timestamp, metric_type, value, node_id, edge_id, tags)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                timestamp.isoformat(),
                metric_type.value,
                value,
                node_id,
                edge_id,
                tags_json,
            ))

    def record_batch(self, points: List[MetricPoint]):
        """
        Record multiple metric points efficiently.

        Args:
            points: List of MetricPoint objects
        """
        with self._transaction() as conn:
            conn.executemany("""
                INSERT INTO metrics (timestamp, metric_type, value, node_id, edge_id, tags)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [
                (
                    p.timestamp.isoformat(),
                    p.metric_type.value,
                    p.value,
                    p.node_id,
                    p.edge_id,
                    json.dumps(p.tags) if p.tags else None,
                )
                for p in points
            ])

    def get_recent(self, metric_type: MetricType = None,
                   node_id: str = None, edge_id: str = None,
                   hours: float = 24, limit: int = 1000) -> List[MetricPoint]:
        """
        Get recent metric values.

        Args:
            metric_type: Filter by metric type (optional)
            node_id: Filter by node ID (optional)
            edge_id: Filter by edge ID (optional)
            hours: How many hours back to query
            limit: Maximum number of results

        Returns:
            List of MetricPoint objects, oldest first
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        conditions = ["timestamp >= ?"]
        params: List[Any] = [cutoff.isoformat()]

        if metric_type:
            conditions.append("metric_type = ?")
            params.append(metric_type.value)

        if node_id:
            conditions.append("node_id = ?")
            params.append(node_id)

        if edge_id:
            conditions.append("edge_id = ?")
            params.append(edge_id)

        params.append(limit)

        query = f"""
            SELECT timestamp, metric_type, value, node_id, edge_id, tags
            FROM metrics
            WHERE {' AND '.join(conditions)}
            ORDER BY timestamp ASC
            LIMIT ?
        """

        conn = self._get_connection()
        cursor = conn.execute(query, params)

        points = []
        for row in cursor:
            tags = json.loads(row['tags']) if row['tags'] else {}
            points.append(MetricPoint(
                timestamp=datetime.fromisoformat(row['timestamp']),
                metric_type=MetricType(row['metric_type']),
                value=row['value'],
                node_id=row['node_id'],
                edge_id=row['edge_id'],
                tags=tags,
            ))

        return points

    def get_latest(self, metric_type: MetricType,
                   node_id: str = None, edge_id: str = None) -> Optional[MetricPoint]:
        """
        Get the most recent value for a metric.

        Args:
            metric_type: Type of metric
            node_id: Filter by node ID (optional)
            edge_id: Filter by edge ID (optional)

        Returns:
            Most recent MetricPoint or None
        """
        conditions = ["metric_type = ?"]
        params: List[Any] = [metric_type.value]

        if node_id:
            conditions.append("node_id = ?")
            params.append(node_id)

        if edge_id:
            conditions.append("edge_id = ?")
            params.append(edge_id)

        query = f"""
            SELECT timestamp, metric_type, value, node_id, edge_id, tags
            FROM metrics
            WHERE {' AND '.join(conditions)}
            ORDER BY timestamp DESC
            LIMIT 1
        """

        conn = self._get_connection()
        cursor = conn.execute(query, params)
        row = cursor.fetchone()

        if row:
            tags = json.loads(row['tags']) if row['tags'] else {}
            return MetricPoint(
                timestamp=datetime.fromisoformat(row['timestamp']),
                metric_type=MetricType(row['metric_type']),
                value=row['value'],
                node_id=row['node_id'],
                edge_id=row['edge_id'],
                tags=tags,
            )

        return None

    def get_trend(self, metric_type: MetricType,
                  node_id: str = None, edge_id: str = None,
                  hours: float = 24) -> Optional[TrendAnalysis]:
        """
        Analyze trend for a metric over time.

        Args:
            metric_type: Type of metric
            node_id: Filter by node ID (optional)
            edge_id: Filter by edge ID (optional)
            hours: Time period to analyze

        Returns:
            TrendAnalysis object or None if insufficient data
        """
        points = self.get_recent(
            metric_type=metric_type,
            node_id=node_id,
            edge_id=edge_id,
            hours=hours,
            limit=10000
        )

        if len(points) < 2:
            return None

        values = [p.value for p in points]
        count = len(values)
        min_val = min(values)
        max_val = max(values)
        avg_val = sum(values) / count

        # Calculate standard deviation
        variance = sum((v - avg_val) ** 2 for v in values) / count
        std_dev = variance ** 0.5

        # Get first and last values for trend
        first_val = values[0]
        last_val = values[-1]
        change = last_val - first_val
        change_pct = (change / abs(first_val) * 100) if first_val != 0 else 0

        # Determine trend based on metric type
        # For SNR, RSSI, THROUGHPUT, LINK_QUALITY: higher is better
        # For HOPS, LATENCY, PACKET_LOSS: lower is better
        improving_metrics = {
            MetricType.SNR, MetricType.RSSI, MetricType.THROUGHPUT,
            MetricType.LINK_QUALITY, MetricType.BATTERY, MetricType.UPTIME
        }

        threshold = 5  # 5% change threshold for "stable"

        if abs(change_pct) < threshold:
            trend = "stable"
        elif metric_type in improving_metrics:
            trend = "improving" if change > 0 else "degrading"
        else:
            trend = "improving" if change < 0 else "degrading"

        return TrendAnalysis(
            metric_type=metric_type,
            node_id=node_id,
            edge_id=edge_id,
            period_hours=hours,
            count=count,
            min_value=min_val,
            max_value=max_val,
            avg_value=avg_val,
            std_dev=std_dev,
            first_value=first_val,
            last_value=last_val,
            change=change,
            change_percent=change_pct,
            trend=trend,
            start_time=points[0].timestamp,
            end_time=points[-1].timestamp,
        )

    def get_aggregated(self, metric_type: MetricType,
                       node_id: str = None, edge_id: str = None,
                       hours: float = 168,  # 1 week
                       bucket_hours: int = 1) -> List[AggregatedMetric]:
        """
        Get aggregated (bucketed) metrics for charting.

        Args:
            metric_type: Type of metric
            node_id: Filter by node ID (optional)
            edge_id: Filter by edge ID (optional)
            hours: Time period to query
            bucket_hours: Bucket size in hours

        Returns:
            List of AggregatedMetric objects
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        conditions = ["hour_start >= ?", "metric_type = ?"]
        params: List[Any] = [cutoff.isoformat(), metric_type.value]

        if node_id:
            conditions.append("(node_id = ? OR node_id IS NULL)")
            params.append(node_id)

        if edge_id:
            conditions.append("(edge_id = ? OR edge_id IS NULL)")
            params.append(edge_id)

        query = f"""
            SELECT hour_start, count, min_value, max_value, avg_value, sum_value
            FROM metrics_hourly
            WHERE {' AND '.join(conditions)}
            ORDER BY hour_start ASC
        """

        conn = self._get_connection()
        cursor = conn.execute(query, params)

        aggregates = []
        for row in cursor:
            aggregates.append(AggregatedMetric(
                timestamp=datetime.fromisoformat(row['hour_start']),
                bucket_seconds=3600,
                count=row['count'],
                min_value=row['min_value'],
                max_value=row['max_value'],
                avg_value=row['avg_value'],
                sum_value=row['sum_value'],
            ))

        return aggregates

    def get_statistics(self) -> Dict[str, Any]:
        """Get overall statistics about stored metrics."""
        conn = self._get_connection()

        # Count by metric type
        cursor = conn.execute("""
            SELECT metric_type, COUNT(*) as count
            FROM metrics
            GROUP BY metric_type
        """)
        type_counts = {row['metric_type']: row['count'] for row in cursor}

        # Total counts
        cursor = conn.execute("SELECT COUNT(*) as count FROM metrics")
        raw_count = cursor.fetchone()['count']

        cursor = conn.execute("SELECT COUNT(*) as count FROM metrics_hourly")
        hourly_count = cursor.fetchone()['count']

        # Time range
        cursor = conn.execute("""
            SELECT MIN(timestamp) as oldest, MAX(timestamp) as newest
            FROM metrics
        """)
        row = cursor.fetchone()
        oldest = row['oldest']
        newest = row['newest']

        # Unique nodes and edges
        cursor = conn.execute("""
            SELECT COUNT(DISTINCT node_id) as nodes, COUNT(DISTINCT edge_id) as edges
            FROM metrics
        """)
        row = cursor.fetchone()

        return {
            "raw_points": raw_count,
            "hourly_aggregates": hourly_count,
            "metric_types": type_counts,
            "unique_nodes": row['nodes'],
            "unique_edges": row['edges'],
            "oldest_timestamp": oldest,
            "newest_timestamp": newest,
            "retention_days": self._retention_days,
        }

    def export_csv(self, output_path: str, metric_type: MetricType = None,
                   hours: float = 24) -> int:
        """
        Export metrics to CSV file.

        Args:
            output_path: Output file path
            metric_type: Filter by metric type (optional)
            hours: Time period to export

        Returns:
            Number of rows exported
        """
        points = self.get_recent(metric_type=metric_type, hours=hours, limit=100000)

        with open(output_path, 'w') as f:
            f.write("timestamp,metric_type,value,node_id,edge_id,tags\n")
            for p in points:
                tags_str = json.dumps(p.tags) if p.tags else ""
                f.write(f"{p.timestamp.isoformat()},{p.metric_type.value},"
                        f"{p.value},{p.node_id or ''},{p.edge_id or ''},"
                        f"\"{tags_str}\"\n")

        logger.info(f"Exported {len(points)} metrics to {output_path}")
        return len(points)

    def get_node_metrics_summary(self, node_id: str) -> Dict[str, Any]:
        """
        Get a summary of all metrics for a specific node.

        Args:
            node_id: Node identifier

        Returns:
            Dict with latest values and trends for all metric types
        """
        summary = {
            "node_id": node_id,
            "metrics": {},
            "last_seen": None,
        }

        for metric_type in MetricType:
            latest = self.get_latest(metric_type, node_id=node_id)
            trend = self.get_trend(metric_type, node_id=node_id, hours=24)

            if latest:
                summary["metrics"][metric_type.value] = {
                    "latest_value": latest.value,
                    "latest_time": latest.timestamp.isoformat(),
                    "trend": trend.to_dict() if trend else None,
                }

                if summary["last_seen"] is None or latest.timestamp > datetime.fromisoformat(summary["last_seen"]):
                    summary["last_seen"] = latest.timestamp.isoformat()

        return summary

    def iter_all_points(self, batch_size: int = 1000) -> Iterator[List[MetricPoint]]:
        """
        Iterate over all stored metric points in batches.

        Useful for bulk export or migration.

        Args:
            batch_size: Number of points per batch

        Yields:
            Lists of MetricPoint objects
        """
        conn = self._get_connection()
        offset = 0

        while True:
            cursor = conn.execute("""
                SELECT timestamp, metric_type, value, node_id, edge_id, tags
                FROM metrics
                ORDER BY timestamp ASC
                LIMIT ? OFFSET ?
            """, (batch_size, offset))

            rows = cursor.fetchall()
            if not rows:
                break

            points = []
            for row in rows:
                tags = json.loads(row['tags']) if row['tags'] else {}
                points.append(MetricPoint(
                    timestamp=datetime.fromisoformat(row['timestamp']),
                    metric_type=MetricType(row['metric_type']),
                    value=row['value'],
                    node_id=row['node_id'],
                    edge_id=row['edge_id'],
                    tags=tags,
                ))

            yield points
            offset += batch_size


# Global instance
_history: Optional[MetricsHistory] = None


def get_metrics_history() -> MetricsHistory:
    """Get the global metrics history instance."""
    global _history
    if _history is None:
        _history = MetricsHistory()
    return _history


# Integration with NetworkTopology
def record_topology_metrics(topology) -> int:
    """
    Record current topology metrics from a NetworkTopology instance.

    Args:
        topology: NetworkTopology instance

    Returns:
        Number of metrics recorded
    """
    history = get_metrics_history()
    points = []
    now = datetime.now()

    try:
        topo_dict = topology.to_dict()

        # Record edge metrics
        for edge in topo_dict.get("edges", []):
            source_id = edge.get("source_id", "")
            dest_id = edge.get("dest_id", "")
            edge_id = f"{source_id}->{dest_id}"

            # Hop count
            hops = edge.get("hops")
            if hops is not None:
                points.append(MetricPoint(
                    timestamp=now,
                    metric_type=MetricType.HOPS,
                    value=float(hops),
                    edge_id=edge_id,
                ))

            # SNR
            snr = edge.get("snr")
            if snr is not None:
                points.append(MetricPoint(
                    timestamp=now,
                    metric_type=MetricType.SNR,
                    value=float(snr),
                    edge_id=edge_id,
                ))

            # RSSI
            rssi = edge.get("rssi")
            if rssi is not None:
                points.append(MetricPoint(
                    timestamp=now,
                    metric_type=MetricType.RSSI,
                    value=float(rssi),
                    edge_id=edge_id,
                ))

            # Announce count as announce rate indicator
            announce_count = edge.get("announce_count", 0)
            points.append(MetricPoint(
                timestamp=now,
                metric_type=MetricType.ANNOUNCE_RATE,
                value=float(announce_count),
                edge_id=edge_id,
            ))

        if points:
            history.record_batch(points)

        return len(points)

    except Exception as e:
        logger.error(f"Error recording topology metrics: {e}")
        return 0

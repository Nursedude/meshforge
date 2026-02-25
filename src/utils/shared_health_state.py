"""
Shared Health State for MeshForge Multi-Process Coordination.

SQLite-backed shared health state that enables multiple MeshForge processes
(gateway, TUI, monitoring) to share health observations. Based on NGINX
zone pattern for shared worker state.

Usage:
    from utils.shared_health_state import SharedHealthState

    # In gateway process
    state = SharedHealthState()
    state.update_service("meshtasticd", "healthy", latency_ms=45.2)

    # In TUI process (same data visible)
    state = SharedHealthState()
    services = state.get_all_services()
    for svc in services:
        print(f"{svc['service']}: {svc['state']}")

    # Get aggregated metrics for dashboards
    metrics = state.get_metrics()
    print(f"Overall uptime: {metrics['avg_uptime_pct']:.1f}%")

Reference:
    NGINX zone-based shared state:
    https://nginx.org/en/docs/http/ngx_http_upstream_module.html#zone
"""

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class HealthState(Enum):
    """Health state for a monitored service (matches active_health_probe.py)."""
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    RECOVERING = "recovering"


@dataclass
class ServiceHealthRecord:
    """Record of a service's current health state."""
    service: str
    state: HealthState
    reason: str
    latency_ms: float
    updated_at: float
    updated_by: str
    consecutive_passes: int
    consecutive_fails: int
    uptime_pct: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "service": self.service,
            "state": self.state.value,
            "reason": self.reason,
            "latency_ms": self.latency_ms,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "consecutive_passes": self.consecutive_passes,
            "consecutive_fails": self.consecutive_fails,
            "uptime_pct": self.uptime_pct,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> 'ServiceHealthRecord':
        """Create from SQLite row."""
        return cls(
            service=row["service"],
            state=HealthState(row["state"]),
            reason=row["reason"] or "",
            latency_ms=row["latency_ms"] or 0.0,
            updated_at=row["updated_at"],
            updated_by=row["updated_by"] or "",
            consecutive_passes=row["consecutive_passes"],
            consecutive_fails=row["consecutive_fails"],
            uptime_pct=row["uptime_pct"],
        )


@dataclass
class HealthEvent:
    """Historical health state change event."""
    id: int
    service: str
    old_state: str
    new_state: str
    reason: str
    timestamp: float
    process_id: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "service": self.service,
            "old_state": self.old_state,
            "new_state": self.new_state,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "process_id": self.process_id,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> 'HealthEvent':
        """Create from SQLite row."""
        return cls(
            id=row["id"],
            service=row["service"],
            old_state=row["old_state"],
            new_state=row["new_state"],
            reason=row["reason"] or "",
            timestamp=row["timestamp"],
            process_id=row["process_id"] or "",
        )


class SharedHealthState:
    """
    SQLite-backed shared health state for multi-process access.

    Similar to NGINX zone for shared worker state. Multiple MeshForge
    processes can read and write health observations atomically.

    Features:
    - ACID transactions for consistency
    - Process identification for debugging
    - Historical event logging
    - Aggregated metrics for dashboards
    - Automatic stale detection

    Attributes:
        db_path: Path to SQLite database file
        process_id: Identifier for this process (for debugging)
        stale_threshold: Seconds before a service is considered stale
    """

    # Default stale threshold: 2 minutes without update
    DEFAULT_STALE_THRESHOLD = 120

    # History retention: 7 days
    HISTORY_RETENTION_DAYS = 7

    def __init__(
        self,
        db_path: Optional[Path] = None,
        process_id: Optional[str] = None,
        stale_threshold: int = DEFAULT_STALE_THRESHOLD,
    ):
        """
        Initialize shared health state.

        Args:
            db_path: Path to SQLite database. Default: ~/.config/meshforge/health_state.db
            process_id: Identifier for this process. Default: PID
            stale_threshold: Seconds before service considered stale (default: 120)
        """
        if db_path is None:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = config_dir / "health_state.db"

        self.db_path = db_path
        self.process_id = process_id or f"pid-{os.getpid()}"
        self.stale_threshold = stale_threshold
        self._local = threading.local()

        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Get thread-local database connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                timeout=30,
                isolation_level="DEFERRED",
            )
            self._local.conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent access
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")

        conn = self._local.conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            # Current service health state
            conn.execute("""
                CREATE TABLE IF NOT EXISTS service_health (
                    service TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT 'unknown',
                    reason TEXT DEFAULT '',
                    latency_ms REAL DEFAULT 0.0,
                    updated_at REAL NOT NULL,
                    updated_by TEXT DEFAULT '',
                    consecutive_passes INTEGER DEFAULT 0,
                    consecutive_fails INTEGER DEFAULT 0,
                    total_checks INTEGER DEFAULT 0,
                    total_passes INTEGER DEFAULT 0,
                    uptime_pct REAL DEFAULT 0.0
                )
            """)

            # Historical state transitions
            conn.execute("""
                CREATE TABLE IF NOT EXISTS health_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service TEXT NOT NULL,
                    old_state TEXT NOT NULL,
                    new_state TEXT NOT NULL,
                    reason TEXT DEFAULT '',
                    timestamp REAL NOT NULL,
                    process_id TEXT DEFAULT ''
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_service
                ON health_events(service)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp
                ON health_events(timestamp DESC)
            """)

            # Latency samples for percentile calculations
            conn.execute("""
                CREATE TABLE IF NOT EXISTS latency_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_latency_service_time
                ON latency_samples(service, timestamp DESC)
            """)

        logger.debug(f"SharedHealthState initialized: {self.db_path}")

    def close(self) -> None:
        """Close the database connection for this thread."""
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def update_service(
        self,
        service: str,
        state: str,
        reason: str = "",
        latency_ms: float = 0.0,
    ) -> bool:
        """
        Update health state for a service.

        This is the primary write method. It updates the current state
        and logs a transition event if the state changed.

        Args:
            service: Service name (e.g., "meshtasticd", "rnsd")
            state: New state ("healthy", "unhealthy", "recovering", "unknown")
            reason: Reason for state (e.g., "timeout", "connection_refused")
            latency_ms: Probe latency in milliseconds

        Returns:
            True if state changed, False if same state
        """
        now = time.time()

        with self._get_connection() as conn:
            # Get current state
            cursor = conn.execute(
                "SELECT state, consecutive_passes, consecutive_fails, "
                "total_checks, total_passes FROM service_health WHERE service = ?",
                (service,)
            )
            row = cursor.fetchone()

            if row:
                old_state = row["state"]
                cons_passes = row["consecutive_passes"]
                cons_fails = row["consecutive_fails"]
                total_checks = row["total_checks"]
                total_passes = row["total_passes"]
            else:
                old_state = "unknown"
                cons_passes = 0
                cons_fails = 0
                total_checks = 0
                total_passes = 0

            # Update counters
            total_checks += 1
            if state == "healthy":
                cons_passes += 1
                cons_fails = 0
                total_passes += 1
            elif state == "unhealthy":
                cons_fails += 1
                cons_passes = 0
            elif state == "recovering":
                # Recovering is between unhealthy and healthy
                cons_passes += 1
                cons_fails = 0

            uptime_pct = (total_passes / total_checks * 100) if total_checks > 0 else 0.0

            # Update or insert current state
            conn.execute("""
                INSERT OR REPLACE INTO service_health
                (service, state, reason, latency_ms, updated_at, updated_by,
                 consecutive_passes, consecutive_fails, total_checks, total_passes, uptime_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                service, state, reason, latency_ms, now, self.process_id,
                cons_passes, cons_fails, total_checks, total_passes, uptime_pct
            ))

            # Log state transition if changed
            state_changed = old_state != state
            if state_changed:
                conn.execute("""
                    INSERT INTO health_events
                    (service, old_state, new_state, reason, timestamp, process_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (service, old_state, state, reason, now, self.process_id))
                logger.info(
                    f"Health state change: {service} {old_state} -> {state} "
                    f"(reason: {reason})"
                )

            # Record latency sample
            if latency_ms > 0:
                conn.execute("""
                    INSERT INTO latency_samples (service, latency_ms, timestamp)
                    VALUES (?, ?, ?)
                """, (service, latency_ms, now))

        return state_changed

    def get_service(self, service: str) -> Optional[ServiceHealthRecord]:
        """
        Get current health state for a service.

        Args:
            service: Service name

        Returns:
            ServiceHealthRecord or None if service not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM service_health WHERE service = ?",
                (service,)
            )
            row = cursor.fetchone()
            if row:
                return ServiceHealthRecord.from_row(row)
            return None

    def get_all_services(self) -> List[ServiceHealthRecord]:
        """
        Get health state for all services.

        Returns:
            List of ServiceHealthRecord
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM service_health ORDER BY service"
            )
            return [ServiceHealthRecord.from_row(row) for row in cursor.fetchall()]

    def is_healthy(self, service: str) -> bool:
        """
        Check if a service is currently healthy.

        Args:
            service: Service name

        Returns:
            True if service state is "healthy", False otherwise
        """
        record = self.get_service(service)
        if not record:
            return False
        return record.state == HealthState.HEALTHY

    def is_stale(self, service: str) -> bool:
        """
        Check if a service's health data is stale.

        A service is stale if it hasn't been updated within stale_threshold.

        Args:
            service: Service name

        Returns:
            True if service data is stale or not found
        """
        record = self.get_service(service)
        if not record:
            return True
        return (time.time() - record.updated_at) > self.stale_threshold

    def get_stale_services(self) -> List[str]:
        """Get list of services with stale health data."""
        cutoff = time.time() - self.stale_threshold
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT service FROM service_health WHERE updated_at < ?",
                (cutoff,)
            )
            return [row["service"] for row in cursor.fetchall()]

    def get_recent_events(
        self,
        service: Optional[str] = None,
        limit: int = 50,
        hours: int = 24,
    ) -> List[HealthEvent]:
        """
        Get recent health state transition events.

        Args:
            service: Filter by service name (optional)
            limit: Maximum events to return
            hours: Look back this many hours

        Returns:
            List of HealthEvent, newest first
        """
        cutoff = time.time() - (hours * 3600)

        with self._get_connection() as conn:
            if service:
                cursor = conn.execute("""
                    SELECT * FROM health_events
                    WHERE service = ? AND timestamp > ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (service, cutoff, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM health_events
                    WHERE timestamp > ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (cutoff, limit))

            return [HealthEvent.from_row(row) for row in cursor.fetchall()]

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get aggregated health metrics for dashboards.

        Returns metrics suitable for Prometheus export or TUI display.

        Returns:
            Dict with aggregated metrics
        """
        now = time.time()

        with self._get_connection() as conn:
            # Service counts by state
            cursor = conn.execute("""
                SELECT state, COUNT(*) as count
                FROM service_health
                GROUP BY state
            """)
            state_counts = {row["state"]: row["count"] for row in cursor.fetchall()}

            # Average uptime percentage
            cursor = conn.execute(
                "SELECT AVG(uptime_pct) as avg_uptime FROM service_health"
            )
            avg_uptime = cursor.fetchone()["avg_uptime"] or 0.0

            # Events in last hour
            hour_ago = now - 3600
            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM health_events WHERE timestamp > ?",
                (hour_ago,)
            )
            events_last_hour = cursor.fetchone()["count"]

            # Stale services
            stale_cutoff = now - self.stale_threshold
            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM service_health WHERE updated_at < ?",
                (stale_cutoff,)
            )
            stale_count = cursor.fetchone()["count"]

            # Total services
            cursor = conn.execute("SELECT COUNT(*) as count FROM service_health")
            total_services = cursor.fetchone()["count"]

        return {
            "total_services": total_services,
            "healthy_count": state_counts.get("healthy", 0),
            "unhealthy_count": state_counts.get("unhealthy", 0),
            "recovering_count": state_counts.get("recovering", 0),
            "unknown_count": state_counts.get("unknown", 0),
            "stale_count": stale_count,
            "avg_uptime_pct": round(avg_uptime, 2),
            "events_last_hour": events_last_hour,
            "timestamp": now,
        }

    def get_latency_percentiles(
        self,
        service: str,
        hours: int = 1,
    ) -> Dict[str, float]:
        """
        Calculate latency percentiles for a service.

        Args:
            service: Service name
            hours: Look back this many hours

        Returns:
            Dict with p50, p90, p99, avg, min, max latencies
        """
        cutoff = time.time() - (hours * 3600)

        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT latency_ms FROM latency_samples
                WHERE service = ? AND timestamp > ?
                ORDER BY latency_ms
            """, (service, cutoff))

            samples = [row["latency_ms"] for row in cursor.fetchall()]

        if not samples:
            return {
                "p50": 0.0,
                "p90": 0.0,
                "p99": 0.0,
                "avg": 0.0,
                "min": 0.0,
                "max": 0.0,
                "count": 0,
            }

        n = len(samples)
        return {
            "p50": samples[int(n * 0.50)] if n > 0 else 0.0,
            "p90": samples[int(n * 0.90)] if n > 0 else 0.0,
            "p99": samples[int(n * 0.99)] if n > 0 else 0.0,
            "avg": sum(samples) / n,
            "min": min(samples),
            "max": max(samples),
            "count": n,
        }

    def purge_old_data(self, days: int = HISTORY_RETENTION_DAYS) -> Dict[str, int]:
        """
        Purge old historical data.

        Args:
            days: Delete data older than this

        Returns:
            Dict with counts of deleted events and samples
        """
        cutoff = time.time() - (days * 86400)

        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM health_events WHERE timestamp < ?",
                (cutoff,)
            )
            events_deleted = cursor.rowcount

            cursor = conn.execute(
                "DELETE FROM latency_samples WHERE timestamp < ?",
                (cutoff,)
            )
            samples_deleted = cursor.rowcount

        if events_deleted > 0 or samples_deleted > 0:
            logger.info(
                f"Purged health data: {events_deleted} events, "
                f"{samples_deleted} latency samples older than {days} days"
            )

        return {
            "events_deleted": events_deleted,
            "samples_deleted": samples_deleted,
        }

    def clear_service(self, service: str) -> bool:
        """
        Remove a service from tracking.

        Args:
            service: Service name to remove

        Returns:
            True if service was removed
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM service_health WHERE service = ?",
                (service,)
            )
            return cursor.rowcount > 0

    def clear_all(self) -> int:
        """
        Clear all health state data.

        Returns:
            Number of services cleared
        """
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM service_health")
            services_cleared = cursor.rowcount
            conn.execute("DELETE FROM health_events")
            conn.execute("DELETE FROM latency_samples")

        logger.warning(f"Cleared all health state: {services_cleared} services")
        return services_cleared


def create_shared_state() -> SharedHealthState:
    """
    Create a SharedHealthState instance with default configuration.

    Convenience factory for common use case.

    Returns:
        Configured SharedHealthState ready to use
    """
    return SharedHealthState()


def integrate_with_active_probe(
    shared_state: SharedHealthState,
    probe: 'ActiveHealthProbe',
) -> None:
    """
    Integrate SharedHealthState with ActiveHealthProbe.

    Registers callbacks on the probe to automatically update
    shared state when health changes are detected.

    Args:
        shared_state: SharedHealthState instance
        probe: ActiveHealthProbe instance to integrate with

    Usage:
        from utils.shared_health_state import SharedHealthState, integrate_with_active_probe
        from utils.active_health_probe import create_gateway_health_probe

        state = SharedHealthState()
        probe = create_gateway_health_probe()
        integrate_with_active_probe(state, probe)
        probe.start()
    """
    def on_state_change(service: str, new_state) -> None:
        """Callback when probe detects state change."""
        status = probe.get_status(service)
        reason = ""
        latency_ms = 0.0
        if status and status.get("last_result"):
            reason = status["last_result"].get("reason", "")
            latency_ms = status["last_result"].get("latency_ms", 0.0)

        shared_state.update_service(
            service=service,
            state=new_state.value,
            reason=reason,
            latency_ms=latency_ms,
        )

    # Register for all state changes
    probe.register_callback("on_state_change", on_state_change)
    logger.info("SharedHealthState integrated with ActiveHealthProbe")

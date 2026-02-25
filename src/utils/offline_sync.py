"""
Offline-First Data Sync — queue telemetry when offline, sync when back.

Provides a SQLite-backed queue for outbound data (telemetry, events,
map updates) that accumulates locally when internet is unavailable
and automatically syncs when connectivity returns.

Architecture:
    ConnectivityMonitor — periodic internet availability check
    OfflineSyncQueue — SQLite persistence with retry/dead-letter
    SyncEngine — batch upload with pluggable handlers

Usage:
    from utils.offline_sync import SyncEngine, SyncCategory

    engine = SyncEngine()
    engine.register_handler(SyncCategory.TELEMETRY, my_upload_fn)

    # Queue data (works offline or online)
    engine.enqueue(SyncCategory.TELEMETRY, {"node": "!abc", "snr": -5.0})

    # Engine auto-syncs when online (call periodically or in background)
    engine.sync_cycle()
"""

import json
import logging
import os
import socket
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class SyncCategory(Enum):
    """Categories of data that can be queued for sync."""
    TELEMETRY = "telemetry"        # Node metrics (SNR, RSSI, battery)
    POSITION = "position"          # GPS position updates
    EVENT = "event"                # Diagnostic/health events
    MESSAGE = "message"            # Outbound messages
    MAP_UPDATE = "map_update"      # Map data snapshots


class SyncStatus(Enum):
    """Status of a queued sync record."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SYNCED = "synced"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


# Configuration defaults
MAX_QUEUE_SIZE = 10000          # Maximum queued records before shedding
MAX_RETRIES = 5                 # Attempts before dead-letter
BATCH_SIZE = 50                 # Records per sync cycle
CLEANUP_AGE_HOURS = 72          # Remove synced/dead records older than this
CONNECTIVITY_TIMEOUT = 3.0      # Seconds for connectivity check
CONNECTIVITY_CHECK_INTERVAL = 30.0  # Seconds between connectivity checks
STALE_IN_PROGRESS_SEC = 300     # 5 min: reset stuck in_progress records

# Retry backoff: 30s, 90s, 270s, 810s, 2430s
RETRY_BACKOFF_BASE = 30
RETRY_BACKOFF_MULTIPLIER = 3


@dataclass
class SyncRecord:
    """A single queued sync item."""
    record_id: str
    category: str
    payload: str          # JSON-serialized data
    destination: str      # Handler identifier
    status: str = SyncStatus.PENDING.value
    created_at: float = 0.0
    updated_at: float = 0.0
    retry_count: int = 0
    retry_after: float = 0.0
    error_message: str = ""

    @property
    def payload_dict(self) -> dict:
        """Deserialize payload to dict."""
        try:
            return json.loads(self.payload)
        except (json.JSONDecodeError, TypeError):
            return {}


class ConnectivityMonitor:
    """Checks internet availability with caching.

    Uses DNS resolution (8.8.8.8:53) as a lightweight connectivity
    probe. Results are cached for the configured interval.
    """

    def __init__(self, check_interval: float = CONNECTIVITY_CHECK_INTERVAL,
                 timeout: float = CONNECTIVITY_TIMEOUT):
        """Initialize connectivity monitor.

        Args:
            check_interval: Seconds between actual checks (cache TTL).
            timeout: Socket timeout for connectivity probe.
        """
        self._check_interval = check_interval
        self._timeout = timeout
        self._last_check: float = 0.0
        self._last_result: bool = False
        self._lock = threading.Lock()
        self._check_targets = [
            ("8.8.8.8", 53),       # Google DNS
            ("1.1.1.1", 53),       # Cloudflare DNS
            ("208.67.222.222", 53),  # OpenDNS
        ]

    @property
    def is_online(self) -> bool:
        """Check if internet is available (cached).

        Returns:
            True if at least one DNS target is reachable.
        """
        now = time.time()
        with self._lock:
            if (now - self._last_check) < self._check_interval:
                return self._last_result

            result = self._probe()
            self._last_check = now
            self._last_result = result
            return result

    def _probe(self) -> bool:
        """Actually test connectivity against DNS targets."""
        for host, port in self._check_targets:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(self._timeout)
                    result = s.connect_ex((host, port))
                    if result == 0:
                        return True
            except (OSError, socket.error):
                continue
        return False

    def invalidate(self) -> None:
        """Force next check to re-probe."""
        with self._lock:
            self._last_check = 0.0

    def force_check(self) -> bool:
        """Force an immediate connectivity check (bypasses cache)."""
        self.invalidate()
        return self.is_online


class OfflineSyncQueue:
    """SQLite-backed queue for offline data sync.

    Thread-safe, supports retry with backoff, dead-lettering,
    and automatic cleanup of old records.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize sync queue.

        Args:
            db_path: Path to SQLite database. If None, uses default
                     config directory location.
        """
        self._db_path = db_path or self._get_default_path()
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_default_path(self) -> Path:
        """Get default database path."""
        data_dir = get_real_user_home() / ".local" / "share" / "meshforge"
        return data_dir / "offline_sync.db"

    def _init_db(self) -> None:
        """Initialize database schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_queue (
                    record_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    destination TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    retry_after REAL NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_status
                ON sync_queue(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_category
                ON sync_queue(category)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_retry_after
                ON sync_queue(retry_after)
            """)
            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create the persistent database connection.

        Since all access is serialized by self._lock, a single
        connection is safe and avoids file descriptor leaks.
        check_same_thread=False is safe because the lock prevents
        concurrent access.
        """
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path), timeout=5.0,
                check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def enqueue(self, category: SyncCategory, payload: dict,
                destination: str = "") -> str:
        """Add a record to the sync queue.

        Args:
            category: Type of data being queued.
            payload: Data to sync (will be JSON-serialized).
            destination: Optional handler/endpoint identifier.

        Returns:
            Record ID of the queued item.

        Raises:
            ValueError: If queue is at capacity after shedding.
        """
        record_id = str(uuid.uuid4())[:16]
        now = time.time()
        payload_json = json.dumps(payload, default=str)

        with self._lock:
            with self._get_connection() as conn:
                # Check queue size, shed if needed
                count = conn.execute(
                    "SELECT COUNT(*) FROM sync_queue WHERE status IN (?, ?)",
                    (SyncStatus.PENDING.value, SyncStatus.FAILED.value)
                ).fetchone()[0]

                if count >= MAX_QUEUE_SIZE:
                    # Shed oldest low-priority items
                    shed_count = count - MAX_QUEUE_SIZE + BATCH_SIZE
                    conn.execute("""
                        DELETE FROM sync_queue WHERE record_id IN (
                            SELECT record_id FROM sync_queue
                            WHERE status = ?
                            ORDER BY created_at ASC
                            LIMIT ?
                        )
                    """, (SyncStatus.PENDING.value, shed_count))
                    logger.warning(f"Queue overflow: shed {shed_count} oldest records")

                conn.execute("""
                    INSERT INTO sync_queue
                    (record_id, category, payload, destination,
                     status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (record_id, category.value, payload_json,
                      destination, SyncStatus.PENDING.value, now, now))
                conn.commit()

        logger.debug(f"Enqueued sync record {record_id} [{category.value}]")
        return record_id

    def dequeue_batch(self, limit: int = BATCH_SIZE) -> List[SyncRecord]:
        """Get a batch of records ready for sync.

        Returns records that are pending and past their retry_after time.
        Marks them as in_progress.

        Args:
            limit: Maximum records to return.

        Returns:
            List of SyncRecord objects ready for sync.
        """
        now = time.time()
        records = []

        with self._lock:
            with self._get_connection() as conn:
                # Reset stale in_progress records
                conn.execute("""
                    UPDATE sync_queue
                    SET status = ?, updated_at = ?
                    WHERE status = ?
                    AND updated_at < ?
                """, (SyncStatus.PENDING.value, now,
                      SyncStatus.IN_PROGRESS.value,
                      now - STALE_IN_PROGRESS_SEC))

                # Fetch eligible records
                rows = conn.execute("""
                    SELECT record_id, category, payload, destination,
                           status, created_at, updated_at,
                           retry_count, retry_after, error_message
                    FROM sync_queue
                    WHERE status IN (?, ?)
                    AND retry_after <= ?
                    ORDER BY created_at ASC
                    LIMIT ?
                """, (SyncStatus.PENDING.value, SyncStatus.FAILED.value,
                      now, limit)).fetchall()

                # Mark as in_progress
                ids = [row[0] for row in rows]
                if ids:
                    placeholders = ','.join('?' * len(ids))
                    conn.execute(f"""
                        UPDATE sync_queue
                        SET status = ?, updated_at = ?
                        WHERE record_id IN ({placeholders})
                    """, [SyncStatus.IN_PROGRESS.value, now] + ids)
                    conn.commit()

                for row in rows:
                    records.append(SyncRecord(
                        record_id=row[0],
                        category=row[1],
                        payload=row[2],
                        destination=row[3],
                        status=row[4],
                        created_at=row[5],
                        updated_at=row[6],
                        retry_count=row[7],
                        retry_after=row[8],
                        error_message=row[9],
                    ))

        return records

    def mark_synced(self, record_id: str) -> None:
        """Mark a record as successfully synced.

        Args:
            record_id: ID of the synced record.
        """
        now = time.time()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE sync_queue
                    SET status = ?, updated_at = ?, error_message = ''
                    WHERE record_id = ?
                """, (SyncStatus.SYNCED.value, now, record_id))
                conn.commit()

    def mark_failed(self, record_id: str, error: str = "") -> None:
        """Mark a record as failed, schedule retry or dead-letter.

        Args:
            record_id: ID of the failed record.
            error: Error message for diagnostics.
        """
        now = time.time()
        with self._lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT retry_count FROM sync_queue WHERE record_id = ?",
                    (record_id,)
                ).fetchone()

                if row is None:
                    return

                retry_count = row[0] + 1
                if retry_count >= MAX_RETRIES:
                    # Dead-letter
                    conn.execute("""
                        UPDATE sync_queue
                        SET status = ?, updated_at = ?,
                            retry_count = ?, error_message = ?
                        WHERE record_id = ?
                    """, (SyncStatus.DEAD_LETTER.value, now,
                          retry_count, error, record_id))
                    logger.warning(
                        f"Sync record {record_id} dead-lettered after "
                        f"{retry_count} retries: {error}")
                else:
                    # Schedule retry with exponential backoff
                    delay = RETRY_BACKOFF_BASE * (
                        RETRY_BACKOFF_MULTIPLIER ** (retry_count - 1))
                    retry_after = now + delay
                    conn.execute("""
                        UPDATE sync_queue
                        SET status = ?, updated_at = ?,
                            retry_count = ?, retry_after = ?,
                            error_message = ?
                        WHERE record_id = ?
                    """, (SyncStatus.FAILED.value, now,
                          retry_count, retry_after, error, record_id))
                    logger.debug(
                        f"Sync record {record_id} retry {retry_count}, "
                        f"next attempt in {delay}s")

                conn.commit()

    def get_stats(self) -> Dict[str, int]:
        """Get queue statistics.

        Returns:
            Dict with counts by status.
        """
        with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT status, COUNT(*) FROM sync_queue
                    GROUP BY status
                """).fetchall()
                stats = {s.value: 0 for s in SyncStatus}
                for status, count in rows:
                    stats[status] = count
                stats['total'] = sum(stats.values())
                return stats

    def get_pending_count(self) -> int:
        """Get count of records waiting to sync."""
        with self._lock:
            with self._get_connection() as conn:
                row = conn.execute("""
                    SELECT COUNT(*) FROM sync_queue
                    WHERE status IN (?, ?)
                """, (SyncStatus.PENDING.value,
                      SyncStatus.FAILED.value)).fetchone()
                return row[0] if row else 0

    def cleanup(self, max_age_hours: int = CLEANUP_AGE_HOURS) -> int:
        """Remove old synced and dead-letter records.

        Args:
            max_age_hours: Remove records older than this.

        Returns:
            Number of records removed.
        """
        cutoff = time.time() - (max_age_hours * 3600)
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    DELETE FROM sync_queue
                    WHERE status IN (?, ?)
                    AND updated_at < ?
                """, (SyncStatus.SYNCED.value,
                      SyncStatus.DEAD_LETTER.value, cutoff))
                conn.commit()
                removed = cursor.rowcount
                if removed > 0:
                    logger.info(f"Cleaned up {removed} old sync records")
                return removed

    def purge_all(self) -> int:
        """Remove all records from the queue.

        Returns:
            Number of records removed.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute("DELETE FROM sync_queue")
                conn.commit()
                return cursor.rowcount

    def get_records_by_category(self, category: SyncCategory,
                                limit: int = 100) -> List[SyncRecord]:
        """Get records filtered by category.

        Args:
            category: Category to filter by.
            limit: Maximum records to return.

        Returns:
            List of matching SyncRecord objects.
        """
        with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT record_id, category, payload, destination,
                           status, created_at, updated_at,
                           retry_count, retry_after, error_message
                    FROM sync_queue
                    WHERE category = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (category.value, limit)).fetchall()

                return [SyncRecord(
                    record_id=row[0], category=row[1],
                    payload=row[2], destination=row[3],
                    status=row[4], created_at=row[5],
                    updated_at=row[6], retry_count=row[7],
                    retry_after=row[8], error_message=row[9],
                ) for row in rows]


# Type alias for sync handlers
SyncHandler = Callable[[List[SyncRecord]], List[Tuple[str, Optional[str]]]]


class SyncEngine:
    """Orchestrates offline-first data sync.

    Manages the queue, connectivity monitoring, and sync handler
    dispatch. Call sync_cycle() periodically (e.g., every 30s)
    to process the queue when online.

    Usage:
        engine = SyncEngine()

        # Register a handler for telemetry data
        def upload_telemetry(records):
            results = []
            for r in records:
                try:
                    api_call(r.payload_dict)
                    results.append((r.record_id, None))  # Success
                except Exception as e:
                    results.append((r.record_id, str(e)))  # Failure
            return results

        engine.register_handler(SyncCategory.TELEMETRY, upload_telemetry)
        engine.enqueue(SyncCategory.TELEMETRY, {"node": "!abc", "snr": -5.0})
        engine.sync_cycle()  # Syncs if online
    """

    def __init__(self, db_path: Optional[Path] = None,
                 connectivity_interval: float = CONNECTIVITY_CHECK_INTERVAL):
        """Initialize sync engine.

        Args:
            db_path: Path to SQLite database (None for default).
            connectivity_interval: Seconds between connectivity checks.
        """
        self._queue = OfflineSyncQueue(db_path=db_path)
        self._connectivity = ConnectivityMonitor(
            check_interval=connectivity_interval)
        self._handlers: Dict[str, SyncHandler] = {}
        self._lock = threading.Lock()
        self._last_sync: float = 0.0
        self._sync_count: int = 0
        self._error_count: int = 0

    def register_handler(self, category: SyncCategory,
                         handler: SyncHandler) -> None:
        """Register a sync handler for a category.

        The handler receives a list of SyncRecord objects and must
        return a list of (record_id, error_or_none) tuples.

        Args:
            category: Category this handler processes.
            handler: Callable that syncs records.
        """
        self._handlers[category.value] = handler
        logger.debug(f"Registered sync handler for {category.value}")

    def enqueue(self, category: SyncCategory, payload: dict,
                destination: str = "") -> str:
        """Queue data for sync.

        Data is stored locally and will be synced when connectivity
        is available and a handler is registered for the category.

        Args:
            category: Type of data.
            payload: Data to sync.
            destination: Optional endpoint/handler hint.

        Returns:
            Record ID.
        """
        return self._queue.enqueue(category, payload, destination)

    def sync_cycle(self) -> Dict[str, int]:
        """Run one sync cycle: check connectivity, sync pending data.

        Thread-safe: only one sync cycle can run at a time.

        Returns:
            Dict with 'synced', 'failed', 'skipped' counts.
        """
        result = {'synced': 0, 'failed': 0, 'skipped': 0}

        # Check connectivity (thread-safe, cached)
        if not self._connectivity.is_online:
            pending = self._queue.get_pending_count()
            if pending > 0:
                logger.debug(
                    f"Offline: {pending} records queued for sync")
            return result

        # Serialize sync cycles to prevent concurrent handler calls
        if not self._lock.acquire(blocking=False):
            return result  # Another cycle is running

        try:
            return self._do_sync_cycle()
        finally:
            self._lock.release()

    def _do_sync_cycle(self) -> Dict[str, int]:
        """Internal sync cycle (must hold self._lock)."""
        result = {'synced': 0, 'failed': 0, 'skipped': 0}

        # Dequeue batch
        records = self._queue.dequeue_batch()
        if not records:
            return result

        # Group by category
        by_category: Dict[str, List[SyncRecord]] = {}
        for record in records:
            by_category.setdefault(record.category, []).append(record)

        # Dispatch to handlers
        for category, cat_records in by_category.items():
            handler = self._handlers.get(category)
            if handler is None:
                # No handler registered — mark failed with info
                for record in cat_records:
                    self._queue.mark_failed(
                        record.record_id,
                        f"No handler registered for {category}")
                result['skipped'] += len(cat_records)
                continue

            try:
                results = handler(cat_records)
                for record_id, error in results:
                    if error is None:
                        self._queue.mark_synced(record_id)
                        result['synced'] += 1
                        self._sync_count += 1
                    else:
                        self._queue.mark_failed(record_id, error)
                        result['failed'] += 1
                        self._error_count += 1
            except Exception as e:
                # Handler crashed — mark all as failed
                logger.error(f"Sync handler for {category} crashed: {e}")
                for record in cat_records:
                    self._queue.mark_failed(record.record_id, str(e))
                result['failed'] += len(cat_records)
                self._error_count += len(cat_records)

        self._last_sync = time.time()

        # Periodic cleanup
        if self._sync_count % 100 == 0 and self._sync_count > 0:
            self._queue.cleanup()

        total = result['synced'] + result['failed'] + result['skipped']
        if total > 0:
            logger.info(
                f"Sync cycle: {result['synced']} synced, "
                f"{result['failed']} failed, {result['skipped']} skipped")

        return result

    @property
    def is_online(self) -> bool:
        """Check current connectivity status."""
        return self._connectivity.is_online

    @property
    def pending_count(self) -> int:
        """Number of records waiting to sync."""
        return self._queue.get_pending_count()

    def get_stats(self) -> Dict[str, object]:
        """Get comprehensive sync engine statistics.

        Returns:
            Dict with queue stats, connectivity, and engine metrics.
        """
        queue_stats = self._queue.get_stats()
        return {
            'queue': queue_stats,
            'is_online': self._connectivity.is_online,
            'total_synced': self._sync_count,
            'total_errors': self._error_count,
            'last_sync': self._last_sync,
            'handlers_registered': list(self._handlers.keys()),
        }

    def force_sync(self) -> Dict[str, int]:
        """Force a sync attempt regardless of last check time.

        Invalidates connectivity cache and runs a sync cycle.

        Returns:
            Sync cycle results.
        """
        self._connectivity.invalidate()
        return self.sync_cycle()

    def cleanup(self) -> int:
        """Run queue cleanup manually.

        Returns:
            Number of records removed.
        """
        return self._queue.cleanup()

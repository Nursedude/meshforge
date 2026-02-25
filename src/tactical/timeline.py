"""
Tactical Event Timeline for MeshForge.

SQLite-backed persistent log of all tactical events. Auto-populated
from inbound X1 messages via the event bus. Queryable by type, time,
and sender.

Uses the same SQLite patterns as gateway/message_queue.py.

Usage:
    from tactical.timeline import TacticalTimeline

    timeline = TacticalTimeline()
    timeline.record(msg)

    # Query recent check-ins
    checkins = timeline.get_recent_checkins(minutes=60)

    # Get active zones for map overlay
    zones = timeline.get_active_zones()
"""

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from utils.paths import get_real_user_home

from tactical.models import (
    CheckIn,
    EncryptionMode,
    TacticalMessage,
    TacticalPriority,
    TacticalType,
    ZoneMarking,
)

logger = logging.getLogger(__name__)

# Default database location
_DEFAULT_DB_DIR = ".config/meshforge"
_DEFAULT_DB_NAME = "tactical_timeline.db"

# Schema version for migrations
_SCHEMA_VERSION = 1


class TacticalTimeline:
    """Persistent tactical event timeline backed by SQLite.

    Thread-safe. Database is stored in ~/.config/meshforge/ by default
    (uses get_real_user_home() for sudo compatibility per MF001).

    Args:
        db_path: Override database path. If None, uses default location.
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            home = get_real_user_home()
            db_dir = home / _DEFAULT_DB_DIR
            db_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = db_dir / _DEFAULT_DB_NAME
        else:
            self._db_path = Path(db_path)
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tactical_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id TEXT NOT NULL,
                    tactical_type INTEGER NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'R',
                    encryption_mode TEXT NOT NULL DEFAULT 'C',
                    sender_id TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '{}',
                    timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    raw_x1 TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tactical_type
                ON tactical_events(tactical_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON tactical_events(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_msg_id
                ON tactical_events(msg_id)
            """)
            conn.commit()

    @contextmanager
    def _get_conn(self):
        """Get a SQLite connection with proper settings."""
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=10,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def record(self, msg: TacticalMessage) -> int:
        """Record a tactical message to the timeline.

        Args:
            msg: TacticalMessage to record.

        Returns:
            Row ID of the inserted record.
        """
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO tactical_events
                        (msg_id, tactical_type, priority, encryption_mode,
                         sender_id, content, timestamp, raw_x1)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg.id,
                        msg.tactical_type.value,
                        msg.priority.value,
                        msg.encryption_mode.value,
                        msg.sender_id,
                        json.dumps(msg.content),
                        msg.timestamp.isoformat(),
                        msg.raw_x1,
                    ),
                )
                conn.commit()
                row_id = cursor.lastrowid
                logger.debug(
                    f"Recorded {msg.tactical_type.name} from {msg.sender_id} "
                    f"(row {row_id})"
                )
                return row_id

    def query(
        self,
        tactical_type: Optional[TacticalType] = None,
        since: Optional[datetime] = None,
        sender_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[TacticalMessage]:
        """Query timeline with optional filters.

        Args:
            tactical_type: Filter by tactical type.
            since: Only return events after this time.
            sender_id: Filter by sender.
            limit: Maximum results.

        Returns:
            List of TacticalMessage ordered by timestamp (newest first).
        """
        conditions = []
        params: list = []

        if tactical_type is not None:
            conditions.append("tactical_type = ?")
            params.append(tactical_type.value)

        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        if sender_id is not None:
            conditions.append("sender_id = ?")
            params.append(sender_id)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT msg_id, tactical_type, priority, encryption_mode,
                       sender_id, content, timestamp, raw_x1
                FROM tactical_events
                {where_clause}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [self._row_to_message(row) for row in rows]

    def get_active_zones(self) -> List[ZoneMarking]:
        """Get all zone markings (for tactical map overlay).

        Returns:
            List of ZoneMarking objects.
        """
        messages = self.query(tactical_type=TacticalType.ZONE, limit=100)
        zones = []
        for msg in messages:
            try:
                zone = ZoneMarking.from_dict(msg.content)
                zones.append(zone)
            except Exception as e:
                logger.debug(f"Failed to parse zone from {msg.id}: {e}")
        return zones

    def get_recent_checkins(self, minutes: int = 60) -> List[CheckIn]:
        """Get recent check-ins (for tactical map).

        Args:
            minutes: How far back to look.

        Returns:
            List of CheckIn objects.
        """
        since = datetime.now() - timedelta(minutes=minutes)
        messages = self.query(
            tactical_type=TacticalType.CHECKIN,
            since=since,
            limit=200,
        )
        checkins = []
        for msg in messages:
            try:
                checkin = CheckIn.from_dict(msg.content)
                checkins.append(checkin)
            except Exception as e:
                logger.debug(f"Failed to parse check-in from {msg.id}: {e}")
        return checkins

    def get_count(self, tactical_type: Optional[TacticalType] = None) -> int:
        """Get total count of events, optionally filtered by type.

        Args:
            tactical_type: Filter by type, or None for all.

        Returns:
            Event count.
        """
        with self._get_conn() as conn:
            if tactical_type is not None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM tactical_events WHERE tactical_type = ?",
                    (tactical_type.value,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM tactical_events"
                ).fetchone()
            return row[0] if row else 0

    def purge_older_than(self, days: int = 30) -> int:
        """Delete events older than the specified number of days.

        Args:
            days: Events older than this are deleted.

        Returns:
            Number of deleted rows.
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    "DELETE FROM tactical_events WHERE timestamp < ?",
                    (cutoff,),
                )
                conn.commit()
                deleted = cursor.rowcount
                if deleted > 0:
                    logger.info(f"Purged {deleted} tactical events older than {days} days")
                return deleted

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> TacticalMessage:
        """Convert a database row to TacticalMessage."""
        return TacticalMessage(
            id=row['msg_id'],
            tactical_type=TacticalType(row['tactical_type']),
            priority=TacticalPriority(row['priority']),
            encryption_mode=EncryptionMode(row['encryption_mode']),
            sender_id=row['sender_id'],
            content=json.loads(row['content']),
            timestamp=datetime.fromisoformat(row['timestamp']),
            raw_x1=row['raw_x1'],
        )

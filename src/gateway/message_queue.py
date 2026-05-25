"""
Persistent Message Queue for MeshForge Gateway.

Ensures reliable message delivery across network boundaries:
- SQLite-backed persistence (survives restarts)
- Automatic retry with exponential backoff
- Deduplication to prevent message loops
- Priority queuing
- Dead letter queue for failed messages

Usage:
    queue = PersistentMessageQueue()
    queue.enqueue(message, destination="meshtastic")

    # Process queue
    queue.process(send_callback)
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from contextlib import contextmanager

# Import centralized path utility for sudo compatibility
from utils.db_helpers import connect_tuned
from utils.paths import get_real_user_home
from utils.timeouts import MESSAGE_STALE as _MESSAGE_STALE_TIMEOUT
from gateway import delivery_counters as _dc

logger = logging.getLogger(__name__)


class MessagePriority(Enum):
    """Message priority levels."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


class MessageStatus(Enum):
    """Message delivery status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class MessageLifecycleState(Enum):
    """
    Detailed message lifecycle states (Sprint C: Message Visibility).

    Tracks message through the complete delivery pipeline:
    CREATED → QUEUED → SENT → RELAYED → DELIVERED → ACK
                 ↓
           TIMEOUT/FAILED (with reason)
    """
    CREATED = "created"       # Message created in application
    QUEUED = "queued"         # Added to persistent queue
    SENT = "sent"             # Transmitted to destination network
    RELAYED = "relayed"       # Forwarded by relay node(s)
    DELIVERED = "delivered"   # Received by destination
    ACK = "ack"               # Acknowledgement received
    TIMEOUT = "timeout"       # Delivery timed out
    FAILED = "failed"         # Permanent failure
    RETRYING = "retrying"     # Scheduled for retry


@dataclass
class MessageLifecycleEvent:
    """
    A single event in a message's lifecycle history.

    Used to trace message path through the system.
    """
    message_id: str
    state: MessageLifecycleState
    timestamp: datetime
    details: str = ""
    node_id: str = ""  # Node that processed this event
    hop_count: int = 0  # Number of hops so far

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage."""
        return {
            "message_id": self.message_id,
            "state": self.state.value,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
            "node_id": self.node_id,
            "hop_count": self.hop_count,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'MessageLifecycleEvent':
        """Create from dictionary."""
        return cls(
            message_id=data["message_id"],
            state=MessageLifecycleState(data["state"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            details=data.get("details", ""),
            node_id=data.get("node_id", ""),
            hop_count=data.get("hop_count", 0),
        )


@dataclass
class RetryDecision:
    """Result of a retry policy decision."""
    retry: bool
    delay: float = 0.0
    reason: str = ""


class RetryPolicy:
    """
    NGINX-style retry policy for message delivery.

    Distinguishes between retriable (transient) and non-retriable (permanent)
    errors to avoid wasting retries on failures that won't recover.

    Based on NGINX proxy_next_upstream pattern:
    - error, timeout → retry
    - 502, 503 → retry
    - 404, 403 → don't retry

    Usage:
        policy = RetryPolicy(max_tries=3, timeout=30.0)

        # On failure:
        decision = policy.should_retry(error_msg, attempt=1)
        if decision.retry:
            time.sleep(decision.delay)
            retry_send()
        else:
            move_to_dead_letter(decision.reason)

    Reference:
        NGINX proxy_next_upstream:
        http://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_next_upstream
    """

    # Errors that should trigger retry (transient failures)
    RETRIABLE_ERRORS = frozenset({
        "connection_reset",
        "connection reset",
        "connection_refused",
        "connection refused",
        "broken_pipe",
        "broken pipe",
        "timeout",
        "timed out",
        "temporarily_unavailable",
        "temporarily unavailable",
        "network_unreachable",
        "network unreachable",
        "no route to host",
        "resource temporarily unavailable",
        "try again",
        "eagain",
        "econnreset",
        "econnrefused",
        "etimedout",
        # MQTT-specific transient errors
        "not connected",
        "mqtt err",
        "queue full",
    })

    # Errors that should NOT retry (permanent failures)
    NON_RETRIABLE_ERRORS = frozenset({
        "permission_denied",
        "permission denied",
        "invalid_destination",
        "invalid destination",
        "message_too_large",
        "message too large",
        "authentication_failed",
        "authentication failed",
        "no such device",
        "device not found",
        "not found",
        "invalid address",
        "bad address",
        "protocol error",
        "eperm",
        "eacces",
        "einval",
        # MQTT-specific permanent errors
        "not authorised",
        "not authorized",
        "topic invalid",
        "payload too large",
    })

    def __init__(
        self,
        max_tries: int = 3,
        timeout: float = 30.0,
        base_delay: float = 2.0,
        max_delay: float = 60.0,
    ):
        """
        Initialize retry policy.

        Args:
            max_tries: Maximum retry attempts (default: 3)
            timeout: Total timeout for all retries (default: 30s)
            base_delay: Base delay for exponential backoff (default: 2s)
            max_delay: Maximum delay between retries (default: 60s)
        """
        self.max_tries = max_tries
        self.timeout = timeout
        self.base_delay = base_delay
        self.max_delay = max_delay

    def _classify_error(self, error: str) -> str:
        """
        Classify error as retriable or non-retriable.

        Args:
            error: Error message string

        Returns:
            "retriable", "non_retriable", or "unknown"
        """
        error_lower = error.lower()

        # Check non-retriable first (more specific)
        for pattern in self.NON_RETRIABLE_ERRORS:
            if pattern in error_lower:
                return "non_retriable"

        # Check retriable patterns
        for pattern in self.RETRIABLE_ERRORS:
            if pattern in error_lower:
                return "retriable"

        return "unknown"

    def should_retry(self, error: str, attempt: int) -> RetryDecision:
        """
        Determine if an error should trigger a retry.

        Args:
            error: Error message from failed send
            attempt: Current attempt number (1-based)

        Returns:
            RetryDecision with retry=True/False, delay, and reason
        """
        error_class = self._classify_error(error)

        # Non-retriable errors: fail immediately
        if error_class == "non_retriable":
            return RetryDecision(
                retry=False,
                reason=f"permanent_error: {error[:50]}",
            )

        # Check attempt limit
        if attempt >= self.max_tries:
            return RetryDecision(
                retry=False,
                reason=f"max_attempts_exceeded ({attempt}/{self.max_tries})",
            )

        # Retriable errors: calculate backoff delay
        if error_class == "retriable":
            delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
            return RetryDecision(
                retry=True,
                delay=delay,
                reason=f"transient_error_retry_{attempt}",
            )

        # Unknown errors: retry once with short delay
        if attempt < 2:
            return RetryDecision(
                retry=True,
                delay=self.base_delay,
                reason="unknown_error_retry",
            )

        # Unknown error, already retried once
        return RetryDecision(
            retry=False,
            reason=f"unknown_error_exhausted: {error[:50]}",
        )

    def get_delay_for_attempt(self, attempt: int) -> float:
        """
        Calculate delay for a given retry attempt.

        Args:
            attempt: Attempt number (1-based)

        Returns:
            Delay in seconds (exponential backoff capped at max_delay)
        """
        delay = self.base_delay * (2 ** (attempt - 1))
        return min(delay, self.max_delay)

    @classmethod
    def for_meshtastic(cls) -> 'RetryPolicy':
        """
        Create retry policy optimized for Meshtastic.

        Meshtastic has slower transmission, so we use longer timeouts
        and fewer retries.
        """
        return cls(
            max_tries=3,
            timeout=60.0,
            base_delay=5.0,
            max_delay=45.0,
        )

    @classmethod
    def for_rns(cls) -> 'RetryPolicy':
        """
        Create retry policy optimized for RNS/LXMF.

        RNS has its own delivery confirmation, so we use shorter
        timeouts and more retries.
        """
        return cls(
            max_tries=5,
            timeout=30.0,
            base_delay=2.0,
            max_delay=30.0,
        )

    @classmethod
    def for_mqtt(cls) -> 'RetryPolicy':
        """
        Create retry policy optimized for MQTT broker delivery.

        MQTT brokers are typically local with low latency and support QoS.
        Uses fast retries with short delays since broker responses are quick.
        """
        return cls(
            max_tries=5,
            timeout=15.0,
            base_delay=1.0,
            max_delay=15.0,
        )


@dataclass
class QueuedMessage:
    """Message in the queue."""
    id: str
    payload: Dict[str, Any]
    destination: str  # "meshtastic", "rns", "mqtt"
    priority: MessagePriority = MessagePriority.NORMAL
    status: MessageStatus = MessageStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    retry_count: int = 0
    max_retries: int = 3
    retry_after: Optional[datetime] = None
    error_message: str = ""
    content_hash: str = ""  # For deduplication

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "payload": json.dumps(self.payload),
            "destination": self.destination,
            "priority": self.priority.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "retry_after": self.retry_after.isoformat() if self.retry_after else None,
            "error_message": self.error_message,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'QueuedMessage':
        """Create from dictionary."""
        return cls(
            id=data["id"],
            payload=json.loads(data["payload"]) if isinstance(data["payload"], str) else data["payload"],
            destination=data["destination"],
            priority=MessagePriority(data["priority"]),
            status=MessageStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            retry_count=data["retry_count"],
            max_retries=data["max_retries"],
            retry_after=datetime.fromisoformat(data["retry_after"]) if data.get("retry_after") else None,
            error_message=data.get("error_message", ""),
            content_hash=data.get("content_hash", ""),
        )


class PersistentMessageQueue:
    """
    SQLite-backed persistent message queue.

    Features:
    - Survives application restarts
    - ACID transactions
    - Automatic retry with backoff
    - Deduplication within time window
    - Priority ordering
    """

    # Retry backoff: 5s, 15s, 45s
    RETRY_DELAYS = [5, 15, 45]

    # Deduplication window (seconds)
    DEDUP_WINDOW = 60

    # Default max queue size (active messages: pending + in_progress)
    DEFAULT_MAX_QUEUE_SIZE = 1000

    # Auto-cleanup interval (seconds) — purge old delivered/dead_letter
    AUTO_CLEANUP_INTERVAL = 3600  # 1 hour

    # Stale in_progress timeout — canonical source: utils.timeouts
    STALE_TIMEOUT = _MESSAGE_STALE_TIMEOUT

    def __init__(self, db_path: Optional[str] = None,
                 max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
                 retry_policy: Optional[RetryPolicy] = None):
        """
        Initialize the message queue.

        Args:
            db_path: Path to SQLite database. Default: ~/.config/meshforge/message_queue.db
            max_queue_size: Maximum active messages (pending + in_progress).
                          When exceeded, lowest-priority oldest messages are shed.
                          Set to 0 for unlimited (not recommended).
            retry_policy: Optional RetryPolicy for intelligent retry decisions.
                         If not provided, uses default RETRY_DELAYS behavior.
        """
        if db_path is None:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(config_dir / "message_queue.db")

        self._db_path = db_path
        self._max_queue_size = max_queue_size
        self._retry_policy = retry_policy
        self._lock = threading.Lock()
        self._processing = False
        self._process_thread = None
        self._stop_event = threading.Event()
        self._last_auto_cleanup = 0.0

        # Callbacks
        self._send_callbacks: Dict[str, Callable] = {}  # destination -> send_fn
        self._success_callbacks: List[Callable] = []
        self._failure_callbacks: List[Callable] = []

        # Initialize database
        self._init_db()

        # Stats
        self._stats = {
            "enqueued": 0,
            "delivered": 0,
            "failed": 0,
            "retried": 0,
            "deduplicated": 0,
            "shed": 0,
            "shed_rejected": 0,
            "permanent_failures": 0,
        }

    @contextmanager
    def _get_connection(self):
        """Get database connection with context management.

        Enables WAL journal mode for crash resilience and better
        concurrent read/write performance on resource-constrained systems.
        """
        # Tuned via connect_tuned: WAL + sync=NORMAL + 64MB journal cap
        # + 30s busy_timeout. Phase 2 closure (commit B following 2743ded)
        # — was missing sync=NORMAL and journal_size_limit before.
        conn = connect_tuned(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise  # Re-raise after rollback - exception is handled by caller
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    priority INTEGER DEFAULT 2,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    retry_after TEXT,
                    error_message TEXT DEFAULT '',
                    content_hash TEXT DEFAULT ''
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status
                ON messages(status)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_priority_created
                ON messages(priority DESC, created_at ASC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_content_hash
                ON messages(content_hash)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_retry_after
                ON messages(retry_after)
            """)

            # Message lifecycle history table (Sprint C: Message Visibility)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS message_lifecycle (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    details TEXT DEFAULT '',
                    node_id TEXT DEFAULT '',
                    hop_count INTEGER DEFAULT 0
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lifecycle_message
                ON message_lifecycle(message_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lifecycle_timestamp
                ON message_lifecycle(timestamp DESC)
            """)

            # Issue #66: application-layer ack tracking columns. Additive
            # migration — older DBs that predate Issue #66 get the columns
            # ALTER'd in on first open. Adding via ALTER (rather than
            # rebuilding the table) preserves existing rows + their content
            # hashes for the dedup window.
            cursor = conn.execute("PRAGMA table_info(messages)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            for col, ddl in (
                ('ack_required',
                 "ALTER TABLE messages ADD COLUMN ack_required INTEGER DEFAULT 0"),
                ('ack_of',
                 "ALTER TABLE messages ADD COLUMN ack_of TEXT DEFAULT NULL"),
                ('ack_status',
                 "ALTER TABLE messages ADD COLUMN ack_status TEXT DEFAULT NULL"),
                ('ack_timeout_at',
                 "ALTER TABLE messages ADD COLUMN ack_timeout_at TEXT DEFAULT NULL"),
                ('ack_at',
                 "ALTER TABLE messages ADD COLUMN ack_at TEXT DEFAULT NULL"),
                ('ack_origin_network',
                 "ALTER TABLE messages ADD COLUMN ack_origin_network TEXT DEFAULT NULL"),
                ('ack_origin_address',
                 "ALTER TABLE messages ADD COLUMN ack_origin_address TEXT DEFAULT NULL"),
            ):
                if col not in existing_cols:
                    conn.execute(ddl)

            # Sweep index — supports find_overdue_acks() on busy boxes
            # without table-scanning the whole messages table.
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ack_pending
                ON messages(ack_status, ack_timeout_at)
                WHERE ack_status = 'pending'
            """)

    def _compute_hash(self, payload: Dict) -> str:
        """Compute content hash for deduplication.

        Bridge paths use different payload shapes. Keying only on the
        Meshtastic-ingress shape (``from``/``to``/``text``/``type``) made
        EVERY ``message``-shaped payload (RNS→Mesh / queued-RNS) hash
        identically — all four keys absent → constant hash — so within
        ``DEDUP_WINDOW`` all but the first such message were dropped as
        false duplicates. So we branch on the shape:

        - ``message``-shaped (RNS→Mesh): key on the message CONTENT plus
          route (destination + channel). Deliberately NOT the transport
          ``source_id`` — the body already encodes its origin (the
          ``[RNS:src]`` prefix), and the SAME logical message can reach us
          via two transport paths (a direct send and a peer-gateway relay)
          with different ``source_id``s. Keying on source_id let those
          multi-path duplicates escape dedup (observed: identical
          ``[RNS:627f] [ch0:p4] wx`` enqueued twice 0.3s apart).
        - ``text``-shaped (Meshtastic ingress): key on from/to/text/type,
          where ``from`` is the originating node and is meaningful.
        """
        if payload.get("message") is not None:
            key_data = json.dumps({
                "message": payload.get("message"),
                "destination": payload.get("destination"),
                "channel": payload.get("channel"),
            }, sort_keys=True)
        else:
            key_data = json.dumps({
                "from": payload.get("from"),
                "to": payload.get("to"),
                "text": payload.get("text"),
                "type": payload.get("type"),
            }, sort_keys=True)
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]

    def _is_duplicate(self, content_hash: str, destination: str) -> bool:
        """Check if message is a recent duplicate."""
        cutoff = (datetime.now() - timedelta(seconds=self.DEDUP_WINDOW)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM messages
                WHERE content_hash = ? AND destination = ?
                AND created_at > ?
                AND status IN ('pending', 'in_progress', 'delivered')
            """, (content_hash, destination, cutoff))

            count = cursor.fetchone()[0]
            return count > 0

    def enqueue(self, payload: Dict[str, Any], destination: str,
                priority: MessagePriority = MessagePriority.NORMAL,
                max_retries: int = 3, deduplicate: bool = True) -> Optional[str]:
        """
        Add a message to the queue.

        Args:
            payload: Message payload dictionary
            destination: Target system ("meshtastic", "rns", "mqtt")
            priority: Message priority
            max_retries: Maximum retry attempts
            deduplicate: Check for duplicates

        Returns:
            Message ID if enqueued, None if duplicate
        """
        content_hash = self._compute_hash(payload)

        # Check for duplicates
        if deduplicate and self._is_duplicate(content_hash, destination):
            with self._lock:
                self._stats["deduplicated"] += 1
            logger.debug(f"Duplicate message suppressed: {content_hash}")
            # Fork C: dedup-drop is the simplest DROPPED reason — payload
            # would never enter the queue, so the operator-visible
            # message id is the content-hash prefix (the same shape
            # enqueue would have stamped).
            _dc.record(
                _dc.DeliveryState.DROPPED,
                msg_id=f"dedup-{content_hash[:8]}",
                protocol=destination,
                drop_reason=_dc.DropReason.DEDUP,
            )
            return None

        # Periodic auto-cleanup of old delivered/dead_letter messages
        self._maybe_auto_cleanup()

        # Enforce queue size limit
        if self._max_queue_size > 0:
            depth = self.get_queue_depth()
            if depth >= self._max_queue_size:
                # Try to shed lowest-priority oldest messages
                shed = self._shed_overflow(count=max(1, depth - self._max_queue_size + 1))
                if shed == 0:
                    # Cannot shed anything — all messages are higher priority or in_progress
                    with self._lock:
                        self._stats["shed_rejected"] += 1
                    logger.warning(
                        f"Queue full ({depth}/{self._max_queue_size}), "
                        f"cannot enqueue message to {destination}"
                    )
                    # Fork C: incoming message bounced because the queue
                    # is full and nothing was sheddable. Distinct from
                    # QUEUE_SHED (eviction of an already-queued msg).
                    _dc.record(
                        _dc.DeliveryState.DROPPED,
                        msg_id=f"rejected-{content_hash[:8]}",
                        protocol=destination,
                        drop_reason=_dc.DropReason.QUEUE_PRESSURE,
                        note=f"depth={depth}/{self._max_queue_size}",
                    )
                    return None

        # Generate unique ID
        msg_id = f"{int(time.time() * 1000)}-{content_hash[:8]}"

        message = QueuedMessage(
            id=msg_id,
            payload=payload,
            destination=destination,
            priority=priority,
            max_retries=max_retries,
            content_hash=content_hash,
        )

        with self._get_connection() as conn:
            data = message.to_dict()
            conn.execute("""
                INSERT INTO messages
                (id, payload, destination, priority, status, created_at, updated_at,
                 retry_count, max_retries, retry_after, error_message, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["id"], data["payload"], data["destination"],
                data["priority"], data["status"], data["created_at"],
                data["updated_at"], data["retry_count"], data["max_retries"],
                data["retry_after"], data["error_message"], data["content_hash"]
            ))

        with self._lock:
            self._stats["enqueued"] += 1
        logger.debug(f"Message enqueued: {msg_id} -> {destination}")

        # Fork C: queue entry is the QUEUED transition. The same id
        # carries through the rest of the lifecycle (SENT / CONFIRMED
        # / DROPPED) so operators can follow one message end-to-end.
        _dc.record(
            _dc.DeliveryState.QUEUED,
            msg_id=msg_id,
            protocol=destination,
        )

        return msg_id

    def get_pending(self, destination: Optional[str] = None,
                    limit: int = 100) -> List[QueuedMessage]:
        """Get pending messages ready for delivery."""
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            if destination:
                cursor = conn.execute("""
                    SELECT * FROM messages
                    WHERE status = 'pending'
                    AND destination = ?
                    AND (retry_after IS NULL OR retry_after <= ?)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT ?
                """, (destination, now, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM messages
                    WHERE status = 'pending'
                    AND (retry_after IS NULL OR retry_after <= ?)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT ?
                """, (now, limit))

            return [QueuedMessage.from_dict(dict(row)) for row in cursor.fetchall()]

    def mark_in_progress(self, msg_id: str) -> bool:
        """Mark message as in progress."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE messages
                SET status = 'in_progress', updated_at = ?
                WHERE id = ? AND status = 'pending'
            """, (datetime.now().isoformat(), msg_id))
            return cursor.rowcount > 0

    def mark_delivered(self, msg_id: str) -> bool:
        """Mark message as successfully delivered."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE messages
                SET status = 'delivered', updated_at = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), msg_id))

            if cursor.rowcount > 0:
                with self._lock:
                    self._stats["delivered"] += 1
                # Fork C: this is the SENT transition — payload left
                # the gateway successfully. Receiver confirmation is
                # CONFIRMED, surfaced separately by the LXMF delivery
                # callback / wantAck path. Naming gap is intentional:
                # the queue's column is named "delivered" for legacy
                # reasons; the counter taxonomy calls it what it is.
                # Protocol must be pulled from the row since callers
                # don't pass it — keep this cheap by reusing the row
                # we already have.
                row = conn.execute(
                    "SELECT destination FROM messages WHERE id = ?", (msg_id,),
                ).fetchone()
                protocol = row["destination"] if row else None
                _dc.record(
                    _dc.DeliveryState.SENT,
                    msg_id=msg_id,
                    protocol=protocol,
                )
                return True
            return False

    def mark_failed(self, msg_id: str, error: str = "") -> bool:
        """
        Mark message as failed and schedule retry or move to dead letter.

        If a RetryPolicy is configured, uses intelligent retry decisions
        based on error classification (transient vs permanent).
        """
        with self._get_connection() as conn:
            # Get current message
            cursor = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (msg_id,)
            )
            row = cursor.fetchone()
            if not row:
                return False

            message = QueuedMessage.from_dict(dict(row))
            message.retry_count += 1
            message.error_message = error
            message.updated_at = datetime.now()

            # Use RetryPolicy if available for intelligent retry decisions
            if self._retry_policy is not None:
                decision = self._retry_policy.should_retry(error, message.retry_count)

                if not decision.retry:
                    # Policy says don't retry (permanent error or max attempts)
                    message.status = MessageStatus.DEAD_LETTER
                    with self._lock:
                        self._stats["failed"] += 1
                        if "permanent_error" in decision.reason:
                            self._stats["permanent_failures"] += 1
                    logger.warning(
                        f"Message {msg_id} moved to dead letter: {decision.reason}"
                    )
                    # Fork C: terminal drop. Distinguish "retries
                    # exhausted on a transient error" from "policy
                    # judged the error non-retriable on first attempt"
                    # — same operator surface, different remediation.
                    if "permanent_error" in decision.reason or \
                       "non_retriable" in decision.reason:
                        reason = _dc.DropReason.NON_RETRIABLE_ERROR
                    else:
                        reason = _dc.DropReason.RETRIES_EXHAUSTED
                    _dc.record(
                        _dc.DeliveryState.DROPPED,
                        msg_id=msg_id,
                        protocol=message.destination,
                        drop_reason=reason,
                        note=decision.reason[:80],
                    )
                else:
                    # Policy says retry with calculated delay
                    message.retry_after = datetime.now() + timedelta(seconds=decision.delay)
                    message.status = MessageStatus.PENDING
                    with self._lock:
                        self._stats["retried"] += 1
                    logger.debug(
                        f"Message {msg_id} scheduled for retry in {decision.delay:.1f}s "
                        f"({decision.reason})"
                    )
            else:
                # Fallback to original RETRY_DELAYS behavior
                if message.retry_count >= message.max_retries:
                    # Move to dead letter
                    message.status = MessageStatus.DEAD_LETTER
                    with self._lock:
                        self._stats["failed"] += 1
                    logger.warning(
                        f"Message {msg_id} moved to dead letter after "
                        f"{message.retry_count} retries"
                    )
                    # Fork C: terminal drop in the no-policy fallback
                    # path. Always counted as retries-exhausted because
                    # we have no error classifier here.
                    _dc.record(
                        _dc.DeliveryState.DROPPED,
                        msg_id=msg_id,
                        protocol=message.destination,
                        drop_reason=_dc.DropReason.RETRIES_EXHAUSTED,
                        note=f"retry_count={message.retry_count}",
                    )
                else:
                    # Schedule retry with backoff
                    delay_idx = min(message.retry_count - 1, len(self.RETRY_DELAYS) - 1)
                    delay = self.RETRY_DELAYS[delay_idx]
                    message.retry_after = datetime.now() + timedelta(seconds=delay)
                    message.status = MessageStatus.PENDING
                    with self._lock:
                        self._stats["retried"] += 1
                    logger.debug(f"Message {msg_id} scheduled for retry in {delay}s")

            # Update in database
            data = message.to_dict()
            conn.execute("""
                UPDATE messages
                SET status = ?, updated_at = ?, retry_count = ?,
                    retry_after = ?, error_message = ?
                WHERE id = ?
            """, (
                data["status"], data["updated_at"], data["retry_count"],
                data["retry_after"], data["error_message"], msg_id
            ))

            return True

    def set_retry_policy(self, policy: RetryPolicy) -> None:
        """
        Set or update the retry policy.

        Args:
            policy: RetryPolicy instance for intelligent retry decisions
        """
        self._retry_policy = policy
        logger.info(
            f"Retry policy configured: max_tries={policy.max_tries}, "
            f"timeout={policy.timeout}s"
        )

    # --- Issue #66: application-layer ack tracking ---------------------

    def register_pending_ack(
        self,
        message_id: str,
        origin_network: str,
        origin_address: str,
        timeout_seconds: int = 300,
        allow_orphan: bool = False,
    ) -> bool:
        """
        Mark a queued message as expecting an application-layer ACK.

        Issue #66: when a CanonicalMessage with ack_required=True is
        enqueued for delivery, call this to record where the ACK should
        be routed back to. The receiving side calls mark_acked() when it
        observes proof-of-delivery; find_overdue_acks() surfaces records
        that never got one so the caller can emit a synthetic TIMEOUT.

        Args:
            message_id: id of the queued message that requested an ack.
            origin_network: protocol name where the ACK should be
                synthesized back to (e.g. "meshcore", "meshtastic").
            origin_address: address on that network (e.g. "!aabbccdd").
            timeout_seconds: how long to wait before declaring TIMEOUT.
            allow_orphan: when True and the message_id has no queue row
                (e.g. MeshtasticBroadcastBridge does its own LXMF send
                and doesn't go through enqueue()), INSERT a synthetic
                bookkeeping row so the substrate's mark_acked /
                find_overdue_acks / mark_timeout flow can still operate
                on this id. The synthetic row is created with
                status='delivered' so it never enters dispatch loops,
                unique content_hash (the msg_id itself), and zero
                max_retries. Caller still gets the same True/False
                contract — True if the pending-ack now exists (either
                via UPDATE of an existing row or INSERT of a synthetic
                one), False only on hard DB error.

        Returns:
            True if a row was updated (msg exists), False otherwise.
        """
        now = datetime.now()
        timeout_at = (now + timedelta(seconds=timeout_seconds)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE messages
                   SET ack_required = 1,
                       ack_status = 'pending',
                       ack_timeout_at = ?,
                       ack_origin_network = ?,
                       ack_origin_address = ?,
                       updated_at = ?
                 WHERE id = ?
            """, (timeout_at, origin_network, origin_address,
                  now.isoformat(), message_id))
            if cursor.rowcount > 0:
                return True

            if not allow_orphan:
                return False

            # Issue #66 first-caller: no enqueue() preceded this call —
            # synthesize a bookkeeping row so the substrate's mark_acked
            # / find_overdue_acks / mark_timeout flow can correlate the
            # downstream LXMF callback to this msg_id. status='delivered'
            # keeps it out of dispatch loops; auto_cleanup will purge it
            # eventually like any other delivered row.
            conn.execute("""
                INSERT INTO messages
                (id, payload, destination, priority, status, created_at,
                 updated_at, retry_count, max_retries, retry_after,
                 error_message, content_hash,
                 ack_required, ack_status, ack_timeout_at,
                 ack_origin_network, ack_origin_address)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?)
            """, (
                message_id,
                '{"ack_bookkeeping": true}',
                "ack_bookkeeping",
                MessagePriority.NORMAL.value,
                MessageStatus.DELIVERED.value,
                now.isoformat(),
                now.isoformat(),
                0,
                0,
                None,
                "",
                message_id,  # content_hash = unique msg_id avoids dedup
                1,
                'pending',
                timeout_at,
                origin_network,
                origin_address,
            ))
            return True

    def mark_acked(self, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Transition a pending-ack record to 'acked'.

        Idempotent: a second call after the first acked transition returns
        None so the caller doesn't double-emit a synthetic ACK
        CanonicalMessage.

        Returns:
            Dict with message_id, origin_network, origin_address on a
            successful transition; None if the row doesn't exist, isn't
            tracking an ack, or was already finalised (acked/timeout).
        """
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, ack_origin_network, ack_origin_address, ack_status
                  FROM messages
                 WHERE id = ? AND ack_required = 1
            """, (message_id,))
            row = cursor.fetchone()
            if not row or row['ack_status'] != 'pending':
                return None
            conn.execute("""
                UPDATE messages
                   SET ack_status = 'acked',
                       ack_at = ?,
                       updated_at = ?
                 WHERE id = ?
            """, (now, now, message_id))
            return {
                'message_id': row['id'],
                'origin_network': row['ack_origin_network'],
                'origin_address': row['ack_origin_address'],
            }

    def find_overdue_acks(
        self, now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return pending-ack records whose ack_timeout_at has passed.

        Caller is responsible for emitting synthetic TIMEOUT ACKs and
        calling mark_timeout() on each id to finalise the record.
        Records are not auto-finalised here so the sweep stays a pure
        read — callers may want to batch + retry the ACK emission.

        Args:
            now: clock reference (injectable for tests). Defaults to
                datetime.now().
        """
        if now is None:
            now = datetime.now()
        cutoff = now.isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, ack_origin_network, ack_origin_address,
                       ack_timeout_at
                  FROM messages
                 WHERE ack_required = 1
                   AND ack_status = 'pending'
                   AND ack_timeout_at IS NOT NULL
                   AND ack_timeout_at <= ?
                 ORDER BY ack_timeout_at ASC
            """, (cutoff,))
            return [
                {
                    'message_id': r['id'],
                    'origin_network': r['ack_origin_network'],
                    'origin_address': r['ack_origin_address'],
                    'timeout_at': r['ack_timeout_at'],
                }
                for r in cursor.fetchall()
            ]

    def mark_timeout(self, message_id: str) -> bool:
        """
        Transition a pending-ack record to 'timeout' after the caller has
        emitted the synthetic TIMEOUT ACK.

        Returns True if a row was finalised (was 'pending'). Returns
        False if the row was already 'acked' (raced) or 'timeout'
        (idempotent) — caller should not double-emit.
        """
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE messages
                   SET ack_status = 'timeout',
                       updated_at = ?
                 WHERE id = ? AND ack_status = 'pending'
            """, (now, message_id))
            return cursor.rowcount > 0

    def get_ack_status(self, message_id: str) -> Optional[str]:
        """
        Return the current ack_status of a message, or None if the message
        doesn't exist or isn't tracking an ack.

        Operator-visible values: 'pending', 'acked', 'timeout'.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT ack_status
                  FROM messages
                 WHERE id = ? AND ack_required = 1
            """, (message_id,))
            row = cursor.fetchone()
            return row['ack_status'] if row else None

    def register_sender(self, destination: str,
                        send_fn: Callable[[Dict], bool]) -> None:
        """
        Register a send function for a destination.

        Args:
            destination: Target system name
            send_fn: Function that takes payload dict, returns True if sent
        """
        self._send_callbacks[destination] = send_fn

    def register_success_callback(self, callback: Callable[[QueuedMessage], None]) -> None:
        """Register callback for successful delivery."""
        self._success_callbacks.append(callback)

    def register_failure_callback(self, callback: Callable[[QueuedMessage, str], None]) -> None:
        """Register callback for failed delivery."""
        self._failure_callbacks.append(callback)

    def process_once(self, batch_size: int = 10) -> int:
        """
        Process one batch of pending messages.

        Returns:
            Number of messages processed
        """
        processed = 0

        for destination, send_fn in self._send_callbacks.items():
            messages = self.get_pending(destination=destination, limit=batch_size)

            for message in messages:
                if not self.mark_in_progress(message.id):
                    continue

                try:
                    # Fork C / syn-ack: inject the queue row's id so
                    # downstream handlers can pin LXMF delivery callbacks
                    # to the same msg_id that QUEUED + SENT were recorded
                    # against — history_for(msg_id) joins all three.
                    dispatch_payload = {
                        **message.payload, '_queue_msg_id': message.id,
                    }
                    success = send_fn(dispatch_payload)

                    if success:
                        self.mark_delivered(message.id)
                        for callback in self._success_callbacks:
                            try:
                                callback(message)
                            except Exception as e:
                                logger.debug(f"Success callback error: {e}")
                    else:
                        self.mark_failed(message.id, "Send returned False")

                    processed += 1

                except Exception as e:
                    self.mark_failed(message.id, str(e))
                    for callback in self._failure_callbacks:
                        try:
                            callback(message, str(e))
                        except Exception as e2:
                            logger.debug(f"Failure callback error: {e2}")
                    processed += 1

        return processed

    def start_processing(self, interval: float = 1.0) -> None:
        """Start background processing thread."""
        with self._lock:
            if self._processing:
                return
            self._processing = True
        self._stop_event.clear()

        def process_loop():
            while not self._stop_event.is_set():
                try:
                    self.process_once()
                except Exception as e:
                    logger.error(f"Queue processing error: {e}")

                self._stop_event.wait(interval)

            self._processing = False

        self._process_thread = threading.Thread(target=process_loop, daemon=True)
        self._process_thread.start()
        logger.info("Message queue processing started")

    def stop_processing(self) -> None:
        """Stop background processing."""
        self._stop_event.set()
        if self._process_thread:
            self._process_thread.join(timeout=5)
        logger.info("Message queue processing stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics including overflow metrics."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM messages
                GROUP BY status
            """)
            status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

        pending = status_counts.get("pending", 0)
        in_progress = status_counts.get("in_progress", 0)
        queue_depth = pending + in_progress

        stats = {
            **self._stats,
            "pending": pending,
            "in_progress": in_progress,
            "delivered": status_counts.get("delivered", 0),
            "failed": status_counts.get("failed", 0),
            "dead_letter": status_counts.get("dead_letter", 0),
            "queue_depth": queue_depth,
            "max_queue_size": self._max_queue_size,
            "queue_usage_pct": round(
                (queue_depth / self._max_queue_size * 100) if self._max_queue_size > 0 else 0, 1
            ),
            "retry_policy_enabled": self._retry_policy is not None,
        }

        return stats

    def get_queue_depth(self) -> int:
        """Get count of active messages (pending + in_progress).

        This is the primary metric for queue overflow monitoring.
        Does not count delivered or dead_letter messages.

        Returns:
            Number of active messages in the queue.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM messages
                WHERE status IN ('pending', 'in_progress')
            """)
            return cursor.fetchone()[0]

    def _shed_overflow(self, count: int = 1) -> int:
        """Shed lowest-priority oldest pending messages to make room.

        Only sheds PENDING messages (never in_progress). Prefers shedding
        LOW priority first, then NORMAL. Never sheds HIGH or URGENT.

        Args:
            count: Number of messages to shed.

        Returns:
            Number of messages actually shed.
        """
        with self._get_connection() as conn:
            # Find candidates: pending, lowest priority first, oldest first
            cursor = conn.execute("""
                SELECT id FROM messages
                WHERE status = 'pending'
                AND priority <= ?
                ORDER BY priority ASC, created_at ASC
                LIMIT ?
            """, (MessagePriority.NORMAL.value, count))

            ids = [row["id"] for row in cursor.fetchall()]
            if not ids:
                return 0

            placeholders = ",".join("?" * len(ids))
            conn.execute(
                "DELETE FROM messages WHERE id IN ({})".format(placeholders), ids
            )

            shed_count = len(ids)
            with self._lock:
                self._stats["shed"] += shed_count
            logger.info(f"Queue overflow: shed {shed_count} low-priority messages")
            return shed_count

    def _maybe_auto_cleanup(self) -> None:
        """Periodically clean up old delivered/dead_letter messages.

        Called during enqueue to prevent unbounded table growth.
        Only runs once per AUTO_CLEANUP_INTERVAL.
        """
        now = time.time()
        if now - self._last_auto_cleanup < self.AUTO_CLEANUP_INTERVAL:
            return

        self._last_auto_cleanup = now
        try:
            purged = self.purge_old(days=1)
            stale = self.cleanup_stale()
            lifecycle_purged = self.purge_lifecycle_history(days=7)
            if purged > 0 or stale > 0 or lifecycle_purged > 0:
                logger.debug(
                    f"Auto-cleanup: purged {purged} old, "
                    f"unstuck {stale} stale, "
                    f"{lifecycle_purged} lifecycle entries"
                )
        except Exception as e:
            logger.debug(f"Auto-cleanup error: {e}")

    def cleanup_stale(self) -> int:
        """Reset stale in_progress messages back to pending.

        Messages stuck in 'in_progress' for longer than STALE_TIMEOUT
        are likely from crashed processing attempts. Reset them to
        pending so they can be retried.

        Returns:
            Number of messages reset.
        """
        cutoff = (datetime.now() - timedelta(seconds=self.STALE_TIMEOUT)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE messages
                SET status = 'pending', updated_at = ?
                WHERE status = 'in_progress'
                AND updated_at < ?
            """, (datetime.now().isoformat(), cutoff))

            count = cursor.rowcount
            if count > 0:
                logger.info(f"Reset {count} stale in_progress messages to pending")
            return count

    def get_dead_letters(self, limit: int = 100) -> List[QueuedMessage]:
        """Get messages in dead letter queue."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM messages
                WHERE status = 'dead_letter'
                ORDER BY updated_at DESC
                LIMIT ?
            """, (limit,))

            return [QueuedMessage.from_dict(dict(row)) for row in cursor.fetchall()]

    def retry_dead_letter(self, msg_id: str) -> bool:
        """Retry a dead letter message."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE messages
                SET status = 'pending', retry_count = 0,
                    retry_after = NULL, updated_at = ?
                WHERE id = ? AND status = 'dead_letter'
            """, (datetime.now().isoformat(), msg_id))
            return cursor.rowcount > 0

    def purge_old(self, days: int = 7) -> int:
        """
        Purge delivered and dead letter messages older than N days.

        Returns:
            Number of messages purged
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute("""
                DELETE FROM messages
                WHERE status IN ('delivered', 'dead_letter')
                AND updated_at < ?
            """, (cutoff,))

            count = cursor.rowcount
            if count > 0:
                logger.info(f"Purged {count} old messages")
            return count

    def clear_all(self) -> int:
        """Clear all messages (use with caution)."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM messages")
            return cursor.rowcount

    # =========================================================================
    # MESSAGE LIFECYCLE TRACKING (Sprint C: Message Visibility)
    # =========================================================================

    def record_lifecycle_event(
        self,
        message_id: str,
        state: MessageLifecycleState,
        details: str = "",
        node_id: str = "",
        hop_count: int = 0
    ) -> bool:
        """
        Record a lifecycle event for a message.

        Args:
            message_id: ID of the message
            state: New lifecycle state
            details: Additional details about the state change
            node_id: Node ID that processed this event
            hop_count: Current hop count

        Returns:
            True if recorded successfully

        API Contract:
            - Thread-safe
            - Events are append-only (never deleted during recording)
            - Tests: tests/test_message_lifecycle.py
        """
        event = MessageLifecycleEvent(
            message_id=message_id,
            state=state,
            timestamp=datetime.now(),
            details=details,
            node_id=node_id,
            hop_count=hop_count,
        )

        try:
            with self._get_connection() as conn:
                data = event.to_dict()
                conn.execute("""
                    INSERT INTO message_lifecycle
                    (message_id, state, timestamp, details, node_id, hop_count)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    data["message_id"], data["state"], data["timestamp"],
                    data["details"], data["node_id"], data["hop_count"]
                ))
            logger.debug(f"Lifecycle event: {message_id} -> {state.value}")
            return True
        except Exception as e:
            logger.warning(f"Failed to record lifecycle event: {e}")
            return False

    def get_message_trace(self, message_id: str) -> List[MessageLifecycleEvent]:
        """
        Get the complete lifecycle trace for a message.

        Args:
            message_id: ID of the message to trace

        Returns:
            List of lifecycle events in chronological order

        API Contract:
            - Returns empty list if message not found
            - Events ordered by timestamp ascending
            - Thread-safe
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM message_lifecycle
                WHERE message_id = ?
                ORDER BY timestamp ASC
            """, (message_id,))

            return [
                MessageLifecycleEvent.from_dict(dict(row))
                for row in cursor.fetchall()
            ]

    def get_recent_events(
        self,
        limit: int = 50,
        state_filter: Optional[MessageLifecycleState] = None
    ) -> List[MessageLifecycleEvent]:
        """
        Get recent lifecycle events across all messages.

        Args:
            limit: Maximum number of events to return
            state_filter: Optional filter by state

        Returns:
            List of recent events, newest first
        """
        with self._get_connection() as conn:
            if state_filter:
                cursor = conn.execute("""
                    SELECT * FROM message_lifecycle
                    WHERE state = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (state_filter.value, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM message_lifecycle
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))

            return [
                MessageLifecycleEvent.from_dict(dict(row))
                for row in cursor.fetchall()
            ]

    def get_message_summary(self, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a summary of a message's journey through the system.

        Args:
            message_id: ID of the message

        Returns:
            Dict with message info and lifecycle summary, or None if not found
        """
        # Get the message
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            message = QueuedMessage.from_dict(dict(row))

        # Get lifecycle events
        events = self.get_message_trace(message_id)

        # Build summary
        states_reached = [e.state.value for e in events]
        total_time = None
        if len(events) >= 2:
            total_time = (events[-1].timestamp - events[0].timestamp).total_seconds()

        # Find failure reason if failed
        failure_reason = None
        for event in reversed(events):
            if event.state in (MessageLifecycleState.FAILED, MessageLifecycleState.TIMEOUT):
                failure_reason = event.details
                break

        return {
            "message_id": message_id,
            "destination": message.destination,
            "current_status": message.status.value,
            "created_at": message.created_at.isoformat(),
            "updated_at": message.updated_at.isoformat(),
            "retry_count": message.retry_count,
            "lifecycle": {
                "states_reached": states_reached,
                "current_state": events[-1].state.value if events else "unknown",
                "event_count": len(events),
                "total_time_seconds": total_time,
                "max_hops": max((e.hop_count for e in events), default=0),
            },
            "failure_reason": failure_reason,
            "payload_preview": str(message.payload)[:100] + "..." if len(str(message.payload)) > 100 else str(message.payload),
        }

    def get_failed_messages_with_reason(self, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Get failed messages with their failure reasons.

        Useful for debugging delivery issues.

        Args:
            hours: Look back this many hours

        Returns:
            List of failed message summaries with reasons
        """
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT m.id, m.destination, m.error_message, m.updated_at
                FROM messages m
                WHERE m.status IN ('failed', 'dead_letter')
                AND m.updated_at > ?
                ORDER BY m.updated_at DESC
            """, (cutoff,))

            results = []
            for row in cursor.fetchall():
                msg_id = row["id"]
                # Get the last failure event
                events = self.get_message_trace(msg_id)
                failure_details = ""
                for event in reversed(events):
                    if event.state in (MessageLifecycleState.FAILED, MessageLifecycleState.TIMEOUT):
                        failure_details = event.details
                        break

                results.append({
                    "message_id": msg_id,
                    "destination": row["destination"],
                    "error_message": row["error_message"],
                    "failure_details": failure_details,
                    "updated_at": row["updated_at"],
                })

            return results

    def purge_lifecycle_history(self, days: int = 30) -> int:
        """
        Purge old lifecycle history entries.

        Args:
            days: Delete entries older than this

        Returns:
            Number of entries deleted
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute("""
                DELETE FROM message_lifecycle
                WHERE timestamp < ?
            """, (cutoff,))

            count = cursor.rowcount
            if count > 0:
                logger.info(f"Purged {count} old lifecycle entries")
            return count

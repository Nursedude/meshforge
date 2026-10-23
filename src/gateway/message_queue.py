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
import itertools
import json
import logging
import os
import socket
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from contextlib import contextmanager

# Import centralized path utility for sudo compatibility
from utils.db_helpers import connect_tuned
from utils.paths import atomic_write_text, get_real_user_home
from utils.timeouts import MESSAGE_STALE as _MESSAGE_STALE_TIMEOUT
from gateway import delivery_counters as _dc
from gateway import delivery_legs

# UPDATE ... RETURNING needs SQLite >= 3.35; cleanup_stale falls back to
# per-row guarded UPDATEs below it (2026-07-26 review C5).
_SQLITE_SUPPORTS_RETURNING = sqlite3.sqlite_version_info >= (3, 35, 0)

# Split modules (1,500-line rule) — this module stays the import hub:
# every name external code imports remains importable from
# gateway.message_queue. Re-exported model/policy classes live in
# message_queue_models.py; lifecycle tracking is mixed back in from
# message_queue_lifecycle_mixin.py.
from gateway.message_queue_models import (  # noqa: F401 — re-exports
    MessagePriority,
    MessageStatus,
    MessageLifecycleState,
    MessageLifecycleEvent,
    RetryDecision,
    RetryPolicy,
    QueuedMessage,
    SendDeferred,
    M2R_MAX_AGE_S,
    AGE_SANITY_CAP_S,
)
from gateway.message_queue_lifecycle_mixin import MessageQueueLifecycleMixin
from gateway.message_queue_ack_mixin import MessageQueueAckMixin
from gateway.message_queue_outage_mixin import MessageQueueOutageMixin

logger = logging.getLogger(__name__)

# Process-wide message-id sequence — disambiguates ids minted in the same
# millisecond for the same content (see enqueue's id-generation comment).
_MSG_ID_SEQ = itertools.count()


class PersistentMessageQueue(MessageQueueLifecycleMixin, MessageQueueOutageMixin,
                             MessageQueueAckMixin):
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
    # ROADMAP 6f: how often process_once runs expire_overdue().
    _EXPIRE_EVERY_S = 60.0

    RETRY_DELAYS = [5, 15, 45]

    # Deduplication window (seconds)
    DEDUP_WINDOW = 60

    # Default max queue size (active messages: pending + in_progress)
    DEFAULT_MAX_QUEUE_SIZE = 1000

    # Auto-cleanup interval (seconds) — purge old delivered/dead_letter
    AUTO_CLEANUP_INTERVAL = 3600  # 1 hour

    # Stale in_progress timeout — canonical source: utils.timeouts
    STALE_TIMEOUT = _MESSAGE_STALE_TIMEOUT

    # error_message tag stamped by cleanup_stale so a stale-recovered message
    # is greppable and distinguishable from a transport failure (Pri-8).
    STALE_RESET_ERROR = "stale_in_progress_reset"

    # retry_after is a wall-clock schedule; on the fleet's RTC-less Pis a large
    # BACKWARD NTP step after scheduling would strand a message far in the
    # future (Pri-9, gateway review 2026-07-23). No legitimate backoff schedules
    # a retry more than this far out, so get_pending also releases anything
    # beyond the ceiling — a clock-artifact release, not a reschedule rework
    # (honest_failure_modes #6 absurd-delta clamp).
    RETRY_AFTER_CEILING_S = 3600

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
        # Per-destination minimum spacing between consecutive dispatches
        # (seconds; 0 = unpaced). Radios rate-limit API TX: meshtasticd 2.7.x
        # NAKs burst text broadcasts with Routing.Error RATE_LIMIT_EXCEEDED
        # (=38) while reporting HTTP 200 on the toradio hand-off — a 3-chunk
        # message fired ~45ms apart lost chunks 2-3 silently (2026-06-04).
        # Pacing the dispatch loop is the cure for the whole burst class.
        self._send_spacing: Dict[str, float] = {}
        self._send_ready: Dict[str, Callable[[], bool]] = {}
        # Witness for the ready_fn gate: rows skipped untouched would
        # otherwise look like an idle queue (review F4).
        self._gated_destinations: List[str] = []
        # ROADMAP 6f: per-destination max pending age (seconds), enforced by
        # expire_overdue() from process_once at most every _EXPIRE_EVERY_S.
        self._send_max_age: Dict[str, float] = {}
        self._last_expire_mono: Optional[float] = None
        self._last_dispatch_ts: Dict[str, float] = {}  # destination -> time.monotonic()
        self._success_callbacks: List[Callable] = []
        self._failure_callbacks: List[Callable] = []
        # on_terminal hooks per destination (see _fire_terminal)
        self._terminal_callbacks: Dict[str, Callable[[Dict, str], None]] = {}

        # Initialize database
        self._init_db()

        # Stats
        self._stats = {
            "enqueued": 0,
            "gated_skips": 0,
            "deferred_total": 0,
            "expired_total": 0,
            "terminal_callback_errors": 0,
            "age_unknown": 0,
            "clock_jumps": 0,
            "delivered": 0,
            "failed": 0,
            "retried": 0,
            "deduplicated": 0,
            "shed": 0,
            "shed_rejected": 0,
            "permanent_failures": 0,
            # Pri-7 witness: a send reached the wire but mark_delivered then
            # raised (DB error), so the row could not be recorded delivered.
            # Surfaced by get_stats (honest_failure_modes #9).
            "delivered_unrecorded": 0,
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
        - ``content``-shaped (Mesh→RNS spill, ``_spill_to_persistent_queue``):
          carries its text under ``content`` and, like the ``message`` shape,
          NONE of the from/to/text/type ingress keys. Without an explicit
          branch it fell into the text branch below with all four keys absent
          → the SAME degenerate constant hash, so distinct spilled messages
          collapsed to one hash and all but the first were dropped as false
          duplicates within ``DEDUP_WINDOW`` — exactly when an outage makes the
          spill matter most. Key on the content + origin/route fields it does
          carry.
        - ``text``-shaped (Meshtastic ingress): key on from/to/text/type,
          where ``from`` is the originating node and is meaningful.
        """
        if payload.get("message") is not None:
            key_data = json.dumps({
                "message": payload.get("message"),
                "destination": payload.get("destination"),
                "channel": payload.get("channel"),
            }, sort_keys=True)
        elif payload.get("content") is not None:
            key_data = json.dumps({
                "content": payload.get("content"),
                "source_id": payload.get("source_id"),
                "destination_id": payload.get("destination_id"),
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

    def is_recent_duplicate(self, payload: Dict[str, Any],
                            destination: str) -> bool:
        """True if ``payload`` matches a message already queued/delivered to
        ``destination`` within ``DEDUP_WINDOW``.

        Lets a caller distinguish a benign dedup-suppression from a genuine
        enqueue rejection (queue full / unsheddable): BOTH make ``enqueue``
        return ``None``, but only the latter loses data. ``enqueue`` checks
        dedup BEFORE the size limit, so a payload that reached the pressure
        path is by construction not a recent duplicate — classifying the two
        cases here is therefore unambiguous.
        """
        return self._is_duplicate(self._compute_hash(payload), destination)

    def enqueue(self, payload: Dict[str, Any], destination: str,
                priority: MessagePriority = MessagePriority.NORMAL,
                max_retries: int = 3, deduplicate: bool = True,
                created_at: Optional[datetime] = None,
                source: Optional[str] = None) -> Optional[str]:
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
        if source:
            # BIC-2: the row remembers its leg for every later lifecycle event.
            payload = {**payload, delivery_legs.LEG_SOURCE_KEY: source}
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
                protocol=destination, source=source,
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
                        protocol=destination, source=source,
                        drop_reason=_dc.DropReason.QUEUE_PRESSURE,
                        note=f"depth={depth}/{self._max_queue_size}",
                    )
                    return None

        # Generate unique ID. The ms-timestamp + content-hash pair alone is
        # NOT unique: identical content enqueued twice in the same
        # millisecond (dedup disabled, or same content to two destinations —
        # both legitimate, test-pinned cases) collided on the TEXT PRIMARY
        # KEY and the second INSERT failed. A process-wide monotonic
        # sequence makes the id unique within the process; the timestamp
        # keeps it unique across restarts.
        msg_id = (f"{int(time.time() * 1000)}-{content_hash[:8]}"
                  f"-{next(_MSG_ID_SEQ) & 0xFFFF:04x}")

        message = QueuedMessage(
            id=msg_id,
            payload=payload,
            destination=destination,
            priority=priority,
            max_retries=max_retries,
            content_hash=content_hash,
        )
        if created_at is not None:
            # A requeue that continues an older wait keeps its first enqueue
            # time, so a max age counts from there (ROADMAP 6f, Q17).
            message.created_at = created_at

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
            protocol=destination, source=source,
        )

        return msg_id

    def get_pending(self, destination: Optional[str] = None,
                    limit: int = 100) -> List[QueuedMessage]:
        """Get pending messages ready for delivery."""
        now_dt = datetime.now()
        now = now_dt.isoformat()
        # Pri-9: also release messages scheduled absurdly far out — a backward
        # clock step after scheduling, not a legitimate backoff.
        ceiling = (now_dt + timedelta(seconds=self.RETRY_AFTER_CEILING_S)).isoformat()

        with self._get_connection() as conn:
            if destination:
                cursor = conn.execute("""
                    SELECT * FROM messages
                    WHERE status = 'pending'
                    AND destination = ?
                    AND (retry_after IS NULL OR retry_after <= ? OR retry_after > ?)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT ?
                """, (destination, now, ceiling, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM messages
                    WHERE status = 'pending'
                    AND (retry_after IS NULL OR retry_after <= ? OR retry_after > ?)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT ?
                """, (now, ceiling, limit))

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
        """Mark message as successfully delivered.

        Idempotent: a second call on an already-delivered row returns False
        (an unguarded UPDATE re-matched the row, double-counting the
        delivered stat and recording a duplicate SENT transition).
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE messages
                SET status = 'delivered', updated_at = ?
                WHERE id = ? AND status != 'delivered'
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
                    "SELECT destination, payload FROM messages WHERE id = ?",
                    (msg_id,),
                ).fetchone()
                protocol = row["destination"] if row else None
                payload = self._row_payload(row)
                _dc.record(
                    _dc.DeliveryState.SENT,
                    msg_id=msg_id,
                    protocol=protocol,
                    source=delivery_legs.payload_source(payload),
                )
                terminal = (protocol, payload)
            else:
                terminal = None
        if terminal is None:  # hook runs outside the connection
            return False
        self._fire_terminal(terminal[0], terminal[1], "delivered")
        return True

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
            drop = None  # recorded only if the guarded UPDATE lands (F5)
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
                    drop = (reason, decision.reason[:80])
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
                    drop = (_dc.DropReason.RETRIES_EXHAUSTED,
                            f"retry_count={message.retry_count}")
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
            changed = conn.execute("""
                UPDATE messages
                SET status = ?, updated_at = ?, retry_count = ?,
                    retry_after = ?, error_message = ?
                WHERE id = ? AND status NOT IN ('dead_letter', 'delivered')
            """, (
                data["status"], data["updated_at"], data["retry_count"],
                data["retry_after"], data["error_message"], msg_id
            )).rowcount

        if changed == 1 and drop is not None:
            # A late mark_failed on a row the stale reset already
            # dead-lettered matches 0 rows: not a second loss (BIC-2 F5).
            _dc.record(
                _dc.DeliveryState.DROPPED, msg_id=msg_id,
                protocol=message.destination,
                source=delivery_legs.payload_source(message.payload),
                drop_reason=drop[0], note=drop[1],
            )
        if changed == 1 and message.status == MessageStatus.DEAD_LETTER:
            self._fire_terminal(message.destination, message.payload,
                                "dropped")
        return changed == 1  # False: row already finished, nothing changed

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

    def register_sender(self, destination: str,
                        send_fn: Callable[[Dict], bool],
                        min_spacing_s: float = 0.0,
                        ready_fn: Optional[Callable[[], bool]] = None,
                        max_age_s: Optional[float] = None,
                        on_terminal: Optional[Callable[[Dict, str], None]] = None,
                        ) -> None:
        """
        Register a send function for a destination.

        Args:
            destination: Target system name
            send_fn: Function that takes payload dict, returns True if sent
            min_spacing_s: Minimum seconds between consecutive dispatches to
                this destination (0 = unpaced). Set for destinations whose
                transport rate-limits bursts (e.g. meshtasticd NAKs API text
                broadcasts sent back-to-back with RATE_LIMIT_EXCEEDED while
                the HTTP hand-off still returns 200 — silent data loss).
                Not-yet-due messages simply stay 'pending' for a later
                process_once pass; FIFO order within a priority is preserved.
            ready_fn: Optional "can this destination send at all right
                now?" check. While it returns False (or raises) the
                destination's rows are left untouched — no attempt, no
                retry_count bump — so an outage longer than the retry budget
                (3 tries, ~65 s) cannot dead-letter them (F4 review R2).
        """
        self._send_callbacks[destination] = send_fn
        self._send_spacing[destination] = max(0.0, float(min_spacing_s))
        if ready_fn is None:
            self._send_ready.pop(destination, None)
        else:
            self._send_ready[destination] = ready_fn
        # max_age_s: pending rows older than this expire, witnessed
        # (ROADMAP 6f); None = never.
        if max_age_s is None:
            self._send_max_age.pop(destination, None)
        else:
            self._send_max_age[destination] = float(max_age_s)
        # on_terminal(payload, "delivered"|"dropped"): once per row, at every exit
        if on_terminal is None:
            self._terminal_callbacks.pop(destination, None)
        else:
            self._terminal_callbacks[destination] = on_terminal

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

        # Before the ready_fn gate, so a gated destination still ages out.
        now_mono = time.monotonic()
        self.note_clock(datetime.now(), now_mono)
        if (self._send_max_age and (self._last_expire_mono is None
                or now_mono - self._last_expire_mono >= self._EXPIRE_EVERY_S)):
            self._last_expire_mono = now_mono
            try:
                self.expire_overdue()
            except Exception as e:
                logger.error(f"expire_overdue failed: {e}")

        gated: List[str] = []
        for destination, send_fn in self._send_callbacks.items():
            ready_fn = self._send_ready.get(destination)
            if ready_fn is not None:
                try:
                    ready = bool(ready_fn())
                except Exception as e:
                    logger.debug(f"ready_fn for {destination} raised: {e}")
                    ready = False
                if not ready:
                    gated.append(destination)
                    with self._lock:
                        self._stats["gated_skips"] += 1
                    continue
            messages = self.get_pending(destination=destination, limit=batch_size)
            spacing = self._send_spacing.get(destination, 0.0)

            for message in messages:
                # TX pacing: if this destination's last dispatch is too
                # recent, leave the rest of the batch 'pending' — the next
                # process_once pass (start_processing interval) picks them
                # up in the same priority/FIFO order. Never sleep here: this
                # loop shares the worker thread with other destinations.
                if spacing > 0.0:
                    last = self._last_dispatch_ts.get(destination)
                    if last is not None and (time.monotonic() - last) < spacing:
                        break

                if not self.mark_in_progress(message.id):
                    continue

                try:
                    # Fork C / syn-ack: inject the queue row's id so
                    # downstream handlers can pin LXMF delivery callbacks
                    # to the same msg_id that QUEUED + SENT were recorded
                    # against — history_for(msg_id) joins all three.
                    dispatch_payload = {
                        **message.payload, '_queue_msg_id': message.id,
                        '_queue_created_at': message.created_at.isoformat(),
                    }
                    # Stamp BEFORE the call: the transport's rate limiter
                    # counts attempts, including ones that error mid-send.
                    self._last_dispatch_ts[destination] = time.monotonic()
                    success = send_fn(dispatch_payload)

                    if success:
                        # Point of no return: the payload is on the wire. A
                        # bookkeeping failure from here — a mark_delivered DB
                        # error (disk I/O / readonly, which DO happen on the
                        # fleet's SD-card Pis) — must NOT fall through to the
                        # outer except's mark_failed (Pri-7, gateway review
                        # 2026-07-23). Re-queuing re-sends a message that
                        # already went out: a GUARANTEED duplicate on the wire,
                        # recorded with a DB-error reason indistinguishable from
                        # a real send failure (no witness the send succeeded).
                        # Isolate it: leave the row in_progress (get_pending
                        # skips it, so no immediate re-send) and surface a
                        # distinct witness instead of silently re-queuing what
                        # already went out (honest_failure_modes #9). The
                        # in_progress→cleanup_stale re-send guard is Pri-8's
                        # separate concern.
                        try:
                            self.mark_delivered(message.id)
                            for callback in self._success_callbacks:
                                try:
                                    callback(message)
                                except Exception as e:
                                    logger.debug(f"Success callback error: {e}")
                        except Exception as e:
                            with self._lock:
                                self._stats["delivered_unrecorded"] += 1
                            logger.error(
                                "Message %s SENT but mark_delivered failed "
                                "(%s) — left in_progress, NOT re-queued to "
                                "avoid a duplicate on the wire; delivery "
                                "bookkeeping degraded "
                                "(stats.delivered_unrecorded).",
                                message.id, e)
                    else:
                        self.mark_failed(message.id, "Send returned False")

                    processed += 1

                except SendDeferred as d:
                    # Transient (circuit open / no path): not an attempt.
                    self.mark_deferred(message.id, d.delay_s, d.reason)
                    processed += 1

                except Exception as e:
                    self.mark_failed(message.id, str(e))
                    for callback in self._failure_callbacks:
                        try:
                            callback(message, str(e))
                        except Exception as e2:
                            logger.debug(f"Failure callback error: {e2}")
                    processed += 1

        self._gated_destinations = gated
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
            if self._process_thread.is_alive():
                # A send_fn wedged past the join budget: the loop will still
                # exit at its next iteration, but claiming "stopped" now
                # would be false.
                logger.warning(
                    "Message queue processing did not stop within 5s "
                    "(worker still in a send callback); it will exit "
                    "after the current dispatch.")
                return
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
            # NOTE: no "failed" override here. A failing message goes straight to
            # DEAD_LETTER (mark_failed never persists MessageStatus.FAILED), so a
            # point-in-time COUNT(status='failed') is structurally always 0 —
            # overriding the cumulative self._stats["failed"] with it made the
            # exported prometheus/influx "failed" metric read zero forever while
            # messages were actively dead-lettering (honest_failure_modes #1:
            # degraded state -> valid-looking value -> false "no failures" claim).
            # Let **self._stats provide the real cumulative failure counter.
            "dead_letter": status_counts.get("dead_letter", 0),
            "gated_destinations": list(self._gated_destinations),
            "oldest_pending_age_s": self.oldest_pending_age_s(),
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
                SELECT id, destination, payload FROM messages
                WHERE status = 'pending'
                AND priority <= ?
                ORDER BY priority ASC, created_at ASC
                LIMIT ?
            """, (MessagePriority.NORMAL.value, count))

            fetched = cursor.fetchall()
            rows = [(row["id"], row["destination"]) for row in fetched]
            shed_payloads = [self._row_payload(row) for row in fetched]
            if not rows:
                return 0

            ids = [r[0] for r in rows]
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                "DELETE FROM messages WHERE id IN ({})".format(placeholders), ids
            )

            shed_count = len(ids)
            with self._lock:
                self._stats["shed"] += shed_count
            logger.info(f"Queue overflow: shed {shed_count} low-priority messages")

        # Fork C: each shed message was recorded QUEUED at enqueue — without
        # a terminal DROPPED here its lifecycle reads QUEUED-then-nothing
        # forever, and the shed silently vanishes from the delivery-counter
        # populations. QUEUE_SHED existed in the closed taxonomy with no
        # recording site until 2026-07-09 (frontier review Pri-2).
        for (msg_id, destination), payload in zip(rows, shed_payloads):
            _dc.record(
                _dc.DeliveryState.DROPPED,
                msg_id=msg_id,
                protocol=destination,
                source=delivery_legs.payload_source(payload),
                drop_reason=_dc.DropReason.QUEUE_SHED,
            )
            self._fire_terminal(destination, payload, "dropped")
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
        """Recover stale in_progress messages, bounding the retry loop.

        Messages stuck in 'in_progress' past STALE_TIMEOUT (crashed or wedged
        dispatch) are reset to pending so they can be retried — but each reset
        now BUMPS retry_count, and a message that has exhausted max_retries is
        moved to dead_letter with a witness instead of being reset again
        (Pri-8, gateway review 2026-07-23).

        The prior bare `UPDATE ... SET status='pending'` never touched
        retry_count, so a dispatch that wedged past STALE_TIMEOUT on every
        attempt was reset to pending forever — retried endlessly, never
        dead-lettered, with no per-message witness (honest_failure_modes: an
        unbounded loop mapped to a valid-looking 'pending', no terminal state).
        Bumping retry_count gives every stale message a bounded life; the
        terminal drop records DROPPED/RETRIES_EXHAUSTED so it is observable.

        (Note: Pri-7 deliberately parks a delivered-but-unrecorded row in
        in_progress. Such a row is now bounded here too — re-sent at most
        max_retries times before dead-lettering, rather than looping — the best
        achievable while the bookkeeping DB is degraded.)

        retry_after is intentionally NOT set: a recovered stale message is
        immediately re-eligible (a crashed attempt shouldn't also serve a
        backoff), preserving the reset-to-pending contract.

        Returns:
            Number of stale messages processed (reset-for-retry + dead-lettered).
        """
        cutoff = (datetime.now() - timedelta(seconds=self.STALE_TIMEOUT)).isoformat()
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            # The DROPPED witness set derives from what the UPDATE actually
            # changed — a row a concurrent mark_delivered flipped between
            # observation and update must not be witnessed as dropped
            # (2026-07-26 review C5). RETURNING where the runtime SQLite has
            # it; per-row guarded UPDATEs otherwise. Terminal dead-lettering
            # runs first so the reset UPDATE below no longer matches.
            # exhausted rows carry the POST-bump retry_count.
            if _SQLITE_SUPPORTS_RETURNING:
                exhausted = [
                    {"id": r["id"], "destination": r["destination"],
                     "retry_count": r["retry_count"],
                     "payload": self._row_payload(r)}
                    for r in conn.execute("""
                        UPDATE messages
                        SET status = 'dead_letter',
                            retry_count = retry_count + 1,
                            updated_at = ?, error_message = ?
                        WHERE status = 'in_progress' AND updated_at < ?
                        AND retry_count + 1 >= max_retries
                        RETURNING id, destination, retry_count, payload
                    """, (now, self.STALE_RESET_ERROR, cutoff)).fetchall()
                ]
            else:
                exhausted = self._dead_letter_stale_per_row(conn, cutoff, now)

            # The rest get one more retry with retry_count bumped so they
            # progress toward dead_letter instead of resetting forever.
            reset_count = conn.execute("""
                UPDATE messages
                SET status = 'pending', retry_count = retry_count + 1,
                    updated_at = ?, error_message = ?
                WHERE status = 'in_progress' AND updated_at < ?
            """, (now, self.STALE_RESET_ERROR, cutoff)).rowcount

        # Witness for terminal drops (outside the txn — _dc has its own store).
        for row in exhausted:
            with self._lock:
                self._stats["failed"] += 1
            _dc.record(
                _dc.DeliveryState.DROPPED,
                msg_id=row["id"],
                protocol=row["destination"],
                source=delivery_legs.payload_source(row.get("payload")),
                drop_reason=_dc.DropReason.RETRIES_EXHAUSTED,
                note=f"stale_in_progress retry_count={row['retry_count']}",
            )
            self._fire_terminal(row["destination"], row.get("payload"),
                                "dropped")

        dead_count = len(exhausted)
        total = reset_count + dead_count
        if total > 0:
            logger.info(
                "cleanup_stale: reset %d stale in_progress to pending "
                "(retry_count bumped), dead-lettered %d at max_retries",
                reset_count, dead_count)
        return total

    def _stale_exhausted_candidates(self, conn, cutoff: str) -> list:
        """Rows a stale-reset would push to/past max_retries. Observation
        only — the per-row UPDATE in _dead_letter_stale_per_row re-checks
        status so a concurrent transition is never witnessed as dropped."""
        return conn.execute("""
            SELECT id, destination, retry_count, payload FROM messages
            WHERE status = 'in_progress' AND updated_at < ?
            AND retry_count + 1 >= max_retries
        """, (cutoff,)).fetchall()

    def _dead_letter_stale_per_row(self, conn, cutoff: str,
                                   now: str) -> List[Dict[str, Any]]:
        """Pre-3.35 fallback for cleanup_stale's terminal leg: per-row guarded
        UPDATEs whose rowcount decides the witness set, so only rows this
        call actually transitioned are reported (returns POST-bump
        retry_count, matching the RETURNING leg)."""
        out: List[Dict[str, Any]] = []
        for row in self._stale_exhausted_candidates(conn, cutoff):
            changed = conn.execute("""
                UPDATE messages
                SET status = 'dead_letter', retry_count = retry_count + 1,
                    updated_at = ?, error_message = ?
                WHERE id = ? AND status = 'in_progress' AND updated_at < ?
                AND retry_count + 1 >= max_retries
            """, (now, self.STALE_RESET_ERROR, row["id"], cutoff)).rowcount
            if changed:
                out.append({"id": row["id"],
                            "destination": row["destination"],
                            "retry_count": row["retry_count"] + 1,
                            "payload": self._row_payload(row)})
        return out

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


# ── Queue stats state file — the map-less box's /api/gateway/queue ──────────
#
# Same shape and rationale as delivery_counters' delivery snapshot state file
# (2026-07-31, moc3 detector_blind fix): on a gateway-only box the map that
# serves /api/gateway/queue is disabled BY DESIGN, so probe_queue_backlog was
# structurally blind there. The gateway publishes its own queue.get_stats()
# next to the DB; the probe falls back to the file when :5000 is unreachable.

QUEUE_STATS_STATE_SCHEMA = 1

#: One relative path shared by this WRITER and the watchdog-probe READER
#: (``utils.watchdog_probes_gateway._QUEUE_STATS_SUBPATH``) — two consumers
#: of one artifact share ONE constant (honest_failure_modes #5); a test pins
#: the two equal. The reader cannot call ``queue_stats_state_path()``: it
#: runs as root in the watchdog, where ``get_real_user_home()`` is not the
#: operator's home.
QUEUE_STATS_STATE_SUBPATH = os.path.join(
    ".local", "share", "meshforge", "queue_stats.json")


def queue_stats_state_path() -> Path:
    """Where the gateway publishes its queue stats (atomic JSON).

    ``MESHFORGE_QUEUE_STATS_STATE`` (full path) overrides — a test seam,
    mirrors ``MESHFORGE_DELIVERY_SNAPSHOT_STATE``."""
    override = os.environ.get("MESHFORGE_QUEUE_STATS_STATE")
    if override:
        return Path(override)
    return get_real_user_home() / QUEUE_STATS_STATE_SUBPATH


def write_queue_stats_state(
    *,
    stats: Optional[Dict[str, Any]] = None,
    path: Optional[Path] = None,
    host: Optional[str] = None,
    now: Optional[float] = None,
) -> bool:
    """Best-effort: publish ``get_stats()`` output to the state file
    (atomic). NEVER raises — a publish failure must never touch the bridge.
    Returns True on success.

    ``stats`` is required in practice (the caller owns a live queue; this
    module never constructs one just to publish). A non-dict is NOT
    published — an absent file is honestly "no data" to the reader, never a
    forged healthy zero. The envelope carries ``ts`` so the reader can
    refuse a corpse (honest_failure_modes #2)."""
    try:
        if not isinstance(stats, dict):
            return False
        payload = {
            "schema": QUEUE_STATS_STATE_SCHEMA,
            "host": host if host is not None else socket.gethostname(),
            "ts": float(now) if now is not None else time.time(),
            "stats": stats,
        }
        target = Path(path) if path is not None else queue_stats_state_path()
        atomic_write_text(target, json.dumps(payload, separators=(",", ":")))
        return True
    except Exception:
        logger.debug("queue stats state publish failed", exc_info=True)
        return False

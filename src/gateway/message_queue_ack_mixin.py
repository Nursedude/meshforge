"""PersistentMessageQueue mixin: Issue #66 application-layer ack tracking.

Split out of ``message_queue.py`` (2026-10-23) when that file sat at the
MF025 1,500-line cap and BIC-2 slice 2 needed room there. Behaviour-neutral
move: the methods are byte-identical to their previous bodies.

Host contract: ``self._get_connection()`` (a context manager yielding a
sqlite3 connection with ``row_factory`` set) and ``self.enqueue``.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from gateway.message_queue_models import MessagePriority, MessageStatus


class MessageQueueAckMixin:
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
            # The UPDATE is the gate: a SELECT-then-UPDATE let two concurrent
            # callers (an LXMF delivery callback racing the overdue-ack
            # sweep, or a re-fired callback) both read 'pending' and both
            # return the origin — a double synthetic ACK on the origin
            # network. Exactly one caller wins this row (2026-07-09 review).
            cursor = conn.execute("""
                UPDATE messages
                   SET ack_status = 'acked',
                       ack_at = ?,
                       updated_at = ?
                 WHERE id = ? AND ack_required = 1
                   AND ack_status = 'pending'
            """, (now, now, message_id))
            if cursor.rowcount == 0:
                return None
            row = conn.execute("""
                SELECT id, ack_origin_network, ack_origin_address
                  FROM messages
                 WHERE id = ?
            """, (message_id,)).fetchone()
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

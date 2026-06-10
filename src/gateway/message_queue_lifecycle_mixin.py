"""
Message lifecycle tracking mixin for PersistentMessageQueue.

Code-motion extraction from message_queue.py (1,500-line rule), following the
bridge_send_mixin.py idiom: PersistentMessageQueue inherits this mixin, so
every method here still runs as a bound method of the queue and uses the
queue's own ``self._get_connection()`` (the connect_tuned chokepoint stays
in message_queue.py — MF013 creator_module is unchanged).

Covers the Sprint C "Message Visibility" surface: the append-only
``message_lifecycle`` event log (recording, per-message trace, recent-events
feed, journey summaries, failure-reason queries) and its retention purge.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from .message_queue_models import (
    MessageLifecycleEvent,
    MessageLifecycleState,
    QueuedMessage,
)

logger = logging.getLogger(__name__)


class MessageQueueLifecycleMixin:
    """Lifecycle-tracking methods mixed into PersistentMessageQueue.

    Expects the host class to provide ``self._get_connection()``.
    """

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

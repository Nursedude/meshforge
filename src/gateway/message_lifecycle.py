"""
Message Flow Tracker — In-memory lifecycle tracking for bridge messages.

Provides a lightweight, thread-safe tracker that records message state
transitions as they flow through the bridge. Complements the persistent
MessageLifecycleState/record_lifecycle_event() in message_queue.py with
a fast in-memory view suitable for TUI dashboard display.

Usage:
    tracker = MessageFlowTracker()
    tracker.record_event("msg-123", MessageLifecycleState.SENT,
                         source="meshtastic", destination="rns",
                         content_preview="Hello from mesh")
    recent = tracker.get_recent(limit=20)
    summary = tracker.get_summary()
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .message_queue import MessageLifecycleState

logger = logging.getLogger(__name__)

# Maximum flow records to retain in memory
MAX_FLOW_RECORDS = 500


@dataclass
class FlowRecord:
    """A single message flow event with routing context."""
    message_id: str
    state: MessageLifecycleState
    timestamp: float
    source_network: str = ""
    destination_network: str = ""
    content_preview: str = ""
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for display/serialization."""
        return {
            "message_id": self.message_id,
            "state": self.state.value,
            "timestamp": self.timestamp,
            "source_network": self.source_network,
            "destination_network": self.destination_network,
            "content_preview": self.content_preview,
            "details": self.details,
        }


class MessageFlowTracker:
    """Thread-safe in-memory message flow tracker.

    Tracks message lifecycle events in a bounded deque for fast
    dashboard display. Does not persist to disk — use message_queue.py's
    record_lifecycle_event() for durable storage.
    """

    def __init__(self, max_records: int = MAX_FLOW_RECORDS):
        self._records: deque = deque(maxlen=max_records)
        self._lock = threading.Lock()
        self._counts: Dict[str, int] = {}

    def record_event(
        self,
        message_id: str,
        state: MessageLifecycleState,
        source_network: str = "",
        destination_network: str = "",
        content_preview: str = "",
        details: str = "",
    ) -> None:
        """Record a message flow event.

        Args:
            message_id: Unique message identifier.
            state: Current lifecycle state.
            source_network: Origin network (meshtastic/rns/meshcore).
            destination_network: Target network.
            content_preview: Truncated message content for display.
            details: Additional context (error reason, hop count, etc).
        """
        record = FlowRecord(
            message_id=message_id,
            state=state,
            timestamp=time.time(),
            source_network=source_network,
            destination_network=destination_network,
            content_preview=content_preview[:50] if content_preview else "",
            details=details,
        )

        with self._lock:
            self._records.append(record)
            state_key = state.value
            self._counts[state_key] = self._counts.get(state_key, 0) + 1

        logger.debug(
            f"Flow: {message_id[:8]}... {source_network}→{destination_network} "
            f"[{state.value}]"
        )

    def get_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent flow records, newest first.

        Args:
            limit: Maximum number of records to return.

        Returns:
            List of flow record dicts, newest first.
        """
        with self._lock:
            records = list(self._records)

        # Reverse for newest-first ordering
        records.reverse()
        return [r.to_dict() for r in records[:limit]]

    def get_summary(self) -> Dict[str, Any]:
        """Get aggregate counts by state.

        Returns:
            Dict with state counts and total.
        """
        with self._lock:
            counts = self._counts.copy()
            total = len(self._records)

        return {
            "total_tracked": total,
            "by_state": counts,
            "sent": counts.get("sent", 0),
            "delivered": counts.get("delivered", 0),
            "failed": counts.get("failed", 0),
            "timeout": counts.get("timeout", 0),
        }

    def get_latest_state(self, message_id: str) -> Optional[str]:
        """Get the most recent state for a specific message.

        Args:
            message_id: The message to look up.

        Returns:
            State value string, or None if message not found.
        """
        with self._lock:
            for record in reversed(self._records):
                if record.message_id == message_id:
                    return record.state.value
        return None

    def clear(self) -> None:
        """Clear all tracked records."""
        with self._lock:
            self._records.clear()
            self._counts.clear()

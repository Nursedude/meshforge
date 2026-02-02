"""
Bridge Health Monitor - Tracks gateway bridge reliability metrics.

Monitors connection health, message flow, error rates, delivery
confirmations, and provides status summaries for the TUI and diagnostics.

Usage:
    from gateway.bridge_health import BridgeHealthMonitor, DeliveryTracker

    health = BridgeHealthMonitor()
    health.record_message_sent("mesh_to_rns")
    health.record_connection_event("meshtastic", "connected")
    print(health.get_summary())

    # Cross-network health check
    status = health.get_bridge_status()
    if status == BridgeStatus.DEGRADED:
        print("Warning: Bridge operating in degraded mode")

    tracker = DeliveryTracker()
    tracker.track_message("msg-123", b'\\xab\\xcd', "Hello")
    tracker.confirm_delivery("msg-123")
    print(tracker.get_stats())
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BridgeStatus(Enum):
    """Bridge operational status.

    HEALTHY: Both networks connected, error rate acceptable
    DEGRADED: One network down or high error rate
    OFFLINE: Both networks disconnected
    """
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class MessageOrigin(Enum):
    """Message origin classification.

    Tracks where a message originated to enable filtering
    of internet-originated messages from pure radio mesh.
    """
    RADIO = "radio"      # Direct radio reception
    MQTT = "mqtt"        # Via MQTT/internet gateway
    API = "api"          # Via local API call
    BRIDGE = "bridge"    # Bridged from another network
    UNKNOWN = "unknown"  # Origin not determined


@dataclass
class ConnectionEvent:
    """A connection state change event."""
    timestamp: float
    service: str  # "meshtastic" or "rns"
    event: str    # "connected", "disconnected", "error", "retry"
    detail: str = ""


@dataclass
class ErrorEvent:
    """A categorized error event."""
    timestamp: float
    service: str
    category: str    # "transient", "permanent", "unknown"
    message: str
    is_retriable: bool = True


# Error patterns that indicate permanent (non-retriable) failures
PERMANENT_ERROR_PATTERNS = [
    "signal only works in main thread",
    "reinitialise",
    "already running",
    "permission denied",
    "no such device",
    "module not found",
    "import error",
]

# Error patterns that indicate transient (retriable) failures
TRANSIENT_ERROR_PATTERNS = [
    "connection reset",
    "connection refused",
    "broken pipe",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "network unreachable",
    "no route to host",
    "address already in use",
]


def classify_error(error: Exception) -> str:
    """Classify an error as transient or permanent.

    Args:
        error: The exception to classify.

    Returns:
        "transient", "permanent", or "unknown"
    """
    msg = str(error).lower()

    for pattern in PERMANENT_ERROR_PATTERNS:
        if pattern in msg:
            return "permanent"

    for pattern in TRANSIENT_ERROR_PATTERNS:
        if pattern in msg:
            return "transient"

    # Connection-type exceptions are generally transient
    if isinstance(error, (ConnectionError, BrokenPipeError,
                          ConnectionResetError, TimeoutError, OSError)):
        return "transient"

    return "unknown"


class BridgeHealthMonitor:
    """Monitors bridge health and collects operational metrics.

    Thread-safe. Maintains rolling windows of events for analysis
    without unbounded memory growth.
    """

    def __init__(self, window_size: int = 1000):
        """Initialize health monitor.

        Args:
            window_size: Maximum events to keep in rolling windows.
        """
        self._lock = threading.RLock()  # Reentrant: get_summary calls get_uptime_percent
        self._window_size = window_size

        # Connection state
        self._connected: Dict[str, bool] = {
            "meshtastic": False,
            "rns": False,
        }
        self._last_connected: Dict[str, float] = {}
        self._last_disconnected: Dict[str, float] = {}
        self._connection_count: Dict[str, int] = {
            "meshtastic": 0,
            "rns": 0,
        }

        # Message counters
        self._messages_sent: Dict[str, int] = {
            "mesh_to_rns": 0,
            "rns_to_mesh": 0,
        }
        self._messages_failed: Dict[str, int] = {
            "mesh_to_rns": 0,
            "rns_to_mesh": 0,
        }
        self._messages_requeued: int = 0

        # Rolling event windows
        self._connection_events: deque = deque(maxlen=window_size)
        self._error_events: deque = deque(maxlen=window_size)
        self._message_timestamps: deque = deque(maxlen=window_size)

        # Timing
        self._start_time: float = time.time()
        self._uptime_seconds: Dict[str, float] = {
            "meshtastic": 0.0,
            "rns": 0.0,
        }

    def record_connection_event(self, service: str, event: str,
                                detail: str = "") -> None:
        """Record a connection state change.

        Args:
            service: "meshtastic" or "rns"
            event: "connected", "disconnected", "error", "retry"
            detail: Optional detail message.
        """
        now = time.time()
        with self._lock:
            self._connection_events.append(ConnectionEvent(
                timestamp=now, service=service, event=event, detail=detail
            ))

            if event == "connected":
                # Track uptime from last disconnect
                if not self._connected[service]:
                    self._connected[service] = True
                    self._last_connected[service] = now
                    self._connection_count[service] += 1

            elif event in ("disconnected", "error"):
                if self._connected[service]:
                    # Accumulate uptime
                    connected_at = self._last_connected.get(service, now)
                    self._uptime_seconds[service] += now - connected_at
                self._connected[service] = False
                self._last_disconnected[service] = now

    def record_message_sent(self, direction: str) -> None:
        """Record a successfully bridged message.

        Args:
            direction: "mesh_to_rns" or "rns_to_mesh"
        """
        now = time.time()
        with self._lock:
            self._messages_sent[direction] = self._messages_sent.get(direction, 0) + 1
            self._message_timestamps.append(now)

    def record_message_failed(self, direction: str, requeued: bool = False) -> None:
        """Record a failed message send.

        Args:
            direction: "mesh_to_rns" or "rns_to_mesh"
            requeued: Whether the message was saved to persistent queue.
        """
        with self._lock:
            self._messages_failed[direction] = self._messages_failed.get(direction, 0) + 1
            if requeued:
                self._messages_requeued += 1

    def record_error(self, service: str, error: Exception) -> str:
        """Record and classify an error.

        Args:
            service: "meshtastic" or "rns"
            error: The exception that occurred.

        Returns:
            The error category ("transient", "permanent", "unknown").
        """
        category = classify_error(error)
        now = time.time()
        with self._lock:
            self._error_events.append(ErrorEvent(
                timestamp=now,
                service=service,
                category=category,
                message=str(error)[:200],
                is_retriable=(category == "transient"),
            ))
        return category

    def get_message_rate(self, window_seconds: int = 300) -> float:
        """Get messages per minute over a time window.

        Args:
            window_seconds: Time window to calculate rate over.

        Returns:
            Messages per minute.
        """
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            recent = sum(1 for t in self._message_timestamps if t >= cutoff)
        return (recent / window_seconds) * 60 if window_seconds > 0 else 0

    def get_error_rate(self, window_seconds: int = 300) -> Dict[str, int]:
        """Get error counts by category in a time window.

        Args:
            window_seconds: Time window to count errors in.

        Returns:
            Dict with transient/permanent/unknown counts.
        """
        now = time.time()
        cutoff = now - window_seconds
        counts = {"transient": 0, "permanent": 0, "unknown": 0}
        with self._lock:
            for event in self._error_events:
                if event.timestamp >= cutoff:
                    counts[event.category] = counts.get(event.category, 0) + 1
        return counts

    def get_uptime_percent(self, service: str) -> float:
        """Get connection uptime percentage for a service.

        Args:
            service: "meshtastic" or "rns"

        Returns:
            Uptime as percentage (0-100).
        """
        now = time.time()
        total_time = now - self._start_time
        if total_time <= 0:
            return 0.0

        with self._lock:
            uptime = self._uptime_seconds.get(service, 0.0)
            # Add current connected time if still connected
            if self._connected.get(service, False):
                connected_at = self._last_connected.get(service, now)
                uptime += now - connected_at

        return min(100.0, (uptime / total_time) * 100)

    def get_summary(self) -> Dict[str, Any]:
        """Get a comprehensive health summary.

        Returns:
            Dict with all health metrics suitable for API/display.
        """
        now = time.time()
        with self._lock:
            return {
                "uptime_seconds": now - self._start_time,
                "connections": {
                    "meshtastic": {
                        "connected": self._connected.get("meshtastic", False),
                        "uptime_percent": self.get_uptime_percent("meshtastic"),
                        "reconnect_count": self._connection_count.get("meshtastic", 0),
                        "last_connected": self._last_connected.get("meshtastic"),
                        "last_disconnected": self._last_disconnected.get("meshtastic"),
                    },
                    "rns": {
                        "connected": self._connected.get("rns", False),
                        "uptime_percent": self.get_uptime_percent("rns"),
                        "reconnect_count": self._connection_count.get("rns", 0),
                        "last_connected": self._last_connected.get("rns"),
                        "last_disconnected": self._last_disconnected.get("rns"),
                    },
                },
                "messages": {
                    "mesh_to_rns": self._messages_sent.get("mesh_to_rns", 0),
                    "rns_to_mesh": self._messages_sent.get("rns_to_mesh", 0),
                    "failed_mesh_to_rns": self._messages_failed.get("mesh_to_rns", 0),
                    "failed_rns_to_mesh": self._messages_failed.get("rns_to_mesh", 0),
                    "requeued": self._messages_requeued,
                    "rate_per_min": self.get_message_rate(),
                },
                "errors": self.get_error_rate(),
            }

    def is_healthy(self) -> bool:
        """Quick health check: is the bridge operational?

        Returns True if at least one connection is active and
        error rate is not excessive.

        Note: For stricter checks, use get_bridge_status() which
        distinguishes between HEALTHY (both networks) and DEGRADED
        (single network).
        """
        with self._lock:
            any_connected = any(self._connected.values())
        errors = self.get_error_rate(window_seconds=60)
        error_count = sum(errors.values())
        return any_connected and error_count < 10

    def get_bridge_status(self) -> BridgeStatus:
        """
        Get detailed bridge operational status.

        Cross-network health check that distinguishes between:
        - HEALTHY: Both Meshtastic and RNS connected, error rate < 10/min
        - DEGRADED: Only one network connected, or error rate >= 10/min
        - OFFLINE: No networks connected

        Returns:
            BridgeStatus enum value
        """
        with self._lock:
            mesh_connected = self._connected.get("meshtastic", False)
            rns_connected = self._connected.get("rns", False)

        errors = self.get_error_rate(window_seconds=60)
        error_count = sum(errors.values())
        high_error_rate = error_count >= 10

        if not mesh_connected and not rns_connected:
            return BridgeStatus.OFFLINE

        if mesh_connected and rns_connected and not high_error_rate:
            return BridgeStatus.HEALTHY

        # One network down or high error rate
        return BridgeStatus.DEGRADED

    def is_bridge_fully_healthy(self) -> bool:
        """
        Check if bridge is fully operational (both networks up).

        Stricter than is_healthy() - requires BOTH networks connected.
        Use this when you need reliable bidirectional bridging.

        Returns:
            True only if both Meshtastic and RNS are connected
            and error rate is acceptable.
        """
        return self.get_bridge_status() == BridgeStatus.HEALTHY

    def get_degraded_reason(self) -> Optional[str]:
        """
        Get reason if bridge is in degraded mode.

        Returns:
            Human-readable reason string, or None if healthy.
        """
        with self._lock:
            mesh_connected = self._connected.get("meshtastic", False)
            rns_connected = self._connected.get("rns", False)

        errors = self.get_error_rate(window_seconds=60)
        error_count = sum(errors.values())

        reasons = []

        if not mesh_connected:
            reasons.append("Meshtastic disconnected")
        if not rns_connected:
            reasons.append("RNS disconnected")
        if error_count >= 10:
            reasons.append(f"High error rate ({error_count}/min)")

        return "; ".join(reasons) if reasons else None

    def should_pause_bridging(self) -> bool:
        """
        Check if bridging should be paused due to health issues.

        Returns True if:
        - Both networks are disconnected (nothing to bridge)
        - Error rate is critically high (> 20/min)

        This is more permissive than is_bridge_fully_healthy() -
        allows degraded operation but stops on critical failures.
        """
        status = self.get_bridge_status()
        if status == BridgeStatus.OFFLINE:
            return True

        errors = self.get_error_rate(window_seconds=60)
        error_count = sum(errors.values())
        return error_count > 20  # Critical threshold


@dataclass
class DeliveryRecord:
    """Tracks a single LXMF message delivery attempt."""
    msg_id: str
    destination_hash: str  # hex string of destination
    content_preview: str   # first 50 chars of content
    sent_at: float
    status: str = "pending"  # "pending", "delivered", "failed"
    confirmed_at: Optional[float] = None
    failure_reason: str = ""


class DeliveryTracker:
    """Tracks LXMF message delivery confirmations.

    Registers pending deliveries when LXMF messages are sent, and
    updates their status when delivery callbacks fire from the
    LXMF router.

    Thread-safe. Maintains a bounded history of delivery attempts.
    """

    # Maximum tracked deliveries (prevent unbounded growth)
    MAX_HISTORY = 500

    # Delivery timeout (seconds) — consider failed if no confirmation
    DELIVERY_TIMEOUT = 300  # 5 minutes

    def __init__(self):
        self._lock = threading.RLock()
        self._pending: Dict[str, DeliveryRecord] = {}
        self._history: deque = deque(maxlen=self.MAX_HISTORY)
        self._stats = {
            "total_sent": 0,
            "confirmed": 0,
            "failed": 0,
            "timed_out": 0,
        }

    def _force_timeout_oldest(self) -> None:
        """Force-timeout the oldest pending record to make room.

        Called under _lock when _pending exceeds MAX_HISTORY.
        Prevents unbounded memory growth if check_timeouts() is delayed.
        """
        if not self._pending:
            return
        oldest_id = min(self._pending, key=lambda k: self._pending[k].sent_at)
        record = self._pending.pop(oldest_id)
        record.status = "failed"
        record.confirmed_at = time.time()
        record.failure_reason = "evicted_overflow"
        self._history.append(record)
        self._stats["timed_out"] += 1

    def track_message(self, msg_id: str, destination_hash: bytes,
                      content_preview: str = "") -> None:
        """Register a new LXMF message for delivery tracking.

        Args:
            msg_id: Unique message identifier.
            destination_hash: RNS destination hash (bytes).
            content_preview: First portion of message content for display.
        """
        record = DeliveryRecord(
            msg_id=msg_id,
            destination_hash=destination_hash.hex() if isinstance(destination_hash, bytes) else str(destination_hash),
            content_preview=content_preview[:50],
            sent_at=time.time(),
        )
        with self._lock:
            # Prevent unbounded growth if check_timeouts() is delayed
            if len(self._pending) >= self.MAX_HISTORY:
                self._force_timeout_oldest()
            self._pending[msg_id] = record
            self._stats["total_sent"] += 1
            logger.debug(f"Tracking delivery: {msg_id} -> {record.destination_hash[:8]}...")

    def confirm_delivery(self, msg_id: str) -> bool:
        """Mark a message as successfully delivered.

        Called by the LXMF delivery callback when recipient confirms.

        Args:
            msg_id: The message identifier.

        Returns:
            True if the message was found and updated.
        """
        with self._lock:
            record = self._pending.pop(msg_id, None)
            if record is None:
                return False

            record.status = "delivered"
            record.confirmed_at = time.time()
            self._history.append(record)
            self._stats["confirmed"] += 1

            latency = record.confirmed_at - record.sent_at
            logger.info(
                f"LXMF delivery confirmed: {msg_id} "
                f"(latency: {latency:.1f}s)"
            )
            return True

    def confirm_failure(self, msg_id: str, reason: str = "") -> bool:
        """Mark a message delivery as failed.

        Called by the LXMF failure callback.

        Args:
            msg_id: The message identifier.
            reason: Failure reason from LXMF.

        Returns:
            True if the message was found and updated.
        """
        with self._lock:
            record = self._pending.pop(msg_id, None)
            if record is None:
                return False

            record.status = "failed"
            record.confirmed_at = time.time()
            record.failure_reason = reason
            self._history.append(record)
            self._stats["failed"] += 1

            logger.warning(f"LXMF delivery failed: {msg_id} — {reason}")
            return True

    def check_timeouts(self) -> int:
        """Check for timed-out pending deliveries.

        Messages pending longer than DELIVERY_TIMEOUT are considered
        failed (no confirmation received).

        Returns:
            Number of messages timed out.
        """
        now = time.time()
        timed_out = []

        with self._lock:
            for msg_id, record in list(self._pending.items()):
                if now - record.sent_at > self.DELIVERY_TIMEOUT:
                    timed_out.append(msg_id)

            for msg_id in timed_out:
                record = self._pending.pop(msg_id)
                record.status = "failed"
                record.confirmed_at = now
                record.failure_reason = "delivery_timeout"
                self._history.append(record)
                self._stats["timed_out"] += 1

        if timed_out:
            logger.debug(f"Delivery timeout: {len(timed_out)} messages")
        return len(timed_out)

    def get_pending(self) -> List[Dict[str, Any]]:
        """Get list of messages awaiting delivery confirmation.

        Returns:
            List of pending delivery records with age in seconds.
        """
        now = time.time()
        with self._lock:
            return [
                {
                    "msg_id": r.msg_id,
                    "destination": r.destination_hash[:8],
                    "content_preview": r.content_preview,
                    "age_seconds": round(now - r.sent_at, 1),
                }
                for r in self._pending.values()
            ]

    def get_stats(self) -> Dict[str, Any]:
        """Get delivery confirmation statistics.

        Returns:
            Dict with total_sent, confirmed, failed, timed_out,
            pending_count, confirmation_rate.
        """
        with self._lock:
            pending = len(self._pending)
            stats = dict(self._stats)

        total = stats["total_sent"]
        confirmed = stats["confirmed"]
        rate = (confirmed / total * 100) if total > 0 else 0.0

        return {
            **stats,
            "pending_count": pending,
            "confirmation_rate_pct": round(rate, 1),
        }

    def get_recent_deliveries(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent delivery history.

        Args:
            limit: Maximum number of records to return.

        Returns:
            List of recent delivery records (newest first).
        """
        with self._lock:
            records = list(self._history)[-limit:]

        return [
            {
                "msg_id": r.msg_id,
                "destination": r.destination_hash[:8],
                "status": r.status,
                "sent_at": r.sent_at,
                "latency_seconds": round(r.confirmed_at - r.sent_at, 1) if r.confirmed_at else None,
                "failure_reason": r.failure_reason or None,
            }
            for r in reversed(records)
        ]

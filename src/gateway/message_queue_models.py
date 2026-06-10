"""
Data model and retry-policy classes for the persistent gateway queue.

Code-motion extraction from message_queue.py (1,500-line rule). The classes
here are the queue's shared vocabulary: priority/status/lifecycle enums, the
lifecycle event record, the NGINX-style RetryPolicy, and the QueuedMessage
row dataclass. Import them from gateway.message_queue (the hub) — that
module re-exports every public name so external import paths are unchanged.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Any


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

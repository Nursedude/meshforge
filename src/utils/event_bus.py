"""
MeshForge Event Bus

Simple pub/sub event system for decoupled component communication.
Primary use case: Gateway RX messages → UI panel updates.

Issue #17 Phase 3: Instead of RX messages only appearing in logs,
this event bus allows UI panels to subscribe and display them.

Usage:
    from utils.event_bus import event_bus, MessageEvent

    # In gateway (publisher):
    event_bus.emit('message', MessageEvent(
        direction='rx',
        content='Hello from mesh',
        node_id='!abc123',
        channel=0
    ))

    # In UI panel (subscriber):
    def _on_message(event):
        GLib.idle_add(self._add_to_message_list, event)

    event_bus.subscribe('message', _on_message)

    # Cleanup on panel destroy:
    event_bus.unsubscribe('message', _on_message)
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class MessageDirection(Enum):
    """Direction of a mesh message."""
    TX = "tx"  # Transmitted (outgoing)
    RX = "rx"  # Received (incoming)


@dataclass
class MessageEvent:
    """Event representing a mesh network message."""
    direction: str  # 'tx' or 'rx'
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    node_id: str = ""  # Node ID (e.g., '!abc123')
    node_name: str = ""  # Human-readable node name if available
    channel: int = 0  # Channel number
    network: str = ""  # 'meshtastic', 'rns', or 'bridge'
    raw_data: Optional[Dict] = None  # Original packet data if available

    def __str__(self):
        direction = "←" if self.direction == "rx" else "→"
        source = self.node_name or self.node_id or "unknown"
        time_str = self.timestamp.strftime("%H:%M:%S")
        return f"[{time_str}] {direction} {source}: {self.content[:50]}"


@dataclass
class ServiceEvent:
    """Event representing a service status change."""
    service_name: str
    available: bool
    message: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class NodeEvent:
    """Event representing a node update (new node, position change, etc.)."""
    event_type: str  # 'discovered', 'updated', 'lost'
    node_id: str
    node_name: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)
    raw_data: Optional[Dict] = None


@dataclass
class TacticalEvent:
    """Event representing a tactical message (SITREP, TASK, CHECKIN, etc.)."""
    tactical_type: str  # "SITREP", "TASK", "CHECKIN", etc.
    message_id: str
    sender_id: str = ""
    content: Optional[Dict] = None
    encryption_mode: str = "C"  # "C" = CLEAR, "S" = SECURE
    timestamp: datetime = field(default_factory=datetime.now)


class EventBus:
    """
    Thread-safe event bus for pub/sub messaging.

    Subscribers are called in a separate thread to avoid blocking the publisher.
    For GTK UI updates, subscribers should use GLib.idle_add().
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()
        self._worker_thread: Optional[threading.Thread] = None
        self._event_queue: List[tuple] = []
        self._queue_lock = threading.Lock()
        self._running = False

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """
        Subscribe to an event type.

        Args:
            event_type: Type of event (e.g., 'message', 'service', 'node')
            callback: Function to call when event is emitted
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)
                logger.debug(f"Subscribed to '{event_type}': {callback.__name__}")

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """
        Unsubscribe from an event type.

        Args:
            event_type: Type of event
            callback: The callback function to remove
        """
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(callback)
                    logger.debug(f"Unsubscribed from '{event_type}': {callback.__name__}")
                except ValueError:
                    pass  # Callback wasn't subscribed

    def emit(self, event_type: str, event: Any) -> None:
        """
        Emit an event to all subscribers.

        Subscribers are called in their own threads to avoid blocking.

        Args:
            event_type: Type of event
            event: The event data (MessageEvent, ServiceEvent, etc.)
        """
        with self._lock:
            subscribers = self._subscribers.get(event_type, []).copy()

        if not subscribers:
            logger.debug(f"Event '{event_type}' emitted with no subscribers")
            return

        logger.debug(f"Emitting '{event_type}' to {len(subscribers)} subscribers")

        # Call each subscriber in a separate thread
        for callback in subscribers:
            try:
                # Use daemon thread so it doesn't block app shutdown
                thread = threading.Thread(
                    target=self._safe_call,
                    args=(callback, event),
                    daemon=True
                )
                thread.start()
            except Exception as e:
                logger.error(f"Error starting callback thread: {e}")

    def emit_sync(self, event_type: str, event: Any) -> None:
        """
        Emit an event synchronously (for testing or simple cases).

        Args:
            event_type: Type of event
            event: The event data
        """
        with self._lock:
            subscribers = self._subscribers.get(event_type, []).copy()

        for callback in subscribers:
            self._safe_call(callback, event)

    def _safe_call(self, callback: Callable, event: Any) -> None:
        """Call a callback with exception handling."""
        try:
            callback(event)
        except Exception as e:
            logger.error(f"Error in event callback {callback.__name__}: {e}")

    def clear_subscribers(self, event_type: Optional[str] = None) -> None:
        """
        Clear all subscribers for an event type, or all subscribers.

        Args:
            event_type: If specified, only clear this type. Otherwise clear all.
        """
        with self._lock:
            if event_type:
                self._subscribers[event_type] = []
            else:
                self._subscribers.clear()

    def get_subscriber_count(self, event_type: str) -> int:
        """Get the number of subscribers for an event type."""
        with self._lock:
            return len(self._subscribers.get(event_type, []))


# Global singleton instance
event_bus = EventBus()


# =============================================================================
# Convenience functions for common event types
# =============================================================================

def emit_message(
    direction: str,
    content: str,
    node_id: str = "",
    node_name: str = "",
    channel: int = 0,
    network: str = "",
    raw_data: Optional[Dict] = None
) -> None:
    """
    Emit a message event.

    Args:
        direction: 'tx' or 'rx'
        content: Message content
        node_id: Node ID
        node_name: Human-readable node name
        channel: Channel number
        network: Network source ('meshtastic', 'rns', 'bridge')
        raw_data: Optional raw packet data
    """
    event = MessageEvent(
        direction=direction,
        content=content,
        node_id=node_id,
        node_name=node_name,
        channel=channel,
        network=network,
        raw_data=raw_data
    )
    event_bus.emit('message', event)


def emit_service_status(service_name: str, available: bool, message: str) -> None:
    """
    Emit a service status event.

    Args:
        service_name: Name of the service
        available: Whether the service is available
        message: Status message
    """
    event = ServiceEvent(
        service_name=service_name,
        available=available,
        message=message
    )
    event_bus.emit('service', event)


def emit_node_update(
    event_type: str,
    node_id: str,
    node_name: str = "",
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    raw_data: Optional[Dict] = None
) -> None:
    """
    Emit a node update event.

    Args:
        event_type: 'discovered', 'updated', or 'lost'
        node_id: Node ID
        node_name: Human-readable name
        latitude: GPS latitude
        longitude: GPS longitude
        raw_data: Optional raw node data
    """
    event = NodeEvent(
        event_type=event_type,
        node_id=node_id,
        node_name=node_name,
        latitude=latitude,
        longitude=longitude,
        raw_data=raw_data
    )
    event_bus.emit('node', event)


def emit_tactical(
    tactical_type: str,
    message_id: str,
    sender_id: str = "",
    content: Optional[Dict] = None,
    encryption_mode: str = "C",
) -> None:
    """
    Emit a tactical message event.

    Args:
        tactical_type: Type name ('SITREP', 'TASK', 'CHECKIN', etc.)
        message_id: Unique message ID (Crockford Base32)
        sender_id: Sender node ID or callsign
        content: Template-specific content dict
        encryption_mode: 'C' (CLEAR) or 'S' (SECURE)
    """
    event = TacticalEvent(
        tactical_type=tactical_type,
        message_id=message_id,
        sender_id=sender_id,
        content=content,
        encryption_mode=encryption_mode,
    )
    event_bus.emit('tactical', event)


# Export public API
__all__ = [
    'event_bus',
    'EventBus',
    'MessageEvent',
    'MessageDirection',
    'ServiceEvent',
    'NodeEvent',
    'TacticalEvent',
    'emit_message',
    'emit_service_status',
    'emit_node_update',
    'emit_tactical',
]

"""
Base class for gateway message handlers (ABC).

All gateway handlers (Meshtastic, MQTT, MeshCore) share a common constructor
signature and interface. This ABC codifies that contract and provides shared
concrete methods to eliminate duplication.
"""

from abc import ABC, abstractmethod
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from utils.defaults import MAX_MESHTASTIC_MSG_LENGTH

if TYPE_CHECKING:
    from .bridge_health import BridgeHealthMonitor
    from .config import GatewayConfig
    from .node_tracker import UnifiedNodeTracker

logger = logging.getLogger(__name__)


class BaseMessageHandler(ABC):
    """Abstract base for network message handlers."""

    def __init__(
        self,
        config: 'GatewayConfig',
        node_tracker: 'UnifiedNodeTracker',
        health: 'BridgeHealthMonitor',
        stop_event: threading.Event,
        stats: Dict[str, Any],
        stats_lock: threading.Lock,
        message_queue,
        message_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        should_bridge: Optional[Callable] = None,
    ):
        self.config = config
        self.node_tracker = node_tracker
        self.health = health
        self._stop_event = stop_event
        self.stats = stats
        self._stats_lock = stats_lock
        self._message_queue = message_queue
        self._message_callback = message_callback
        self._status_callback = status_callback
        self._should_bridge = should_bridge
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if handler is connected."""
        return self._connected

    @abstractmethod
    def run_loop(self) -> None:
        """Main loop — blocks until stop_event is set."""
        ...

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect and clean up resources."""
        ...

    @abstractmethod
    def send_text(self, message: str, destination: Optional[str] = None,
                  channel: int = 0) -> bool:
        """Send a text message. Returns True on success."""
        ...

    @abstractmethod
    def queue_send(self, payload: Dict) -> bool:
        """Send from persistent queue. Returns True on success."""
        ...

    @abstractmethod
    def test_connection(self) -> bool:
        """Test if the underlying transport is reachable."""
        ...

    def _notify_status(self, status: str) -> None:
        """Notify status callback."""
        if self._status_callback:
            try:
                self._status_callback(status)
            except Exception as e:
                logger.error(f"Status callback error: {e}")

    def _truncate_if_needed(self, message: str,
                            max_length: int = MAX_MESHTASTIC_MSG_LENGTH) -> str:
        """Truncate message to byte limit if needed."""
        msg_bytes = message.encode('utf-8')
        if len(msg_bytes) > max_length:
            logger.warning(
                f"Message exceeds limit "
                f"({len(msg_bytes)} > {max_length} bytes), truncating"
            )
            truncated = msg_bytes[:max_length - 3]
            return truncated.decode('utf-8', errors='ignore') + '...'
        return message

"""
Webhook Support for MeshForge Events

Allows external systems to receive notifications about:
- Node status changes (online/offline)
- Message received
- Position updates
- Alert conditions (low battery, poor signal, etc.)
"""

import json
import logging
import threading
import queue
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any
import urllib.request
import urllib.error

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Webhook event types."""
    NODE_ONLINE = "node_online"
    NODE_OFFLINE = "node_offline"
    MESSAGE_RECEIVED = "message_received"
    POSITION_UPDATE = "position_update"
    TELEMETRY_UPDATE = "telemetry_update"
    ALERT_BATTERY_LOW = "alert_battery_low"
    ALERT_SIGNAL_POOR = "alert_signal_poor"
    ALERT_NODE_UNREACHABLE = "alert_node_unreachable"
    GATEWAY_STATUS = "gateway_status"
    SERVICE_STATUS = "service_status"
    CUSTOM = "custom"


@dataclass
class WebhookEvent:
    """A webhook event to be delivered."""
    event_type: str
    timestamp: str
    data: Dict[str, Any]
    source: str = "meshforge"
    version: str = "1.0"

    def to_dict(self) -> Dict:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "source": self.source,
            "version": self.version,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class WebhookEndpoint:
    """Configuration for a webhook endpoint."""
    url: str
    name: str
    enabled: bool = True
    events: List[str] = None  # None = all events
    secret: Optional[str] = None  # For HMAC signing
    timeout_seconds: int = 10
    retry_count: int = 3
    headers: Dict[str, str] = None

    def __post_init__(self):
        if self.events is None:
            self.events = []
        if self.headers is None:
            self.headers = {}

    def to_dict(self) -> Dict:
        return {
            "url": self.url,
            "name": self.name,
            "enabled": self.enabled,
            "events": self.events,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": self.retry_count,
            "headers": self.headers,
            # Don't expose secret in API responses
        }


class WebhookManager:
    """Manages webhook subscriptions and event delivery."""

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            config_path = get_real_user_home() / ".config" / "meshforge" / "webhooks.json"
        self.config_path = config_path
        self.endpoints: List[WebhookEndpoint] = []
        self._event_queue: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        self._callbacks: Dict[str, List[Callable]] = {}
        self._load_config()

    def _load_config(self):
        """Load webhook configuration from file."""
        try:
            if self.config_path.exists():
                with open(self.config_path) as f:
                    data = json.load(f)
                    self.endpoints = [
                        WebhookEndpoint(**ep) for ep in data.get('endpoints', [])
                    ]
                logger.info(f"Loaded {len(self.endpoints)} webhook endpoints")
        except Exception as e:
            logger.error(f"Error loading webhook config: {e}")
            self.endpoints = []

    def _save_config(self):
        """Save webhook configuration to file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump({
                    'endpoints': [ep.to_dict() for ep in self.endpoints]
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving webhook config: {e}")

    def add_endpoint(self, endpoint: WebhookEndpoint) -> bool:
        """Add a new webhook endpoint."""
        # Check for duplicate URL
        if any(ep.url == endpoint.url for ep in self.endpoints):
            logger.warning(f"Endpoint already exists: {endpoint.url}")
            return False

        self.endpoints.append(endpoint)
        self._save_config()
        logger.info(f"Added webhook endpoint: {endpoint.name}")
        return True

    def remove_endpoint(self, url: str) -> bool:
        """Remove a webhook endpoint by URL."""
        for i, ep in enumerate(self.endpoints):
            if ep.url == url:
                del self.endpoints[i]
                self._save_config()
                logger.info(f"Removed webhook endpoint: {url}")
                return True
        return False

    def update_endpoint(self, url: str, **kwargs) -> bool:
        """Update endpoint configuration."""
        for ep in self.endpoints:
            if ep.url == url:
                for key, value in kwargs.items():
                    if hasattr(ep, key):
                        setattr(ep, key, value)
                self._save_config()
                return True
        return False

    def list_endpoints(self) -> List[Dict]:
        """List all configured endpoints."""
        return [ep.to_dict() for ep in self.endpoints]

    def register_callback(self, event_type: str, callback: Callable):
        """Register a local callback for an event type."""
        if event_type not in self._callbacks:
            self._callbacks[event_type] = []
        self._callbacks[event_type].append(callback)

    def emit(self, event_type: EventType, data: Dict[str, Any]):
        """
        Emit an event to all subscribed webhooks.

        Args:
            event_type: Type of event
            data: Event data payload
        """
        event = WebhookEvent(
            event_type=event_type.value,
            timestamp=datetime.now().isoformat(),
            data=data,
        )

        # Queue for async delivery
        self._event_queue.put(event)

        # Trigger local callbacks synchronously
        event_str = event_type.value
        if event_str in self._callbacks:
            for callback in self._callbacks[event_str]:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"Callback error for {event_str}: {e}")

    def start(self):
        """Start the webhook delivery worker."""
        if self._running:
            return

        self._running = True
        self._worker_thread = threading.Thread(
            target=self._delivery_worker,
            daemon=True,
            name="webhook-delivery"
        )
        self._worker_thread.start()
        logger.info("Webhook delivery worker started")

    def stop(self):
        """Stop the webhook delivery worker."""
        self._running = False
        # Put sentinel to unblock worker
        self._event_queue.put(None)
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
        logger.info("Webhook delivery worker stopped")

    def _delivery_worker(self):
        """Background worker for delivering webhooks."""
        while self._running:
            try:
                event = self._event_queue.get(timeout=1)
                if event is None:
                    continue

                for endpoint in self.endpoints:
                    if not endpoint.enabled:
                        continue

                    # Check event filter
                    if endpoint.events and event.event_type not in endpoint.events:
                        continue

                    self._deliver_to_endpoint(endpoint, event)

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Webhook delivery error: {e}")

    def _deliver_to_endpoint(self, endpoint: WebhookEndpoint, event: WebhookEvent):
        """Deliver event to a specific endpoint with retries."""
        payload = event.to_json().encode('utf-8')

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'MeshForge-Webhook/1.0',
            'X-MeshForge-Event': event.event_type,
            'X-MeshForge-Timestamp': event.timestamp,
        }
        headers.update(endpoint.headers)

        # Add HMAC signature if secret configured
        if endpoint.secret:
            import hmac
            import hashlib
            signature = hmac.new(
                endpoint.secret.encode(),
                payload,
                hashlib.sha256
            ).hexdigest()
            headers['X-MeshForge-Signature'] = f'sha256={signature}'

        for attempt in range(endpoint.retry_count):
            try:
                req = urllib.request.Request(
                    endpoint.url,
                    data=payload,
                    headers=headers,
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=endpoint.timeout_seconds) as response:
                    if response.status < 300:
                        logger.debug(f"Webhook delivered to {endpoint.name}")
                        return True

            except urllib.error.HTTPError as e:
                logger.warning(
                    f"Webhook HTTP error ({endpoint.name}): {e.code} "
                    f"(attempt {attempt + 1}/{endpoint.retry_count})"
                )
            except urllib.error.URLError as e:
                logger.warning(
                    f"Webhook URL error ({endpoint.name}): {e.reason} "
                    f"(attempt {attempt + 1}/{endpoint.retry_count})"
                )
            except Exception as e:
                logger.warning(
                    f"Webhook error ({endpoint.name}): {e} "
                    f"(attempt {attempt + 1}/{endpoint.retry_count})"
                )

            # Exponential backoff
            if attempt < endpoint.retry_count - 1:
                import time
                time.sleep(2 ** attempt)

        logger.error(f"Webhook delivery failed after {endpoint.retry_count} attempts: {endpoint.name}")
        return False


# Singleton instance
_webhook_manager: Optional[WebhookManager] = None


def get_webhook_manager() -> WebhookManager:
    """Get the global webhook manager instance."""
    global _webhook_manager
    if _webhook_manager is None:
        _webhook_manager = WebhookManager()
        _webhook_manager.start()
    return _webhook_manager


def emit_event(event_type: EventType, data: Dict[str, Any]):
    """Convenience function to emit an event."""
    get_webhook_manager().emit(event_type, data)


# Convenience functions for common events
def emit_node_online(node_id: str, node_name: str, **kwargs):
    """Emit node online event."""
    emit_event(EventType.NODE_ONLINE, {
        'node_id': node_id,
        'node_name': node_name,
        **kwargs
    })


def emit_node_offline(node_id: str, node_name: str, last_seen: str = None, **kwargs):
    """Emit node offline event."""
    emit_event(EventType.NODE_OFFLINE, {
        'node_id': node_id,
        'node_name': node_name,
        'last_seen': last_seen,
        **kwargs
    })


def emit_message_received(from_node: str, to_node: str, message: str, **kwargs):
    """Emit message received event."""
    emit_event(EventType.MESSAGE_RECEIVED, {
        'from_node': from_node,
        'to_node': to_node,
        'message': message,
        **kwargs
    })


def emit_position_update(node_id: str, lat: float, lon: float, alt: float = 0, **kwargs):
    """Emit position update event."""
    emit_event(EventType.POSITION_UPDATE, {
        'node_id': node_id,
        'latitude': lat,
        'longitude': lon,
        'altitude': alt,
        **kwargs
    })


def emit_alert(alert_type: EventType, node_id: str, message: str, **kwargs):
    """Emit alert event."""
    emit_event(alert_type, {
        'node_id': node_id,
        'message': message,
        **kwargs
    })

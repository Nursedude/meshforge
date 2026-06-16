"""
Mesh Alert Engine — Evaluates mesh telemetry for alertable conditions.

Hooks into MeshForge's existing MQTTNodelessSubscriber via callbacks and
emits AlertEvent to the event_bus. Optionally delegates to meshing_around's
alert models when available for type compatibility.

All features degrade gracefully if meshing_around is not installed.
"""

import logging
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional

from utils.common import SettingsManager
from utils.event_bus import AlertEvent, emit_alert
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Add meshing_around to path if available
_MA_PATH = "/opt/meshing_around_meshforge"
if _MA_PATH not in sys.path:
    sys.path.insert(0, _MA_PATH)

# Import meshing_around alert types for reference (optional)
_AlertType, _Alert, _HAS_MA_MODELS = safe_import(
    'meshing_around_clients.core.models', 'AlertType', 'Alert'
)

# Default configuration
_DEFAULT_CONFIG = {
    "enabled": True,
    "battery_threshold": 20,
    "disconnect_timeout_minutes": 30,
    "cooldown_seconds": 300,
    "emergency_keywords": ["help", "emergency", "sos", "mayday", "rescue"],
    "noisy_node_threshold": 10,  # messages per minute
    "snr_threshold": -10.0,
    "enabled_types": [
        "battery", "emergency", "new_node", "disconnect",
        "noisy_node", "snr",
    ],
}

# Alert history bounds
_ALERT_HISTORY_MAX = 200


class MeshAlertEngine:
    """Evaluates mesh network data for alertable conditions.

    Hooks into MQTTNodelessSubscriber callbacks to receive node and message
    updates, evaluates alert conditions with per-node per-type cooldowns,
    and emits AlertEvent via MeshForge's event_bus.
    """

    def __init__(self):
        self._settings = SettingsManager("mesh_alerts", defaults=_DEFAULT_CONFIG)
        self._active_alerts: deque = deque(maxlen=_ALERT_HISTORY_MAX)
        self._cooldowns: Dict[str, float] = {}
        self._cooldown_lock = threading.Lock()
        self._message_rates: Dict[str, List[float]] = {}
        self._known_nodes: Dict[str, float] = {}  # node_id -> last_seen timestamp
        self._stop_event = threading.Event()
        self._disconnect_thread: Optional[threading.Thread] = None
        self._subscriber = None
        self._started = False

    @property
    def config(self) -> dict:
        return self._settings.all()

    def _is_type_enabled(self, alert_type: str) -> bool:
        """Check if an alert type is enabled in config."""
        if not self._settings.get("enabled", True):
            return False
        enabled = self._settings.get("enabled_types", _DEFAULT_CONFIG["enabled_types"])
        return alert_type in enabled

    def _is_cooled_down(self, node_id: str, alert_type: str) -> bool:
        """Check if alert should be suppressed (still in cooldown).

        Returns True if suppressed, False if the alert should fire.
        """
        key = f"{node_id}|{alert_type}"
        now = time.monotonic()
        cooldown = self._settings.get("cooldown_seconds", 300)
        with self._cooldown_lock:
            last = self._cooldowns.get(key)
            if last is not None and (now - last) < cooldown:
                return True
            self._cooldowns[key] = now
            # Prune stale entries
            if len(self._cooldowns) > 1000:
                cutoff = now - (cooldown * 3)
                self._cooldowns = {
                    k: v for k, v in self._cooldowns.items() if v > cutoff
                }
        return False

    def _emit(self, alert_type: str, title: str, message: str,
              severity: int, source_node: str = "",
              metadata: Optional[Dict] = None) -> None:
        """Create and emit an alert event."""
        event = AlertEvent(
            alert_type=alert_type,
            title=title,
            message=message,
            severity=severity,
            source_node=source_node,
            metadata=metadata,
        )
        self._active_alerts.append(event)
        emit_alert(
            alert_type=alert_type,
            title=title,
            message=message,
            severity=severity,
            source_node=source_node,
            metadata=metadata,
        )
        logger.info("Mesh alert [%s]: %s — %s", alert_type, title, message)

    # ── Evaluators ──────────────────────────────────────────────

    def _evaluate_battery(self, node) -> None:
        """Check node battery level against threshold."""
        if not self._is_type_enabled("battery"):
            return
        battery = getattr(node, 'battery_level', None)
        if battery is None:
            return
        threshold = self._settings.get("battery_threshold", 20)
        if battery > threshold:
            return
        node_id = getattr(node, 'node_id', str(node))
        if self._is_cooled_down(node_id, "battery"):
            return
        name = getattr(node, 'long_name', '') or getattr(node, 'short_name', '') or node_id
        self._emit(
            alert_type="battery",
            title=f"Low battery: {name}",
            message=f"{name} at {battery}% (threshold: {threshold}%)",
            severity=3 if battery < 10 else 2,
            source_node=node_id,
            metadata={"battery_level": battery},
        )

    def _evaluate_snr(self, node) -> None:
        """Check node SNR against threshold."""
        if not self._is_type_enabled("snr"):
            return
        snr = getattr(node, 'snr', None)
        if snr is None:
            return
        threshold = self._settings.get("snr_threshold", -10.0)
        if snr >= threshold:
            return
        node_id = getattr(node, 'node_id', str(node))
        if self._is_cooled_down(node_id, "snr"):
            return
        name = getattr(node, 'long_name', '') or getattr(node, 'short_name', '') or node_id
        self._emit(
            alert_type="snr",
            title=f"Low SNR: {name}",
            message=f"{name} SNR {snr:.1f} dB (threshold: {threshold} dB)",
            severity=2,
            source_node=node_id,
            metadata={"snr": snr},
        )

    def _evaluate_emergency(self, message) -> None:
        """Check message text for emergency keywords."""
        if not self._is_type_enabled("emergency"):
            return
        text = getattr(message, 'text', '') or ''
        if not text:
            return
        keywords = self._settings.get("emergency_keywords",
                                       _DEFAULT_CONFIG["emergency_keywords"])
        text_lower = text.lower()
        matched = [kw for kw in keywords if kw.lower() in text_lower]
        if not matched:
            return
        sender = (getattr(message, 'sender_id', '') or
                  getattr(message, 'from_id', '') or
                  getattr(message, 'node_id', ''))
        sender_name = getattr(message, 'sender_name', '') or sender
        if self._is_cooled_down(sender, "emergency"):
            return
        self._emit(
            alert_type="emergency",
            title=f"Emergency: {sender_name}",
            message=f'{sender_name} "{text[:80]}"',
            severity=4,
            source_node=sender,
            metadata={"keywords": matched, "text": text[:200]},
        )

    def _evaluate_new_node(self, node) -> None:
        """Alert on newly discovered nodes."""
        if not self._is_type_enabled("new_node"):
            return
        node_id = getattr(node, 'node_id', str(node))
        if node_id in self._known_nodes:
            return
        self._known_nodes[node_id] = time.time()
        # Don't alert during initial population (first 30 seconds)
        if self._started and (time.time() - self._start_time) < 30:
            return
        if self._is_cooled_down(node_id, "new_node"):
            return
        name = getattr(node, 'long_name', '') or getattr(node, 'short_name', '') or node_id
        self._emit(
            alert_type="new_node",
            title=f"New node: {name}",
            message=f"{name} ({node_id}) joined the mesh",
            severity=1,
            source_node=node_id,
        )

    def _evaluate_noisy_node(self, message) -> None:
        """Track message rates and alert on noisy nodes."""
        if not self._is_type_enabled("noisy_node"):
            return
        sender = (getattr(message, 'sender_id', '') or
                  getattr(message, 'from_id', '') or
                  getattr(message, 'node_id', ''))
        if not sender:
            return
        now = time.time()
        if sender not in self._message_rates:
            self._message_rates[sender] = []
        timestamps = self._message_rates[sender]
        timestamps.append(now)
        # Keep only last 60 seconds of timestamps
        cutoff = now - 60
        self._message_rates[sender] = [t for t in timestamps if t > cutoff]
        rate = len(self._message_rates[sender])
        threshold = self._settings.get("noisy_node_threshold", 10)
        if rate < threshold:
            return
        if self._is_cooled_down(sender, "noisy_node"):
            return
        sender_name = (getattr(message, 'sender_name', '') or
                       getattr(message, 'long_name', '') or sender)
        self._emit(
            alert_type="noisy_node",
            title=f"Noisy node: {sender_name}",
            message=f"{sender_name} sent {rate} messages in 60s (threshold: {threshold})",
            severity=2,
            source_node=sender,
            metadata={"rate_per_minute": rate},
        )

    def _disconnect_check_loop(self) -> None:
        """Periodically check for nodes that haven't been heard from."""
        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=30):
                break
            if not self._is_type_enabled("disconnect"):
                continue
            timeout_min = self._settings.get("disconnect_timeout_minutes", 30)
            timeout_sec = timeout_min * 60
            now = time.time()
            for node_id, last_seen in list(self._known_nodes.items()):
                if (now - last_seen) > timeout_sec:
                    if self._is_cooled_down(node_id, "disconnect"):
                        continue
                    self._emit(
                        alert_type="disconnect",
                        title=f"Node disconnected: {node_id}",
                        message=f"{node_id} not heard for {timeout_min} minutes",
                        severity=2,
                        source_node=node_id,
                        metadata={"last_seen": last_seen},
                    )

    # ── MQTT Subscriber Integration ─────────────────────────────

    def _on_node_update(self, node) -> None:
        """Callback for MQTTNodelessSubscriber node updates."""
        node_id = getattr(node, 'node_id', str(node))
        self._known_nodes[node_id] = time.time()
        self._evaluate_battery(node)
        self._evaluate_snr(node)
        self._evaluate_new_node(node)

    def _on_message(self, message) -> None:
        """Callback for MQTTNodelessSubscriber messages."""
        self._evaluate_emergency(message)
        self._evaluate_noisy_node(message)

    def attach_subscriber(self, mqtt_subscriber) -> None:
        """Hook into an MQTTNodelessSubscriber for live alert evaluation."""
        if mqtt_subscriber is None:
            return
        try:
            mqtt_subscriber.register_node_callback(self._on_node_update)
            mqtt_subscriber.register_message_callback(self._on_message)
            logger.info("Alert engine attached to MQTT subscriber")
        except Exception as e:
            logger.warning("Failed to attach alert engine to subscriber: %s", e)

    def start(self) -> None:
        """Start the alert engine (disconnect checker thread)."""
        if self._started:
            return
        self._started = True
        self._start_time = time.time()
        self._stop_event.clear()
        self._disconnect_thread = threading.Thread(
            target=self._disconnect_check_loop,
            daemon=True,
            name="mesh-alert-disconnect",
        )
        self._disconnect_thread.start()
        logger.info("Mesh alert engine started")

    def stop(self) -> None:
        """Stop the alert engine."""
        self._stop_event.set()
        if self._disconnect_thread and self._disconnect_thread.is_alive():
            self._disconnect_thread.join(timeout=5)
        self._started = False
        logger.info("Mesh alert engine stopped")

    # ── Public API ──────────────────────────────────────────────

    def get_active_alerts(self) -> List[AlertEvent]:
        """Get recent unacknowledged alerts."""
        return [a for a in self._active_alerts if not a.acknowledged]

    def get_all_alerts(self, limit: int = 50) -> List[AlertEvent]:
        """Get recent alerts (acknowledged and unacknowledged)."""
        return list(self._active_alerts)[-limit:]

    def acknowledge_all(self) -> int:
        """Mark all active alerts as acknowledged. Returns count."""
        count = 0
        for alert in self._active_alerts:
            if not alert.acknowledged:
                alert.acknowledged = True
                count += 1
        return count

    def update_config(self, key: str, value) -> bool:
        """Update a config value and save.

        Returns True iff the change was persisted to disk (``SettingsManager.save()``
        returns False on IOError/OSError). Callers must surface a False — a
        silently-unsaved alert setting (e.g. emergency keywords) is the #74 class.
        """
        self._settings.set(key, value)
        return self._settings.save()

    def get_alert_count_by_type(self) -> Dict[str, int]:
        """Get count of active alerts grouped by type."""
        counts: Dict[str, int] = {}
        for alert in self._active_alerts:
            if not alert.acknowledged:
                counts[alert.alert_type] = counts.get(alert.alert_type, 0) + 1
        return counts


# ── Module-level singleton ──────────────────────────────────────

_engine: Optional[MeshAlertEngine] = None
_engine_lock = threading.Lock()


def get_alert_engine() -> MeshAlertEngine:
    """Get or create the singleton MeshAlertEngine."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = MeshAlertEngine()
    return _engine

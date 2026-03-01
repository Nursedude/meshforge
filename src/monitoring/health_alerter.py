"""
Health Alerter — Predictive node health alerts from trending data.

Generates actionable alerts when node metrics (battery, SNR) show
declining trends, enabling proactive intervention before nodes go offline.

Works with data from NodeMonitor (node_monitor.py) and SignalTrend
(signal_trending.py) when available.

Usage:
    from monitoring.health_alerter import HealthAlerter

    alerter = HealthAlerter()
    alerts = alerter.check_nodes(nodes)
    for alert in alerts:
        print(f"[{alert.severity}] {alert.message}")
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HealthAlert:
    """A predictive health alert for a mesh node."""
    node_id: str
    node_name: str
    alert_type: str      # battery_low, battery_critical, signal_degrading, node_stale
    severity: str        # critical, warning, info
    message: str
    predicted_hours: Optional[float] = None  # Hours until predicted failure
    value: Optional[float] = None            # Current metric value
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for display/serialization."""
        return {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "message": self.message,
            "predicted_hours": self.predicted_hours,
            "value": self.value,
            "timestamp": self.timestamp,
        }


# Alert thresholds
BATTERY_WARNING_PCT = 20
BATTERY_CRITICAL_PCT = 10
SNR_DEGRADING_RATE_THRESHOLD = 1.0  # dB/hr — alert if degrading faster than this
STALE_HOURS = 2.0  # Hours without hearing from a previously active node


class HealthAlerter:
    """Generates predictive health alerts from node metrics.

    Checks battery levels, signal trends, and staleness to produce
    actionable alerts before nodes fail.
    """

    def __init__(self):
        # Deduplication: {(node_id, alert_type): expiry_timestamp}
        self._suppressed: Dict[tuple, float] = {}
        self._suppress_duration = 3600  # 1 hour

    def check_nodes(self, nodes: List[Any]) -> List[HealthAlert]:
        """Check a list of nodes and generate health alerts.

        Args:
            nodes: List of node objects or dicts with metrics.
                   Supports NodeInfo objects, GeoJSON feature dicts,
                   or plain dicts with id/name/battery/snr/last_heard keys.

        Returns:
            List of HealthAlert objects, sorted by severity (critical first).
        """
        alerts = []
        now = time.time()

        for node in nodes:
            node_data = self._normalize_node(node)
            if not node_data:
                continue

            node_id = node_data["id"]
            node_name = node_data["name"]

            # Battery checks
            battery = node_data.get("battery")
            if battery is not None:
                if battery <= BATTERY_CRITICAL_PCT:
                    alerts.append(HealthAlert(
                        node_id=node_id,
                        node_name=node_name,
                        alert_type="battery_critical",
                        severity="critical",
                        message=f'Node "{node_name}" battery critical at {battery}%',
                        value=battery,
                    ))
                elif battery <= BATTERY_WARNING_PCT:
                    alerts.append(HealthAlert(
                        node_id=node_id,
                        node_name=node_name,
                        alert_type="battery_low",
                        severity="warning",
                        message=f'Node "{node_name}" battery low at {battery}%',
                        value=battery,
                    ))

            # Signal degradation check
            snr = node_data.get("snr")
            trend_direction = node_data.get("trend_direction")
            trend_rate = node_data.get("trend_rate", 0)

            if trend_direction == "degrading" and abs(trend_rate) > SNR_DEGRADING_RATE_THRESHOLD:
                predicted_hours = None
                if snr is not None and trend_rate < 0:
                    # Predict time to reach -20 dB (near-unusable)
                    target_snr = -20.0
                    if snr > target_snr:
                        predicted_hours = round((snr - target_snr) / abs(trend_rate), 1)

                alerts.append(HealthAlert(
                    node_id=node_id,
                    node_name=node_name,
                    alert_type="signal_degrading",
                    severity="warning",
                    message=(
                        f'Node "{node_name}" signal degrading at '
                        f'{abs(trend_rate):.1f} dB/hr'
                        f' (SNR: {snr:.1f} dB)' if snr is not None else ''
                    ),
                    predicted_hours=predicted_hours,
                    value=snr,
                ))

            # Stale node check
            last_heard = node_data.get("last_heard")
            if last_heard is not None:
                hours_since = (now - last_heard) / 3600
                if hours_since > STALE_HOURS:
                    alerts.append(HealthAlert(
                        node_id=node_id,
                        node_name=node_name,
                        alert_type="node_stale",
                        severity="warning",
                        message=(
                            f'Node "{node_name}" not heard for '
                            f'{hours_since:.1f}h'
                        ),
                        value=hours_since,
                    ))

        # Deduplicate
        alerts = self._deduplicate(alerts)

        # Sort by severity: critical first, then warning, then info
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        alerts.sort(key=lambda a: severity_order.get(a.severity, 3))

        return alerts

    def _normalize_node(self, node) -> Optional[Dict[str, Any]]:
        """Normalize node data from various formats to a standard dict.

        Handles NodeInfo objects, GeoJSON feature dicts, and plain dicts.
        """
        if node is None:
            return None

        # GeoJSON feature
        if isinstance(node, dict) and "properties" in node:
            props = node["properties"]
            return {
                "id": props.get("id", ""),
                "name": props.get("name", props.get("id", "Unknown")),
                "battery": props.get("battery"),
                "snr": props.get("snr"),
                "last_heard": props.get("last_heard"),
                "trend_direction": props.get("trend_direction"),
                "trend_rate": props.get("trend_rate", 0),
            }

        # Plain dict
        if isinstance(node, dict):
            return {
                "id": node.get("id", node.get("node_id", "")),
                "name": node.get("name", node.get("long_name", "Unknown")),
                "battery": node.get("battery", node.get("battery_level")),
                "snr": node.get("snr"),
                "last_heard": node.get("last_heard"),
                "trend_direction": node.get("trend_direction"),
                "trend_rate": node.get("trend_rate", 0),
            }

        # Object with attributes (NodeInfo, etc.)
        node_id = getattr(node, 'node_id', '') or getattr(node, 'id', '')
        name = getattr(node, 'long_name', '') or getattr(node, 'name', '') or node_id

        # Extract battery from nested metrics
        battery = None
        metrics = getattr(node, 'metrics', None)
        if metrics:
            battery = getattr(metrics, 'battery_level', None)

        # Extract last_heard as timestamp
        last_heard = getattr(node, 'last_heard', None)
        if last_heard is not None and hasattr(last_heard, 'timestamp'):
            last_heard = last_heard.timestamp()

        return {
            "id": node_id,
            "name": name,
            "battery": battery,
            "snr": getattr(node, 'snr', None),
            "last_heard": last_heard,
            "trend_direction": getattr(node, 'trend_direction', None),
            "trend_rate": getattr(node, 'trend_rate', 0),
        }

    def _deduplicate(self, alerts: List[HealthAlert]) -> List[HealthAlert]:
        """Remove alerts that were recently generated for the same node+type."""
        now = time.time()
        result = []

        # Clean expired suppressions
        self._suppressed = {
            k: v for k, v in self._suppressed.items() if v > now
        }

        for alert in alerts:
            key = (alert.node_id, alert.alert_type)
            if key in self._suppressed:
                continue
            self._suppressed[key] = now + self._suppress_duration
            result.append(alert)

        return result

    def clear_suppressions(self) -> None:
        """Clear all suppressed alerts (for testing or manual reset)."""
        self._suppressed.clear()

"""
Prometheus Metrics Export for MeshForge.

Exports MeshForge metrics in Prometheus exposition format, enabling
integration with Grafana dashboards, alerting, and the broader
observability ecosystem.

Usage:
    from utils.metrics_export import PrometheusExporter, start_metrics_server

    # Option 1: Generate metrics string
    exporter = PrometheusExporter()
    metrics_text = exporter.export()
    print(metrics_text)

    # Option 2: Start HTTP server (for Prometheus scraping)
    server = start_metrics_server(port=9090)
    # Prometheus can now scrape http://localhost:9090/metrics

    # Option 3: Write to file for pushgateway or file-based collection
    exporter.write_to_file("/var/lib/meshforge/metrics.prom")

Reference:
    Prometheus exposition format:
    https://prometheus.io/docs/instrumenting/exposition_formats/

    NGINX Prometheus Exporter (inspiration):
    https://github.com/nginx/nginx-prometheus-exporter
"""

import http.server
import logging
import os
import socketserver
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        """Fallback for when utils.paths is not in Python path."""
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            return Path(f'/home/{sudo_user}')
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            return Path(f'/home/{logname}')
        return Path('/root')


# Metric type constants (Prometheus types)
COUNTER = "counter"
GAUGE = "gauge"
HISTOGRAM = "histogram"
SUMMARY = "summary"


@dataclass
class MetricDefinition:
    """Definition of a Prometheus metric."""
    name: str
    metric_type: str
    help_text: str
    labels: List[str]


# MeshForge metric definitions
METRICS = {
    # Health metrics
    "meshforge_service_healthy": MetricDefinition(
        name="meshforge_service_healthy",
        metric_type=GAUGE,
        help_text="Whether a service is healthy (1) or not (0)",
        labels=["service"],
    ),
    "meshforge_service_uptime_percent": MetricDefinition(
        name="meshforge_service_uptime_percent",
        metric_type=GAUGE,
        help_text="Service uptime percentage (0-100)",
        labels=["service"],
    ),
    "meshforge_service_latency_ms": MetricDefinition(
        name="meshforge_service_latency_ms",
        metric_type=GAUGE,
        help_text="Service health check latency in milliseconds",
        labels=["service"],
    ),
    "meshforge_service_consecutive_fails": MetricDefinition(
        name="meshforge_service_consecutive_fails",
        metric_type=GAUGE,
        help_text="Number of consecutive health check failures",
        labels=["service"],
    ),
    "meshforge_health_score": MetricDefinition(
        name="meshforge_health_score",
        metric_type=GAUGE,
        help_text="Overall network health score (0-100)",
        labels=["category"],
    ),

    # Message metrics
    "meshforge_messages_total": MetricDefinition(
        name="meshforge_messages_total",
        metric_type=COUNTER,
        help_text="Total messages processed",
        labels=["direction", "status"],
    ),
    "meshforge_message_queue_depth": MetricDefinition(
        name="meshforge_message_queue_depth",
        metric_type=GAUGE,
        help_text="Current message queue depth",
        labels=["status"],
    ),
    "meshforge_message_retries_total": MetricDefinition(
        name="meshforge_message_retries_total",
        metric_type=COUNTER,
        help_text="Total message retry attempts",
        labels=[],
    ),
    "meshforge_dead_letter_count": MetricDefinition(
        name="meshforge_dead_letter_count",
        metric_type=GAUGE,
        help_text="Messages in dead letter queue",
        labels=[],
    ),

    # Node metrics
    "meshforge_node_snr": MetricDefinition(
        name="meshforge_node_snr",
        metric_type=GAUGE,
        help_text="Node signal-to-noise ratio in dB",
        labels=["node_id"],
    ),
    "meshforge_node_rssi": MetricDefinition(
        name="meshforge_node_rssi",
        metric_type=GAUGE,
        help_text="Node received signal strength in dBm",
        labels=["node_id"],
    ),
    "meshforge_node_last_seen_seconds": MetricDefinition(
        name="meshforge_node_last_seen_seconds",
        metric_type=GAUGE,
        help_text="Seconds since node was last seen",
        labels=["node_id"],
    ),
    "meshforge_node_battery_percent": MetricDefinition(
        name="meshforge_node_battery_percent",
        metric_type=GAUGE,
        help_text="Node battery level percentage",
        labels=["node_id"],
    ),
    "meshforge_nodes_total": MetricDefinition(
        name="meshforge_nodes_total",
        metric_type=GAUGE,
        help_text="Total number of tracked nodes",
        labels=["state"],
    ),

    # Gateway metrics
    "meshforge_gateway_connections": MetricDefinition(
        name="meshforge_gateway_connections",
        metric_type=GAUGE,
        help_text="Number of active gateway connections",
        labels=["network"],
    ),
    "meshforge_gateway_reconnects_total": MetricDefinition(
        name="meshforge_gateway_reconnects_total",
        metric_type=COUNTER,
        help_text="Total reconnection attempts",
        labels=["network"],
    ),
    "meshforge_gateway_errors_total": MetricDefinition(
        name="meshforge_gateway_errors_total",
        metric_type=COUNTER,
        help_text="Total gateway errors",
        labels=["network", "error_type"],
    ),

    # System metrics
    "meshforge_info": MetricDefinition(
        name="meshforge_info",
        metric_type=GAUGE,
        help_text="MeshForge version and build info",
        labels=["version"],
    ),
    "meshforge_uptime_seconds": MetricDefinition(
        name="meshforge_uptime_seconds",
        metric_type=GAUGE,
        help_text="MeshForge process uptime in seconds",
        labels=[],
    ),
    "meshforge_last_scrape_timestamp": MetricDefinition(
        name="meshforge_last_scrape_timestamp",
        metric_type=GAUGE,
        help_text="Unix timestamp of last metrics collection",
        labels=[],
    ),
}


def _escape_label_value(value: str) -> str:
    """Escape special characters in Prometheus label values.

    Per Prometheus exposition format, label values must escape:
    - Backslash (\\) -> \\\\
    - Double quote (") -> \\"
    - Newline (\\n) -> \\n
    """
    # Order matters: escape backslash first to avoid double-escaping
    value = value.replace('\\', '\\\\')
    value = value.replace('"', '\\"')
    value = value.replace('\n', '\\n')
    return value


def _format_labels(labels: Dict[str, str]) -> str:
    """Format labels for Prometheus exposition format."""
    if not labels:
        return ""
    pairs = [f'{k}="{_escape_label_value(str(v))}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(pairs) + "}"


def _format_metric_line(name: str, value: float, labels: Dict[str, str] = None) -> str:
    """Format a single metric line."""
    label_str = _format_labels(labels or {})
    return f"{name}{label_str} {value}"


class PrometheusExporter:
    """
    Export MeshForge metrics in Prometheus format.

    Collects metrics from various MeshForge components and formats
    them for Prometheus scraping. Supports:

    - SharedHealthState for service health
    - MetricsHistory for node/network metrics
    - HealthScorer for health scores
    - PersistentMessageQueue for message stats

    Attributes:
        start_time: When the exporter was created (for uptime)
    """

    def __init__(self):
        """Initialize the Prometheus exporter."""
        self.start_time = time.time()
        self._collectors: List[Callable[[], List[str]]] = []
        self._custom_metrics: Dict[str, Tuple[float, Dict[str, str]]] = {}

        # Register built-in collectors
        self._register_builtin_collectors()

    def _register_builtin_collectors(self) -> None:
        """Register built-in metric collectors."""
        self._collectors.append(self._collect_info_metrics)
        self._collectors.append(self._collect_health_metrics)
        self._collectors.append(self._collect_message_metrics)
        self._collectors.append(self._collect_node_metrics)
        self._collectors.append(self._collect_gateway_metrics)

    def register_collector(self, collector: Callable[[], List[str]]) -> None:
        """
        Register a custom metric collector.

        Args:
            collector: Function that returns list of metric lines
        """
        self._collectors.append(collector)

    def set_custom_metric(
        self,
        name: str,
        value: float,
        labels: Dict[str, str] = None,
    ) -> None:
        """
        Set a custom metric value.

        Args:
            name: Metric name
            value: Metric value
            labels: Optional labels
        """
        self._custom_metrics[name] = (value, labels or {})

    def _collect_info_metrics(self) -> List[str]:
        """Collect MeshForge info metrics."""
        lines = []

        # Version info
        try:
            from __version__ import __version__
        except ImportError:
            __version__ = "unknown"

        defn = METRICS["meshforge_info"]
        lines.append(f"# HELP {defn.name} {defn.help_text}")
        lines.append(f"# TYPE {defn.name} {defn.metric_type}")
        lines.append(_format_metric_line(defn.name, 1, {"version": __version__}))

        # Uptime
        defn = METRICS["meshforge_uptime_seconds"]
        lines.append(f"# HELP {defn.name} {defn.help_text}")
        lines.append(f"# TYPE {defn.name} {defn.metric_type}")
        uptime = time.time() - self.start_time
        lines.append(_format_metric_line(defn.name, uptime))

        # Last scrape timestamp
        defn = METRICS["meshforge_last_scrape_timestamp"]
        lines.append(f"# HELP {defn.name} {defn.help_text}")
        lines.append(f"# TYPE {defn.name} {defn.metric_type}")
        lines.append(_format_metric_line(defn.name, time.time()))

        return lines

    def _collect_health_metrics(self) -> List[str]:
        """Collect service health metrics from SharedHealthState."""
        lines = []

        try:
            from utils.shared_health_state import SharedHealthState
            state = SharedHealthState()
            services = state.get_all_services()
            state.close()

            if not services:
                return lines

            # Service healthy gauge
            defn = METRICS["meshforge_service_healthy"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            for svc in services:
                healthy = 1 if svc.state.value == "healthy" else 0
                lines.append(_format_metric_line(defn.name, healthy, {"service": svc.service}))

            # Uptime percentage
            defn = METRICS["meshforge_service_uptime_percent"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            for svc in services:
                lines.append(_format_metric_line(defn.name, svc.uptime_pct, {"service": svc.service}))

            # Latency
            defn = METRICS["meshforge_service_latency_ms"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            for svc in services:
                lines.append(_format_metric_line(defn.name, svc.latency_ms, {"service": svc.service}))

            # Consecutive failures
            defn = METRICS["meshforge_service_consecutive_fails"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            for svc in services:
                lines.append(_format_metric_line(defn.name, svc.consecutive_fails, {"service": svc.service}))

        except ImportError:
            logger.debug("SharedHealthState not available")
        except Exception as e:
            logger.debug(f"Error collecting health metrics: {e}")

        # Health scores from HealthScorer
        try:
            from utils.health_score import HealthScorer
            scorer = HealthScorer()
            snapshot = scorer.get_snapshot()

            defn = METRICS["meshforge_health_score"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, snapshot.overall_score, {"category": "overall"}))
            lines.append(_format_metric_line(defn.name, snapshot.connectivity_score, {"category": "connectivity"}))
            lines.append(_format_metric_line(defn.name, snapshot.performance_score, {"category": "performance"}))
            lines.append(_format_metric_line(defn.name, snapshot.reliability_score, {"category": "reliability"}))
            lines.append(_format_metric_line(defn.name, snapshot.freshness_score, {"category": "freshness"}))

        except ImportError:
            logger.debug("HealthScorer not available")
        except Exception as e:
            logger.debug(f"Error collecting health scores: {e}")

        return lines

    def _collect_message_metrics(self) -> List[str]:
        """Collect message queue metrics from PersistentMessageQueue."""
        lines = []

        try:
            from gateway.message_queue import PersistentMessageQueue
            queue = PersistentMessageQueue()
            stats = queue.get_stats()

            # Queue depth by status
            defn = METRICS["meshforge_message_queue_depth"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("pending", 0), {"status": "pending"}))
            lines.append(_format_metric_line(defn.name, stats.get("in_progress", 0), {"status": "in_progress"}))

            # Total messages
            defn = METRICS["meshforge_messages_total"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(
                defn.name, stats.get("enqueued", 0),
                {"direction": "incoming", "status": "enqueued"}
            ))
            lines.append(_format_metric_line(
                defn.name, stats.get("delivered", 0),
                {"direction": "outgoing", "status": "delivered"}
            ))
            lines.append(_format_metric_line(
                defn.name, stats.get("failed", 0),
                {"direction": "outgoing", "status": "failed"}
            ))

            # Retries
            defn = METRICS["meshforge_message_retries_total"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("retried", 0)))

            # Dead letters
            defn = METRICS["meshforge_dead_letter_count"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("dead_letter", 0)))

        except ImportError:
            logger.debug("PersistentMessageQueue not available")
        except Exception as e:
            logger.debug(f"Error collecting message metrics: {e}")

        return lines

    def _collect_node_metrics(self) -> List[str]:
        """Collect node metrics from MapDataCollector and MetricsHistory."""
        lines = []
        node_count = 0
        nodes_with_gps = 0

        # Primary source: MapDataCollector (has actual node data)
        try:
            from utils.map_data_collector import MapDataCollector
            collector = MapDataCollector(enable_history=False)
            geojson = collector.collect(max_age_seconds=60)
            props = geojson.get("properties", {})
            node_count = props.get("total_nodes", 0)
            nodes_with_gps = props.get("nodes_with_position", 0)
            logger.debug(f"MapDataCollector: {node_count} total, {nodes_with_gps} with GPS")
        except ImportError:
            logger.debug("MapDataCollector not available")
        except Exception as e:
            logger.debug(f"Error collecting from MapDataCollector: {e}")

        # Fallback to MetricsHistory if MapDataCollector returned 0
        if node_count == 0:
            try:
                from utils.metrics_history import get_metrics_history, MetricType
                history = get_metrics_history()
                stats = history.get_statistics()
                node_count = stats.get("unique_nodes", 0)
            except ImportError:
                logger.debug("MetricsHistory not available")
            except Exception as e:
                logger.debug(f"Error collecting from MetricsHistory: {e}")

        # Emit node count metrics
        defn = METRICS["meshforge_nodes_total"]
        lines.append(f"# HELP {defn.name} {defn.help_text}")
        lines.append(f"# TYPE {defn.name} {defn.metric_type}")
        lines.append(_format_metric_line(defn.name, node_count, {"state": "tracked"}))
        if nodes_with_gps > 0:
            lines.append(_format_metric_line(defn.name, nodes_with_gps, {"state": "with_gps"}))

        # Per-node SNR/RSSI metrics from MetricsHistory
        try:
            from utils.metrics_history import get_metrics_history, MetricType
            history = get_metrics_history()

            # SNR metrics
            snr_added = False
            for point in history.get_recent(metric_type=MetricType.SNR, hours=1, limit=100):
                if point.node_id:
                    if not snr_added:
                        defn = METRICS["meshforge_node_snr"]
                        lines.append(f"# HELP {defn.name} {defn.help_text}")
                        lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                        snr_added = True
                    lines.append(_format_metric_line(defn.name, point.value, {"node_id": point.node_id}))

            # RSSI metrics
            rssi_added = False
            for point in history.get_recent(metric_type=MetricType.RSSI, hours=1, limit=100):
                if point.node_id:
                    if not rssi_added:
                        defn = METRICS["meshforge_node_rssi"]
                        lines.append(f"# HELP {defn.name} {defn.help_text}")
                        lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                        rssi_added = True
                    lines.append(_format_metric_line(defn.name, point.value, {"node_id": point.node_id}))

        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Error collecting SNR/RSSI metrics: {e}")

        return lines

    def _collect_gateway_metrics(self) -> List[str]:
        """Collect gateway-specific metrics from service status."""
        lines = []

        meshtastic_connected = 0
        rns_connected = 0

        # Check meshtasticd service status
        try:
            from utils.service_check import check_service
            mesh_status = check_service("meshtasticd")
            if mesh_status.available:
                meshtastic_connected = 1
        except ImportError:
            # Fallback: check if port 4403 is listening
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(("localhost", 4403))
                sock.close()
                if result == 0:
                    meshtastic_connected = 1
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Error checking meshtasticd: {e}")

        # Check rnsd service status
        try:
            from utils.service_check import check_service
            rns_status = check_service("rnsd")
            if rns_status.available:
                rns_connected = 1
        except ImportError:
            # Fallback: check if UDP port 37428 is in use (rnsd default)
            try:
                import subprocess
                result = subprocess.run(
                    ["ss", "-uln"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if "37428" in result.stdout:
                    rns_connected = 1
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Error checking rnsd: {e}")

        defn = METRICS["meshforge_gateway_connections"]
        lines.append(f"# HELP {defn.name} {defn.help_text}")
        lines.append(f"# TYPE {defn.name} {defn.metric_type}")
        lines.append(_format_metric_line(defn.name, meshtastic_connected, {"network": "meshtastic"}))
        lines.append(_format_metric_line(defn.name, rns_connected, {"network": "rns"}))

        return lines

    def _collect_custom_metrics(self) -> List[str]:
        """Collect custom metrics set via set_custom_metric()."""
        lines = []

        for name, (value, labels) in self._custom_metrics.items():
            lines.append(_format_metric_line(name, value, labels))

        return lines

    def export(self) -> str:
        """
        Generate complete Prometheus metrics output.

        Returns:
            String in Prometheus exposition format
        """
        all_lines = []

        # Add header comment
        all_lines.append(f"# MeshForge Prometheus Metrics")
        all_lines.append(f"# Generated at {datetime.now().isoformat()}")
        all_lines.append("")

        # Run all collectors
        for collector in self._collectors:
            try:
                lines = collector()
                if lines:
                    all_lines.extend(lines)
                    all_lines.append("")
            except Exception as e:
                logger.warning(f"Metric collector error: {e}")

        # Add custom metrics
        custom = self._collect_custom_metrics()
        if custom:
            all_lines.append("# Custom metrics")
            all_lines.extend(custom)
            all_lines.append("")

        return "\n".join(all_lines)

    def write_to_file(self, path: str) -> bool:
        """
        Write metrics to file for file-based collection.

        Useful for Prometheus pushgateway or node_exporter textfile collector.

        Args:
            path: Output file path

        Returns:
            True if written successfully
        """
        try:
            # Atomic write using temp file
            temp_path = f"{path}.tmp"
            content = self.export()

            with open(temp_path, 'w') as f:
                f.write(content)

            # Atomic rename
            os.replace(temp_path, path)
            logger.debug(f"Metrics written to {path}")
            return True

        except Exception as e:
            logger.error(f"Failed to write metrics: {e}")
            return False


class MetricsHTTPHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for Prometheus metrics endpoint and Grafana JSON API."""

    exporter: Optional[PrometheusExporter] = None

    def do_GET(self):
        """Handle GET request."""
        # CORS headers for Grafana
        if self.path == "/metrics":
            self._serve_metrics()
        elif self.path == "/health" or self.path == "/healthz":
            self._serve_health()
        # Grafana JSON API endpoints
        elif self.path == "/api/json/metrics":
            self._serve_json_metrics()
        elif self.path == "/api/json/nodes":
            self._serve_json_nodes()
        elif self.path == "/api/json/status":
            self._serve_json_status()
        elif self.path == "/":
            self._serve_index()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def _add_cors_headers(self):
        """Add CORS headers for Grafana."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _serve_index(self):
        """Serve index page with available endpoints."""
        content = """MeshForge Metrics Server

Endpoints:
  /metrics          - Prometheus format (for Prometheus scraper)
  /health           - Health check
  /api/json/metrics - JSON metrics (for Grafana Infinity plugin)
  /api/json/nodes   - Node data JSON
  /api/json/status  - System status JSON

Grafana Setup:
  1. Install 'Infinity' data source plugin
  2. Add data source: URL = http://localhost:9090
  3. Query: /api/json/metrics or /api/json/nodes
"""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def _serve_metrics(self):
        """Serve Prometheus metrics."""
        if self.exporter is None:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"Exporter not initialized")
            return

        try:
            content = self.exporter.export()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))

        except Exception as e:
            logger.error(f"Error serving metrics: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

    def _serve_json_metrics(self):
        """Serve metrics as JSON for Grafana Infinity plugin."""
        import json

        try:
            metrics = {}

            # Get node counts from MapDataCollector
            try:
                from utils.map_data_collector import MapDataCollector
                collector = MapDataCollector(enable_history=False)
                geojson = collector.collect(max_age_seconds=60)
                props = geojson.get('properties', {})
                metrics['nodes_total'] = props.get('total_nodes', 0)
                metrics['nodes_with_gps'] = props.get('nodes_with_position', 0)
                metrics['sources'] = props.get('sources', {})
            except Exception as e:
                logger.debug(f"MapDataCollector error: {e}")
                metrics['nodes_total'] = 0
                metrics['nodes_with_gps'] = 0

            # Get service status
            try:
                from utils.service_check import check_service
                mesh_status = check_service("meshtasticd")
                rns_status = check_service("rnsd")
                metrics['meshtasticd_running'] = 1 if mesh_status.available else 0
                metrics['rnsd_running'] = 1 if rns_status.available else 0
            except Exception:
                metrics['meshtasticd_running'] = 0
                metrics['rnsd_running'] = 0

            # Uptime
            if self.exporter:
                metrics['uptime_seconds'] = time.time() - self.exporter.start_time

            metrics['timestamp'] = datetime.now().isoformat()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._add_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(metrics, indent=2).encode('utf-8'))

        except Exception as e:
            logger.error(f"Error serving JSON metrics: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def _serve_json_nodes(self):
        """Serve node data as JSON for Grafana."""
        import json

        try:
            nodes = []

            try:
                from utils.map_data_collector import MapDataCollector
                collector = MapDataCollector(enable_history=False)
                geojson = collector.collect(max_age_seconds=60)

                for feature in geojson.get('features', []):
                    props = feature.get('properties', {})
                    coords = feature.get('geometry', {}).get('coordinates', [0, 0])
                    nodes.append({
                        'id': props.get('id', ''),
                        'name': props.get('name', ''),
                        'lat': coords[1] if len(coords) > 1 else 0,
                        'lon': coords[0] if len(coords) > 0 else 0,
                        'snr': props.get('snr'),
                        'battery': props.get('battery'),
                        'last_heard': props.get('last_heard'),
                        'online': props.get('online', False),
                    })
            except Exception as e:
                logger.debug(f"Node collection error: {e}")

            result = {
                'timestamp': datetime.now().isoformat(),
                'count': len(nodes),
                'nodes': nodes
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._add_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(result, indent=2).encode('utf-8'))

        except Exception as e:
            logger.error(f"Error serving JSON nodes: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def _serve_json_status(self):
        """Serve system status as JSON."""
        import json

        try:
            from __version__ import __version__
        except ImportError:
            __version__ = "unknown"

        status = {
            'version': __version__,
            'timestamp': datetime.now().isoformat(),
            'services': {},
        }

        # Check services
        for svc in ['meshtasticd', 'rnsd', 'mosquitto', 'grafana-server']:
            try:
                import subprocess
                result = subprocess.run(
                    ['systemctl', 'is-active', svc],
                    capture_output=True, text=True, timeout=5
                )
                status['services'][svc] = result.stdout.strip()
            except Exception:
                status['services'][svc] = 'unknown'

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(status, indent=2).encode('utf-8'))

    def _serve_health(self):
        """Serve health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


class MetricsServer:
    """
    HTTP server for Prometheus metrics scraping.

    Starts a simple HTTP server that serves metrics at /metrics endpoint.

    Attributes:
        port: Server port
        exporter: PrometheusExporter instance
    """

    def __init__(self, port: int = 9090, exporter: PrometheusExporter = None):
        """
        Initialize metrics server.

        Args:
            port: Port to listen on (default: 9090)
            exporter: PrometheusExporter instance (creates one if not provided)
        """
        self.port = port
        self.exporter = exporter or PrometheusExporter()
        self._server: Optional[socketserver.TCPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """
        Start the metrics server.

        Returns:
            True if started successfully
        """
        try:
            # Create handler class with exporter reference
            handler_class = type(
                'MetricsHandler',
                (MetricsHTTPHandler,),
                {'exporter': self.exporter}
            )

            self._server = socketserver.TCPServer(("", self.port), handler_class)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()

            logger.info(f"Prometheus metrics server started on port {self.port}")
            logger.info(f"Metrics available at http://localhost:{self.port}/metrics")
            return True

        except Exception as e:
            logger.error(f"Failed to start metrics server: {e}")
            return False

    def stop(self):
        """Stop the metrics server."""
        if self._server:
            self._server.shutdown()
            self._server = None
            logger.info("Prometheus metrics server stopped")

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._thread is not None and self._thread.is_alive()


def start_metrics_server(port: int = 9090, exporter: PrometheusExporter = None) -> MetricsServer:
    """
    Start a metrics server (convenience function).

    Args:
        port: Port to listen on
        exporter: Optional PrometheusExporter instance

    Returns:
        Running MetricsServer instance
    """
    server = MetricsServer(port=port, exporter=exporter)
    server.start()
    return server


# File-based metrics export for node_exporter textfile collector
def setup_textfile_exporter(
    output_dir: str = None,
    interval_seconds: int = 15,
) -> threading.Thread:
    """
    Start background thread that writes metrics to textfile for node_exporter.

    This is an alternative to running an HTTP server. The node_exporter
    textfile collector can pick up metrics from a directory.

    Args:
        output_dir: Directory for metrics files (default: /var/lib/node_exporter/textfile_collector)
        interval_seconds: How often to update the file

    Returns:
        Background thread (daemon=True, already started)

    Usage:
        # In MeshForge startup
        setup_textfile_exporter()

        # node_exporter will pick up metrics from the file
    """
    if output_dir is None:
        output_dir = "/var/lib/node_exporter/textfile_collector"

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    metrics_file = output_path / "meshforge.prom"

    exporter = PrometheusExporter()

    def export_loop():
        while True:
            try:
                exporter.write_to_file(str(metrics_file))
            except Exception as e:
                logger.debug(f"Textfile export error: {e}")
            time.sleep(interval_seconds)

    thread = threading.Thread(target=export_loop, daemon=True)
    thread.start()

    logger.info(f"Textfile metrics exporter started: {metrics_file}")
    return thread

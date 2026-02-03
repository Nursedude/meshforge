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


# =============================================================================
# INFLUXDB EXPORT
# =============================================================================

class InfluxDBExporter:
    """
    Export MeshForge metrics to InfluxDB.

    Supports both InfluxDB 1.x and 2.x APIs, with options for
    HTTP or UDP transport.

    Usage:
        # InfluxDB 2.x with token auth
        exporter = InfluxDBExporter(
            url="http://localhost:8086",
            token="your-token",
            org="meshforge",
            bucket="metrics"
        )
        exporter.write_metrics()

        # InfluxDB 1.x with basic auth
        exporter = InfluxDBExporter(
            url="http://localhost:8086",
            database="meshforge",
            username="admin",
            password="admin"
        )
        exporter.write_metrics()

        # UDP transport (InfluxDB 1.x only)
        exporter = InfluxDBExporter(
            host="localhost",
            udp_port=8089,
            database="meshforge"
        )
        exporter.write_metrics()

    InfluxDB Line Protocol Reference:
        https://docs.influxdata.com/influxdb/latest/reference/syntax/line-protocol/
    """

    def __init__(
        self,
        url: str = None,
        host: str = "localhost",
        http_port: int = 8086,
        udp_port: int = None,
        database: str = "meshforge",
        bucket: str = None,
        org: str = None,
        token: str = None,
        username: str = None,
        password: str = None,
        precision: str = "s",
        batch_size: int = 100,
        flush_interval: int = 10,
    ):
        """
        Initialize InfluxDB exporter.

        Args:
            url: Full URL to InfluxDB (e.g., http://localhost:8086)
            host: InfluxDB host (if url not provided)
            http_port: HTTP API port (default: 8086)
            udp_port: UDP port for line protocol (enables UDP mode)
            database: Database name (InfluxDB 1.x)
            bucket: Bucket name (InfluxDB 2.x)
            org: Organization (InfluxDB 2.x)
            token: API token (InfluxDB 2.x)
            username: Username (InfluxDB 1.x)
            password: Password (InfluxDB 1.x)
            precision: Time precision (ns, us, ms, s)
            batch_size: Points to batch before writing
            flush_interval: Seconds between flushes
        """
        self._url = url or f"http://{host}:{http_port}"
        self._host = host
        self._udp_port = udp_port
        self._database = database
        self._bucket = bucket or database
        self._org = org
        self._token = token
        self._username = username
        self._password = password
        self._precision = precision
        self._batch_size = batch_size
        self._flush_interval = flush_interval

        # Batch buffer
        self._batch: List[str] = []
        self._batch_lock = threading.Lock()
        self._last_flush = time.time()

        # Background flush thread
        self._running = False
        self._flush_thread: Optional[threading.Thread] = None

        # Determine API version
        self._is_v2 = bool(token and org)

        # UDP socket for UDP mode
        self._udp_socket = None
        if udp_port:
            import socket
            self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _format_line_protocol(
        self,
        measurement: str,
        fields: Dict[str, Union[float, int, str, bool]],
        tags: Dict[str, str] = None,
        timestamp: int = None,
    ) -> str:
        """
        Format a single point in InfluxDB line protocol.

        Format: <measurement>,<tag_set> <field_set> [timestamp]

        Args:
            measurement: Measurement name
            fields: Field key-value pairs
            tags: Optional tag key-value pairs
            timestamp: Optional Unix timestamp (nanoseconds)

        Returns:
            Line protocol string
        """
        # Escape special characters
        def escape_tag(value: str) -> str:
            return value.replace(',', r'\,').replace('=', r'\=').replace(' ', r'\ ')

        def escape_field_key(value: str) -> str:
            return value.replace(',', r'\,').replace('=', r'\=').replace(' ', r'\ ')

        def format_field_value(value: Any) -> str:
            if isinstance(value, bool):
                return "true" if value else "false"
            elif isinstance(value, int):
                return f"{value}i"
            elif isinstance(value, float):
                return f"{value}"
            elif isinstance(value, str):
                # String fields need quotes
                escaped = value.replace('\\', '\\\\').replace('"', '\\"')
                return f'"{escaped}"'
            return str(value)

        # Build line
        line = escape_tag(measurement)

        # Add tags
        if tags:
            tag_pairs = [f"{escape_tag(k)}={escape_tag(str(v))}" for k, v in sorted(tags.items())]
            if tag_pairs:
                line += "," + ",".join(tag_pairs)

        # Add space before fields
        line += " "

        # Add fields
        field_pairs = [f"{escape_field_key(k)}={format_field_value(v)}" for k, v in fields.items()]
        line += ",".join(field_pairs)

        # Add timestamp if provided
        if timestamp:
            line += f" {timestamp}"

        return line

    def _get_timestamp(self) -> int:
        """Get current timestamp in specified precision."""
        now = time.time()
        if self._precision == "ns":
            return int(now * 1_000_000_000)
        elif self._precision == "us":
            return int(now * 1_000_000)
        elif self._precision == "ms":
            return int(now * 1_000)
        else:  # seconds
            return int(now)

    def write_point(
        self,
        measurement: str,
        fields: Dict[str, Union[float, int, str, bool]],
        tags: Dict[str, str] = None,
        timestamp: int = None,
    ) -> None:
        """
        Write a single point to InfluxDB (batched).

        Args:
            measurement: Measurement name
            fields: Field key-value pairs
            tags: Optional tag key-value pairs
            timestamp: Optional Unix timestamp
        """
        if not timestamp:
            timestamp = self._get_timestamp()

        line = self._format_line_protocol(measurement, fields, tags, timestamp)

        with self._batch_lock:
            self._batch.append(line)
            if len(self._batch) >= self._batch_size:
                self._flush_batch()

    def _flush_batch(self) -> bool:
        """Flush the current batch to InfluxDB."""
        with self._batch_lock:
            if not self._batch:
                return True

            lines = list(self._batch)
            self._batch.clear()

        payload = "\n".join(lines)

        # UDP transport
        if self._udp_socket:
            return self._write_udp(payload)

        # HTTP transport
        return self._write_http(payload)

    def _write_udp(self, payload: str) -> bool:
        """Write payload via UDP."""
        try:
            self._udp_socket.sendto(
                payload.encode('utf-8'),
                (self._host, self._udp_port)
            )
            return True
        except Exception as e:
            logger.error(f"InfluxDB UDP write failed: {e}")
            return False

    def _write_http(self, payload: str) -> bool:
        """Write payload via HTTP API."""
        import urllib.request
        import urllib.error

        try:
            if self._is_v2:
                # InfluxDB 2.x API
                url = f"{self._url}/api/v2/write?org={self._org}&bucket={self._bucket}&precision={self._precision}"
                headers = {
                    "Authorization": f"Token {self._token}",
                    "Content-Type": "text/plain; charset=utf-8",
                }
            else:
                # InfluxDB 1.x API
                url = f"{self._url}/write?db={self._database}&precision={self._precision}"
                headers = {"Content-Type": "text/plain; charset=utf-8"}

            request = urllib.request.Request(
                url,
                data=payload.encode('utf-8'),
                headers=headers,
                method='POST'
            )

            # Add basic auth for 1.x
            if self._username and self._password:
                import base64
                credentials = base64.b64encode(
                    f"{self._username}:{self._password}".encode()
                ).decode()
                request.add_header("Authorization", f"Basic {credentials}")

            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status < 300:
                    return True
                logger.warning(f"InfluxDB write returned {response.status}")
                return False

        except urllib.error.HTTPError as e:
            logger.error(f"InfluxDB HTTP error: {e.code} - {e.read().decode()}")
            return False
        except Exception as e:
            logger.error(f"InfluxDB write failed: {e}")
            return False

    def write_metrics(self) -> bool:
        """
        Collect and write all MeshForge metrics to InfluxDB.

        Returns:
            True if write successful
        """
        timestamp = self._get_timestamp()
        success = True

        # Version info
        try:
            from __version__ import __version__
            version = __version__
        except ImportError:
            version = "unknown"

        self.write_point(
            "meshforge_info",
            {"value": 1},
            {"version": version},
            timestamp
        )

        # Service health
        try:
            from utils.service_check import check_service
            for service in ["meshtasticd", "rnsd", "mosquitto"]:
                try:
                    status = check_service(service)
                    self.write_point(
                        "meshforge_service_healthy",
                        {
                            "healthy": 1 if status.available else 0,
                        },
                        {"service": service},
                        timestamp
                    )
                except Exception:
                    pass
        except ImportError:
            pass

        # Node counts
        try:
            from utils.map_data_collector import MapDataCollector
            collector = MapDataCollector(enable_history=False)
            geojson = collector.collect(max_age_seconds=60)
            props = geojson.get("properties", {})

            self.write_point(
                "meshforge_nodes",
                {
                    "total": props.get("total_nodes", 0),
                    "with_gps": props.get("nodes_with_position", 0),
                },
                {},
                timestamp
            )

            # Per-node metrics
            for feature in geojson.get("features", []):
                node_props = feature.get("properties", {})
                node_id = node_props.get("id", "")
                if not node_id:
                    continue

                fields = {}
                if node_props.get("snr") is not None:
                    fields["snr"] = float(node_props["snr"])
                if node_props.get("rssi") is not None:
                    fields["rssi"] = int(node_props["rssi"])
                if node_props.get("battery") is not None:
                    fields["battery"] = int(node_props["battery"])

                if fields:
                    self.write_point(
                        "meshforge_node",
                        fields,
                        {"node_id": node_id, "name": node_props.get("name", "")},
                        timestamp
                    )

        except ImportError:
            logger.debug("MapDataCollector not available for InfluxDB export")
        except Exception as e:
            logger.debug(f"Error collecting node metrics: {e}")

        # Message queue stats
        try:
            from gateway.message_queue import PersistentMessageQueue
            queue = PersistentMessageQueue()
            stats = queue.get_stats()

            self.write_point(
                "meshforge_messages",
                {
                    "pending": stats.get("pending", 0),
                    "delivered": stats.get("delivered", 0),
                    "failed": stats.get("failed", 0),
                    "retried": stats.get("retried", 0),
                },
                {},
                timestamp
            )
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Error collecting message metrics: {e}")

        # Flush remaining batch
        if not self._flush_batch():
            success = False

        return success

    def start_background_export(self, interval: int = None) -> None:
        """
        Start background thread for periodic metric export.

        Args:
            interval: Export interval in seconds (default: flush_interval)
        """
        if self._running:
            return

        self._running = True
        interval = interval or self._flush_interval

        def export_loop():
            while self._running:
                try:
                    self.write_metrics()
                except Exception as e:
                    logger.debug(f"InfluxDB export error: {e}")
                time.sleep(interval)

        self._flush_thread = threading.Thread(target=export_loop, daemon=True)
        self._flush_thread.start()
        logger.info(f"InfluxDB exporter started (interval: {interval}s)")

    def stop(self) -> None:
        """Stop background export thread."""
        self._running = False
        if self._flush_thread:
            self._flush_thread.join(timeout=5)
            self._flush_thread = None

        # Flush remaining points
        self._flush_batch()

        # Close UDP socket
        if self._udp_socket:
            self._udp_socket.close()
            self._udp_socket = None

        logger.info("InfluxDB exporter stopped")

    def is_running(self) -> bool:
        """Check if background export is running."""
        return self._running

    def get_line_protocol_export(self) -> str:
        """
        Get all metrics as InfluxDB line protocol string.

        Useful for manual export or debugging.

        Returns:
            Multi-line string in InfluxDB line protocol format
        """
        lines = []
        timestamp = self._get_timestamp()

        # Build same metrics as write_metrics but return as string
        try:
            from __version__ import __version__
            version = __version__
        except ImportError:
            version = "unknown"

        lines.append(self._format_line_protocol(
            "meshforge_info", {"value": 1}, {"version": version}, timestamp
        ))

        # Service health
        try:
            from utils.service_check import check_service
            for service in ["meshtasticd", "rnsd", "mosquitto"]:
                try:
                    status = check_service(service)
                    lines.append(self._format_line_protocol(
                        "meshforge_service_healthy",
                        {"healthy": 1 if status.available else 0},
                        {"service": service},
                        timestamp
                    ))
                except Exception:
                    pass
        except ImportError:
            pass

        # Node counts
        try:
            from utils.map_data_collector import MapDataCollector
            collector = MapDataCollector(enable_history=False)
            geojson = collector.collect(max_age_seconds=60)
            props = geojson.get("properties", {})

            lines.append(self._format_line_protocol(
                "meshforge_nodes",
                {
                    "total": props.get("total_nodes", 0),
                    "with_gps": props.get("nodes_with_position", 0),
                },
                {},
                timestamp
            ))
        except Exception:
            pass

        return "\n".join(lines)


def start_influxdb_exporter(
    url: str = "http://localhost:8086",
    database: str = "meshforge",
    bucket: str = None,
    org: str = None,
    token: str = None,
    interval: int = 15,
) -> InfluxDBExporter:
    """
    Convenience function to start InfluxDB exporter.

    Args:
        url: InfluxDB URL
        database: Database name (InfluxDB 1.x)
        bucket: Bucket name (InfluxDB 2.x, defaults to database)
        org: Organization (InfluxDB 2.x)
        token: API token (InfluxDB 2.x)
        interval: Export interval in seconds

    Returns:
        Running InfluxDBExporter instance
    """
    exporter = InfluxDBExporter(
        url=url,
        database=database,
        bucket=bucket,
        org=org,
        token=token,
        flush_interval=interval,
    )
    exporter.start_background_export(interval)
    return exporter

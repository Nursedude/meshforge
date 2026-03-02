"""
Prometheus Metrics Export for MeshForge.

Exports MeshForge metrics in Prometheus exposition format, enabling
integration with Grafana dashboards, alerting, and the broader
observability ecosystem.

Usage:
    from utils.prometheus_exporter import PrometheusExporter, start_metrics_server

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

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.metrics_common import (
    METRICS,
    MetricDefinition,
    format_metric_line,
    _format_metric_line,
)
from utils.safe_import import safe_import

# --- Optional dependency imports (consolidated via safe_import) ---
_meshforge_version, _HAS_VERSION = safe_import('__version__', '__version__')
SharedHealthState, _HAS_HEALTH_STATE = safe_import(
    'utils.shared_health_state', 'SharedHealthState'
)
get_health_scorer, _HAS_HEALTH_SCORER = safe_import(
    'utils.health_score', 'get_health_scorer'
)
PersistentMessageQueue, _HAS_MESSAGE_QUEUE = safe_import(
    'gateway.message_queue', 'PersistentMessageQueue'
)
MapDataCollector, _HAS_MAP_COLLECTOR = safe_import(
    'utils.map_data_collector', 'MapDataCollector'
)
get_metrics_history, MetricType, _HAS_METRICS_HISTORY = safe_import(
    'utils.metrics_history', 'get_metrics_history', 'MetricType'
)
check_service, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service'
)
TCPMonitor, TCPState, _HAS_TCP_MONITOR = safe_import(
    'monitoring.tcp_monitor', 'TCPMonitor', 'TCPState'
)
get_rns_sniffer, _HAS_RNS_SNIFFER = safe_import(
    'monitoring.rns_sniffer', 'get_rns_sniffer'
)
get_local_subscriber, _HAS_MQTT_SUBSCRIBER = safe_import(
    'monitoring.mqtt_subscriber', 'get_local_subscriber'
)
get_topology_snapshot_store, _HAS_TOPOLOGY_SNAPSHOT = safe_import(
    'utils.topology_snapshot', 'get_topology_snapshot_store'
)

logger = logging.getLogger(__name__)

# Node GeoJSON cache and HTTP server (extracted to _metrics_server.py)
from utils._metrics_server import (
    _collect_node_geojson,
    MetricsHTTPHandler,
    MetricsServer,
    start_metrics_server,
    setup_textfile_exporter,
)


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
        self._collectors.append(self._collect_tcp_metrics)
        self._collectors.append(self._collect_rns_metrics)
        self._collectors.append(self._collect_environment_metrics)
        self._collectors.append(self._collect_mqtt_metrics)
        self._collectors.append(self._collect_topology_metrics)

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
        version = _meshforge_version if _HAS_VERSION else "unknown"

        defn = METRICS["meshforge_info"]
        lines.append(f"# HELP {defn.name} {defn.help_text}")
        lines.append(f"# TYPE {defn.name} {defn.metric_type}")
        lines.append(_format_metric_line(defn.name, 1, {"version": version}))

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

        if _HAS_HEALTH_STATE:
            try:
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

            except Exception as e:
                logger.debug(f"Error collecting health metrics: {e}")

        # Health scores from HealthScorer (uses shared singleton)
        if _HAS_HEALTH_SCORER:
            try:
                scorer = get_health_scorer()
                snapshot = scorer.get_snapshot()

                defn = METRICS["meshforge_health_score"]
                lines.append(f"# HELP {defn.name} {defn.help_text}")
                lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                lines.append(_format_metric_line(defn.name, snapshot.overall_score, {"category": "overall"}))
                lines.append(_format_metric_line(defn.name, snapshot.connectivity_score, {"category": "connectivity"}))
                lines.append(_format_metric_line(defn.name, snapshot.performance_score, {"category": "performance"}))
                lines.append(_format_metric_line(defn.name, snapshot.reliability_score, {"category": "reliability"}))
                lines.append(_format_metric_line(defn.name, snapshot.freshness_score, {"category": "freshness"}))

            except Exception as e:
                logger.debug(f"Error collecting health scores: {e}")

        return lines

    def _collect_message_metrics(self) -> List[str]:
        """Collect message queue metrics from PersistentMessageQueue."""
        lines = []

        if _HAS_MESSAGE_QUEUE:
            try:
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

            except Exception as e:
                logger.debug(f"Error collecting message metrics: {e}")

        return lines

    def _collect_node_metrics(self) -> List[str]:
        """Collect node metrics from MapDataCollector and MetricsHistory."""
        lines = []
        node_count = 0
        nodes_with_gps = 0

        # Primary source: MapDataCollector (has actual node data)
        geojson = _collect_node_geojson()
        if geojson:
            props = geojson.get("properties", {})
            node_count = props.get("total_nodes", 0)
            nodes_with_gps = props.get("nodes_with_position", 0)
            logger.debug(f"MapDataCollector: {node_count} total, {nodes_with_gps} with GPS")

        # Fallback to MetricsHistory if MapDataCollector returned 0
        if node_count == 0 and _HAS_METRICS_HISTORY:
            try:
                history = get_metrics_history()
                stats = history.get_statistics()
                node_count = stats.get("unique_nodes", 0)
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
        if _HAS_METRICS_HISTORY:
            try:
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

                # Battery metrics
                battery_added = False
                for point in history.get_recent(metric_type=MetricType.BATTERY, hours=1, limit=100):
                    if point.node_id:
                        if not battery_added:
                            defn = METRICS["meshforge_node_battery_percent"]
                            lines.append(f"# HELP {defn.name} {defn.help_text}")
                            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                            battery_added = True
                        lines.append(_format_metric_line(defn.name, point.value, {"node_id": point.node_id}))

            except Exception as e:
                logger.debug(f"Error collecting SNR/RSSI/battery metrics: {e}")

        return lines

    def _collect_gateway_metrics(self) -> List[str]:
        """Collect gateway-specific metrics from service status."""
        lines = []

        meshtastic_connected = 0
        rns_connected = 0

        # Check meshtasticd service status
        if _HAS_SERVICE_CHECK:
            try:
                mesh_status = check_service("meshtasticd")
                if mesh_status.available:
                    meshtastic_connected = 1
            except Exception as e:
                logger.debug(f"Error checking meshtasticd: {e}")
        else:
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

        # Check rnsd service status
        if _HAS_SERVICE_CHECK:
            try:
                rns_status = check_service("rnsd")
                if rns_status.available:
                    rns_connected = 1
            except Exception as e:
                logger.debug(f"Error checking rnsd: {e}")
        else:
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

        defn = METRICS["meshforge_gateway_connections"]
        lines.append(f"# HELP {defn.name} {defn.help_text}")
        lines.append(f"# TYPE {defn.name} {defn.metric_type}")
        lines.append(_format_metric_line(defn.name, meshtastic_connected, {"network": "meshtastic"}))
        lines.append(_format_metric_line(defn.name, rns_connected, {"network": "rns"}))

        return lines

    def _collect_tcp_metrics(self) -> List[str]:
        """Collect TCP connection metrics."""
        lines = []

        if not _HAS_TCP_MONITOR:
            logger.debug("TCP monitor not available for metrics collection")
            return lines

        try:
            monitor = TCPMonitor()
            connections = monitor._get_tcp_connections()

            # Count connections by state and port
            state_port_counts: Dict[str, Dict[str, int]] = {}
            meshtasticd_connections = []
            total_connections = len(connections)

            for conn in connections:
                state = conn["state"].value
                # Determine the relevant port (4403 for meshtasticd)
                port = "4403" if 4403 in (conn["local_port"], conn["remote_port"]) else "other"

                if state not in state_port_counts:
                    state_port_counts[state] = {}
                if port not in state_port_counts[state]:
                    state_port_counts[state][port] = 0
                state_port_counts[state][port] += 1

                # Track meshtasticd connections
                if conn["local_port"] == 4403 or conn["remote_port"] == 4403:
                    meshtasticd_connections.append(conn)

            # TCP connections by state
            defn = METRICS["meshforge_tcp_connections"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            for state, ports in state_port_counts.items():
                for port, count in ports.items():
                    lines.append(_format_metric_line(
                        defn.name, count, {"state": state, "port": port}
                    ))

            # Meshtasticd connections
            defn = METRICS["meshforge_tcp_meshtasticd_connections"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            for conn in meshtasticd_connections:
                remote = conn["remote_addr"]
                if conn["state"] == TCPState.ESTABLISHED:
                    lines.append(_format_metric_line(defn.name, 1, {"remote_addr": remote}))

            # Total connections
            defn = METRICS["meshforge_tcp_connections_total"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, total_connections, {}))

        except Exception as e:
            logger.debug(f"Error collecting TCP metrics: {e}")

        return lines

    def _collect_rns_metrics(self) -> List[str]:
        """Collect RNS sniffer metrics for Wireshark-grade visibility."""
        lines = []

        if not _HAS_RNS_SNIFFER:
            logger.debug("RNS sniffer not available for metrics collection")
            return lines

        try:
            sniffer = get_rns_sniffer()
            if sniffer is None:
                return lines

            stats = sniffer.get_stats()

            # Sniffer running status
            defn = METRICS["meshforge_rns_sniffer_running"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, 1 if sniffer._running else 0))

            # Packets captured
            defn = METRICS["meshforge_rns_packets_captured"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(
                defn.name, stats.get("packets_captured", 0), {"packet_type": "total"}
            ))

            # Announces seen
            defn = METRICS["meshforge_rns_announces_seen"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("announces_seen", 0)))

            # Bytes captured
            defn = METRICS["meshforge_rns_bytes_captured"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("bytes_captured", 0)))

            # Paths discovered
            defn = METRICS["meshforge_rns_paths_discovered"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("path_count", 0)))

            # Links total
            defn = METRICS["meshforge_rns_links_total"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("links_established", 0)))

            # Active links
            defn = METRICS["meshforge_rns_links_active"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("active_links", 0)))

            # Path hops for known paths (top 10 most recent)
            paths = sniffer.get_path_table()
            if paths:
                defn = METRICS["meshforge_rns_path_hops"]
                lines.append(f"# HELP {defn.name} {defn.help_text}")
                lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                for path in sorted(paths, key=lambda p: p.last_seen, reverse=True)[:10]:
                    dest_short = path.destination_hash.hex()[:16]
                    lines.append(_format_metric_line(
                        defn.name, path.hops, {"destination": dest_short}
                    ))

        except Exception as e:
            logger.debug(f"Error collecting RNS metrics: {e}")

        return lines

    def _collect_environment_metrics(self) -> List[str]:
        """Collect environment sensor metrics from MQTT subscriber nodes.

        Exports temperature, humidity, pressure, gas resistance, air quality,
        and health metrics (heart rate, SpO2) from nodes with attached sensors.
        """
        lines = []

        if not _HAS_MQTT_SUBSCRIBER:
            logger.debug("MQTT subscriber not available for environment metrics")
            return lines

        try:
            subscriber = get_local_subscriber()
            if not subscriber.is_connected():
                return lines

            # Environment sensors (BME280/BME680/BMP280)
            env_nodes = subscriber.get_nodes_with_environment_metrics()
            if env_nodes:
                # Temperature
                temp_nodes = [n for n in env_nodes if n.temperature is not None]
                if temp_nodes:
                    defn = METRICS["meshforge_env_temperature_celsius"]
                    lines.append(f"# HELP {defn.name} {defn.help_text}")
                    lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                    for node in temp_nodes:
                        lines.append(_format_metric_line(
                            defn.name, node.temperature, {"node_id": node.node_id}
                        ))

                # Humidity
                humid_nodes = [n for n in env_nodes if n.humidity is not None]
                if humid_nodes:
                    defn = METRICS["meshforge_env_humidity_percent"]
                    lines.append(f"# HELP {defn.name} {defn.help_text}")
                    lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                    for node in humid_nodes:
                        lines.append(_format_metric_line(
                            defn.name, node.humidity, {"node_id": node.node_id}
                        ))

                # Pressure
                press_nodes = [n for n in env_nodes if n.pressure is not None]
                if press_nodes:
                    defn = METRICS["meshforge_env_pressure_hpa"]
                    lines.append(f"# HELP {defn.name} {defn.help_text}")
                    lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                    for node in press_nodes:
                        lines.append(_format_metric_line(
                            defn.name, node.pressure, {"node_id": node.node_id}
                        ))

                # Gas resistance (BME680)
                gas_nodes = [n for n in env_nodes if n.gas_resistance is not None]
                if gas_nodes:
                    defn = METRICS["meshforge_env_gas_resistance_ohms"]
                    lines.append(f"# HELP {defn.name} {defn.help_text}")
                    lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                    for node in gas_nodes:
                        lines.append(_format_metric_line(
                            defn.name, node.gas_resistance, {"node_id": node.node_id}
                        ))

            # Air quality sensors (PMSA003I, SCD4X)
            aq_nodes = subscriber.get_nodes_with_air_quality()
            if aq_nodes:
                pm25_nodes = [n for n in aq_nodes if n.pm25_standard is not None]
                if pm25_nodes:
                    defn = METRICS["meshforge_air_quality_pm25"]
                    lines.append(f"# HELP {defn.name} {defn.help_text}")
                    lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                    for node in pm25_nodes:
                        lines.append(_format_metric_line(
                            defn.name, node.pm25_standard, {"node_id": node.node_id}
                        ))

                pm10_nodes = [n for n in aq_nodes if n.pm10_standard is not None]
                if pm10_nodes:
                    defn = METRICS["meshforge_air_quality_pm10"]
                    lines.append(f"# HELP {defn.name} {defn.help_text}")
                    lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                    for node in pm10_nodes:
                        lines.append(_format_metric_line(
                            defn.name, node.pm10_standard, {"node_id": node.node_id}
                        ))

                co2_nodes = [n for n in aq_nodes if n.co2 is not None]
                if co2_nodes:
                    defn = METRICS["meshforge_air_quality_co2_ppm"]
                    lines.append(f"# HELP {defn.name} {defn.help_text}")
                    lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                    for node in co2_nodes:
                        lines.append(_format_metric_line(
                            defn.name, node.co2, {"node_id": node.node_id}
                        ))

                iaq_nodes = [n for n in aq_nodes if n.iaq is not None]
                if iaq_nodes:
                    defn = METRICS["meshforge_air_quality_iaq"]
                    lines.append(f"# HELP {defn.name} {defn.help_text}")
                    lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                    for node in iaq_nodes:
                        lines.append(_format_metric_line(
                            defn.name, node.iaq, {"node_id": node.node_id}
                        ))

            # Health metrics (MAX30102, pulse oximeters) - Meshtastic 2.7+
            all_nodes = subscriber.get_nodes()
            hr_nodes = [n for n in all_nodes if n.heart_bpm is not None]
            if hr_nodes:
                defn = METRICS["meshforge_health_heart_bpm"]
                lines.append(f"# HELP {defn.name} {defn.help_text}")
                lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                for node in hr_nodes:
                    lines.append(_format_metric_line(
                        defn.name, node.heart_bpm, {"node_id": node.node_id}
                    ))

            spo2_nodes = [n for n in all_nodes if n.spo2 is not None]
            if spo2_nodes:
                defn = METRICS["meshforge_health_spo2_percent"]
                lines.append(f"# HELP {defn.name} {defn.help_text}")
                lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                for node in spo2_nodes:
                    lines.append(_format_metric_line(
                        defn.name, node.spo2, {"node_id": node.node_id}
                    ))

        except Exception as e:
            logger.debug(f"Error collecting environment metrics: {e}")

        return lines

    def _collect_mqtt_metrics(self) -> List[str]:
        """Collect MQTT subscriber statistics.

        Exports connection state, node counts, mesh size, and message
        counts from the MQTT subscriber singleton.
        """
        lines = []

        if not _HAS_MQTT_SUBSCRIBER:
            logger.debug("MQTT subscriber not available for metrics")
            return lines

        try:
            subscriber = get_local_subscriber()
            stats = subscriber.get_stats()
            connected = 1 if subscriber.is_connected() else 0

            # MQTT connected status
            defn = METRICS["meshforge_mqtt_connected"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, connected))

            # MQTT total nodes
            defn = METRICS["meshforge_mqtt_nodes_total"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("node_count", 0)))

            # MQTT online nodes
            defn = METRICS["meshforge_mqtt_nodes_online"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("online_count", 0)))

            # MQTT messages received
            defn = METRICS["meshforge_mqtt_messages_received"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("message_count", 0)))

            # Mesh size (24h unique nodes)
            defn = METRICS["meshforge_mqtt_mesh_size"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, stats.get("mesh_size_24h", 0)))

        except Exception as e:
            logger.debug(f"Error collecting MQTT metrics: {e}")

        return lines

    def _collect_topology_metrics(self) -> List[str]:
        """Collect network topology graph statistics.

        Exports node count, edge count, and snapshot count from
        the topology snapshot store.
        """
        lines = []

        if not _HAS_TOPOLOGY_SNAPSHOT:
            logger.debug("Topology snapshot store not available")
            return lines

        try:
            store = get_topology_snapshot_store()
            snapshots = store.get_snapshots(hours=24)

            # Snapshot count
            defn = METRICS["meshforge_topology_snapshots"]
            lines.append(f"# HELP {defn.name} {defn.help_text}")
            lines.append(f"# TYPE {defn.name} {defn.metric_type}")
            lines.append(_format_metric_line(defn.name, len(snapshots)))

            # Latest snapshot stats (if any)
            if snapshots:
                latest = snapshots[-1]
                stats = latest.stats if hasattr(latest, 'stats') else {}

                defn = METRICS["meshforge_topology_nodes"]
                lines.append(f"# HELP {defn.name} {defn.help_text}")
                lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                lines.append(_format_metric_line(defn.name, stats.get("node_count", len(latest.nodes))))

                defn = METRICS["meshforge_topology_edges"]
                lines.append(f"# HELP {defn.name} {defn.help_text}")
                lines.append(f"# TYPE {defn.name} {defn.metric_type}")
                lines.append(_format_metric_line(defn.name, stats.get("edge_count", len(latest.edges))))

        except Exception as e:
            logger.debug(f"Error collecting topology metrics: {e}")

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


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

import http.server
import logging
import os
import socketserver
import threading
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

# Shared node data cache to avoid repeated MapDataCollector instantiation
_node_geojson_cache: Dict[str, Any] = {}
_node_geojson_cache_time: float = 0.0
_NODE_CACHE_TTL: float = 5.0  # seconds


def _collect_node_geojson() -> Dict[str, Any]:
    """Collect node GeoJSON from MapDataCollector with short-lived cache.

    Returns cached data if called within _NODE_CACHE_TTL seconds.
    Returns empty dict if MapDataCollector is unavailable.
    """
    global _node_geojson_cache, _node_geojson_cache_time
    now = time.time()
    if now - _node_geojson_cache_time < _NODE_CACHE_TTL and _node_geojson_cache:
        return _node_geojson_cache
    if not _HAS_MAP_COLLECTOR:
        return {}
    try:
        collector = MapDataCollector(enable_history=False)
        geojson = collector.collect(max_age_seconds=60)
        _node_geojson_cache = geojson
        _node_geojson_cache_time = now
        return geojson
    except Exception as e:
        logger.debug(f"MapDataCollector error: {e}")
        return _node_geojson_cache if _node_geojson_cache else {}


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
        # Prometheus API endpoints (for Grafana Prometheus data source)
        elif self.path.startswith("/api/v1/query_range"):
            self._serve_prometheus_query_range()
        elif self.path.startswith("/api/v1/query"):
            self._serve_prometheus_query()
        elif self.path.startswith("/api/v1/labels"):
            self._serve_prometheus_labels()
        elif self.path.startswith("/api/v1/label"):
            self._serve_prometheus_label_values()
        elif self.path == "/":
            self._serve_index()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def _add_cors_headers(self):
        """Add CORS headers for Grafana."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Authorization")

    def do_OPTIONS(self):
        """Handle OPTIONS request (CORS preflight)."""
        self.send_response(200)
        self._add_cors_headers()
        self.end_headers()

    def do_POST(self):
        """Handle POST request (for Grafana Prometheus data source)."""
        # Grafana Prometheus data source sends POST requests
        # Route to same handlers as GET for our endpoints
        if self.path == "/metrics":
            self._serve_metrics()
        elif self.path == "/api/json/metrics":
            self._serve_json_metrics()
        elif self.path == "/api/json/nodes":
            self._serve_json_nodes()
        elif self.path == "/api/json/status":
            self._serve_json_status()
        # Prometheus API compatibility endpoints
        elif self.path.startswith("/api/v1/query_range"):
            self._serve_prometheus_query_range()
        elif self.path.startswith("/api/v1/query"):
            self._serve_prometheus_query()
        elif self.path.startswith("/api/v1/labels"):
            self._serve_prometheus_labels()
        elif self.path.startswith("/api/v1/label"):
            self._serve_prometheus_label_values()
        else:
            self.send_response(404)
            self._add_cors_headers()
            self.end_headers()
            self.wfile.write(b"Not Found")

    def _serve_prometheus_query(self):
        """Serve Prometheus query API for Grafana compatibility."""
        import json
        import re
        import urllib.parse

        # Parse query from URL or body
        query = ""
        if "?" in self.path:
            params = urllib.parse.parse_qs(self.path.split("?", 1)[1])
            query = params.get("query", [""])[0]
        else:
            # Read POST body
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                body = self.rfile.read(content_length).decode('utf-8')
                params = urllib.parse.parse_qs(body)
                query = params.get("query", [""])[0]

        # Return metrics in Prometheus API format
        result = {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": []
            }
        }

        try:
            # Parse all metrics from exporter
            if self.exporter:
                metrics_text = self.exporter.export()
                now = time.time()

                # Parse Prometheus format lines: metric_name{labels} value
                # or metric_name value
                metric_pattern = re.compile(
                    r'^([a-zA-Z_][a-zA-Z0-9_]*)(\{[^}]*\})?\s+([0-9.eE+-]+|NaN|Inf|-Inf)$'
                )

                for line in metrics_text.split('\n'):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    match = metric_pattern.match(line)
                    if match:
                        metric_name = match.group(1)
                        labels_str = match.group(2) or ""
                        value = match.group(3)

                        # Filter by query if specified
                        if query and query not in metric_name:
                            continue

                        # Parse labels
                        labels = {"__name__": metric_name, "job": "meshforge"}
                        if labels_str:
                            # Parse {key="value",key2="value2"}
                            label_pattern = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"')
                            for label_match in label_pattern.finditer(labels_str):
                                labels[label_match.group(1)] = label_match.group(2)

                        result["data"]["result"].append({
                            "metric": labels,
                            "value": [now, value]
                        })

            # Always include 'up' metric
            if not query or "up" in query:
                result["data"]["result"].append({
                    "metric": {"__name__": "up", "job": "meshforge"},
                    "value": [time.time(), "1"]
                })

        except Exception as e:
            logger.debug(f"Query error: {e}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(result).encode('utf-8'))

    def _serve_prometheus_labels(self):
        """Serve Prometheus labels API for Grafana compatibility."""
        import json

        result = {
            "status": "success",
            "data": ["__name__", "job", "service", "node_id", "state", "network"]
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(result).encode('utf-8'))

    def _serve_prometheus_query_range(self):
        """Serve Prometheus query_range API for Grafana time-series panels."""
        import json
        import re
        import urllib.parse

        # Parse query from URL or body
        query = ""
        if "?" in self.path:
            params = urllib.parse.parse_qs(self.path.split("?", 1)[1])
            query = params.get("query", [""])[0]
        else:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                body = self.rfile.read(content_length).decode('utf-8')
                params = urllib.parse.parse_qs(body)
                query = params.get("query", [""])[0]

        # Return metrics in Prometheus API matrix format
        result = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": []
            }
        }

        try:
            if self.exporter:
                metrics_text = self.exporter.export()
                now = time.time()

                metric_pattern = re.compile(
                    r'^([a-zA-Z_][a-zA-Z0-9_]*)(\{[^}]*\})?\s+([0-9.eE+-]+|NaN|Inf|-Inf)$'
                )

                for line in metrics_text.split('\n'):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    match = metric_pattern.match(line)
                    if match:
                        metric_name = match.group(1)
                        labels_str = match.group(2) or ""
                        value = match.group(3)

                        if query and query not in metric_name:
                            continue

                        labels = {"__name__": metric_name, "job": "meshforge"}
                        if labels_str:
                            label_pattern = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"')
                            for label_match in label_pattern.finditer(labels_str):
                                labels[label_match.group(1)] = label_match.group(2)

                        # For query_range, return values array (time series)
                        # We only have current value, so return single point
                        result["data"]["result"].append({
                            "metric": labels,
                            "values": [[now, value]]
                        })

        except Exception as e:
            logger.debug(f"Query range error: {e}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(result).encode('utf-8'))

    def _serve_prometheus_label_values(self):
        """Serve Prometheus label values API for Grafana variable queries."""
        import json
        import re

        # Extract label name from path: /api/v1/label/<label>/values
        label_match = re.search(r'/api/v1/label/([^/]+)/values', self.path)
        label_name = label_match.group(1) if label_match else ""

        values = set()

        try:
            if self.exporter and label_name:
                metrics_text = self.exporter.export()

                if label_name == "__name__":
                    # Return metric names
                    metric_pattern = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)')
                    for line in metrics_text.split('\n'):
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        match = metric_pattern.match(line)
                        if match:
                            values.add(match.group(1))
                else:
                    # Return label values
                    label_pattern = re.compile(rf'{label_name}="([^"]*)"')
                    for line in metrics_text.split('\n'):
                        for match in label_pattern.finditer(line):
                            values.add(match.group(1))

        except Exception as e:
            logger.debug(f"Label values error: {e}")

        result = {
            "status": "success",
            "data": sorted(list(values))
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(result).encode('utf-8'))

    def _serve_index(self):
        """Serve index page with available endpoints."""
        content = """MeshForge Metrics Server

Endpoints:
  /metrics          - Prometheus format (for Prometheus scraper)
  /health           - Health check
  /api/json/metrics - JSON metrics (for Grafana Infinity plugin)
  /api/json/nodes   - Node data JSON
  /api/json/status  - System status JSON
  /api/v1/query     - Prometheus API (for Grafana Prometheus data source)
  /api/v1/labels    - Prometheus labels API

Grafana Setup (Option 1 - Prometheus data source):
  1. Add data source: Type = Prometheus
  2. URL = http://localhost:9090
  3. Query: meshforge_uptime_seconds, etc.

Grafana Setup (Option 2 - Infinity plugin):
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

            # Get node counts from MapDataCollector (uses shared cache)
            geojson = _collect_node_geojson()
            if geojson:
                props = geojson.get('properties', {})
                metrics['nodes_total'] = props.get('total_nodes', 0)
                metrics['nodes_with_gps'] = props.get('nodes_with_position', 0)
                metrics['sources'] = props.get('sources', {})
            else:
                metrics['nodes_total'] = 0
                metrics['nodes_with_gps'] = 0

            # Get service status
            if _HAS_SERVICE_CHECK:
                try:
                    mesh_status = check_service("meshtasticd")
                    rns_status = check_service("rnsd")
                    metrics['meshtasticd_running'] = 1 if mesh_status.available else 0
                    metrics['rnsd_running'] = 1 if rns_status.available else 0
                except Exception:
                    metrics['meshtasticd_running'] = 0
                    metrics['rnsd_running'] = 0
            else:
                metrics['meshtasticd_running'] = 0
                metrics['rnsd_running'] = 0

            # MQTT stats
            if _HAS_MQTT_SUBSCRIBER:
                try:
                    subscriber = get_local_subscriber()
                    mqtt_stats = subscriber.get_stats()
                    metrics['mqtt_connected'] = 1 if subscriber.is_connected() else 0
                    metrics['mqtt_nodes'] = mqtt_stats.get('node_count', 0)
                    metrics['mqtt_online'] = mqtt_stats.get('online_count', 0)
                    metrics['mqtt_mesh_size_24h'] = mqtt_stats.get('mesh_size_24h', 0)
                    metrics['mqtt_nodes_with_env'] = mqtt_stats.get('nodes_with_env_metrics', 0)
                    metrics['mqtt_nodes_with_aq'] = mqtt_stats.get('nodes_with_aq_metrics', 0)
                    metrics['mesh_health_status'] = mqtt_stats.get('mesh_health_status', 'unknown')
                except Exception:
                    metrics['mqtt_connected'] = 0
            else:
                metrics['mqtt_connected'] = 0

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

            geojson = _collect_node_geojson()
            if geojson:
                try:
                    for feature in geojson.get('features', []):
                        props = feature.get('properties', {})
                        coords = feature.get('geometry', {}).get('coordinates', [0, 0])
                        node_data = {
                            'id': props.get('id', ''),
                            'name': props.get('name', ''),
                            'lat': coords[1] if len(coords) > 1 else 0,
                            'lon': coords[0] if len(coords) > 0 else 0,
                            'snr': props.get('snr'),
                            'rssi': props.get('rssi'),
                            'battery': props.get('battery'),
                            'last_heard': props.get('last_heard'),
                            'online': props.get('online', False),
                            'hardware': props.get('hardware', ''),
                            'role': props.get('role', ''),
                            # Environment sensors
                            'temperature': props.get('temperature'),
                            'humidity': props.get('humidity'),
                            'pressure': props.get('pressure'),
                            # Air quality
                            'pm25': props.get('pm25'),
                            'co2': props.get('co2'),
                            'iaq': props.get('iaq'),
                        }
                        nodes.append(node_data)
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

        version = _meshforge_version if _HAS_VERSION else "unknown"

        status = {
            'version': version,
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

            self._server = socketserver.TCPServer(("127.0.0.1", self.port), handler_class)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()

            logger.info(f"Prometheus metrics server started on 127.0.0.1:{self.port}")
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

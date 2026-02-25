"""
InfluxDB Metrics Export for MeshForge.

Export MeshForge metrics to InfluxDB for time-series storage and
Grafana visualization.

Usage:
    from utils.influxdb_exporter import InfluxDBExporter, start_influxdb_exporter

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

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# First-party imports
import __version__ as version_mod
from utils.service_check import check_service
from utils.map_data_collector import MapDataCollector
from gateway.message_queue import PersistentMessageQueue
from monitoring.mqtt_subscriber import get_local_subscriber


class InfluxDBExporter:
    """
    Export MeshForge metrics to InfluxDB.

    Supports both InfluxDB 1.x and 2.x APIs, with options for
    HTTP or UDP transport.
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
        self._stop_event = threading.Event()
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
        version = version_mod.__version__

        self.write_point(
            "meshforge_info",
            {"value": 1},
            {"version": version},
            timestamp
        )

        # Service health
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

        # Node counts
        try:
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
        except Exception as e:
            logger.debug(f"Error collecting node metrics: {e}")

        # Message queue stats
        try:
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
        except Exception as e:
            logger.debug(f"Error collecting message metrics: {e}")

        # Environment sensor metrics from MQTT subscriber
        try:
            subscriber = get_local_subscriber()
            if subscriber.is_connected():
                # MQTT stats
                mqtt_stats = subscriber.get_stats()
                self.write_point(
                    "meshforge_mqtt",
                    {
                        "nodes_total": mqtt_stats.get("node_count", 0),
                        "nodes_online": mqtt_stats.get("online_count", 0),
                        "mesh_size_24h": mqtt_stats.get("mesh_size_24h", 0),
                        "messages": mqtt_stats.get("message_count", 0),
                    },
                    {},
                    timestamp
                )

                # Environment sensors per node
                for node in subscriber.get_nodes_with_environment_metrics():
                    fields = {}
                    if node.temperature is not None:
                        fields["temperature"] = float(node.temperature)
                    if node.humidity is not None:
                        fields["humidity"] = float(node.humidity)
                    if node.pressure is not None:
                        fields["pressure"] = float(node.pressure)
                    if node.gas_resistance is not None:
                        fields["gas_resistance"] = float(node.gas_resistance)
                    if fields:
                        self.write_point(
                            "meshforge_environment",
                            fields,
                            {"node_id": node.node_id, "name": node.long_name or node.short_name or ""},
                            timestamp
                        )

                # Air quality sensors per node
                for node in subscriber.get_nodes_with_air_quality():
                    fields = {}
                    if node.pm25_standard is not None:
                        fields["pm25"] = int(node.pm25_standard)
                    if node.pm10_standard is not None:
                        fields["pm10"] = int(node.pm10_standard)
                    if node.co2 is not None:
                        fields["co2"] = int(node.co2)
                    if node.iaq is not None:
                        fields["iaq"] = int(node.iaq)
                    if fields:
                        self.write_point(
                            "meshforge_air_quality",
                            fields,
                            {"node_id": node.node_id, "name": node.long_name or node.short_name or ""},
                            timestamp
                        )

                # Health metrics per node
                for node in subscriber.get_nodes():
                    fields = {}
                    if node.heart_bpm is not None:
                        fields["heart_bpm"] = int(node.heart_bpm)
                    if node.spo2 is not None:
                        fields["spo2"] = int(node.spo2)
                    if node.body_temperature is not None:
                        fields["body_temperature"] = float(node.body_temperature)
                    if fields:
                        self.write_point(
                            "meshforge_health",
                            fields,
                            {"node_id": node.node_id, "name": node.long_name or node.short_name or ""},
                            timestamp
                        )
        except Exception as e:
            logger.debug(f"Error collecting MQTT/environment metrics: {e}")

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
            while self._running and not self._stop_event.is_set():
                try:
                    self.write_metrics()
                except Exception as e:
                    logger.debug(f"InfluxDB export error: {e}")
                self._stop_event.wait(interval)

        self._stop_event.clear()
        self._flush_thread = threading.Thread(target=export_loop, daemon=True)
        self._flush_thread.start()
        logger.info(f"InfluxDB exporter started (interval: {interval}s)")

    def stop(self) -> None:
        """Stop background export thread."""
        self._running = False
        self._stop_event.set()
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
        version = version_mod.__version__

        lines.append(self._format_line_protocol(
            "meshforge_info", {"value": 1}, {"version": version}, timestamp
        ))

        # Service health
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

        # Node counts
        try:
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

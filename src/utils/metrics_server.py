"""Prometheus metrics HTTP server and textfile exporter.

HTTP server for Prometheus scraping and textfile-based metrics export
for node_exporter integration.

Extracted from prometheus_exporter.py for file size compliance (CLAUDE.md #6).
"""

import logging
import socketserver
import threading
from pathlib import Path
from typing import Optional

from utils.http_listener import LISTEN_BACKLOG

logger = logging.getLogger(__name__)


class MetricsTCPServer(socketserver.TCPServer):
    """The :9090 scrape listener: single-threaded TCPServer, burst-deep
    accept queue (stock is 5; MAP-H H13 follow-up 2026-11-07)."""

    request_queue_size = LISTEN_BACKLOG


class MetricsServer:
    """
    HTTP server for Prometheus metrics scraping.

    Starts a simple HTTP server that serves metrics at /metrics endpoint.

    Attributes:
        port: Server port
        exporter: PrometheusExporter instance
    """

    def __init__(self, port: int = 9090, exporter=None):
        """
        Initialize metrics server.

        Args:
            port: Port to listen on (default: 9090)
            exporter: PrometheusExporter instance (creates one if not provided)
        """
        from utils.prometheus_exporter import PrometheusExporter
        self.port = port
        self.exporter = exporter or PrometheusExporter()
        self._server: Optional[MetricsTCPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """
        Start the metrics server.

        Returns:
            True if started successfully
        """
        try:
            from utils.prometheus_exporter import MetricsHTTPHandler

            # Create handler class with exporter reference
            handler_class = type(
                'MetricsHandler',
                (MetricsHTTPHandler,),
                {'exporter': self.exporter}
            )

            self._server = MetricsTCPServer(("127.0.0.1", self.port), handler_class)
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
            self._server.server_close()  # release the bound fd now, not at GC
            self._server = None
            if self._thread:
                self._thread.join(timeout=5.0)
            logger.info("Prometheus metrics server stopped")

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._thread is not None and self._thread.is_alive()


def start_metrics_server(port: int = 9090, exporter=None) -> MetricsServer:
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
    stop_event: threading.Event = None,
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
    from utils.prometheus_exporter import PrometheusExporter

    if output_dir is None:
        output_dir = "/var/lib/node_exporter/textfile_collector"

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    metrics_file = output_path / "meshforge.prom"

    exporter = PrometheusExporter()
    _stop = stop_event or threading.Event()

    def export_loop():
        while not _stop.is_set():
            try:
                exporter.write_to_file(str(metrics_file))
            except Exception as e:
                logger.debug(f"Textfile export error: {e}")
            _stop.wait(interval_seconds)

    thread = threading.Thread(target=export_loop, daemon=True)
    thread.start()

    logger.info(f"Textfile metrics exporter started: {metrics_file}")
    return thread

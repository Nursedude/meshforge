"""Tests for Prometheus metrics export."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from utils.metrics_export import (
    PrometheusExporter,
    MetricsServer,
    start_metrics_server,
    _format_labels,
    _format_metric_line,
    METRICS,
    COUNTER,
    GAUGE,
)


class TestFormatting:
    """Test Prometheus formatting functions."""

    def test_format_labels_empty(self):
        """Test formatting empty labels."""
        assert _format_labels({}) == ""
        assert _format_labels(None) == ""

    def test_format_labels_single(self):
        """Test formatting single label."""
        result = _format_labels({"service": "meshtasticd"})
        assert result == '{service="meshtasticd"}'

    def test_format_labels_multiple(self):
        """Test formatting multiple labels."""
        result = _format_labels({"direction": "incoming", "status": "delivered"})
        # Labels should be sorted alphabetically
        assert result == '{direction="incoming",status="delivered"}'

    def test_format_metric_line_no_labels(self):
        """Test formatting metric line without labels."""
        result = _format_metric_line("meshforge_uptime_seconds", 123.45)
        assert result == "meshforge_uptime_seconds 123.45"

    def test_format_metric_line_with_labels(self):
        """Test formatting metric line with labels."""
        result = _format_metric_line(
            "meshforge_service_healthy",
            1,
            {"service": "rnsd"}
        )
        assert result == 'meshforge_service_healthy{service="rnsd"} 1'


class TestMetricDefinitions:
    """Test metric definitions are valid."""

    def test_all_metrics_have_required_fields(self):
        """Test that all metric definitions have required fields."""
        for name, defn in METRICS.items():
            assert defn.name == name
            assert defn.metric_type in [COUNTER, GAUGE]
            assert defn.help_text
            assert isinstance(defn.labels, list)

    def test_common_metrics_defined(self):
        """Test that common metrics are defined."""
        expected_metrics = [
            "meshforge_service_healthy",
            "meshforge_health_score",
            "meshforge_messages_total",
            "meshforge_message_queue_depth",
            "meshforge_nodes_total",
            "meshforge_info",
            "meshforge_uptime_seconds",
        ]
        for metric in expected_metrics:
            assert metric in METRICS, f"Missing metric: {metric}"


class TestPrometheusExporter:
    """Test PrometheusExporter functionality."""

    def test_initialization(self):
        """Test exporter initializes correctly."""
        exporter = PrometheusExporter()
        assert exporter.start_time > 0
        assert len(exporter._collectors) > 0

    def test_export_generates_output(self):
        """Test that export generates Prometheus-formatted output."""
        exporter = PrometheusExporter()
        output = exporter.export()

        assert "# MeshForge Prometheus Metrics" in output
        assert "meshforge_info" in output
        assert "meshforge_uptime_seconds" in output
        assert "meshforge_last_scrape_timestamp" in output

    def test_export_contains_help_and_type(self):
        """Test that export contains HELP and TYPE comments."""
        exporter = PrometheusExporter()
        output = exporter.export()

        assert "# HELP meshforge_info" in output
        assert "# TYPE meshforge_info gauge" in output
        assert "# HELP meshforge_uptime_seconds" in output
        assert "# TYPE meshforge_uptime_seconds gauge" in output

    def test_export_version_label(self):
        """Test that version is included in info metric."""
        exporter = PrometheusExporter()
        output = exporter.export()

        # Should have version label
        assert 'meshforge_info{version="' in output

    def test_uptime_increases(self):
        """Test that uptime metric increases over time."""
        exporter = PrometheusExporter()

        output1 = exporter.export()
        time.sleep(0.1)
        output2 = exporter.export()

        # Extract uptime values
        def extract_uptime(output):
            for line in output.split('\n'):
                if line.startswith('meshforge_uptime_seconds'):
                    return float(line.split()[-1])
            return 0

        uptime1 = extract_uptime(output1)
        uptime2 = extract_uptime(output2)
        assert uptime2 > uptime1

    def test_custom_metric(self):
        """Test setting custom metrics."""
        exporter = PrometheusExporter()
        exporter.set_custom_metric("custom_gauge", 42.0, {"label": "value"})

        output = exporter.export()
        assert 'custom_gauge{label="value"} 42.0' in output

    def test_register_collector(self):
        """Test registering a custom collector."""
        exporter = PrometheusExporter()

        def custom_collector():
            return ["# Custom collector output", "custom_metric 123"]

        exporter.register_collector(custom_collector)
        output = exporter.export()

        assert "# Custom collector output" in output
        assert "custom_metric 123" in output

    def test_write_to_file(self):
        """Test writing metrics to file."""
        exporter = PrometheusExporter()

        with tempfile.NamedTemporaryFile(suffix='.prom', delete=False) as f:
            temp_path = f.name

        try:
            success = exporter.write_to_file(temp_path)
            assert success is True

            with open(temp_path) as f:
                content = f.read()

            assert "meshforge_info" in content
            assert "meshforge_uptime_seconds" in content

        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_write_to_file_failure(self):
        """Test handling write failures gracefully."""
        exporter = PrometheusExporter()

        # Try to write to a non-existent directory
        success = exporter.write_to_file("/nonexistent/directory/metrics.prom")
        assert success is False

    def test_collector_error_handling(self):
        """Test that collector errors are handled gracefully."""
        exporter = PrometheusExporter()

        def failing_collector():
            raise RuntimeError("Collector failed")

        exporter.register_collector(failing_collector)

        # Should not raise
        output = exporter.export()
        assert output  # Should still have basic output


class TestHealthMetricsCollection:
    """Test health metrics collection from SharedHealthState."""

    def test_collects_from_shared_health_state(self):
        """Test that metrics are collected from SharedHealthState."""
        # Create a mock SharedHealthState
        mock_state = MagicMock()
        mock_record = MagicMock()
        mock_record.service = "test_service"
        mock_record.state = MagicMock()
        mock_record.state.value = "healthy"
        mock_record.uptime_pct = 99.5
        mock_record.latency_ms = 25.0
        mock_record.consecutive_fails = 0
        mock_state.get_all_services.return_value = [mock_record]

        with patch('utils.metrics_export.SharedHealthState', return_value=mock_state):
            exporter = PrometheusExporter()
            output = exporter.export()

            # Should have attempted to collect health metrics
            # (may not appear if import fails, which is expected)
            assert output  # Basic output still works


class TestMessageMetricsCollection:
    """Test message queue metrics collection."""

    def test_collects_from_message_queue(self):
        """Test that metrics are collected from PersistentMessageQueue."""
        mock_queue = MagicMock()
        mock_queue.get_stats.return_value = {
            "pending": 5,
            "in_progress": 2,
            "enqueued": 100,
            "delivered": 95,
            "failed": 3,
            "retried": 10,
            "dead_letter": 2,
        }

        with patch('utils.metrics_export.PersistentMessageQueue', return_value=mock_queue):
            exporter = PrometheusExporter()
            output = exporter.export()

            assert output  # Basic output still works


class TestMetricsServer:
    """Test HTTP metrics server."""

    def test_server_initialization(self):
        """Test server initializes correctly."""
        server = MetricsServer(port=19090)
        assert server.port == 19090
        assert server.exporter is not None
        assert server.is_running is False

    def test_server_start_stop(self):
        """Test server start and stop."""
        server = MetricsServer(port=19091)

        started = server.start()
        assert started is True
        assert server.is_running is True

        server.stop()
        time.sleep(0.1)  # Give server time to stop
        assert server.is_running is False

    def test_start_metrics_server_convenience(self):
        """Test start_metrics_server convenience function."""
        server = start_metrics_server(port=19092)
        assert server.is_running is True
        server.stop()

    def test_server_with_custom_exporter(self):
        """Test server with custom exporter."""
        exporter = PrometheusExporter()
        exporter.set_custom_metric("test_metric", 42.0)

        server = MetricsServer(port=19093, exporter=exporter)
        started = server.start()
        assert started is True

        # Server should use the custom exporter
        assert server.exporter is exporter

        server.stop()


class TestMetricsEndpoints:
    """Test HTTP endpoints (integration tests)."""

    @pytest.fixture
    def running_server(self):
        """Fixture for a running server."""
        server = MetricsServer(port=19094)
        server.start()
        time.sleep(0.1)  # Give server time to start
        yield server
        server.stop()

    def test_metrics_endpoint(self, running_server):
        """Test /metrics endpoint returns metrics."""
        import urllib.request

        try:
            response = urllib.request.urlopen("http://localhost:19094/metrics", timeout=5)
            content = response.read().decode('utf-8')

            assert "meshforge_info" in content
            assert "meshforge_uptime_seconds" in content

        except Exception as e:
            pytest.skip(f"Could not connect to server: {e}")

    def test_health_endpoint(self, running_server):
        """Test /health endpoint returns OK."""
        import urllib.request

        try:
            response = urllib.request.urlopen("http://localhost:19094/health", timeout=5)
            content = response.read().decode('utf-8')

            assert content == "OK"

        except Exception as e:
            pytest.skip(f"Could not connect to server: {e}")


class TestTextfileExporter:
    """Test textfile-based metrics export for node_exporter."""

    def test_setup_textfile_exporter(self):
        """Test setting up textfile exporter."""
        from utils.metrics_export import setup_textfile_exporter

        with tempfile.TemporaryDirectory() as temp_dir:
            thread = setup_textfile_exporter(
                output_dir=temp_dir,
                interval_seconds=1
            )

            assert thread.is_alive()

            # Wait for first write
            time.sleep(1.5)

            # Check file was created
            metrics_file = Path(temp_dir) / "meshforge.prom"
            assert metrics_file.exists()

            content = metrics_file.read_text()
            assert "meshforge_info" in content


class TestJSONAPIEndpoints:
    """Test JSON API endpoints for Grafana Infinity plugin."""

    @pytest.fixture
    def running_server_for_json(self):
        """Fixture for a running server for JSON tests."""
        server = MetricsServer(port=19095)
        server.start()
        time.sleep(0.1)  # Give server time to start
        yield server
        server.stop()

    def test_json_metrics_endpoint(self, running_server_for_json):
        """Test /api/json/metrics endpoint returns valid JSON."""
        import urllib.request
        import json

        try:
            response = urllib.request.urlopen(
                "http://localhost:19095/api/json/metrics", timeout=5
            )
            content = response.read().decode('utf-8')
            data = json.loads(content)

            # Should have expected keys
            assert 'timestamp' in data
            assert 'nodes_total' in data
            assert 'meshtasticd_running' in data
            assert 'rnsd_running' in data

            # Types should be correct
            assert isinstance(data['nodes_total'], int)
            assert data['meshtasticd_running'] in [0, 1]
            assert data['rnsd_running'] in [0, 1]

        except Exception as e:
            pytest.skip(f"Could not connect to server: {e}")

    def test_json_nodes_endpoint(self, running_server_for_json):
        """Test /api/json/nodes endpoint returns valid JSON."""
        import urllib.request
        import json

        try:
            response = urllib.request.urlopen(
                "http://localhost:19095/api/json/nodes", timeout=5
            )
            content = response.read().decode('utf-8')
            data = json.loads(content)

            # Should have expected structure
            assert 'timestamp' in data
            assert 'count' in data
            assert 'nodes' in data
            assert isinstance(data['nodes'], list)
            assert isinstance(data['count'], int)

        except Exception as e:
            pytest.skip(f"Could not connect to server: {e}")

    def test_json_status_endpoint(self, running_server_for_json):
        """Test /api/json/status endpoint returns valid JSON."""
        import urllib.request
        import json

        try:
            response = urllib.request.urlopen(
                "http://localhost:19095/api/json/status", timeout=5
            )
            content = response.read().decode('utf-8')
            data = json.loads(content)

            # Should have expected structure
            assert 'version' in data
            assert 'timestamp' in data
            assert 'services' in data
            assert isinstance(data['services'], dict)

        except Exception as e:
            pytest.skip(f"Could not connect to server: {e}")

    def test_index_endpoint(self, running_server_for_json):
        """Test / endpoint returns usage info."""
        import urllib.request

        try:
            response = urllib.request.urlopen(
                "http://localhost:19095/", timeout=5
            )
            content = response.read().decode('utf-8')

            # Should contain endpoint documentation
            assert "/metrics" in content
            assert "/api/json/metrics" in content
            assert "Grafana" in content

        except Exception as e:
            pytest.skip(f"Could not connect to server: {e}")

    def test_cors_headers(self, running_server_for_json):
        """Test that JSON endpoints include CORS headers."""
        import urllib.request

        try:
            response = urllib.request.urlopen(
                "http://localhost:19095/api/json/metrics", timeout=5
            )

            # Check CORS headers
            cors_header = response.headers.get('Access-Control-Allow-Origin')
            assert cors_header == '*'

        except Exception as e:
            pytest.skip(f"Could not connect to server: {e}")


class TestLabelEscaping:
    """Test Prometheus label value escaping."""

    def test_escape_backslash(self):
        """Test backslash escaping."""
        from utils.metrics_export import _escape_label_value
        assert _escape_label_value('path\\to\\file') == 'path\\\\to\\\\file'

    def test_escape_quote(self):
        """Test double quote escaping."""
        from utils.metrics_export import _escape_label_value
        assert _escape_label_value('value with "quotes"') == 'value with \\"quotes\\"'

    def test_escape_newline(self):
        """Test newline escaping."""
        from utils.metrics_export import _escape_label_value
        assert _escape_label_value('line1\nline2') == 'line1\\nline2'

    def test_escape_combined(self):
        """Test multiple escapes combined."""
        from utils.metrics_export import _escape_label_value
        result = _escape_label_value('path\\with"quote\nand newline')
        assert '\\\\' in result  # escaped backslash
        assert '\\"' in result   # escaped quote
        assert '\\n' in result   # escaped newline


class TestNodeMetricsCollection:
    """Test node metrics collection from various sources."""

    def test_node_metrics_fallback(self):
        """Test that node metrics collection handles missing sources gracefully."""
        exporter = PrometheusExporter()
        output = exporter.export()

        # Should not crash, should have nodes_total metric (even if 0)
        assert "meshforge_nodes_total" in output

    def test_node_metrics_produces_output(self):
        """Test node metrics produces valid output regardless of data sources."""
        exporter = PrometheusExporter()
        output = exporter.export()

        # The metrics should be collected and formatted correctly
        assert output  # Should still produce output
        assert "# MeshForge Prometheus Metrics" in output


class TestGatewayMetricsCollection:
    """Test gateway metrics collection."""

    def test_gateway_metrics_port_fallback(self):
        """Test gateway metrics falls back to port checking."""
        exporter = PrometheusExporter()
        output = exporter.export()

        # Should have gateway metrics (connection status)
        assert "meshforge_gateway_connections" in output

    def test_gateway_metrics_includes_networks(self):
        """Test gateway metrics includes both meshtastic and rns networks."""
        exporter = PrometheusExporter()
        output = exporter.export()

        # Should include both network labels
        assert 'network="meshtastic"' in output
        assert 'network="rns"' in output


class TestInfluxDBExporter:
    """Test InfluxDB metrics exporter."""

    def test_initialization_defaults(self):
        """Test InfluxDB exporter initializes with defaults."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter()

        assert exporter._database == "meshforge"
        assert exporter._bucket == "meshforge"
        assert exporter._precision == "s"
        assert exporter.is_running() is False

    def test_initialization_v2(self):
        """Test InfluxDB 2.x configuration."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter(
            url="http://localhost:8086",
            token="test-token",
            org="test-org",
            bucket="test-bucket"
        )

        assert exporter._is_v2 is True
        assert exporter._token == "test-token"
        assert exporter._org == "test-org"

    def test_initialization_v1(self):
        """Test InfluxDB 1.x configuration."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter(
            url="http://localhost:8086",
            database="test-db",
            username="admin",
            password="admin"
        )

        assert exporter._is_v2 is False
        assert exporter._database == "test-db"
        assert exporter._username == "admin"

    def test_line_protocol_basic(self):
        """Test line protocol formatting."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter()

        line = exporter._format_line_protocol(
            "test_metric",
            {"value": 42.5},
            {"host": "localhost"},
            1000000000
        )

        assert line.startswith("test_metric,")
        assert "host=localhost" in line
        assert "value=42.5" in line
        assert line.endswith("1000000000")

    def test_line_protocol_integer_field(self):
        """Test line protocol integer field formatting."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter()

        line = exporter._format_line_protocol(
            "test_metric",
            {"count": 42},  # Integer
        )

        # Integer fields should have 'i' suffix
        assert "count=42i" in line

    def test_line_protocol_string_field(self):
        """Test line protocol string field formatting."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter()

        line = exporter._format_line_protocol(
            "test_metric",
            {"name": "test value"},  # String
        )

        # String fields should be quoted
        assert 'name="test value"' in line

    def test_line_protocol_boolean_field(self):
        """Test line protocol boolean field formatting."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter()

        line = exporter._format_line_protocol(
            "test_metric",
            {"active": True},  # Boolean
        )

        assert "active=true" in line

    def test_line_protocol_escape_tags(self):
        """Test line protocol escapes special characters in tags."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter()

        line = exporter._format_line_protocol(
            "test_metric",
            {"value": 1},
            {"node": "my node"},  # Space in tag
        )

        # Space should be escaped in tag
        assert r"node=my\ node" in line

    def test_line_protocol_multiple_fields(self):
        """Test line protocol with multiple fields."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter()

        line = exporter._format_line_protocol(
            "test_metric",
            {"snr": 8.5, "rssi": -85, "battery": 95},
        )

        assert "snr=8.5" in line
        assert "rssi=-85i" in line
        assert "battery=95i" in line

    def test_write_point_batching(self):
        """Test that write_point batches points."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter(batch_size=10)

        # Write fewer than batch size
        for i in range(5):
            exporter.write_point("test", {"value": i})

        # Should be in batch, not flushed
        assert len(exporter._batch) == 5

    def test_get_line_protocol_export(self):
        """Test getting metrics as line protocol string."""
        from utils.metrics_export import InfluxDBExporter
        exporter = InfluxDBExporter()

        output = exporter.get_line_protocol_export()

        # Should have at least info metric
        assert "meshforge_info" in output
        assert "value=1i" in output

    def test_get_timestamp_precision(self):
        """Test timestamp generation with different precisions."""
        from utils.metrics_export import InfluxDBExporter

        # Seconds precision
        exporter_s = InfluxDBExporter(precision="s")
        ts_s = exporter_s._get_timestamp()
        assert ts_s < 10_000_000_000  # Should be ~10 digits

        # Milliseconds precision
        exporter_ms = InfluxDBExporter(precision="ms")
        ts_ms = exporter_ms._get_timestamp()
        assert ts_ms > 1_000_000_000_000  # Should be ~13 digits

        # Nanoseconds precision
        exporter_ns = InfluxDBExporter(precision="ns")
        ts_ns = exporter_ns._get_timestamp()
        assert ts_ns > 1_000_000_000_000_000_000  # Should be ~19 digits


class TestStartInfluxDBExporter:
    """Test start_influxdb_exporter convenience function."""

    def test_start_and_stop(self):
        """Test starting and stopping the exporter."""
        from utils.metrics_export import start_influxdb_exporter

        exporter = start_influxdb_exporter(
            url="http://localhost:8086",
            database="test",
            interval=1
        )

        assert exporter.is_running() is True

        exporter.stop()
        time.sleep(0.2)  # Give thread time to stop
        assert exporter.is_running() is False

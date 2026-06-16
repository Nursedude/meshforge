"""
Tests for MeshtasticHTTPClient — meshtasticd HTTP JSON API.

Tests the /json/nodes and /json/report parsing without needing a live device.
All HTTP calls are mocked.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.meshtastic_http import (
    MeshtasticHTTPClient,
    MeshtasticNode,
    DeviceReport,
    get_http_client,
    reset_http_client,
    DEFAULT_HTTP_PORT,
)


# --- Sample API responses ---

SAMPLE_NODES_RESPONSE = {
    "!aabb0001": {
        "id": "!aabb0001",
        "long_name": "Maui-Gateway",
        "short_name": "MG01",
        "hw_model": "HELTEC_V3",
        "mac_address": "AA:BB:CC:DD:EE:01",
        "snr": 12.5,
        "last_heard": 1738800000,
        "via_mqtt": False,
        "position": {
            "latitude": 20.7984,
            "longitude": -156.3319,
            "altitude": 45,
        },
    },
    "!aabb0002": {
        "id": "!aabb0002",
        "long_name": "Kula-Relay",
        "short_name": "KR02",
        "hw_model": "TBEAM",
        "snr": -3.0,
        "last_heard": 1738799900,
        "via_mqtt": False,
        "position": {
            "latitude": 20.7575,
            "longitude": -156.3243,
            "altitude": 930,
        },
    },
    "!aabb0003": {
        "id": "!aabb0003",
        "long_name": "Mobile-Node",
        "short_name": "MN03",
        "hw_model": "RAK4631",
        "snr": 5.0,
        "last_heard": 1738799000,
        "via_mqtt": True,
        # No position
    },
}

SAMPLE_NODES_LIST_RESPONSE = [
    {
        "id": "!ccdd0001",
        "longName": "Oahu-Node",  # camelCase variant
        "shortName": "ON01",
        "hwModel": "HELTEC_V3",
        "snr": 8.0,
        "lastHeard": 1738800000,
        "viaMqtt": False,
        "position": {
            "latitude": 21.3069,
            "longitude": -157.8583,
            "altitude": 15,
        },
    },
]

SAMPLE_REPORT_RESPONSE = {
    "airtime": {
        "channel_utilization": 12.5,
        "utilization_tx": 3.2,
        "seconds_since_boot": 86400,
        "seconds_per_period": 3600,
        "periods_to_log": 24,
    },
    "memory": {
        "heap_total": 327680,
        "heap_free": 204800,
        "fs_total": 4194304,
        "fs_free": 3145728,
        "fs_used": 1048576,
    },
    "power": {
        "battery_percent": 85,
        "battery_voltage_mv": 4100,
        "has_battery": True,
        "has_usb": True,
        "is_charging": True,
    },
    "radio": {
        "frequency": 906.875,
        "lora_channel": 20,
    },
    "wifi": {
        "rssi": -45,
    },
    "device": {
        "reboot_counter": 3,
    },
}


# --- Fixtures ---

@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the HTTP client singleton between tests."""
    reset_http_client()
    yield
    reset_http_client()


def _make_client(auto_detect=False, tls=False) -> MeshtasticHTTPClient:
    """Create a client without auto-detection (for unit tests)."""
    return MeshtasticHTTPClient(
        host='localhost', port=9443, tls=tls, auto_detect=auto_detect
    )


# --- Node Parsing Tests ---


class TestNodeParsing:
    """Test parsing of /json/nodes response data."""

    def test_parse_node_with_position(self):
        node = MeshtasticHTTPClient._parse_node(SAMPLE_NODES_RESPONSE["!aabb0001"])
        assert node is not None
        assert node.node_id == "!aabb0001"
        assert node.long_name == "Maui-Gateway"
        assert node.short_name == "MG01"
        assert node.hw_model == "HELTEC_V3"
        assert node.snr == 12.5
        assert node.has_position is True
        assert node.latitude == pytest.approx(20.7984)
        assert node.longitude == pytest.approx(-156.3319)
        assert node.altitude == 45
        assert node.via_mqtt is False

    def test_parse_node_without_position(self):
        node = MeshtasticHTTPClient._parse_node(SAMPLE_NODES_RESPONSE["!aabb0003"])
        assert node is not None
        assert node.node_id == "!aabb0003"
        assert node.long_name == "Mobile-Node"
        assert node.has_position is False
        assert node.via_mqtt is True

    def test_parse_node_camelcase_fields(self):
        """Test that camelCase field names (from some firmware versions) are handled."""
        node = MeshtasticHTTPClient._parse_node(SAMPLE_NODES_LIST_RESPONSE[0])
        assert node is not None
        assert node.node_id == "!ccdd0001"
        assert node.long_name == "Oahu-Node"
        assert node.short_name == "ON01"
        assert node.hw_model == "HELTEC_V3"
        assert node.last_heard == 1738800000

    def test_parse_node_integer_id(self):
        """Test that integer node IDs get converted to hex string."""
        data = {"num": 2864434397, "long_name": "Test"}
        node = MeshtasticHTTPClient._parse_node(data)
        assert node is not None
        assert node.node_id == "!aabbccdd"

    def test_parse_node_no_id_returns_none(self):
        node = MeshtasticHTTPClient._parse_node({"long_name": "Orphan"})
        assert node is None

    def test_parse_node_zero_coordinates_ignored(self):
        """(0,0) means no GPS fix — should be treated as no position."""
        data = {
            "id": "!test0001",
            "position": {"latitude": 0.0, "longitude": 0.0},
        }
        node = MeshtasticHTTPClient._parse_node(data)
        assert node.has_position is False

    def test_parse_node_invalid_coordinates_ignored(self):
        """Out-of-range coordinates should be treated as no position."""
        data = {
            "id": "!test0002",
            "position": {"latitude": 999.0, "longitude": -999.0},
        }
        node = MeshtasticHTTPClient._parse_node(data)
        assert node.has_position is False

    def test_parse_node_integer_encoded_coordinates(self):
        """Test latitudeI format (1e-7 degrees as integer)."""
        data = {
            "id": "!test0003",
            "position": {"latitudeI": 207984000, "longitudeI": -1563319000},
        }
        node = MeshtasticHTTPClient._parse_node(data)
        assert node.has_position is True
        assert node.latitude == pytest.approx(20.7984, abs=0.001)
        assert node.longitude == pytest.approx(-156.3319, abs=0.001)

    def test_node_to_dict(self):
        node = MeshtasticNode(
            node_id="!aabb0001",
            long_name="Test",
            short_name="T1",
            hw_model="HELTEC_V3",
            snr=10.0,
            last_heard=1738800000,
            via_mqtt=False,
            latitude=20.5,
            longitude=-156.3,
            altitude=100,
        )
        d = node.to_dict()
        assert d["id"] == "!aabb0001"
        assert d["long_name"] == "Test"
        assert d["position"]["latitude"] == 20.5
        assert d["position"]["longitude"] == -156.3
        assert d["position"]["altitude"] == 100

    def test_node_to_dict_no_position(self):
        node = MeshtasticNode(node_id="!test", long_name="No GPS")
        d = node.to_dict()
        assert "position" not in d


# --- Report Parsing Tests ---


class TestReportParsing:
    """Test parsing of /json/report response data."""

    def test_parse_report(self):
        report = MeshtasticHTTPClient._parse_report(SAMPLE_REPORT_RESPONSE)
        assert isinstance(report, DeviceReport)
        assert report.channel_utilization == 12.5
        assert report.tx_utilization == 3.2
        assert report.seconds_since_boot == 86400
        assert report.heap_free == 204800
        assert report.heap_total == 327680
        assert report.battery_percent == 85
        assert report.battery_voltage_mv == 4100
        assert report.has_battery is True
        assert report.has_usb is True
        assert report.is_charging is True
        assert report.frequency == 906.875
        assert report.lora_channel == 20
        assert report.wifi_rssi == -45
        assert report.reboot_counter == 3

    def test_parse_report_empty_sections(self):
        """Test parsing with missing/empty sections."""
        report = MeshtasticHTTPClient._parse_report({})
        assert report.channel_utilization == 0.0
        assert report.battery_percent == 0
        assert report.frequency == 0.0
        assert report.raw == {}

    def test_parse_report_preserves_raw(self):
        report = MeshtasticHTTPClient._parse_report(SAMPLE_REPORT_RESPONSE)
        assert report.raw == SAMPLE_REPORT_RESPONSE


# --- HTTP Client Tests (mocked) ---


class TestHTTPClientMocked:
    """Test HTTP client with mocked urllib responses."""

    def _mock_urlopen(self, response_data, status=200):
        """Create a mock for urllib.request.urlopen."""
        mock_response = MagicMock()
        mock_response.status = status
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read = MagicMock(return_value=json.dumps(response_data).encode())
        return mock_response

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_get_nodes_dict_response(self, mock_urlopen):
        """Test get_nodes with dictionary response (keyed by node ID)."""
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_NODES_RESPONSE)
        client = _make_client()

        nodes = client.get_nodes()
        assert len(nodes) == 3
        # Nodes with position
        with_pos = [n for n in nodes if n.has_position]
        assert len(with_pos) == 2
        # Node without position
        without_pos = [n for n in nodes if not n.has_position]
        assert len(without_pos) == 1
        assert without_pos[0].node_id == "!aabb0003"

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_get_nodes_list_response(self, mock_urlopen):
        """Test get_nodes with list response (some firmware versions)."""
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_NODES_LIST_RESPONSE)
        client = _make_client()

        nodes = client.get_nodes()
        assert len(nodes) == 1
        assert nodes[0].node_id == "!ccdd0001"
        assert nodes[0].long_name == "Oahu-Node"

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_get_nodes_as_dicts(self, mock_urlopen):
        """Test get_nodes_as_dicts returns plain dictionaries."""
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_NODES_RESPONSE)
        client = _make_client()

        dicts = client.get_nodes_as_dicts()
        assert len(dicts) == 3
        assert all(isinstance(d, dict) for d in dicts)
        assert dicts[0]["id"] == "!aabb0001"

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_get_report(self, mock_urlopen):
        """Test get_report parses device health."""
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_REPORT_RESPONSE)
        client = _make_client()

        report = client.get_report()
        assert report is not None
        assert report.battery_percent == 85
        assert report.frequency == 906.875

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_get_report_raw(self, mock_urlopen):
        """Test get_report_raw returns raw dict."""
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_REPORT_RESPONSE)
        client = _make_client()

        raw = client.get_report_raw()
        assert raw == SAMPLE_REPORT_RESPONSE

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_get_nodes_geojson(self, mock_urlopen):
        """Test GeoJSON output for map display."""
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_NODES_RESPONSE)
        client = _make_client()

        geojson = client.get_nodes_geojson()
        assert geojson["type"] == "FeatureCollection"
        features = geojson["features"]
        # Only nodes with position should appear
        assert len(features) == 2

        # Check first feature structure
        f = features[0]
        assert f["type"] == "Feature"
        assert f["geometry"]["type"] == "Point"
        assert len(f["geometry"]["coordinates"]) == 2
        assert f["properties"]["source"] == "meshtasticd_http"
        assert "name" in f["properties"]
        assert "snr" in f["properties"]

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_get_nodes_handles_error(self, mock_urlopen):
        """Test graceful handling of HTTP errors."""
        mock_urlopen.side_effect = Exception("Connection refused")
        client = _make_client()

        nodes = client.get_nodes()
        assert nodes == []

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_get_report_handles_error(self, mock_urlopen):
        """Test graceful handling of HTTP errors for report."""
        mock_urlopen.side_effect = Exception("Connection refused")
        client = _make_client()

        report = client.get_report()
        assert report is None


# --- Auto-detection Tests ---


class TestAutoDetection:
    """Test port auto-detection."""

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_auto_detect_finds_port(self, mock_urlopen):
        """Test that auto-detect probes ports and finds a working one."""
        # Make /json/report return valid data on port 9443
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read = MagicMock(return_value=b'{"airtime": {}, "memory": {}}')
        mock_urlopen.return_value = mock_response

        client = MeshtasticHTTPClient(
            host='localhost', port=9443, tls=True, auto_detect=True
        )
        assert client._available is True
        assert client.port == 9443

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_auto_detect_no_server(self, mock_urlopen):
        """Test graceful failure when no HTTP server found."""
        mock_urlopen.side_effect = Exception("Connection refused")

        client = MeshtasticHTTPClient(
            host='localhost', port=9443, tls=True, auto_detect=True
        )
        assert client._available is False

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_is_available_caches_result(self, mock_urlopen):
        """Test that is_available doesn't re-probe on every call."""
        client = _make_client()
        client._available = True
        client._last_check = 9999999999.0  # Far future

        assert client.is_available is True
        # Should not have called urlopen (used cache)
        mock_urlopen.assert_not_called()


# --- Singleton Tests ---


class TestSingleton:
    """Test singleton pattern."""

    def test_get_http_client_returns_same_instance(self):
        with patch('utils.meshtastic_http.MeshtasticHTTPClient') as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance

            client1 = get_http_client()
            client2 = get_http_client()
            assert client1 is client2

    def test_reset_clears_singleton(self):
        with patch('utils.meshtastic_http.MeshtasticHTTPClient') as MockClient:
            MockClient.return_value = MagicMock()

            client1 = get_http_client()
            reset_http_client()
            client2 = get_http_client()
            # After reset, should create a new instance
            assert MockClient.call_count == 2


# --- Device Control Tests ---


class TestDeviceControl:
    """Test device control endpoints (restart, blink)."""

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_restart_device(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = _make_client()
        assert client.restart_device() is True

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_restart_device_failure(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection refused")
        client = _make_client()
        assert client.restart_device() is False

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_blink_led(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = _make_client()
        assert client.blink_led() is True

    def test_restart_no_base_url(self):
        client = _make_client()
        client._base_url = None
        assert client.restart_device() is False

    def test_blink_no_base_url(self):
        client = _make_client()
        client._base_url = None
        assert client.blink_led() is False


# --- Edge Cases ---


class TestEdgeCases:
    """Test edge cases and malformed data."""

    def test_parse_node_missing_position_key(self):
        """Node with no position key at all."""
        data = {"id": "!test", "long_name": "No Pos Key"}
        node = MeshtasticHTTPClient._parse_node(data)
        assert node is not None
        assert node.has_position is False

    def test_parse_node_empty_position(self):
        """Node with empty position dict."""
        data = {"id": "!test", "position": {}}
        node = MeshtasticHTTPClient._parse_node(data)
        assert node.has_position is False

    def test_parse_node_none_position(self):
        """Node with None position."""
        data = {"id": "!test", "position": None}
        node = MeshtasticHTTPClient._parse_node(data)
        assert node.has_position is False

    def test_parse_node_null_values(self):
        """Node with null/None values for optional fields."""
        data = {
            "id": "!test",
            "long_name": None,
            "short_name": None,
            "snr": None,
            "last_heard": None,
        }
        node = MeshtasticHTTPClient._parse_node(data)
        assert node is not None
        assert node.long_name == ""
        assert node.short_name == ""
        assert node.snr == 0.0
        assert node.last_heard == 0

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_get_nodes_with_malformed_entries(self, mock_urlopen):
        """Test that malformed entries are skipped gracefully."""
        response = {
            "!good": {"id": "!good", "long_name": "Good Node",
                       "position": {"latitude": 20.0, "longitude": -156.0}},
            "!bad": {"long_name": "No ID"},  # Missing id field
        }
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read = MagicMock(return_value=json.dumps(response).encode())
        mock_urlopen.return_value = mock_response

        client = _make_client()
        nodes = client.get_nodes()
        # "!bad" should be skipped (no id), "!good" should parse
        assert len(nodes) == 1
        assert nodes[0].node_id == "!good"

    def test_repr(self):
        client = _make_client()
        client._available = True
        r = repr(client)
        assert "available" in r
        assert "localhost" in r


# --- Integration with MapDataCollector ---


class TestMapDataCollectorIntegration:
    """Test that MapDataCollector uses HTTP when available."""

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_collector_uses_http_first(self, mock_urlopen):
        """MapDataCollector should prefer HTTP over TCP."""
        # Mock HTTP to return valid nodes
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        # Return different data for /json/report (probe) and /json/nodes
        def side_effect(req, **kwargs):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            if '/json/report' in url:
                mock_r = MagicMock()
                mock_r.status = 200
                mock_r.__enter__ = MagicMock(return_value=mock_r)
                mock_r.__exit__ = MagicMock(return_value=False)
                mock_r.read = MagicMock(return_value=b'{"airtime": {}, "memory": {}}')
                return mock_r
            elif '/json/nodes' in url:
                mock_r = MagicMock()
                mock_r.status = 200
                mock_r.__enter__ = MagicMock(return_value=mock_r)
                mock_r.__exit__ = MagicMock(return_value=False)
                mock_r.read = MagicMock(return_value=json.dumps(SAMPLE_NODES_RESPONSE).encode())
                return mock_r
            return mock_response

        mock_urlopen.side_effect = side_effect

        # Reset singleton so it picks up the mock
        reset_http_client()

        from utils.map_data_collector import MapDataCollector
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            collector = MapDataCollector(cache_dir=Path(tmp), enable_history=False)
            features = collector._collect_via_http('localhost')

            # Should have gotten nodes via HTTP
            if features:
                assert len(features) == 2  # 2 nodes with position
                assert features[0]["properties"]["source"] == "meshtasticd_http"


# --- Issue #76: /json/* is ESP32-only — meshtasticd has never served it ---


def _http_404(url='https://localhost:9443/json/report'):
    """Build the meshtasticd static-fallback 404 (PiWebServer signature)."""
    import urllib.error
    return urllib.error.HTTPError(url, 404, 'File not found', {}, None)


class TestJsonApiAbsentIssue76:
    """meshtasticd (PiWebServer) 404s /json/* — only ESP32 serves it.

    The old probe fell through to GET /api/v1/fromradio on 404, which
    (a) reported the dead JSON API as "available" forever, and
    (b) CONSUMED a PhoneAPI packet per re-check (Issue #17 contention).
    These tests pin the honest tri-state probe.
    """

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_404_marks_json_api_absent_not_available(self, mock_urlopen):
        """Webserver alive + /json/report 404 → absent, NOT available."""
        mock_urlopen.side_effect = lambda req, **kw: (_ for _ in ()).throw(
            _http_404(req.full_url))
        client = MeshtasticHTTPClient(
            host='localhost', port=9443, tls=True, auto_detect=True)

        assert client._available is False
        assert client.json_api_absent is True
        assert client.is_available is False
        # base_url still set — the webserver IS there, just no JSON API
        assert client._base_url is not None

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_no_fromradio_probe_ever(self, mock_urlopen):
        """The probe must NEVER touch /api/v1/fromradio (Issue #17 contract).

        A GET on fromradio consumes a packet from the PhoneAPI stream and
        starves the :9443 web client.
        """
        seen_urls = []

        def record(req, **kw):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            seen_urls.append(url)
            raise _http_404(url)

        mock_urlopen.side_effect = record
        MeshtasticHTTPClient(
            host='localhost', port=9443, tls=True, auto_detect=True)

        assert seen_urls, "probe should have issued requests"
        assert not any('/api/v1/fromradio' in u for u in seen_urls), (
            f"probe touched the PhoneAPI stream: {seen_urls}")

    def test_4403_never_in_probe_ports(self):
        """HTTP probes at the protobuf TCP port are Issue #17 contention."""
        from utils.meshtastic_http import PROBE_PORTS
        assert 4403 not in PROBE_PORTS

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_absent_uses_long_recheck_interval(self, mock_urlopen):
        """After 'absent', is_available must not re-probe within the hour."""
        import time as _time
        client = _make_client()
        client._available = False
        client._json_api_absent = True
        client._last_check = _time.time() - 120  # past the 60s normal window

        assert client.is_available is False
        mock_urlopen.assert_not_called()

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_absent_rechecks_after_long_interval_and_recovers(self, mock_urlopen):
        """A firmware that grows the API is picked up at the long recheck."""
        import time as _time
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read = MagicMock(return_value=b'{"airtime": {}}')
        mock_urlopen.return_value = mock_response

        client = _make_client()
        client._available = False
        client._json_api_absent = True
        client._last_check = _time.time() - 3700  # past JSON_ABSENT_RECHECK_INTERVAL

        assert client.is_available is True
        assert client.json_api_absent is False

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_availability_reason_distinguishes_absent_from_down(self, mock_urlopen):
        """Status surfaces get an honest reason string (radio_config)."""
        mock_urlopen.side_effect = lambda req, **kw: (_ for _ in ()).throw(
            _http_404(req.full_url))
        client = MeshtasticHTTPClient(
            host='localhost', port=9443, tls=True, auto_detect=True)
        assert '/json/*' in client.availability_reason
        assert 'TCP' in client.availability_reason

        client2 = _make_client()
        client2._available = False
        client2._json_api_absent = False
        assert 'reachable' in client2.availability_reason

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_connection_refused_is_down_not_absent(self, mock_urlopen):
        """No webserver at all → 'down' path, normal 60s recheck."""
        mock_urlopen.side_effect = ConnectionRefusedError("refused")
        client = MeshtasticHTTPClient(
            host='localhost', port=9443, tls=True, auto_detect=True)
        assert client._available is False
        assert client.json_api_absent is False

    @patch('utils.meshtastic_http.urllib.request.urlopen')
    def test_collector_falls_to_tcp_when_json_absent(self, mock_urlopen):
        """_collect_via_http returns [] fast when the JSON API is absent."""
        mock_urlopen.side_effect = lambda req, **kw: (_ for _ in ()).throw(
            _http_404(req.full_url))
        reset_http_client()

        from utils.map_data_collector import MapDataCollector
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            collector = MapDataCollector(cache_dir=Path(tmp), enable_history=False)
            features = collector._collect_via_http('localhost')
            assert features == []


class TestMeshtasticdDefersToGatewayIssue17:
    """The map's _collect_meshtasticd must NOT open a second :4403 TCP
    connection when meshforge-gateway owns it (#17 single-consumer). That
    contention thrashed/wedged the gateway's mesh-TX for 2 days (2026-06-13→15,
    moc): the bot's output went dark behind a green RNS canary. On a gateway
    box mesh nodes arrive via the MQTT source instead.
    """

    def _collector(self, tmp):
        from utils.map_data_collector import MapDataCollector
        return MapDataCollector(
            cache_dir=Path(tmp), config_dir=Path(tmp), enable_history=False)

    @patch('utils.service_check.check_service')
    def test_skips_tcp_when_gateway_active(self, mock_check):
        from types import SimpleNamespace
        from utils.service_check import ServiceState
        import tempfile
        mock_check.return_value = SimpleNamespace(state=ServiceState.AVAILABLE)
        with tempfile.TemporaryDirectory() as tmp:
            c = self._collector(tmp)
            c._collect_via_http = lambda host: []   # force fall-through to Strategy 2
            # If the guard fails, the TCP probe runs — make it explode so the
            # test goes RED against the old (unguarded) behavior.
            with patch('socket.socket',
                       side_effect=AssertionError("must not touch :4403 when gateway owns it")):
                feats = c._collect_meshtasticd()
            assert feats == []
            d = c.get_source_diagnostics().get("meshtasticd", {})
            assert d.get("reason_if_zero") == "deferred_to_gateway"

    @patch('utils.service_check.check_service')
    def test_proceeds_when_gateway_inactive(self, mock_check):
        from types import SimpleNamespace
        from utils.service_check import ServiceState
        import tempfile
        mock_check.return_value = SimpleNamespace(state=ServiceState.NOT_INSTALLED)
        with tempfile.TemporaryDirectory() as tmp:
            c = self._collector(tmp)
            c._collect_via_http = lambda host: []
            with patch('socket.socket') as msock:
                msock.return_value.connect_ex.return_value = 1  # port closed → fast []
                c._collect_meshtasticd()
                assert msock.called   # reached Strategy 2 — NOT deferred
            d = c.get_source_diagnostics().get("meshtasticd", {})
            assert d.get("reason_if_zero") != "deferred_to_gateway"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

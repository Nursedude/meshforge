"""
Tests for Meshtastic Protobuf-over-HTTP Client.

Tests the protobuf transport, session management, config read/write,
event polling, and neighbor/metadata/traceroute parsing.

All HTTP calls and protobuf messages are mocked — no live device needed.
"""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock, call

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Skip entire module if meshtastic is not installed
meshtastic = pytest.importorskip("meshtastic", reason="meshtastic package not installed")

from meshtastic.protobuf import (
    admin_pb2,
    config_pb2,
    mesh_pb2,
    module_config_pb2,
    portnums_pb2,
    telemetry_pb2,
)

from gateway.meshtastic_protobuf_ops import (
    CONFIG_TYPE_NAMES,
    MODULE_CONFIG_TYPE_NAMES,
    DeviceConfigSnapshot,
    DeviceMetadataResult,
    ModuleConfigSnapshot,
    NeighborEntry,
    NeighborReport,
    ProtobufEventType,
    ProtobufTransportConfig,
    TracerouteResult,
    parse_device_metadata,
    parse_neighbor_info,
    parse_position,
    parse_traceroute,
)

from gateway.meshtastic_protobuf_client import (
    MeshtasticProtobufClient,
    get_protobuf_client,
    reset_protobuf_client,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def transport_config():
    """Default transport config for testing."""
    return ProtobufTransportConfig(
        host="localhost",
        port=9443,
        tls=True,
        poll_interval=0.1,
        connect_timeout=2.0,
        read_timeout=2.0,
        session_timeout=5.0,
        max_empty_polls=3,
        backoff_interval=0.2,
    )


@pytest.fixture
def client(transport_config):
    """Create a fresh client instance."""
    c = MeshtasticProtobufClient(transport_config)
    yield c
    c.disconnect()


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset singleton between tests."""
    reset_protobuf_client()
    yield
    reset_protobuf_client()


# ---------------------------------------------------------------------------
# Helper: build mock FromRadio messages
# ---------------------------------------------------------------------------

def make_my_info(node_num: int = 0xAABB0001) -> bytes:
    """Build a FromRadio with MyNodeInfo."""
    fr = mesh_pb2.FromRadio()
    fr.my_info.my_node_num = node_num
    return fr.SerializeToString()


def make_node_info(node_num: int, long_name: str = "TestNode") -> bytes:
    """Build a FromRadio with NodeInfo."""
    fr = mesh_pb2.FromRadio()
    fr.node_info.num = node_num
    fr.node_info.user.long_name = long_name
    fr.node_info.user.short_name = long_name[:4]
    fr.node_info.user.id = f"!{node_num:08x}"
    return fr.SerializeToString()


def make_config(lora_region: int = 1) -> bytes:
    """Build a FromRadio with Config (lora section)."""
    fr = mesh_pb2.FromRadio()
    fr.config.lora.region = lora_region
    fr.config.lora.use_preset = True
    return fr.SerializeToString()


def make_module_config_mqtt(enabled: bool = True) -> bytes:
    """Build a FromRadio with ModuleConfig (mqtt section)."""
    fr = mesh_pb2.FromRadio()
    fr.moduleConfig.mqtt.enabled = enabled
    return fr.SerializeToString()


def make_channel(index: int = 0, name: str = "Primary") -> bytes:
    """Build a FromRadio with Channel."""
    fr = mesh_pb2.FromRadio()
    fr.channel.index = index
    fr.channel.settings.name = name
    return fr.SerializeToString()


def make_config_complete(config_id: int) -> bytes:
    """Build a FromRadio with config_complete_id."""
    fr = mesh_pb2.FromRadio()
    fr.config_complete_id = config_id
    return fr.SerializeToString()


def make_neighbor_info_packet(
    from_node: int, neighbors: list
) -> bytes:
    """Build a FromRadio with a NEIGHBORINFO_APP MeshPacket."""
    ni = mesh_pb2.NeighborInfo()
    ni.node_id = from_node
    ni.node_broadcast_interval_secs = 900
    for n_id, n_snr in neighbors:
        n = ni.neighbors.add()
        n.node_id = n_id
        n.snr = n_snr

    fr = mesh_pb2.FromRadio()
    setattr(fr.packet, 'from', from_node)
    fr.packet.decoded.portnum = portnums_pb2.PortNum.NEIGHBORINFO_APP
    fr.packet.decoded.payload = ni.SerializeToString()
    fr.packet.id = 12345
    return fr.SerializeToString()


def make_traceroute_response(
    from_node: int, to_node: int, route: list
) -> bytes:
    """Build a FromRadio with a TRACEROUTE_APP response."""
    rd = mesh_pb2.RouteDiscovery()
    rd.route.extend(route)

    fr = mesh_pb2.FromRadio()
    setattr(fr.packet, 'from', from_node)
    setattr(fr.packet, 'to', to_node)
    fr.packet.decoded.portnum = portnums_pb2.PortNum.TRACEROUTE_APP
    fr.packet.decoded.payload = rd.SerializeToString()
    fr.packet.id = 99999
    return fr.SerializeToString()


def make_position_packet(
    from_node: int, lat_i: int, lon_i: int, alt: int = 0
) -> bytes:
    """Build a FromRadio with a POSITION_APP packet."""
    pos = mesh_pb2.Position()
    pos.latitude_i = lat_i
    pos.longitude_i = lon_i
    pos.altitude = alt

    fr = mesh_pb2.FromRadio()
    setattr(fr.packet, 'from', from_node)
    fr.packet.decoded.portnum = portnums_pb2.PortNum.POSITION_APP
    fr.packet.decoded.payload = pos.SerializeToString()
    fr.packet.id = 55555
    return fr.SerializeToString()


# ---------------------------------------------------------------------------
# Data class tests
# ---------------------------------------------------------------------------

class TestDataClasses:
    """Tests for protobuf ops data classes."""

    def test_transport_config_defaults(self):
        cfg = ProtobufTransportConfig()
        assert cfg.host == "localhost"
        assert cfg.port == 9443
        assert cfg.tls is True
        assert cfg.poll_interval == 0.5

    def test_device_config_snapshot_empty(self):
        snap = DeviceConfigSnapshot()
        assert snap.lora is None
        assert snap.to_dict() == {}

    def test_module_config_snapshot_empty(self):
        snap = ModuleConfigSnapshot()
        assert snap.mqtt is None
        assert snap.to_dict() == {}

    def test_neighbor_entry(self):
        ne = NeighborEntry(node_id=0xAABB, snr=8.5, last_rx_time=1000)
        assert ne.node_id == 0xAABB
        assert ne.snr == 8.5

    def test_neighbor_report(self):
        nr = NeighborReport(
            reporting_node_id=0xAABB,
            neighbors=[
                NeighborEntry(node_id=0xCCDD, snr=12.0),
                NeighborEntry(node_id=0xEEFF, snr=-3.0),
            ],
        )
        assert len(nr.neighbors) == 2
        assert nr.neighbors[0].snr == 12.0

    def test_device_metadata_result(self):
        md = DeviceMetadataResult(
            firmware_version="2.7.7",
            hw_model="HELTEC_V3",
            has_wifi=True,
        )
        assert md.firmware_version == "2.7.7"
        assert md.has_wifi is True

    def test_traceroute_result(self):
        tr = TracerouteResult(
            destination=0xAABB,
            route=[0xCCDD, 0xEEFF],
            completed=True,
        )
        assert tr.destination == 0xAABB
        assert len(tr.route) == 2

    def test_event_type_values(self):
        assert ProtobufEventType.PACKET_RECEIVED.value == "packet_received"
        assert ProtobufEventType.NEIGHBOR_INFO.value == "neighbor_info"

    def test_config_type_names(self):
        assert CONFIG_TYPE_NAMES[0] == 'device'
        assert CONFIG_TYPE_NAMES[5] == 'lora'

    def test_module_config_type_names(self):
        assert MODULE_CONFIG_TYPE_NAMES[0] == 'mqtt'
        assert MODULE_CONFIG_TYPE_NAMES[9] == 'neighbor_info'


# ---------------------------------------------------------------------------
# Protobuf parsing helper tests
# ---------------------------------------------------------------------------

class TestParsers:
    """Tests for stateless protobuf parsing helpers."""

    def test_parse_neighbor_info(self):
        ni = mesh_pb2.NeighborInfo()
        ni.node_id = 0xAABB
        ni.node_broadcast_interval_secs = 900
        n = ni.neighbors.add()
        n.node_id = 0xCCDD
        n.snr = 12.5
        n.last_rx_time = 1000

        result = parse_neighbor_info(ni.SerializeToString(), 0xAABB)
        assert result is not None
        assert result.reporting_node_id == 0xAABB
        assert len(result.neighbors) == 1
        assert result.neighbors[0].node_id == 0xCCDD
        assert result.neighbors[0].snr == 12.5

    def test_parse_neighbor_info_empty(self):
        ni = mesh_pb2.NeighborInfo()
        ni.node_id = 0xAABB
        result = parse_neighbor_info(ni.SerializeToString(), 0xAABB)
        assert result is not None
        assert result.neighbors == []

    def test_parse_neighbor_info_invalid(self):
        result = parse_neighbor_info(b"invalid", 0xAABB)
        assert result is None

    def test_parse_device_metadata(self):
        admin = admin_pb2.AdminMessage()
        md = admin.get_device_metadata_response
        md.firmware_version = "2.7.7"
        md.canShutdown = True
        md.hasWifi = True
        md.hasBluetooth = True

        result = parse_device_metadata(admin.SerializeToString())
        assert result is not None
        assert result.firmware_version == "2.7.7"
        assert result.can_shutdown is True
        assert result.has_wifi is True

    def test_parse_device_metadata_wrong_field(self):
        admin = admin_pb2.AdminMessage()
        admin.begin_edit_settings = True
        result = parse_device_metadata(admin.SerializeToString())
        assert result is None

    def test_parse_traceroute(self):
        rd = mesh_pb2.RouteDiscovery()
        rd.route.extend([0xCCDD, 0xEEFF])
        rd.snr_towards.extend([48, 24])  # raw values, divided by 4

        result = parse_traceroute(rd.SerializeToString(), 0xAABB)
        assert result is not None
        assert result.destination == 0xAABB
        assert result.route == [0xCCDD, 0xEEFF]
        assert result.snr_towards == [12.0, 6.0]
        assert result.completed is True

    def test_parse_traceroute_empty(self):
        rd = mesh_pb2.RouteDiscovery()
        result = parse_traceroute(rd.SerializeToString(), 0xAABB)
        assert result is not None
        assert result.route == []

    def test_parse_traceroute_invalid(self):
        result = parse_traceroute(b"invalid", 0xAABB)
        assert result is None

    def test_parse_position(self):
        pos = mesh_pb2.Position()
        pos.latitude_i = 207984000  # ~20.7984
        pos.longitude_i = -1563319000  # ~-156.3319
        pos.altitude = 45

        result = parse_position(pos.SerializeToString())
        assert result is not None
        assert abs(result['latitude'] - 20.7984) < 0.001
        assert abs(result['longitude'] - (-156.3319)) < 0.001
        assert result['altitude'] == 45

    def test_parse_position_empty(self):
        pos = mesh_pb2.Position()
        result = parse_position(pos.SerializeToString())
        assert result is None  # No data set

    def test_parse_position_invalid(self):
        result = parse_position(b"invalid")
        assert result is None


# ---------------------------------------------------------------------------
# Client constructor tests
# ---------------------------------------------------------------------------

class TestClientInit:
    """Tests for MeshtasticProtobufClient initialization."""

    def test_default_config(self):
        c = MeshtasticProtobufClient()
        assert c.base_url == "https://localhost:9443"
        assert c.is_connected is False
        assert c.my_node_num is None

    def test_custom_config(self, transport_config):
        c = MeshtasticProtobufClient(transport_config)
        assert c.base_url == "https://localhost:9443"

    def test_http_config(self):
        cfg = ProtobufTransportConfig(tls=False, port=80)
        c = MeshtasticProtobufClient(cfg)
        assert c.base_url == "http://localhost:80"

    def test_repr(self, client):
        assert "disconnected" in repr(client)
        assert "9443" in repr(client)


# ---------------------------------------------------------------------------
# Singleton tests
# ---------------------------------------------------------------------------

class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_returns_same_instance(self):
        c1 = get_protobuf_client()
        c2 = get_protobuf_client()
        assert c1 is c2

    def test_reset_creates_new_instance(self):
        c1 = get_protobuf_client()
        reset_protobuf_client()
        c2 = get_protobuf_client()
        assert c1 is not c2


# ---------------------------------------------------------------------------
# Transport tests
# ---------------------------------------------------------------------------

class TestTransport:
    """Tests for HTTP transport layer."""

    @patch('gateway.meshtastic_protobuf_client.urllib.request.urlopen')
    def test_post_toradio_success(self, mock_urlopen, client):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        to_radio = mesh_pb2.ToRadio()
        to_radio.disconnect = True
        result = client._post_toradio(to_radio.SerializeToString())
        assert result is True

        # Verify the request
        req = mock_urlopen.call_args[0][0]
        assert '/api/v1/toradio' in req.full_url
        assert req.method == 'PUT'
        assert req.get_header('Content-type') == 'application/x-protobuf'

    @patch('gateway.meshtastic_protobuf_client.urllib.request.urlopen')
    def test_post_toradio_failure(self, mock_urlopen, client):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        result = client._post_toradio(b"test")
        assert result is False

    @patch('gateway.meshtastic_protobuf_client.urllib.request.urlopen')
    def test_get_fromradio_success(self, mock_urlopen, client):
        fr_bytes = make_my_info()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fr_bytes
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = client._get_fromradio()
        assert result == fr_bytes

    @patch('gateway.meshtastic_protobuf_client.urllib.request.urlopen')
    def test_get_fromradio_empty(self, mock_urlopen, client):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = client._get_fromradio()
        assert result is None


import urllib.error


# ---------------------------------------------------------------------------
# Session tests
# ---------------------------------------------------------------------------

class TestSession:
    """Tests for session establishment (connect/disconnect)."""

    @patch('gateway.meshtastic_protobuf_client.urllib.request.urlopen')
    def test_connect_success(self, mock_urlopen, client):
        """Test full session setup with config drain."""
        # Build the sequence of FromRadio messages
        config_id = [None]

        responses = []
        call_count = [0]

        def side_effect(req, **kwargs):
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)

            url = req.full_url if hasattr(req, 'full_url') else str(req)

            if 'toradio' in url:
                # Capture the config_id from the ToRadio message
                data = req.data
                to_radio = mesh_pb2.ToRadio()
                to_radio.ParseFromString(data)
                if to_radio.want_config_id:
                    config_id[0] = to_radio.want_config_id
                mock_resp.status = 200
                return mock_resp

            if 'fromradio' in url:
                seq = [
                    make_my_info(0xAABB0001),
                    make_node_info(0xAABB0001, "LocalNode"),
                    make_node_info(0xAABB0002, "RemoteNode"),
                    make_config(1),
                    make_module_config_mqtt(True),
                    make_channel(0, "Primary"),
                    None,  # Will be replaced with config_complete
                ]
                idx = call_count[0]
                call_count[0] += 1

                if idx < len(seq) - 1:
                    mock_resp.read.return_value = seq[idx]
                elif config_id[0] is not None:
                    mock_resp.read.return_value = make_config_complete(config_id[0])
                else:
                    mock_resp.read.return_value = b""
                return mock_resp

            mock_resp.status = 200
            return mock_resp

        mock_urlopen.side_effect = side_effect

        result = client.connect()
        assert result is True
        assert client.is_connected is True
        assert client.my_node_num == 0xAABB0001

    @patch('gateway.meshtastic_protobuf_client.urllib.request.urlopen')
    def test_connect_timeout(self, mock_urlopen, transport_config):
        """Test connect times out when config_complete never arrives."""
        transport_config.session_timeout = 1.0  # Short timeout

        client = MeshtasticProtobufClient(transport_config)

        def side_effect(req, **kwargs):
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            url = req.full_url if hasattr(req, 'full_url') else str(req)

            if 'toradio' in url:
                mock_resp.status = 200
                return mock_resp
            if 'fromradio' in url:
                mock_resp.read.return_value = b""
                return mock_resp
            return mock_resp

        mock_urlopen.side_effect = side_effect

        result = client.connect()
        assert result is False
        assert client.is_connected is False

    def test_disconnect(self, client):
        """Test disconnect clears state."""
        with client._lock:
            client._connected = True
        client.disconnect()
        assert client.is_connected is False


# ---------------------------------------------------------------------------
# Callback tests
# ---------------------------------------------------------------------------

class TestCallbacks:
    """Tests for the event callback system."""

    def test_register_callback(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.PACKET_RECEIVED, cb)
        assert cb in client._callbacks[ProtobufEventType.PACKET_RECEIVED]

    def test_unregister_callback(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.PACKET_RECEIVED, cb)
        client.unregister_callback(ProtobufEventType.PACKET_RECEIVED, cb)
        assert cb not in client._callbacks[ProtobufEventType.PACKET_RECEIVED]

    def test_unregister_nonexistent(self, client):
        cb = MagicMock()
        # Should not raise
        client.unregister_callback(ProtobufEventType.PACKET_RECEIVED, cb)

    def test_notify_fires_callbacks(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.NODE_INFO_UPDATED, cb)
        client._notify(ProtobufEventType.NODE_INFO_UPDATED, {'num': 123})
        cb.assert_called_once_with(
            ProtobufEventType.NODE_INFO_UPDATED, {'num': 123}
        )

    def test_notify_multiple_callbacks(self, client):
        cb1 = MagicMock()
        cb2 = MagicMock()
        client.register_callback(ProtobufEventType.NEIGHBOR_INFO, cb1)
        client.register_callback(ProtobufEventType.NEIGHBOR_INFO, cb2)
        client._notify(ProtobufEventType.NEIGHBOR_INFO, {'data': True})
        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_callback_error_doesnt_crash(self, client):
        cb = MagicMock(side_effect=RuntimeError("boom"))
        client.register_callback(ProtobufEventType.PACKET_RECEIVED, cb)
        # Should not raise
        client._notify(ProtobufEventType.PACKET_RECEIVED, {})


# ---------------------------------------------------------------------------
# Dispatch tests
# ---------------------------------------------------------------------------

class TestDispatch:
    """Tests for FromRadio dispatch logic."""

    def test_dispatch_node_info(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.NODE_INFO_UPDATED, cb)
        data = make_node_info(0xCCDD0001, "TestNode")
        client._dispatch_fromradio(data)
        cb.assert_called_once()
        assert 0xCCDD0001 in client._node_infos

    def test_dispatch_neighbor_info_packet(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.NEIGHBOR_INFO, cb)
        data = make_neighbor_info_packet(
            0xAABB, [(0xCCDD, 12.0), (0xEEFF, -3.0)]
        )
        client._dispatch_fromradio(data)
        cb.assert_called_once()
        event_type, event_data = cb.call_args[0]
        assert event_type == ProtobufEventType.NEIGHBOR_INFO
        report = event_data['report']
        assert len(report.neighbors) == 2

    def test_dispatch_position_packet(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.POSITION_RECEIVED, cb)
        data = make_position_packet(0xAABB, 207984000, -1563319000, 45)
        client._dispatch_fromradio(data)
        cb.assert_called_once()
        event_type, event_data = cb.call_args[0]
        assert abs(event_data['position']['latitude'] - 20.7984) < 0.001

    def test_dispatch_config(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.CONFIG_RECEIVED, cb)
        data = make_config(1)
        client._dispatch_fromradio(data)
        cb.assert_called_once()

    def test_dispatch_module_config(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.MODULE_CONFIG_RECEIVED, cb)
        data = make_module_config_mqtt(True)
        client._dispatch_fromradio(data)
        cb.assert_called_once()

    def test_dispatch_channel(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.CHANNEL_RECEIVED, cb)
        data = make_channel(0, "LongFast")
        client._dispatch_fromradio(data)
        cb.assert_called_once()

    def test_dispatch_log_record(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.LOG_RECORD, cb)

        fr = mesh_pb2.FromRadio()
        fr.log_record.message = "Test log entry"
        client._dispatch_fromradio(fr.SerializeToString())
        cb.assert_called_once()

    def test_dispatch_invalid_data(self, client):
        """Invalid data should not crash."""
        client._dispatch_fromradio(b"not a protobuf")
        # No crash = pass


# ---------------------------------------------------------------------------
# Packet ID tests
# ---------------------------------------------------------------------------

class TestPacketId:
    """Tests for packet ID generation."""

    def test_unique_ids(self, client):
        ids = set()
        for _ in range(1000):
            ids.add(client._generate_packet_id())
        assert len(ids) == 1000

    def test_wraps_at_max(self, client):
        with client._lock:
            client._packet_id_counter = 0xFFFFFFFE
        id1 = client._generate_packet_id()
        id2 = client._generate_packet_id()
        assert id1 == 0xFFFFFFFF
        # Should wrap (0 is skipped, goes to 1)
        assert id2 == 1


# ---------------------------------------------------------------------------
# Polling tests
# ---------------------------------------------------------------------------

class TestPolling:
    """Tests for the background polling loop."""

    def test_start_stop_polling(self, client):
        with client._lock:
            client._connected = True

        with patch.object(client, '_get_fromradio', return_value=None):
            client.start_polling()
            assert client.is_polling is True
            time.sleep(0.3)
            client.stop_polling()
            assert client.is_polling is False

    def test_polling_daemon_thread(self, client):
        with patch.object(client, '_get_fromradio', return_value=None):
            client.start_polling()
            assert client._poll_thread.daemon is True
            client.stop_polling()

    def test_polling_dispatches_events(self, client):
        cb = MagicMock()
        client.register_callback(ProtobufEventType.NODE_INFO_UPDATED, cb)

        call_count = [0]

        def mock_get():
            call_count[0] += 1
            if call_count[0] == 1:
                return make_node_info(0xAAAA, "PolledNode")
            return None

        with patch.object(client, '_get_fromradio', side_effect=mock_get):
            client.start_polling()
            time.sleep(0.5)
            client.stop_polling()

        assert cb.called


# ---------------------------------------------------------------------------
# Pending request system tests
# ---------------------------------------------------------------------------

class TestPendingRequests:
    """Tests for the synchronous request/response system."""

    def test_register_and_resolve(self, client):
        event = client._register_pending(42)
        assert not event.is_set()

        mock_decoded = MagicMock()
        client._resolve_pending(42, mock_decoded)
        assert event.is_set()

    def test_wait_for_response_success(self, client):
        event = client._register_pending(42)

        # Resolve in another thread
        def resolve():
            time.sleep(0.1)
            client._resolve_pending(42, "response_data")

        t = threading.Thread(target=resolve, daemon=True)
        t.start()

        result = client._wait_for_response(42, timeout=2.0)
        assert result == "response_data"
        t.join()

    def test_wait_for_response_timeout(self, client):
        client._register_pending(42)
        result = client._wait_for_response(42, timeout=0.2)
        assert result is None

    def test_cleanup_on_timeout(self, client):
        client._register_pending(42)
        client._wait_for_response(42, timeout=0.1)
        with client._pending_lock:
            assert 42 not in client._pending_events
            assert 42 not in client._pending_responses


# ---------------------------------------------------------------------------
# Config read tests
# ---------------------------------------------------------------------------

class TestConfigRead:
    """Tests for config read operations."""

    def test_get_cached_config(self, client):
        """Test cached config from session setup."""
        snap = client.get_cached_config()
        assert isinstance(snap, DeviceConfigSnapshot)

    def test_get_cached_module_config(self, client):
        snap = client.get_cached_module_config()
        assert isinstance(snap, ModuleConfigSnapshot)

    def test_get_cached_node_infos(self, client):
        infos = client.get_cached_node_infos()
        assert isinstance(infos, dict)

    def test_get_cached_channels(self, client):
        channels = client.get_cached_channels()
        assert isinstance(channels, list)


# ---------------------------------------------------------------------------
# Config write tests
# ---------------------------------------------------------------------------

class TestConfigWrite:
    """Tests for config write operations."""

    @patch.object(MeshtasticProtobufClient, '_send_admin')
    def test_set_config_sends_begin_set_commit(self, mock_send, client):
        """Test that set_config sends begin_edit, set_config, commit_edit."""
        with client._lock:
            client._my_node_num = 0xAABB
            client._connected = True

        mock_send.return_value = 1  # success packet ID

        lora = config_pb2.Config.LoRaConfig()
        lora.region = 1
        lora.tx_power = 20

        result = client.set_config('lora', lora)
        assert result is True
        assert mock_send.call_count == 3  # begin, set, commit

    @patch.object(MeshtasticProtobufClient, '_send_admin')
    def test_set_module_config_sends_begin_set_commit(self, mock_send, client):
        with client._lock:
            client._my_node_num = 0xAABB
            client._connected = True

        mock_send.return_value = 1

        mqtt = module_config_pb2.ModuleConfig.MQTTConfig()
        mqtt.enabled = True
        mqtt.address = "mqtt.meshtastic.org"

        result = client.set_module_config('mqtt', mqtt)
        assert result is True
        assert mock_send.call_count == 3

    @patch.object(MeshtasticProtobufClient, '_send_admin')
    def test_set_config_unknown_section(self, mock_send, client):
        with client._lock:
            client._my_node_num = 0xAABB

        mock_send.return_value = 1
        lora = config_pb2.Config.LoRaConfig()
        result = client.set_config('nonexistent', lora)
        assert result is False


# ---------------------------------------------------------------------------
# Owner tests
# ---------------------------------------------------------------------------

class TestOwner:
    """Tests for owner get/set."""

    @patch.object(MeshtasticProtobufClient, '_send_admin')
    def test_set_owner(self, mock_send, client):
        with client._lock:
            client._my_node_num = 0xAABB

        mock_send.return_value = 1
        result = client.set_owner(long_name="Maui Gateway", short_name="MG01")
        assert result is True

    @patch.object(MeshtasticProtobufClient, '_send_admin')
    def test_set_owner_truncation(self, mock_send, client):
        with client._lock:
            client._my_node_num = 0xAABB

        mock_send.return_value = 1
        result = client.set_owner(short_name="TOOLONG")
        assert result is True
        # Verify truncation happened (check the admin message)
        admin_call = mock_send.call_args[0][0]
        assert len(admin_call.set_owner.short_name) <= 4


# ---------------------------------------------------------------------------
# Channel tests
# ---------------------------------------------------------------------------

class TestChannels:
    """Tests for channel operations."""

    def test_get_channels_from_cache(self, client):
        """Channels cached during session setup."""
        fr = mesh_pb2.FromRadio()
        fr.channel.index = 0
        fr.channel.settings.name = "TestCh"
        client._channels = [fr.channel]

        channels = client.get_channels()
        assert len(channels) == 1

    @patch.object(MeshtasticProtobufClient, '_send_admin')
    def test_set_channel(self, mock_send, client):
        with client._lock:
            client._my_node_num = 0xAABB

        mock_send.return_value = 1

        from meshtastic.protobuf import channel_pb2
        ch = channel_pb2.Channel()
        ch.index = 0
        ch.settings.name = "NewChannel"

        result = client.set_channel(ch)
        assert result is True


# ---------------------------------------------------------------------------
# send_mesh_packet tests
# ---------------------------------------------------------------------------

class TestSendMeshPacket:
    """Tests for low-level packet sending."""

    @patch.object(MeshtasticProtobufClient, '_send_toradio')
    def test_send_mesh_packet_success(self, mock_send, client):
        mock_send.return_value = True
        packet_id = client.send_mesh_packet(
            payload=b"test",
            dest_num=0xFFFFFFFF,
            portnum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
        )
        assert packet_id > 0
        mock_send.assert_called_once()

    @patch.object(MeshtasticProtobufClient, '_send_toradio')
    def test_send_mesh_packet_failure(self, mock_send, client):
        mock_send.return_value = False
        packet_id = client.send_mesh_packet(
            payload=b"test",
            dest_num=0xFFFFFFFF,
            portnum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
        )
        assert packet_id == 0

    @patch.object(MeshtasticProtobufClient, '_send_toradio')
    def test_send_mesh_packet_with_hop_limit(self, mock_send, client):
        mock_send.return_value = True
        packet_id = client.send_mesh_packet(
            payload=b"test",
            dest_num=0xAABB,
            portnum=portnums_pb2.PortNum.TRACEROUTE_APP,
            hop_limit=3,
            want_response=True,
        )
        assert packet_id > 0


# ---------------------------------------------------------------------------
# Integration-style tests
# ---------------------------------------------------------------------------

class TestIntegration:
    """Higher-level integration tests."""

    def test_full_dispatch_pipeline(self, client):
        """Test that multiple FromRadio messages dispatch correctly."""
        ni_cb = MagicMock()
        pos_cb = MagicMock()
        pkt_cb = MagicMock()

        client.register_callback(ProtobufEventType.NEIGHBOR_INFO, ni_cb)
        client.register_callback(ProtobufEventType.POSITION_RECEIVED, pos_cb)
        client.register_callback(ProtobufEventType.NODE_INFO_UPDATED, pkt_cb)

        # Dispatch sequence
        client._dispatch_fromradio(make_node_info(0xAAAA, "Node1"))
        client._dispatch_fromradio(
            make_neighbor_info_packet(0xBBBB, [(0xCCCC, 10.0)])
        )
        client._dispatch_fromradio(
            make_position_packet(0xDDDD, 207984000, -1563319000)
        )

        assert pkt_cb.call_count == 1
        assert ni_cb.call_count == 1
        assert pos_cb.call_count == 1

    def test_device_config_snapshot_stores_during_connect(self, client):
        """Test that config is stored in snapshot during session setup."""
        fr = mesh_pb2.FromRadio()
        fr.config.lora.region = 1
        fr.config.lora.tx_power = 27

        client._handle_session_setup(fr)
        assert client._device_config.lora is not None
        assert client._device_config.lora.region == 1

    def test_module_config_snapshot_stores_during_connect(self, client):
        fr = mesh_pb2.FromRadio()
        fr.moduleConfig.mqtt.enabled = True
        fr.moduleConfig.mqtt.address = "mqtt.meshtastic.org"

        client._handle_session_setup(fr)
        assert client._module_config.mqtt is not None
        assert client._module_config.mqtt.enabled is True

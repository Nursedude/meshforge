"""
Tests for Traffic Inspector - Wireshark-Grade Traffic Visibility.

Tests cover:
- Packet dissection (Meshtastic, RNS)
- PacketTree hierarchical structure
- Display filtering
- Path tracing
- Statistics aggregation
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import os
import sys

# Add src to path for imports
_src_dir = Path(__file__).parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from monitoring.traffic_inspector import (
    MeshPacket,
    PacketField,
    PacketTree,
    PacketDirection,
    PacketProtocol,
    FieldType,
    HopInfo,
    HopState,
    MeshtasticDissector,
    RNSDissector,
    DisplayFilter,
    TrafficCapture,
    TrafficAnalyzer,
    TrafficInspector,
    MESHTASTIC_PORTS,
)


class TestPacketField:
    """Tests for PacketField."""

    def test_create_field(self):
        """Test basic field creation."""
        field = PacketField(
            name="Test Field",
            abbrev="test.field",
            value="test_value",
            field_type=FieldType.STRING,
        )
        assert field.name == "Test Field"
        assert field.abbrev == "test.field"
        assert field.value == "test_value"

    def test_field_display_value(self):
        """Test display value formatting."""
        # String
        f1 = PacketField("Name", "test", "hello", FieldType.STRING)
        assert f1.get_display_value() == "hello"

        # Integer
        f2 = PacketField("Count", "test", 42, FieldType.INTEGER)
        assert f2.get_display_value() == "42"

        # Float
        f3 = PacketField("SNR", "test", 8.5, FieldType.FLOAT)
        assert "8.5" in f3.get_display_value()

        # Boolean
        f4 = PacketField("Active", "test", True, FieldType.BOOLEAN)
        assert f4.get_display_value() == "True"

        # Bytes (short)
        f5 = PacketField("Hash", "test", b"\xde\xad\xbe\xef", FieldType.BYTES)
        assert f5.get_display_value() == "deadbeef"

        # None
        f6 = PacketField("Empty", "test", None, FieldType.STRING)
        assert f6.get_display_value() == "<none>"

    def test_field_filter_matching(self):
        """Test filter expression matching."""
        # Integer comparisons
        field_int = PacketField("Hops", "mesh.hops", 3, FieldType.INTEGER)
        assert field_int.matches_filter("==", "3") is True
        assert field_int.matches_filter("!=", "5") is True
        assert field_int.matches_filter(">", "2") is True
        assert field_int.matches_filter("<", "5") is True
        assert field_int.matches_filter(">=", "3") is True
        assert field_int.matches_filter("<=", "3") is True

        # String comparisons
        field_str = PacketField("Source", "mesh.from", "!abc123", FieldType.STRING)
        assert field_str.matches_filter("==", "!abc123") is True
        assert field_str.matches_filter("contains", "abc") is True
        assert field_str.matches_filter("matches", r"!abc\d+") is True

        # Float comparisons
        field_float = PacketField("SNR", "mesh.snr", 8.5, FieldType.FLOAT)
        assert field_float.matches_filter(">=", "5.0") is True
        assert field_float.matches_filter("<", "10.0") is True

    def test_field_to_dict(self):
        """Test dictionary serialization."""
        field = PacketField(
            name="Test",
            abbrev="test.field",
            value="value",
            field_type=FieldType.STRING,
            description="A test field",
        )
        d = field.to_dict()
        assert d["name"] == "Test"
        assert d["abbrev"] == "test.field"
        assert d["value"] == "value"
        assert d["type"] == "string"


class TestPacketTree:
    """Tests for PacketTree hierarchical structure."""

    def test_create_tree(self):
        """Test creating a packet tree."""
        tree = PacketTree()
        assert len(tree.root_fields) == 0

    def test_add_layer(self):
        """Test adding protocol layers."""
        tree = PacketTree()
        frame = tree.add_layer("Frame", "frame")
        mesh = tree.add_layer("Meshtastic", "mesh")

        assert len(tree.root_fields) == 2
        assert tree.root_fields[0].name == "Frame"
        assert tree.root_fields[1].name == "Meshtastic"

    def test_add_fields(self):
        """Test adding fields to layers."""
        tree = PacketTree()
        mesh = tree.add_layer("Meshtastic", "mesh")

        tree.add_field(mesh, "Source", "mesh.from", "!abc123", FieldType.STRING)
        tree.add_field(mesh, "Hops", "mesh.hops", 3, FieldType.INTEGER)

        assert len(mesh.children) == 2
        assert tree.get_field("mesh.from").value == "!abc123"
        assert tree.get_field("mesh.hops").value == 3

    def test_format_ascii(self):
        """Test ASCII tree formatting."""
        tree = PacketTree()
        mesh = tree.add_layer("Meshtastic", "mesh")
        tree.add_field(mesh, "Source", "mesh.from", "!abc123", FieldType.STRING)
        tree.add_field(mesh, "Hops", "mesh.hops", 3, FieldType.INTEGER)

        ascii_output = tree.format_ascii()
        assert "Meshtastic" in ascii_output
        assert "Source" in ascii_output
        assert "!abc123" in ascii_output


class TestMeshPacket:
    """Tests for MeshPacket unified representation."""

    def test_create_packet(self):
        """Test basic packet creation."""
        pkt = MeshPacket(
            source="!abc123",
            destination="!def456",
            protocol=PacketProtocol.MESHTASTIC,
            portnum=1,
        )
        assert pkt.source == "!abc123"
        assert pkt.destination == "!def456"
        assert pkt.port_name == "TEXT_MESSAGE"

    def test_packet_hops_calculation(self):
        """Test hops taken calculation."""
        pkt = MeshPacket(
            hop_start=3,
            hop_limit=1,
        )
        assert pkt.hops_taken == 2

    def test_packet_summary(self):
        """Test packet summary generation."""
        pkt = MeshPacket(
            source="!abc123",
            destination="!def456",
            direction=PacketDirection.INBOUND,
            protocol=PacketProtocol.MESHTASTIC,
            portnum=1,
            snr=8.5,
        )
        summary = pkt.get_summary()
        assert "abc123" in summary.lower()
        assert "TEXT_MESSAGE" in summary or "text" in summary.lower()

    def test_packet_to_dict(self):
        """Test packet serialization."""
        pkt = MeshPacket(
            id="test_pkt_1",
            source="!abc123",
            protocol=PacketProtocol.MESHTASTIC,
        )
        d = pkt.to_dict()
        assert d["id"] == "test_pkt_1"
        assert d["source"] == "!abc123"
        assert d["protocol"] == "meshtastic"

    def test_packet_from_dict(self):
        """Test packet deserialization."""
        data = {
            "id": "test_pkt",
            "timestamp": "2024-01-01T12:00:00",
            "source": "!abc123",
            "destination": "broadcast",
            "direction": "inbound",
            "protocol": "meshtastic",
            "portnum": 1,
        }
        pkt = MeshPacket.from_dict(data)
        assert pkt.id == "test_pkt"
        assert pkt.source == "!abc123"
        assert pkt.protocol == PacketProtocol.MESHTASTIC


class TestMeshtasticDissector:
    """Tests for Meshtastic packet dissector."""

    def test_can_dissect(self):
        """Test protocol detection."""
        dissector = MeshtasticDissector()

        # Should match meshtastic metadata
        assert dissector.can_dissect(b"", {"protocol": "meshtastic"}) is True
        assert dissector.can_dissect(b"", {"from": "!abc", "to": "!def"}) is True

        # Should not match RNS
        assert dissector.can_dissect(b"", {"protocol": "rns"}) is False

    def test_dissect_text_message(self):
        """Test dissecting a text message."""
        dissector = MeshtasticDissector()

        metadata = {
            "from": "!abc12345",
            "to": "!def67890",
            "channel": 0,
            "hopLimit": 2,
            "hopStart": 3,
            "portnum": 1,
            "decoded": {
                "portnum": 1,
                "text": "Hello mesh!",
            },
            "snr": 8.5,
            "rssi": -85,
        }

        pkt = dissector.dissect(b"", metadata)

        assert pkt.source == "!abc12345"
        assert pkt.destination == "!def67890"
        assert pkt.protocol == PacketProtocol.MESHTASTIC
        assert pkt.portnum == 1
        assert pkt.port_name == "TEXT_MESSAGE"
        assert pkt.hops_taken == 1
        assert pkt.snr == 8.5
        assert pkt.rssi == -85

        # Check tree
        assert pkt.tree is not None
        assert pkt.tree.get_field("mesh.from").value == "!abc12345"
        assert pkt.tree.get_field("mesh.hops").value == 1

    def test_dissect_position(self):
        """Test dissecting a position packet."""
        dissector = MeshtasticDissector()

        metadata = {
            "from": "!abc",
            "to": "^all",
            "portnum": 4,
            "decoded": {
                "portnum": 4,
                "position": {
                    "latitude": 21.3069,
                    "longitude": -157.8583,
                    "altitude": 10,
                },
            },
        }

        pkt = dissector.dissect(b"", metadata)
        assert pkt.port_name == "POSITION"


class TestRNSDissector:
    """Tests for RNS packet dissector."""

    def test_can_dissect(self):
        """Test protocol detection."""
        dissector = RNSDissector()

        assert dissector.can_dissect(b"", {"protocol": "rns"}) is True
        assert dissector.can_dissect(b"", {"dest_hash": "abcd1234"}) is True
        assert dissector.can_dissect(b"", {"protocol": "meshtastic"}) is False

    def test_dissect_announce(self):
        """Test dissecting an RNS announce."""
        dissector = RNSDissector()

        metadata = {
            "protocol": "rns",
            "dest_hash": "0123456789abcdef",
            "hops": 2,
            "interface": "AutoInterface",
            "service_type": "nomadnetwork.node",
        }

        pkt = dissector.dissect(b"", metadata)

        assert pkt.protocol == PacketProtocol.RNS
        assert pkt.rns_dest_hash == bytes.fromhex("0123456789abcdef")
        assert pkt.hops_taken == 2
        assert pkt.rns_service == "nomadnetwork.node"

        # Check tree
        assert pkt.tree is not None
        assert pkt.tree.get_field("rns.hops").value == 2


class TestDisplayFilter:
    """Tests for Wireshark-style display filtering."""

    def test_empty_filter(self):
        """Empty filter matches all."""
        f = DisplayFilter("")
        assert f.compile() is True

        pkt = MeshPacket(source="test")
        pkt.tree = PacketTree()
        assert f.matches(pkt) is True

    def test_simple_filter(self):
        """Test simple field comparison."""
        f = DisplayFilter('mesh.hops == 3')
        assert f.compile() is True

        # Create packet with tree
        dissector = MeshtasticDissector()
        pkt = dissector.dissect(b"", {
            "from": "!abc",
            "to": "!def",
            "hopLimit": 0,
            "hopStart": 3,
        })

        assert f.matches(pkt) is True

        # Non-matching
        pkt2 = dissector.dissect(b"", {
            "from": "!abc",
            "to": "!def",
            "hopLimit": 2,
            "hopStart": 3,
        })
        assert f.matches(pkt2) is False

    def test_string_filter(self):
        """Test string field filtering."""
        f = DisplayFilter('mesh.from == "!abc123"')
        assert f.compile() is True

        dissector = MeshtasticDissector()
        pkt = dissector.dissect(b"", {"from": "!abc123", "to": "!def"})

        assert f.matches(pkt) is True

    def test_available_fields(self):
        """Test getting available filter fields."""
        fields = DisplayFilter.get_available_fields()

        assert "mesh.from" in fields
        assert "mesh.hops" in fields
        assert "mesh.snr" in fields
        assert "rns.hops" in fields


class TestHopInfo:
    """Tests for hop information tracking."""

    def test_create_hop(self):
        """Test creating hop info."""
        hop = HopInfo(
            hop_number=1,
            node_id="!abc123",
            node_name="Relay-1",
            state=HopState.RELAYED,
            snr=8.5,
            rssi=-85,
            latency_ms=150,
        )

        assert hop.hop_number == 1
        assert hop.node_id == "!abc123"
        assert hop.state == HopState.RELAYED
        assert hop.snr == 8.5

    def test_hop_to_dict(self):
        """Test hop serialization."""
        hop = HopInfo(
            hop_number=0,
            node_id="local",
            state=HopState.RECEIVED,
        )

        d = hop.to_dict()
        assert d["hop"] == 0
        assert d["node_id"] == "local"
        assert d["state"] == "received"


class TestTrafficCapture:
    """Tests for traffic capture and storage."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database for tests."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            yield f.name
        os.unlink(f.name)

    def test_capture_packet(self, temp_db):
        """Test capturing a packet."""
        capture = TrafficCapture(db_path=temp_db)

        metadata = {
            "from": "!abc123",
            "to": "!def456",
            "portnum": 1,
        }

        pkt = capture.capture_packet(b"", metadata)

        assert pkt is not None
        assert pkt.source == "!abc123"

    def test_get_packets(self, temp_db):
        """Test retrieving captured packets."""
        capture = TrafficCapture(db_path=temp_db)

        # Capture several packets
        for i in range(5):
            capture.capture_packet(b"", {
                "from": f"!node{i}",
                "to": "broadcast",
                "portnum": 1,
            })

        packets = capture.get_packets(limit=10)
        assert len(packets) == 5

    def test_filter_packets(self, temp_db):
        """Test filtering packets by expression."""
        capture = TrafficCapture(db_path=temp_db)

        # Capture packets with different hop counts
        # hops_taken = hop_start - hop_limit, so set hop_limit=0 and hop_start=hops
        for hops in [1, 2, 3, 4, 5]:
            capture.capture_packet(b"", {
                "from": "!test",
                "to": "!dest",
                "hopLimit": 0,
                "hopStart": hops,
            })

        # Filter for hops > 2
        packets = capture.get_packets(filter_expr="mesh.hops > 2")
        assert len(packets) == 3  # hops 3, 4, 5

    def test_capture_stats(self, temp_db):
        """Test capture statistics."""
        capture = TrafficCapture(db_path=temp_db)

        # Capture some packets
        capture.capture_packet(b"test", {"from": "!a", "to": "!b", "portnum": 1})
        capture.capture_packet(b"data", {"protocol": "rns", "dest_hash": "abcd"})

        stats = capture.get_stats()
        assert stats["packets_captured"] == 2

    def test_clear_capture(self, temp_db):
        """Test clearing captured data."""
        capture = TrafficCapture(db_path=temp_db)

        # Capture and clear
        capture.capture_packet(b"", {"from": "!a", "to": "!b"})
        count = capture.clear_all()

        assert count == 1
        assert len(capture.get_packets()) == 0


class TestTrafficAnalyzer:
    """Tests for traffic statistics analyzer."""

    @pytest.fixture
    def capture_with_data(self):
        """Create capture with test data."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        capture = TrafficCapture(db_path=db_path)

        # Add varied traffic
        for i in range(10):
            capture.capture_packet(b"", {
                "from": f"!node{i % 3}",
                "to": "broadcast",
                "portnum": [1, 4, 67][i % 3],
                "hopLimit": 3 - (i % 4),
                "hopStart": 3,
                "snr": 10 - i,
                "rssi": -80 - i,
            })

        yield capture, db_path
        os.unlink(db_path)

    def test_get_stats(self, capture_with_data):
        """Test getting traffic statistics."""
        capture, _ = capture_with_data
        analyzer = TrafficAnalyzer(capture)

        stats = analyzer.get_stats()

        assert stats.total_packets == 10
        assert "meshtastic" in stats.packets_by_protocol
        assert stats.avg_hops > 0

    def test_node_stats(self, capture_with_data):
        """Test getting per-node statistics."""
        capture, _ = capture_with_data
        analyzer = TrafficAnalyzer(capture)

        node_stats = analyzer.get_node_stats("!node0")

        assert node_stats["node_id"] == "!node0"
        assert node_stats["packets_sent"] >= 0


class TestTrafficInspector:
    """Tests for main TrafficInspector interface."""

    @pytest.fixture
    def inspector(self):
        """Create inspector with temp database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        inspector = TrafficInspector(db_path=db_path)
        yield inspector
        os.unlink(db_path)

    def test_capture_and_retrieve(self, inspector):
        """Test basic capture and retrieval."""
        pkt = inspector.capture(b"", {
            "from": "!test",
            "to": "broadcast",
            "portnum": 1,
        })

        assert pkt is not None

        packets = inspector.get_packets()
        assert len(packets) >= 1

    def test_get_filter_fields(self, inspector):
        """Test getting available filter fields."""
        fields = inspector.get_filter_fields()
        assert "mesh.from" in fields
        assert "mesh.hops" in fields

    def test_format_output(self, inspector):
        """Test formatting methods."""
        # Capture test data
        inspector.capture(b"", {
            "from": "!abc",
            "to": "!def",
            "portnum": 1,
            "snr": 8.5,
        })

        packets = inspector.get_packets()

        # Test list format
        list_output = inspector.format_packet_list(packets)
        assert "TRAFFIC CAPTURE" in list_output

        # Test detail format
        if packets:
            detail = inspector.format_packet_detail(packets[0])
            assert "PACKET DETAIL" in detail

        # Test stats format
        stats = inspector.get_stats()
        stats_output = inspector.format_stats(stats)
        assert "TRAFFIC STATISTICS" in stats_output


class TestPathVisualization:
    """Tests for path visualization components."""

    def test_import_path_visualizer(self):
        """Test that path visualizer can be imported."""
        from monitoring.path_visualizer import (
            PathVisualizer,
            TracedPath,
            PathSegment,
        )
        assert PathVisualizer is not None

    def test_create_traced_path(self):
        """Test creating a traced path."""
        from monitoring.path_visualizer import TracedPath

        hops = [
            HopInfo(0, "local", "Local", HopState.RECEIVED, snr=10),
            HopInfo(1, "relay1", "Relay-1", HopState.RELAYED, snr=8, latency_ms=100),
            HopInfo(2, "dest", "Destination", HopState.DELIVERED, snr=5, latency_ms=150),
        ]

        path = TracedPath.from_hop_list("test_path", hops, "pkt_123")

        assert path.total_hops == 2
        assert path.success is True
        assert len(path.segments) == 2

    def test_path_visualizer_generate(self):
        """Test generating path visualization HTML."""
        from monitoring.path_visualizer import PathVisualizer

        visualizer = PathVisualizer()
        visualizer.add_node("local", "Local Node", "local")
        visualizer.add_node("relay", "Relay", "relay")
        visualizer.add_node("dest", "Destination", "destination")

        hops = [
            HopInfo(0, "local", "Local Node", HopState.RECEIVED, snr=12),
            HopInfo(1, "relay", "Relay", HopState.RELAYED, snr=8, latency_ms=100),
            HopInfo(2, "dest", "Destination", HopState.DELIVERED, snr=5, latency_ms=150),
        ]

        visualizer.add_path_trace("test_path", hops)

        # Generate to temp file
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            output_path = f.name

        result = visualizer.generate(output_path)
        assert Path(result).exists()

        # Check content
        with open(result) as f:
            html = f.read()
        assert "Path Visualization" in html
        assert "d3.js" in html.lower() or "D3" in html

        os.unlink(result)

    def test_path_ascii_format(self):
        """Test ASCII path formatting."""
        from monitoring.path_visualizer import PathVisualizer, TracedPath

        visualizer = PathVisualizer()

        hops = [
            HopInfo(0, "local", "Local", HopState.RECEIVED),
            HopInfo(1, "relay", "Relay", HopState.RELAYED, snr=8),
            HopInfo(2, "dest", "Dest", HopState.DELIVERED, snr=5),
        ]

        path = TracedPath.from_hop_list("test", hops)
        visualizer.add_path(path)

        ascii_out = visualizer.generate_ascii()
        assert "PATH" in ascii_out.upper()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

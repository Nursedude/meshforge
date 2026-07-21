"""
Tests for node tracker (node cache management, position/telemetry).

Run: python3 -m pytest tests/test_node_tracker.py -v
"""

import json
import pytest
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.gateway.node_tracker import (
    Position,
    Telemetry,
    UnifiedNode,
    UnifiedNodeTracker,
)


class TestPosition:
    """Tests for Position dataclass."""

    def test_defaults(self):
        """Test default position values."""
        pos = Position()

        assert pos.latitude == 0.0
        assert pos.longitude == 0.0
        assert pos.altitude == 0.0
        assert pos.precision == 5
        assert pos.timestamp is None

    def test_is_valid_with_coordinates(self):
        """Test is_valid returns True with valid coordinates."""
        pos = Position(latitude=21.3069, longitude=-157.8583)
        assert pos.is_valid() is True

    def test_is_valid_false_at_origin(self):
        """Test is_valid returns False at 0,0 (unlikely real location)."""
        pos = Position(latitude=0.0, longitude=0.0)
        assert pos.is_valid() is False

    def test_is_valid_false_out_of_range(self):
        """Test is_valid returns False for out-of-range coordinates."""
        pos1 = Position(latitude=91.0, longitude=0.0)
        pos2 = Position(latitude=0.0, longitude=181.0)

        assert pos1.is_valid() is False
        assert pos2.is_valid() is False

    def test_to_dict(self):
        """Test to_dict serialization."""
        pos = Position(
            latitude=21.3069,
            longitude=-157.8583,
            altitude=10.5,
            timestamp=datetime(2026, 1, 9, 12, 0, 0)
        )

        d = pos.to_dict()

        assert d['latitude'] == 21.3069
        assert d['longitude'] == -157.8583
        assert d['altitude'] == 10.5
        assert '2026-01-09' in d['timestamp']

    def test_to_dict_rounds_precision(self):
        """Test that to_dict respects precision setting."""
        pos = Position(
            latitude=21.30694567,
            longitude=-157.85834567,
            precision=3
        )

        d = pos.to_dict()

        assert d['latitude'] == 21.307
        assert d['longitude'] == -157.858


class TestTelemetry:
    """Tests for Telemetry dataclass."""

    def test_defaults(self):
        """Test default telemetry values are None."""
        telem = Telemetry()

        assert telem.battery_level is None
        assert telem.voltage is None
        assert telem.temperature is None

    def test_to_dict_excludes_none(self):
        """Test to_dict excludes None values."""
        telem = Telemetry(battery_level=85, voltage=3.7)

        d = telem.to_dict()

        assert d['battery_level'] == 85
        assert d['voltage'] == 3.7
        assert 'temperature' not in d
        assert 'humidity' not in d

    def test_to_dict_with_timestamp(self):
        """Test to_dict includes timestamp as ISO string."""
        telem = Telemetry(
            battery_level=90,
            timestamp=datetime(2026, 1, 9, 12, 0, 0)
        )

        d = telem.to_dict()

        assert '2026-01-09' in d['timestamp']


class TestUnifiedNode:
    """Tests for UnifiedNode dataclass."""

    def test_defaults(self):
        """Test default node values."""
        node = UnifiedNode(id="test_123", network="meshtastic")

        assert node.id == "test_123"
        assert node.network == "meshtastic"
        assert node.name == ""
        assert node.is_online is False
        assert node.is_gateway is False
        assert node.first_seen is not None

    def test_update_seen(self):
        """Test update_seen updates timestamp and sets online."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.is_online = False

        node.update_seen()

        assert node.is_online is True
        assert node.last_seen is not None

    def test_get_age_string_never(self):
        """Test get_age_string returns 'Never' when not seen."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.last_seen = None

        assert node.get_age_string() == "Never"

    def test_get_age_string_seconds(self):
        """Test get_age_string for recent nodes."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.last_seen = datetime.now() - timedelta(seconds=30)

        assert "30s ago" == node.get_age_string()

    def test_get_age_string_minutes(self):
        """Test get_age_string for nodes seen minutes ago."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.last_seen = datetime.now() - timedelta(minutes=5)

        assert "5m ago" == node.get_age_string()

    def test_get_age_string_hours(self):
        """Test get_age_string for nodes seen hours ago."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.last_seen = datetime.now() - timedelta(hours=2)

        assert "2h ago" == node.get_age_string()

    def test_get_age_string_days(self):
        """Test get_age_string for nodes seen days ago."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.last_seen = datetime.now() - timedelta(days=3)

        assert "3d ago" == node.get_age_string()

    def test_to_dict(self):
        """Test to_dict serialization."""
        node = UnifiedNode(
            id="mesh_!abcd1234",
            network="meshtastic",
            name="Test Node",
            short_name="TEST",
            meshtastic_id="!abcd1234",
            is_online=True
        )
        node.position = Position(latitude=21.3, longitude=-157.8)

        d = node.to_dict()

        assert d['id'] == "mesh_!abcd1234"
        assert d['network'] == "meshtastic"
        assert d['name'] == "Test Node"
        assert d['meshtastic_id'] == "!abcd1234"
        assert d['is_online'] is True
        assert d['position'] is not None

    def test_to_dict_with_rns_hash(self):
        """Test to_dict serializes RNS hash as hex."""
        node = UnifiedNode(
            id="rns_abc123",
            network="rns",
            rns_hash=bytes.fromhex('abcd1234')
        )

        d = node.to_dict()

        assert d['rns_hash'] == 'abcd1234'

    def test_from_meshtastic(self):
        """Test creating node from Meshtastic data."""
        mesh_data = {
            'num': 0xabcd1234,
            'user': {
                'longName': 'Test Node',
                'shortName': 'TEST',
                'hwModel': 'HELTEC_V3'
            },
            'position': {
                'latitude': 21.3,
                'longitude': -157.8
            },
            'deviceMetrics': {
                'batteryLevel': 85,
                'voltage': 3.7
            }
        }

        node = UnifiedNode.from_meshtastic(mesh_data)

        assert node.network == "meshtastic"
        assert node.name == "Test Node"
        assert node.short_name == "TEST"
        assert node.meshtastic_id == "!abcd1234"
        assert node.position.latitude == 21.3
        assert node.telemetry.battery_level == 85

    def test_from_rns(self):
        """Test creating node from RNS data."""
        rns_hash = bytes.fromhex('abcd1234567890abcdef')

        node = UnifiedNode.from_rns(rns_hash, name="RNS Node")

        assert node.network == "rns"
        assert node.name == "RNS Node"
        assert node.rns_hash == rns_hash
        assert 'rns_' in node.id


class TestUnifiedNodeTracker:
    """Tests for UnifiedNodeTracker class."""

    def test_init(self):
        """Test tracker initialization."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            assert len(tracker._nodes) == 0
            assert tracker._running is False

    def test_add_node(self):
        """Test adding a node."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            node = UnifiedNode(id="test_1", network="meshtastic", name="Test")

            tracker.add_node(node)

            assert "test_1" in tracker._nodes
            assert tracker.get_node("test_1") == node

    def test_remove_node(self):
        """Test removing a node."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            node = UnifiedNode(id="test_1", network="meshtastic")
            tracker.add_node(node)

            tracker.remove_node("test_1")

            assert tracker.get_node("test_1") is None

    def test_get_all_nodes(self):
        """Test getting all nodes."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            node1 = UnifiedNode(id="test_1", network="meshtastic")
            node2 = UnifiedNode(id="test_2", network="rns")
            tracker.add_node(node1)
            tracker.add_node(node2)

            all_nodes = tracker.get_all_nodes()

            assert len(all_nodes) == 2

    def test_get_meshtastic_nodes(self):
        """Test filtering meshtastic nodes."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(id="m1", network="meshtastic"))
            tracker.add_node(UnifiedNode(id="r1", network="rns"))
            tracker.add_node(UnifiedNode(id="b1", network="both"))

            mesh_nodes = tracker.get_meshtastic_nodes()

            assert len(mesh_nodes) == 2  # meshtastic + both

    def test_get_rns_nodes(self):
        """Test filtering RNS nodes."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(id="m1", network="meshtastic"))
            tracker.add_node(UnifiedNode(id="r1", network="rns"))
            tracker.add_node(UnifiedNode(id="b1", network="both"))

            rns_nodes = tracker.get_rns_nodes()

            assert len(rns_nodes) == 2  # rns + both

    def test_get_node_by_mesh_id(self):
        """Test finding node by Meshtastic ID."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            node = UnifiedNode(id="test_1", network="meshtastic", meshtastic_id="!abcd1234")
            tracker.add_node(node)
            tracker.add_node(UnifiedNode(id="test_2", network="meshtastic", meshtastic_id="!efgh5678"))

            result = tracker.get_node_by_mesh_id("!abcd1234")

            assert result is not None
            assert result.id == "test_1"
            assert result.meshtastic_id == "!abcd1234"

    def test_get_node_by_mesh_id_not_found(self):
        """Test get_node_by_mesh_id returns None when not found."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(id="test_1", network="meshtastic", meshtastic_id="!abcd1234"))

            result = tracker.get_node_by_mesh_id("!nonexistent")

            assert result is None

    def test_get_node_by_short_name_found(self):
        """Case-insensitive short_name lookup returns the matching node."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(
                id="hat3", network="meshtastic",
                meshtastic_id="!ebfa1b11", short_name="HAT3",
            ))

            result = tracker.get_node_by_short_name("hat3")
            assert result is not None
            assert result.meshtastic_id == "!ebfa1b11"

    def test_get_node_by_short_name_empty_returns_none(self):
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            assert tracker.get_node_by_short_name("") is None
            assert tracker.get_node_by_short_name("   ") is None

    def test_get_node_by_short_name_ambiguous_returns_none(self):
        """Two nodes sharing the same short_name -> None (fail safe)."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(
                id="a", network="meshtastic",
                meshtastic_id="!aaaa0001", short_name="DUPE",
            ))
            tracker.add_node(UnifiedNode(
                id="b", network="meshtastic",
                meshtastic_id="!bbbb0002", short_name="dupe",
            ))

            assert tracker.get_node_by_short_name("DUPE") is None

    def test_get_node_by_short_name_skips_nodes_without_meshtastic_id(self):
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(
                id="rnsnode", network="rns", short_name="RNS1",
            ))
            assert tracker.get_node_by_short_name("RNS1") is None

    def test_get_node_by_rns_hash(self):
        """Test finding node by RNS hash."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            rns_hash = bytes.fromhex('abcd1234567890abcdef0123456789ab')
            node = UnifiedNode(id="rns_1", network="rns", rns_hash=rns_hash)
            tracker.add_node(node)

            result = tracker.get_node_by_rns_hash(rns_hash)

            assert result is not None
            assert result.id == "rns_1"
            assert result.rns_hash == rns_hash

    def test_get_node_by_rns_hash_not_found(self):
        """Test get_node_by_rns_hash returns None when not found."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            rns_hash = bytes.fromhex('abcd1234567890abcdef0123456789ab')
            tracker.add_node(UnifiedNode(id="rns_1", network="rns", rns_hash=rns_hash))

            other_hash = bytes.fromhex('ffff1234567890abcdef0123456789ff')
            result = tracker.get_node_by_rns_hash(other_hash)

            assert result is None

    def test_get_nodes_with_position(self):
        """Test filtering nodes with valid positions."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            node_with_pos = UnifiedNode(id="pos1", network="meshtastic")
            node_with_pos.position = Position(latitude=21.3, longitude=-157.8)

            node_without_pos = UnifiedNode(id="nopos", network="meshtastic")

            tracker.add_node(node_with_pos)
            tracker.add_node(node_without_pos)

            positioned = tracker.get_nodes_with_position()

            assert len(positioned) == 1
            assert positioned[0].id == "pos1"

    def test_get_online_nodes(self):
        """Test filtering online nodes."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            online = UnifiedNode(id="on1", network="meshtastic")
            online.is_online = True

            offline = UnifiedNode(id="off1", network="meshtastic")
            offline.is_online = False

            tracker.add_node(online)
            tracker.add_node(offline)

            online_nodes = tracker.get_online_nodes()

            assert len(online_nodes) == 1

    def test_get_stats(self):
        """Test statistics generation."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(id="m1", network="meshtastic"))
            tracker.add_node(UnifiedNode(id="r1", network="rns"))

            node_online = UnifiedNode(id="m2", network="meshtastic")
            node_online.is_online = True
            tracker.add_node(node_online)

            stats = tracker.get_stats()

            assert stats['total'] == 3
            assert stats['meshtastic'] == 2
            assert stats['rns'] == 1
            assert stats['online'] == 1

    def test_register_callback(self):
        """Test callback registration."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            callback = MagicMock()

            tracker.register_callback(callback)
            node = UnifiedNode(id="test", network="meshtastic")
            tracker.add_node(node)

            callback.assert_called_once_with("update", node)

    def test_unregister_callback(self):
        """Test callback unregistration."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            callback = MagicMock()

            tracker.register_callback(callback)
            tracker.unregister_callback(callback)
            tracker.add_node(UnifiedNode(id="test", network="meshtastic"))

            callback.assert_not_called()

    def test_merge_node_updates_network(self):
        """Test that merging nodes updates network to 'both'."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            mesh_node = UnifiedNode(id="test", network="meshtastic")
            tracker.add_node(mesh_node)

            rns_node = UnifiedNode(id="test", network="rns")
            tracker.add_node(rns_node)

            result = tracker.get_node("test")
            assert result.network == "both"

    def test_merge_node_keeps_better_name(self):
        """Test that merge keeps better name."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            node1 = UnifiedNode(id="test", network="meshtastic", name="!abcd1234")
            tracker.add_node(node1)

            node2 = UnifiedNode(id="test", network="meshtastic", name="Good Name")
            tracker.add_node(node2)

            result = tracker.get_node("test")
            assert result.name == "Good Name"

    def test_thread_safety(self):
        """Test thread-safe node operations."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            errors = []

            def add_nodes(prefix, count):
                try:
                    for i in range(count):
                        node = UnifiedNode(id=f"{prefix}_{i}", network="meshtastic")
                        tracker.add_node(node)
                except Exception as e:
                    errors.append(e)

            threads = [
                threading.Thread(target=add_nodes, args=("a", 50)),
                threading.Thread(target=add_nodes, args=("b", 50)),
            ]

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0
            assert len(tracker.get_all_nodes()) == 100


class TestNodeTrackerCache:
    """Tests for cache save/load functionality."""

    def test_save_cache(self, tmp_path):
        """Test saving node cache."""
        cache_file = tmp_path / "node_cache.json"

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            with patch.object(UnifiedNodeTracker, '_load_cache'):
                tracker = UnifiedNodeTracker()
                tracker.add_node(UnifiedNode(
                    id="test_1",
                    network="meshtastic",
                    name="Test Node"
                ))

                tracker._save_cache()

                assert cache_file.exists()
                with open(cache_file) as f:
                    data = json.load(f)
                assert len(data['nodes']) == 1

    def test_load_cache(self, tmp_path):
        """Test loading node cache."""
        cache_file = tmp_path / "node_cache.json"
        cache_data = {
            'version': 1,
            'nodes': [{
                'id': 'cached_1',
                'network': 'meshtastic',
                'name': 'Cached Node',
                'short_name': 'CN',
                'meshtastic_id': '!12345678'
            }]
        }
        cache_file.write_text(json.dumps(cache_data))

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            tracker = UnifiedNodeTracker()

            assert len(tracker._nodes) == 1
            assert 'cached_1' in tracker._nodes
            assert tracker._nodes['cached_1'].name == 'Cached Node'

    def test_load_cache_restores_service_type(self, tmp_path):
        """service_type must survive the save/load round-trip.

        Regression, live-caught 2026-07-21: ``to_dict()`` wrote service_type
        but ``_load_cache()`` restored 14 other fields and silently dropped
        this one, so every gateway restart erased the RNS service type of
        every known node until it announced again (up to 6h for a propagation
        node's 360-min interval).

        That is honest_failure_modes #4 — a writer with no matching reader —
        and it broke a real consumer: ``probe_lxmf_propagation_node_dark``
        reads service_type to find the configured propagation node, so after
        the restart that ADOPTION ITSELF REQUIRES it saw the node as "never
        heard" and paged, while the node was healthy and serving.
        """
        cache_file = tmp_path / "node_cache.json"
        cache_data = {
            'version': 1,
            'nodes': [{
                'id': 'rns_3968a2eeac25e2e7',
                'network': 'rns',
                'name': 'propagation node',
                'rns_hash': '3968a2eeac25e2e7a7961f25842d3d85',
                'service_type': 'LXMF_PROPAGATION',
                'last_seen': '2026-07-21T03:37:44.163446',
            }]
        }
        cache_file.write_text(json.dumps(cache_data))

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            tracker = UnifiedNodeTracker()

            node = tracker._nodes['rns_3968a2eeac25e2e7']
            assert node.service_type == 'LXMF_PROPAGATION'

    def test_save_then_load_preserves_service_type(self, tmp_path):
        """The full round-trip, not just the read half.

        Pins writer and reader together so a future change to either side
        fails here rather than fleet-wide six hours later.
        """
        cache_file = tmp_path / "node_cache.json"

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            with patch.object(UnifiedNodeTracker, '_load_cache'):
                tracker = UnifiedNodeTracker()
                node = UnifiedNode(id="rns_abc", network="rns", name="pn")
                node.service_type = "LXMF_PROPAGATION"
                tracker.add_node(node)
                tracker._save_cache()

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            reloaded = UnifiedNodeTracker()

        assert reloaded._nodes["rns_abc"].service_type == "LXMF_PROPAGATION"

    def test_load_cache_handles_missing_file(self, tmp_path):
        """Test loading when cache file doesn't exist."""
        cache_file = tmp_path / "nonexistent.json"

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            tracker = UnifiedNodeTracker()

            assert len(tracker._nodes) == 0

    def test_load_cache_handles_corrupted_file(self, tmp_path):
        """Test loading handles corrupted cache gracefully."""
        cache_file = tmp_path / "node_cache.json"
        cache_file.write_text("not valid json {{{")

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            tracker = UnifiedNodeTracker()

            # Should not raise, just start empty
            assert len(tracker._nodes) == 0


class TestRNSAnnounceHandling:
    """Tests for RNS announce parsing and handling."""

    def test_from_rns_with_name_in_app_data(self):
        """Test from_rns extracts display name from app_data."""
        rns_hash = bytes.fromhex('abcd1234567890abcdef1234567890ab')
        app_data = b"Alice's Node"

        node = UnifiedNode.from_rns(rns_hash, app_data=app_data)

        assert node.name == "Alice's Node"
        assert node.network == "rns"

    def test_from_rns_with_msgpack_telemetry(self):
        """Test from_rns parses msgpack telemetry with position."""
        try:
            import msgpack
        except ImportError:
            pytest.skip("msgpack not installed")

        rns_hash = bytes.fromhex('abcd1234567890abcdef1234567890ab')

        # Create app_data: display name + msgpack telemetry
        name_bytes = b"GPS Node"
        telemetry = {"latitude": 21.3069, "longitude": -157.8583, "altitude": 10.0}
        telemetry_bytes = msgpack.packb(telemetry)
        app_data = name_bytes + telemetry_bytes

        node = UnifiedNode.from_rns(rns_hash, app_data=app_data)

        assert node.name == "GPS Node"
        assert node.position.is_valid()
        assert abs(node.position.latitude - 21.3069) < 0.001
        assert abs(node.position.longitude - (-157.8583)) < 0.001

    def test_from_rns_with_sideband_style_telemetry(self):
        """Test from_rns parses Sideband-style telemetry keys."""
        try:
            import msgpack
        except ImportError:
            pytest.skip("msgpack not installed")

        rns_hash = bytes.fromhex('1234567890abcdef1234567890abcdef')

        # Sideband uses 'lat', 'lon', 'alt' keys
        telemetry = {"lat": 19.896, "lon": -155.582, "alt": 45.0, "speed": 5.2}
        app_data = b"Sideband" + msgpack.packb(telemetry)

        node = UnifiedNode.from_rns(rns_hash, app_data=app_data)

        assert node.position.is_valid()
        assert abs(node.position.latitude - 19.896) < 0.001
        assert abs(node.position.longitude - (-155.582)) < 0.001

    def test_from_rns_with_invalid_coordinates(self):
        """Test from_rns rejects out-of-range coordinates."""
        try:
            import msgpack
        except ImportError:
            pytest.skip("msgpack not installed")

        rns_hash = bytes.fromhex('deadbeef12345678deadbeef12345678')
        telemetry = {"latitude": 999.0, "longitude": -157.8}  # Invalid lat
        app_data = b"BadGPS" + msgpack.packb(telemetry)

        node = UnifiedNode.from_rns(rns_hash, app_data=app_data)

        # Position should NOT be set for invalid coordinates
        assert not node.position.is_valid()

    def test_from_rns_without_app_data(self):
        """Test from_rns works without app_data."""
        rns_hash = bytes.fromhex('cafebabe12345678cafebabe12345678')

        node = UnifiedNode.from_rns(rns_hash)

        assert node.network == "rns"
        assert node.rns_hash == rns_hash
        # Name should be derived from hash
        assert node.name == rns_hash.hex()[:8]

    def test_parse_lxmf_app_data_name_only(self):
        """Test _parse_lxmf_app_data with name-only data."""
        app_data = b"My Node Name"

        result = UnifiedNode._parse_lxmf_app_data(app_data)

        assert result.get("display_name") == "My Node Name"
        assert result.get("latitude") is None

    def test_parse_lxmf_app_data_empty(self):
        """Test _parse_lxmf_app_data with empty data."""
        result = UnifiedNode._parse_lxmf_app_data(b"")
        assert result == {}

    def test_on_rns_announce_adds_node(self):
        """Test _on_rns_announce adds node to tracker."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            dest_hash = bytes.fromhex('1122334455667788aabbccddeeff0011')
            announced_identity = MagicMock()
            app_data = b"Announced Node"

            tracker._on_rns_announce(dest_hash, announced_identity, app_data)

            # Verify node was added
            nodes = tracker.get_rns_nodes()
            assert len(nodes) == 1
            assert nodes[0].name == "Announced Node"

    def test_on_rns_announce_error_handling(self):
        """Test _on_rns_announce handles errors gracefully."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            # Should not raise even with bad data
            tracker._on_rns_announce(None, None, None)

            # Tracker should still be operational
            assert len(tracker._nodes) == 0


class TestAnnounceHandlerRegistration:
    """Regression guard: announce handlers must NOT include a None
    catch-all alongside aspect-specific handlers.

    Pre-fix the tracker registered both, so RNS fired every announce
    twice — once via the aspect-scoped handler (correct service type)
    and once via the catch-all (UNKNOWN). Symptoms: doubled
    ``Parsed announce ...`` log lines and node service_type flapping.
    Captured 2026-04-25 across the fleet.
    """

    def test_no_catchall_alongside_aspect_handlers(self):
        """The known_aspects loop should NOT be followed by a None registration."""
        import inspect
        from gateway import node_tracker

        src = inspect.getsource(node_tracker)
        # Locate the aspect-handler registration block.
        assert 'known_aspects = [' in src, (
            "expected the known_aspects list to be the registration site"
        )
        # The fix removed `register_announce_handler(... None)` from
        # the connection block. If a future refactor reintroduces a
        # None-aspect handler, we want the test to scream.
        # Strip comments before scanning so doc-strings explaining
        # *why* the catch-all is absent don't false-positive.
        non_comment_lines = [
            line for line in src.splitlines()
            if not line.lstrip().startswith('#')
        ]
        joined = '\n'.join(non_comment_lines)
        assert 'AspectAnnounceHandler(self, None)' not in joined, (
            "do not register a None catch-all announce handler — it "
            "duplicates every announce already covered by the aspect-"
            "scoped handlers (see node_tracker.py comment)"
        )


class TestGeoJSON:
    """Tests for GeoJSON export."""

    def test_to_geojson_format(self):
        """Test GeoJSON output format."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            node = UnifiedNode(id="geo1", network="meshtastic", name="GeoNode")
            node.position = Position(latitude=21.3, longitude=-157.8)
            tracker.add_node(node)

            geojson = tracker.to_geojson()

            assert geojson['type'] == 'FeatureCollection'
            assert len(geojson['features']) == 1

            feature = geojson['features'][0]
            assert feature['type'] == 'Feature'
            assert feature['geometry']['type'] == 'Point'
            assert feature['geometry']['coordinates'] == [-157.8, 21.3]

    def test_to_geojson_excludes_nodes_without_position(self):
        """Test GeoJSON excludes nodes without valid positions."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            with_pos = UnifiedNode(id="pos1", network="meshtastic")
            with_pos.position = Position(latitude=21.3, longitude=-157.8)

            without_pos = UnifiedNode(id="nopos", network="meshtastic")

            tracker.add_node(with_pos)
            tracker.add_node(without_pos)

            geojson = tracker.to_geojson()

            assert len(geojson['features']) == 1


class TestSignalQualityTrending:
    """Tests for signal quality trending feature."""

    def test_record_signal_quality_snr(self):
        """Test recording SNR values."""
        node = UnifiedNode(id="test", network="meshtastic")

        node.record_signal_quality(snr=10.5)

        assert node.snr == 10.5
        assert len(node.snr_history) == 1
        assert node.snr_history[0].value == 10.5

    def test_record_signal_quality_rssi(self):
        """Test recording RSSI values."""
        node = UnifiedNode(id="test", network="meshtastic")

        node.record_signal_quality(rssi=-75)

        assert node.rssi == -75
        assert len(node.rssi_history) == 1
        assert node.rssi_history[0].value == -75.0

    def test_record_signal_quality_both(self):
        """Test recording both SNR and RSSI together."""
        node = UnifiedNode(id="test", network="meshtastic")

        node.record_signal_quality(snr=8.0, rssi=-80)

        assert node.snr == 8.0
        assert node.rssi == -80
        assert len(node.snr_history) == 1
        assert len(node.rssi_history) == 1

    def test_history_accumulates(self):
        """Test that signal history accumulates over multiple recordings."""
        node = UnifiedNode(id="test", network="meshtastic")

        for i in range(5):
            node.record_signal_quality(snr=float(i))

        assert len(node.snr_history) == 5
        assert node.snr_history[0].value == 0.0
        assert node.snr_history[4].value == 4.0

    def test_history_max_samples(self):
        """Test that history is trimmed to MAX_SIGNAL_SAMPLES."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.MAX_SIGNAL_SAMPLES = 10  # Override for testing

        for i in range(15):
            node.record_signal_quality(snr=float(i))

        assert len(node.snr_history) == 10
        # Should keep the most recent 10
        assert node.snr_history[0].value == 5.0
        assert node.snr_history[9].value == 14.0

    def test_snr_trend_unknown_insufficient_data(self):
        """Test trend returns 'unknown' with insufficient data."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Less than 5 samples
        for i in range(3):
            node.record_signal_quality(snr=float(i))

        assert node.snr_trend == "unknown"

    def test_snr_trend_improving(self):
        """Test SNR trend detection for improving signal."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Older samples: low SNR (0-4)
        for i in range(5):
            node.record_signal_quality(snr=float(i))
        # Recent samples: high SNR (10-14) - clear improvement
        for i in range(10, 15):
            node.record_signal_quality(snr=float(i))

        assert node.snr_trend == "improving"

    def test_snr_trend_degrading(self):
        """Test SNR trend detection for degrading signal."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Older samples: high SNR
        for i in range(10, 15):
            node.record_signal_quality(snr=float(i))
        # Recent samples: low SNR - clear degradation
        for i in range(5):
            node.record_signal_quality(snr=float(i))

        assert node.snr_trend == "degrading"

    def test_snr_trend_stable(self):
        """Test SNR trend detection for stable signal."""
        node = UnifiedNode(id="test", network="meshtastic")

        # All samples around the same value
        for _ in range(10):
            node.record_signal_quality(snr=5.0)

        assert node.snr_trend == "stable"

    def test_rssi_trend_improving(self):
        """Test RSSI trend detection for improving signal."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Older samples: low RSSI (worse signal)
        for i in range(5):
            node.record_signal_quality(rssi=-90 + i)
        # Recent samples: high RSSI (better signal)
        for i in range(5):
            node.record_signal_quality(rssi=-70 + i)

        assert node.rssi_trend == "improving"

    def test_get_signal_stats(self):
        """Test signal statistics calculation."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Add some varied SNR values
        for snr in [5.0, 10.0, 8.0, 12.0, 6.0, 9.0, 11.0, 7.0, 10.0, 8.0]:
            node.record_signal_quality(snr=snr)

        stats = node.get_signal_stats()

        assert 'snr' in stats
        assert stats['snr']['min'] == 5.0
        assert stats['snr']['max'] == 12.0
        assert stats['snr']['samples'] == 10
        assert stats['snr']['current'] == 8.0
        assert 'trend' in stats['snr']

    def test_to_dict_includes_trends(self):
        """Test that to_dict includes trend information."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Add enough samples for trend
        for i in range(10):
            node.record_signal_quality(snr=float(i), rssi=-80 + i)

        d = node.to_dict()

        assert 'snr_trend' in d
        assert 'rssi_trend' in d
        assert d['snr_trend'] is not None

    def test_to_dict_with_signal_history(self):
        """Test that to_dict includes history when requested."""
        node = UnifiedNode(id="test", network="meshtastic")

        node.record_signal_quality(snr=10.0, rssi=-75)

        # Without history
        d_minimal = node.to_dict(include_signal_history=False)
        assert 'snr_history' not in d_minimal

        # With history
        d_full = node.to_dict(include_signal_history=True)
        assert 'snr_history' in d_full
        assert len(d_full['snr_history']) == 1
        assert 'rssi_history' in d_full

    def test_merge_node_records_signal(self):
        """Test that merging nodes records signal quality."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            node1 = UnifiedNode(id="test", network="meshtastic", snr=5.0)
            tracker.add_node(node1)

            node2 = UnifiedNode(id="test", network="meshtastic", snr=8.0)
            tracker.add_node(node2)

            result = tracker.get_node("test")

            # Should have recorded both signal values
            assert result.snr == 8.0
            assert len(result.snr_history) == 1  # Only from merge, not initial add

    def test_signal_sample_to_dict(self):
        """Test SignalSample serialization."""
        from src.gateway.node_tracker import SignalSample

        sample = SignalSample(
            timestamp=datetime(2026, 1, 15, 12, 30, 0),
            value=10.5
        )

        d = sample.to_dict()

        assert d['value'] == 10.5
        assert '2026-01-15' in d['timestamp']

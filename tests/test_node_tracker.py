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

"""
Tests for MeshCore Protocol Plugin

Tests the MeshCore plugin functionality including:
- Plugin metadata
- Device connection (simulation mode)
- Message sending/receiving
- Node management
- Statistics tracking
"""

import pytest
import sys
from datetime import datetime
from unittest.mock import patch, MagicMock

sys.path.insert(0, 'src')


class TestMeshCoreNode:
    """Test MeshCoreNode dataclass"""

    def test_node_creation(self):
        """Create a node with basic properties"""
        from plugins.meshcore import MeshCoreNode

        node = MeshCoreNode(
            node_id="mc001a2b3c",
            name="Test Node",
            role="client"
        )

        assert node.node_id == "mc001a2b3c"
        assert node.name == "Test Node"
        assert node.role == "client"
        assert node.hops == 0

    def test_node_to_dict(self):
        """Convert node to dictionary"""
        from plugins.meshcore import MeshCoreNode

        node = MeshCoreNode(
            node_id="mc001a2b3c",
            name="Gateway",
            role="gateway",
            hops=0,
            rssi=-65,
            snr=10.5,
            battery=100
        )

        d = node.to_dict()

        assert d['node_id'] == "mc001a2b3c"
        assert d['name'] == "Gateway"
        assert d['role'] == "gateway"
        assert d['rssi'] == -65
        assert d['snr'] == 10.5
        assert d['battery'] == 100

    def test_node_default_name(self):
        """Node with no name uses ID prefix"""
        from plugins.meshcore import MeshCoreNode

        node = MeshCoreNode(node_id="mc001a2b3c")
        d = node.to_dict()

        assert d['name'] == "mc001a2b"  # First 8 chars


class TestMeshCoreConfig:
    """Test MeshCoreConfig dataclass"""

    def test_default_config(self):
        """Default configuration values"""
        from plugins.meshcore import MeshCoreConfig

        config = MeshCoreConfig()

        assert config.connection_type == "serial"
        assert config.port == "/dev/ttyUSB0"
        assert config.host == "localhost"
        assert config.tcp_port == 4405
        assert config.baud_rate == 115200
        assert config.simulation_mode is False

    def test_custom_config(self):
        """Custom configuration values"""
        from plugins.meshcore import MeshCoreConfig

        config = MeshCoreConfig(
            connection_type="tcp",
            host="192.168.1.100",
            tcp_port=5000
        )

        assert config.connection_type == "tcp"
        assert config.host == "192.168.1.100"
        assert config.tcp_port == 5000


class TestMeshCorePlugin:
    """Test MeshCorePlugin class"""

    @pytest.fixture
    def plugin(self):
        """Create a fresh plugin instance"""
        from plugins.meshcore import MeshCorePlugin
        return MeshCorePlugin()

    def test_plugin_creation(self, plugin):
        """Plugin initializes correctly"""
        assert plugin._connected is False
        assert plugin._device is None
        assert len(plugin._nodes) == 0

    def test_get_metadata(self, plugin):
        """Plugin metadata is valid"""
        metadata = plugin.get_metadata()

        assert metadata.name == "meshcore"
        assert metadata.version == "0.1.0"
        assert "MeshCore" in metadata.description
        assert metadata.homepage == "https://meshcore.co.uk/"

    def test_get_protocol_name(self, plugin):
        """Protocol name is MeshCore"""
        assert plugin.get_protocol_name() == "MeshCore"

    def test_get_max_hops(self, plugin):
        """MeshCore supports 64 hops"""
        assert plugin.get_max_hops() == 64

    def test_get_supported_transports(self, plugin):
        """All transports are listed"""
        transports = plugin.get_supported_transports()

        assert "serial" in transports
        assert "tcp" in transports
        assert "ble" in transports
        assert "wifi" in transports
        assert "udp" in transports
        assert "simulation" in transports


class TestMeshCoreConnection:
    """Test connection methods"""

    @pytest.fixture
    def plugin(self):
        from plugins.meshcore import MeshCorePlugin
        return MeshCorePlugin()

    def test_connect_simulation_mode(self, plugin):
        """Simulation mode connects successfully"""
        result = plugin.connect_device(type="simulation")

        assert result is True
        assert plugin._connected is True
        assert plugin.is_connected() is True

    def test_connect_creates_simulated_nodes(self, plugin):
        """Simulation mode creates test nodes"""
        plugin.connect_device(type="simulation")
        nodes = plugin.get_nodes()

        assert len(nodes) >= 2
        # Check for different roles
        roles = [n['role'] for n in nodes]
        assert 'gateway' in roles or 'repeater' in roles

    def test_disconnect(self, plugin):
        """Disconnect clears state"""
        plugin.connect_device(type="simulation")
        assert plugin.is_connected() is True

        plugin.disconnect()

        assert plugin.is_connected() is False
        assert len(plugin.get_nodes()) == 0

    def test_connect_serial_fallback_to_simulation(self, plugin):
        """Serial connect falls back to simulation when port missing"""
        # Use a non-existent port
        result = plugin.connect_device(type="serial", port="/dev/nonexistent")

        # Should fall back to simulation
        assert result is True
        assert plugin._connected is True

    def test_connect_tcp_fallback_to_simulation(self, plugin):
        """TCP connect falls back to simulation"""
        result = plugin.connect_device(type="tcp", host="localhost", tcp_port=99999)

        assert result is True
        assert plugin._connected is True

    def test_connect_ble_fallback_to_simulation(self, plugin):
        """BLE connect falls back to simulation"""
        result = plugin.connect_device(type="ble", port="TestDevice")

        assert result is True
        assert plugin._connected is True

    def test_connect_unsupported_type(self, plugin):
        """Unsupported connection type fails"""
        result = plugin.connect_device(type="quantum")

        assert result is False
        assert plugin._connected is False


class TestMeshCoreMessaging:
    """Test message handling"""

    @pytest.fixture
    def connected_plugin(self):
        from plugins.meshcore import MeshCorePlugin
        plugin = MeshCorePlugin()
        plugin.connect_device(type="simulation")
        return plugin

    def test_send_message_broadcast(self, connected_plugin):
        """Send broadcast message"""
        result = connected_plugin.send_message("broadcast", "Hello mesh!")

        assert result is True
        stats = connected_plugin.get_stats()
        assert stats["messages_sent"] >= 1

    def test_send_message_to_node(self, connected_plugin):
        """Send message to specific node"""
        result = connected_plugin.send_message("mc001a2b3c", "Direct message")

        assert result is True

    def test_send_message_invalid_destination(self, connected_plugin):
        """Invalid destination fails"""
        result = connected_plugin.send_message("invalid-id", "Test")

        assert result is False
        stats = connected_plugin.get_stats()
        assert stats["messages_failed"] >= 1

    def test_send_message_not_connected(self):
        """Sending without connection fails"""
        from plugins.meshcore import MeshCorePlugin
        plugin = MeshCorePlugin()

        result = plugin.send_message("mc001a2b3c", "Test")

        assert result is False

    def test_message_truncation(self, connected_plugin, caplog):
        """Long messages are truncated to 200 chars"""
        import logging
        long_message = "x" * 300

        with caplog.at_level(logging.WARNING):
            result = connected_plugin.send_message("broadcast", long_message)

        assert result is True
        assert "truncated" in caplog.text.lower()

    def test_receive_message_callback(self, connected_plugin):
        """Message callbacks are called"""
        received = []

        def callback(msg):
            received.append(msg)

        connected_plugin.register_message_callback(callback)
        connected_plugin.on_message({"text": "Hello", "from": "mc001a2b3c"})

        assert len(received) == 1
        assert received[0]["text"] == "Hello"

    def test_multiple_callbacks(self, connected_plugin):
        """Multiple callbacks all receive messages"""
        counts = [0, 0]

        def callback1(msg):
            counts[0] += 1

        def callback2(msg):
            counts[1] += 1

        connected_plugin.register_message_callback(callback1)
        connected_plugin.register_message_callback(callback2)
        connected_plugin.on_message({"text": "Test"})

        assert counts[0] == 1
        assert counts[1] == 1


class TestMeshCoreNodeManagement:
    """Test node discovery and management"""

    @pytest.fixture
    def connected_plugin(self):
        from plugins.meshcore import MeshCorePlugin
        plugin = MeshCorePlugin()
        plugin.connect_device(type="simulation")
        return plugin

    def test_get_nodes_list(self, connected_plugin):
        """Get list of nodes"""
        nodes = connected_plugin.get_nodes()

        assert isinstance(nodes, list)
        assert len(nodes) >= 1

        # Check node structure
        node = nodes[0]
        assert 'node_id' in node
        assert 'name' in node
        assert 'role' in node

    def test_get_specific_node(self, connected_plugin):
        """Get a specific node by ID"""
        # Get first node ID from list
        nodes = connected_plugin.get_nodes()
        first_id = nodes[0]['node_id']

        node = connected_plugin.get_node(first_id)

        assert node is not None
        assert node['node_id'] == first_id

    def test_get_nonexistent_node(self, connected_plugin):
        """Getting non-existent node returns None"""
        node = connected_plugin.get_node("mc_invalid")

        assert node is None


class TestMeshCoreStatistics:
    """Test statistics tracking"""

    @pytest.fixture
    def plugin(self):
        from plugins.meshcore import MeshCorePlugin
        return MeshCorePlugin()

    def test_initial_stats(self, plugin):
        """Initial stats are zero"""
        stats = plugin.get_stats()

        assert stats["messages_sent"] == 0
        assert stats["messages_received"] == 0
        assert stats["messages_failed"] == 0
        assert stats["connect_attempts"] == 0
        assert stats["connected"] is False

    def test_connect_increments_attempts(self, plugin):
        """Connection attempt is tracked"""
        plugin.connect_device(type="simulation")
        stats = plugin.get_stats()

        assert stats["connect_attempts"] == 1
        assert stats["connected"] is True
        assert stats["node_count"] >= 1

    def test_message_stats_tracked(self, plugin):
        """Message statistics are tracked"""
        plugin.connect_device(type="simulation")

        plugin.send_message("broadcast", "Test 1")
        plugin.send_message("broadcast", "Test 2")
        plugin.send_message("invalid", "Should fail")
        plugin.on_message({"text": "Received"})

        stats = plugin.get_stats()
        assert stats["messages_sent"] == 2
        assert stats["messages_failed"] == 1
        assert stats["messages_received"] == 1


class TestMeshCoreActivation:
    """Test plugin activation/deactivation"""

    @pytest.fixture
    def plugin(self):
        from plugins.meshcore import MeshCorePlugin
        return MeshCorePlugin()

    def test_activate(self, plugin, caplog):
        """Activation logs info"""
        import logging
        with caplog.at_level(logging.INFO):
            plugin.activate()

        assert "activated" in caplog.text.lower()

    def test_deactivate_disconnects(self, plugin):
        """Deactivation disconnects if connected"""
        plugin.connect_device(type="simulation")
        assert plugin.is_connected() is True

        plugin.deactivate()

        assert plugin.is_connected() is False

    def test_deactivate_clears_callbacks(self, plugin):
        """Deactivation clears message callbacks"""
        plugin.register_message_callback(lambda x: None)
        plugin.deactivate()

        # Internal state check (callbacks list should be empty)
        assert len(plugin._message_callbacks) == 0


class TestNodeIdValidation:
    """Test node ID validation"""

    @pytest.fixture
    def plugin(self):
        from plugins.meshcore import MeshCorePlugin
        plugin = MeshCorePlugin()
        plugin.connect_device(type="simulation")
        return plugin

    def test_valid_node_id(self, plugin):
        """Valid node IDs are accepted"""
        assert plugin._validate_node_id("mc001a2b3c") is True
        assert plugin._validate_node_id("MC001A2B3C") is True
        assert plugin._validate_node_id("mcabcdef12") is True

    def test_invalid_node_ids(self, plugin):
        """Invalid node IDs are rejected"""
        assert plugin._validate_node_id("invalid") is False
        assert plugin._validate_node_id("001a2b3c") is False  # Missing mc prefix
        assert plugin._validate_node_id("mc001a2b") is False  # Too short
        assert plugin._validate_node_id("mc001a2b3c4") is False  # Too long
        assert plugin._validate_node_id("mcXXXXXXXX") is False  # Invalid hex

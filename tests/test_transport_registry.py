"""
Tests for the Gateway Transport Registry.

Tests cover:
- Path definitions for all bridge modes
- Service dependency queries
- Protocol filtering
- Mode validation and summaries
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.transport_registry import (
    TransportPath,
    TransportRegistry,
    get_registry,
)


class TestTransportPath:
    """Test TransportPath dataclass."""

    def test_short_name(self):
        p = TransportPath(
            name="test", bridge_mode="test",
            source_protocol="meshtastic", dest_protocol="rns",
        )
        assert p.short_name == "mesh→rns"

    def test_is_production(self):
        p = TransportPath(
            name="test", bridge_mode="test",
            source_protocol="meshtastic", dest_protocol="rns",
            status="production",
        )
        assert p.is_production

    def test_is_not_production(self):
        p = TransportPath(
            name="test", bridge_mode="test",
            source_protocol="meshcore", dest_protocol="rns",
            status="alpha",
        )
        assert not p.is_production


class TestTransportRegistry:
    """Test TransportRegistry queries."""

    @pytest.fixture
    def registry(self):
        return TransportRegistry()

    def test_bridge_modes_registered(self, registry):
        """All 6 bridge modes should be registered."""
        modes = registry.bridge_modes
        assert "mqtt_bridge" in modes
        assert "message_bridge" in modes
        assert "rns_transport" in modes
        assert "mesh_bridge" in modes
        assert "meshcore_bridge" in modes
        assert "tri_bridge" in modes

    def test_mqtt_bridge_paths(self, registry):
        """MQTT bridge has bidirectional paths."""
        paths = registry.get_paths_for_mode("mqtt_bridge")
        assert len(paths) >= 2
        protocols = {(p.source_protocol, p.dest_protocol) for p in paths}
        assert ("meshtastic", "rns") in protocols
        assert ("rns", "meshtastic") in protocols

    def test_mqtt_bridge_services(self, registry):
        """MQTT bridge requires meshtasticd, mosquitto, rnsd."""
        services = registry.get_required_services("mqtt_bridge")
        assert "meshtasticd" in services
        assert "mosquitto" in services
        assert "rnsd" in services

    def test_message_bridge_services(self, registry):
        """Message bridge requires meshtasticd and rnsd (no mosquitto)."""
        services = registry.get_required_services("message_bridge")
        assert "meshtasticd" in services
        assert "rnsd" in services
        assert "mosquitto" not in services

    def test_mesh_bridge_is_meshtastic_only(self, registry):
        """Mesh bridge only involves meshtastic protocol."""
        paths = registry.get_paths_for_mode("mesh_bridge")
        for p in paths:
            assert p.source_protocol == "meshtastic"
            assert p.dest_protocol == "meshtastic"

    def test_meshcore_paths_are_alpha(self, registry):
        """MeshCore paths should be alpha status."""
        paths = registry.get_paths_for_mode("meshcore_bridge")
        for p in paths:
            assert p.status == "alpha"

    def test_get_paths_for_protocol(self, registry):
        """Filter paths by protocol."""
        rns_paths = registry.get_paths_for_protocol("rns")
        assert len(rns_paths) > 0
        for p in rns_paths:
            assert p.source_protocol == "rns" or p.dest_protocol == "rns"

    def test_get_production_paths(self, registry):
        """Production paths exclude alpha."""
        prod = registry.get_production_paths()
        for p in prod:
            assert p.status == "production"
        # At least mqtt_bridge and message_bridge paths
        assert len(prod) >= 4

    def test_validate_mode(self, registry):
        """Mode validation."""
        assert registry.validate_mode("mqtt_bridge")
        assert registry.validate_mode("mesh_bridge")
        assert not registry.validate_mode("nonexistent_mode")

    def test_get_mode_summary(self, registry):
        """Mode summary returns dict with expected keys."""
        summary = registry.get_mode_summary("mqtt_bridge")
        assert summary is not None
        assert summary["mode"] == "mqtt_bridge"
        assert summary["paths"] >= 2
        assert "meshtastic" in summary["protocols"]
        assert "rns" in summary["protocols"]
        assert "meshtasticd" in summary["required_services"]

    def test_get_mode_summary_invalid(self, registry):
        """Invalid mode returns None."""
        assert registry.get_mode_summary("fake_mode") is None

    def test_unknown_mode_empty_paths(self, registry):
        """Unknown mode returns empty list."""
        paths = registry.get_paths_for_mode("nonexistent")
        assert paths == []

    def test_unknown_mode_empty_services(self, registry):
        """Unknown mode returns empty set."""
        services = registry.get_required_services("nonexistent")
        assert services == set()

    def test_all_paths_have_required_fields(self, registry):
        """All paths have name, mode, source, dest."""
        for p in registry.all_paths:
            assert p.name
            assert p.bridge_mode
            assert p.source_protocol
            assert p.dest_protocol
            assert p.status in ("production", "alpha", "planned")


class TestGetRegistry:
    """Test singleton registry."""

    def test_singleton(self):
        """get_registry returns same instance."""
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_registry_is_populated(self):
        """Registry has paths on creation."""
        r = get_registry()
        assert len(r.all_paths) > 0

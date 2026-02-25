"""
Transport Path Registry for MeshForge Gateway.

Machine-readable registry of all transport paths across bridge modes.
Powers TUI gateway status display and service pre-flight validation.

Usage:
    from gateway.transport_registry import TransportRegistry, get_registry

    registry = get_registry()
    paths = registry.get_paths_for_mode("mqtt_bridge")
    services = registry.get_required_services("mqtt_bridge")
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class TransportPath:
    """A single directional transport path through the gateway."""

    name: str                         # "mqtt_bridge_mesh_to_rns"
    bridge_mode: str                  # "mqtt_bridge"
    source_protocol: str              # "meshtastic" | "rns" | "meshcore"
    dest_protocol: str                # "meshtastic" | "rns" | "meshcore"
    transport_chain: List[str] = field(default_factory=list)
    required_services: List[str] = field(default_factory=list)
    max_payload_bytes: int = 237
    bidirectional: bool = True
    status: str = "production"        # "production" | "alpha" | "planned"
    description: str = ""
    handler_file: str = ""            # Source file implementing this path

    @property
    def short_name(self) -> str:
        """Abbreviated name for display."""
        return f"{self.source_protocol[:4]}→{self.dest_protocol[:4]}"

    @property
    def is_production(self) -> bool:
        return self.status == "production"


# ── Static Path Definitions ───────────────────────────────────────────────────

def _build_paths() -> List[TransportPath]:
    """Build the complete set of transport path definitions."""
    paths = []

    # --- mqtt_bridge mode ---
    paths.append(TransportPath(
        name="mqtt_bridge_mesh_to_rns",
        bridge_mode="mqtt_bridge",
        source_protocol="meshtastic",
        dest_protocol="rns",
        transport_chain=[
            "lora", "meshtasticd", "mqtt_broker", "mqtt_bridge_handler",
            "canonical_message", "message_router", "lxmf", "rnsd",
        ],
        required_services=["meshtasticd", "mosquitto", "rnsd"],
        max_payload_bytes=237,
        status="production",
        description="MQTT-based Meshtastic to RNS bridge (recommended)",
        handler_file="gateway/mqtt_bridge_handler.py",
    ))
    paths.append(TransportPath(
        name="mqtt_bridge_rns_to_mesh",
        bridge_mode="mqtt_bridge",
        source_protocol="rns",
        dest_protocol="meshtastic",
        transport_chain=[
            "rns_network", "rnsd", "lxmf_callback", "canonical_message",
            "message_router", "http_protobuf", "meshtasticd", "lora",
        ],
        required_services=["meshtasticd", "mosquitto", "rnsd"],
        max_payload_bytes=237,
        status="production",
        description="RNS to Meshtastic via HTTP protobuf TX",
        handler_file="gateway/rns_bridge.py",
    ))

    # --- message_bridge mode ---
    paths.append(TransportPath(
        name="message_bridge_mesh_to_rns",
        bridge_mode="message_bridge",
        source_protocol="meshtastic",
        dest_protocol="rns",
        transport_chain=[
            "lora", "meshtasticd", "tcp_4403", "meshtastic_handler",
            "canonical_message", "message_router", "lxmf", "rnsd",
        ],
        required_services=["meshtasticd", "rnsd"],
        max_payload_bytes=237,
        status="production",
        description="Legacy TCP-based bridge (blocks web client)",
        handler_file="gateway/meshtastic_handler.py",
    ))
    paths.append(TransportPath(
        name="message_bridge_rns_to_mesh",
        bridge_mode="message_bridge",
        source_protocol="rns",
        dest_protocol="meshtastic",
        transport_chain=[
            "rns_network", "rnsd", "lxmf_callback", "canonical_message",
            "message_router", "tcp_4403", "meshtasticd", "lora",
        ],
        required_services=["meshtasticd", "rnsd"],
        max_payload_bytes=237,
        status="production",
        description="RNS to Meshtastic via TCP",
        handler_file="gateway/rns_bridge.py",
    ))

    # --- rns_transport mode ---
    paths.append(TransportPath(
        name="rns_transport_bidirectional",
        bridge_mode="rns_transport",
        source_protocol="rns",
        dest_protocol="meshtastic",
        transport_chain=[
            "rns_packet", "rns_over_meshtastic", "fragment",
            "meshtasticd", "lora", "reassemble",
        ],
        required_services=["meshtasticd", "rnsd"],
        max_payload_bytes=194,  # Per fragment
        bidirectional=True,
        status="production",
        description="RNS packets fragmented over Meshtastic LoRa (raw transport)",
        handler_file="gateway/rns_transport.py",
    ))

    # --- mesh_bridge mode ---
    paths.append(TransportPath(
        name="mesh_bridge_primary_to_secondary",
        bridge_mode="mesh_bridge",
        source_protocol="meshtastic",
        dest_protocol="meshtastic",
        transport_chain=[
            "lora_preset_a", "meshtasticd_4403", "mesh_bridge",
            "meshtasticd_4404", "lora_preset_b",
        ],
        required_services=["meshtasticd"],  # Two instances needed
        max_payload_bytes=237,
        bidirectional=True,
        status="production",
        description="Bridge two Meshtastic networks with different LoRa presets",
        handler_file="gateway/mesh_bridge.py",
    ))

    # --- meshcore_bridge mode ---
    paths.append(TransportPath(
        name="meshcore_bridge_core_to_mesh",
        bridge_mode="meshcore_bridge",
        source_protocol="meshcore",
        dest_protocol="meshtastic",
        transport_chain=[
            "meshcore_radio", "serial_tcp_ble", "meshcore_handler",
            "canonical_message", "message_router", "meshtasticd", "lora",
        ],
        required_services=[],  # meshtasticd optional in meshcore_primary
        max_payload_bytes=160,
        status="alpha",
        description="MeshCore to Meshtastic via companion radio",
        handler_file="gateway/meshcore_handler.py",
    ))
    paths.append(TransportPath(
        name="meshcore_bridge_mesh_to_core",
        bridge_mode="meshcore_bridge",
        source_protocol="meshtastic",
        dest_protocol="meshcore",
        transport_chain=[
            "lora", "meshtasticd", "mqtt_or_tcp", "canonical_message",
            "message_router", "meshcore_handler", "serial_tcp_ble",
            "meshcore_radio",
        ],
        required_services=[],
        max_payload_bytes=160,
        status="alpha",
        description="Meshtastic to MeshCore via companion radio",
        handler_file="gateway/meshcore_bridge_mixin.py",
    ))
    paths.append(TransportPath(
        name="meshcore_bridge_core_to_rns",
        bridge_mode="meshcore_bridge",
        source_protocol="meshcore",
        dest_protocol="rns",
        transport_chain=[
            "meshcore_radio", "serial_tcp_ble", "meshcore_handler",
            "canonical_message", "message_router", "lxmf", "rnsd",
        ],
        required_services=["rnsd"],
        max_payload_bytes=160,
        status="alpha",
        description="MeshCore to RNS via companion radio",
        handler_file="gateway/meshcore_handler.py",
    ))

    # --- tri_bridge mode ---
    paths.append(TransportPath(
        name="tri_bridge_all",
        bridge_mode="tri_bridge",
        source_protocol="meshtastic",
        dest_protocol="rns",
        transport_chain=[
            "all_handlers", "canonical_message", "message_router",
            "all_destinations",
        ],
        required_services=["meshtasticd", "rnsd"],
        max_payload_bytes=160,  # Smallest common denominator
        bidirectional=True,
        status="alpha",
        description="Full 3-way bridge: Meshtastic + MeshCore + RNS",
        handler_file="gateway/rns_bridge.py",
    ))

    return paths


class TransportRegistry:
    """Registry of all gateway transport paths.

    Provides lookup by bridge mode, protocol, and service requirements.
    """

    def __init__(self):
        self._paths = _build_paths()
        self._by_mode: Dict[str, List[TransportPath]] = {}
        for path in self._paths:
            self._by_mode.setdefault(path.bridge_mode, []).append(path)

    @property
    def all_paths(self) -> List[TransportPath]:
        """All registered transport paths."""
        return list(self._paths)

    @property
    def bridge_modes(self) -> List[str]:
        """All registered bridge mode names."""
        return sorted(self._by_mode.keys())

    def get_paths_for_mode(self, bridge_mode: str) -> List[TransportPath]:
        """Get all transport paths for a bridge mode.

        Args:
            bridge_mode: Mode name (e.g., "mqtt_bridge").

        Returns:
            List of TransportPath objects.
        """
        return self._by_mode.get(bridge_mode, [])

    def get_required_services(self, bridge_mode: str) -> Set[str]:
        """Get all services required by a bridge mode.

        Args:
            bridge_mode: Mode name.

        Returns:
            Set of service names (e.g., {"meshtasticd", "mosquitto", "rnsd"}).
        """
        services: Set[str] = set()
        for path in self.get_paths_for_mode(bridge_mode):
            services.update(path.required_services)
        return services

    def get_paths_for_protocol(self, protocol: str) -> List[TransportPath]:
        """Get all paths involving a specific protocol (as source or dest)."""
        return [
            p for p in self._paths
            if p.source_protocol == protocol or p.dest_protocol == protocol
        ]

    def get_production_paths(self) -> List[TransportPath]:
        """Get only production-status paths."""
        return [p for p in self._paths if p.is_production]

    def validate_mode(self, bridge_mode: str) -> bool:
        """Check if a bridge mode is registered."""
        return bridge_mode in self._by_mode

    def get_mode_summary(self, bridge_mode: str) -> Optional[Dict]:
        """Get a summary dict for a bridge mode (for TUI display).

        Returns:
            Dict with mode info, or None if mode not found.
        """
        paths = self.get_paths_for_mode(bridge_mode)
        if not paths:
            return None

        services = self.get_required_services(bridge_mode)
        protocols = set()
        for p in paths:
            protocols.add(p.source_protocol)
            protocols.add(p.dest_protocol)

        return {
            "mode": bridge_mode,
            "paths": len(paths),
            "protocols": sorted(protocols),
            "required_services": sorted(services),
            "status": paths[0].status,
            "description": paths[0].description,
        }


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: Optional[TransportRegistry] = None


def get_registry() -> TransportRegistry:
    """Get the global transport registry singleton."""
    global _registry
    if _registry is None:
        _registry = TransportRegistry()
    return _registry

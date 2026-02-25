"""
Gateway Topology Inspector - Runtime inspection of active gateway topology.

Provides:
- Active bridge mode and connected services status
- Queue depths and persistent queue statistics
- ASCII topology diagram for TUI display
- AREDN router detection in data path
- Integration with BridgeHealthMonitor

Usage:
    from gateway.topology_inspector import TopologyInspector

    inspector = TopologyInspector(config)
    print(inspector.ascii_diagram())
    status = inspector.get_status()
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.safe_import import safe_import

from .config import GatewayConfig
from .transport_registry import get_registry

# Optional imports for runtime inspection
_check_service, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service',
)

_BridgeHealthMonitor, _HAS_HEALTH = safe_import(
    'gateway.bridge_health', 'BridgeHealthMonitor',
)

_AREDNClient, _HAS_AREDN = safe_import(
    'utils.aredn', 'AREDNClient',
)

logger = logging.getLogger(__name__)


@dataclass
class ServiceStatus:
    """Status of an external service."""
    name: str
    required: bool = False
    running: bool = False
    detail: str = ""


@dataclass
class TopologyStatus:
    """Complete topology status snapshot."""
    bridge_mode: str = ""
    bridge_enabled: bool = False
    services: List[ServiceStatus] = field(default_factory=list)
    protocols: List[str] = field(default_factory=list)
    path_count: int = 0
    queue_depths: Dict[str, int] = field(default_factory=dict)
    aredn_detected: bool = False
    aredn_router_ip: str = ""
    health_summary: Dict[str, Any] = field(default_factory=dict)

    @property
    def all_services_ok(self) -> bool:
        """Check if all required services are running."""
        return all(
            s.running for s in self.services if s.required
        )


class TopologyInspector:
    """Runtime inspector for the gateway topology.

    Reads the current GatewayConfig and probes running services
    to build a live topology status.
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        """Initialize inspector.

        Args:
            config: GatewayConfig instance. If None, loads from file.
        """
        if config is None:
            try:
                config = GatewayConfig.load()
            except Exception:
                config = GatewayConfig()
        self._config = config
        self._registry = get_registry()

    def get_status(self) -> TopologyStatus:
        """Get complete topology status.

        Probes services, checks queues, detects AREDN routers.

        Returns:
            TopologyStatus snapshot.
        """
        mode = self._config.bridge_mode
        paths = self._registry.get_paths_for_mode(mode)
        required = self._registry.get_required_services(mode)

        # Check services
        services = []
        for svc_name in sorted(required | {"meshtasticd", "rnsd"}):
            is_required = svc_name in required
            running = False
            detail = ""

            if _HAS_SERVICE_CHECK:
                try:
                    result = _check_service(svc_name)
                    running = getattr(result, 'is_running', False) if result else False
                    detail = "running" if running else "stopped"
                except Exception as e:
                    detail = str(e)

            services.append(ServiceStatus(
                name=svc_name,
                required=is_required,
                running=running,
                detail=detail,
            ))

        # Check MQTT broker if needed
        if "mosquitto" in required:
            mqtt_running = False
            if _HAS_SERVICE_CHECK:
                try:
                    result = _check_service("mosquitto")
                    mqtt_running = getattr(result, 'is_running', False) if result else False
                except Exception:
                    pass
            services.append(ServiceStatus(
                name="mosquitto",
                required=True,
                running=mqtt_running,
                detail="running" if mqtt_running else "stopped",
            ))

        # Protocols involved
        protocols = set()
        for p in paths:
            protocols.add(p.source_protocol)
            protocols.add(p.dest_protocol)

        # AREDN detection
        aredn_detected = False
        aredn_ip = ""
        if _HAS_AREDN:
            try:
                client = _AREDNClient()
                info = client.get_node_info()
                if info:
                    aredn_detected = True
                    aredn_ip = getattr(info, 'ip', '') or ""
            except Exception:
                pass

        return TopologyStatus(
            bridge_mode=mode,
            bridge_enabled=self._config.enabled,
            services=services,
            protocols=sorted(protocols),
            path_count=len(paths),
            aredn_detected=aredn_detected,
            aredn_router_ip=aredn_ip,
        )

    def ascii_diagram(self) -> str:
        """Generate ASCII topology diagram for TUI display.

        Returns:
            Multi-line string with topology visualization.
        """
        status = self.get_status()
        mode = status.bridge_mode
        lines = []

        lines.append(f"Gateway Mode: {mode}")
        lines.append(f"Enabled: {'Yes' if status.bridge_enabled else 'No'}")
        lines.append("")

        # Service status
        lines.append("Services:")
        for svc in status.services:
            marker = "[OK]" if svc.running else "[--]"
            req = " (required)" if svc.required else ""
            lines.append(f"  {marker} {svc.name:<15}{req}")
        lines.append("")

        # Draw topology based on mode
        if mode == "mqtt_bridge":
            lines.extend(self._diagram_mqtt_bridge(status))
        elif mode == "message_bridge":
            lines.extend(self._diagram_message_bridge(status))
        elif mode == "rns_transport":
            lines.extend(self._diagram_rns_transport(status))
        elif mode == "mesh_bridge":
            lines.extend(self._diagram_mesh_bridge(status))
        elif mode in ("meshcore_bridge", "meshcore_primary"):
            lines.extend(self._diagram_meshcore_bridge(status))
        elif mode == "tri_bridge":
            lines.extend(self._diagram_tri_bridge(status))
        else:
            lines.append(f"  (Unknown mode: {mode})")

        # AREDN overlay
        if status.aredn_detected:
            lines.append("")
            lines.append(f"AREDN Router Detected: {status.aredn_router_ip}")
            lines.append("  Node may be reachable via AREDN backhaul")

        return "\n".join(lines)

    # ── Diagram helpers ───────────────────────────────────────────────────

    @staticmethod
    def _diagram_mqtt_bridge(status: TopologyStatus) -> List[str]:
        return [
            "Data Flow:",
            "",
            "  LoRa Mesh",
            "    |",
            "    v",
            "  meshtasticd (:4403)",
            "    |  MQTT publish (JSON)",
            "    v",
            "  mosquitto (:1883)",
            "    |  MQTT subscribe",
            "    v",
            "  MeshForge Gateway",
            "    |  CanonicalMessage + Router",
            "    v",
            "  rnsd (LXMF)",
            "    |",
            "    v",
            "  RNS Network",
            "",
            "  TX: HTTP protobuf → meshtasticd → LoRa",
        ]

    @staticmethod
    def _diagram_message_bridge(status: TopologyStatus) -> List[str]:
        return [
            "Data Flow:",
            "",
            "  LoRa Mesh",
            "    |",
            "    v",
            "  meshtasticd (:4403)",
            "    |  TCP stream (protobuf)",
            "    v",
            "  MeshForge Gateway",
            "    |  CanonicalMessage + Router",
            "    v",
            "  rnsd (LXMF)",
            "    |",
            "    v",
            "  RNS Network",
            "",
            "  WARNING: TCP holds exclusive connection",
            "  (web client blocked at :9443)",
        ]

    @staticmethod
    def _diagram_rns_transport(status: TopologyStatus) -> List[str]:
        return [
            "Data Flow:",
            "",
            "  RNS Application",
            "    |",
            "    v",
            "  rnsd",
            "    |  RNS Interface",
            "    v",
            "  RNSOverMeshtastic",
            "    |  Fragment (194B chunks)",
            "    v",
            "  meshtasticd (:4403)",
            "    |",
            "    v",
            "  LoRa Mesh (bidirectional)",
            "",
            "  Raw RNS transport (no message translation)",
        ]

    @staticmethod
    def _diagram_mesh_bridge(status: TopologyStatus) -> List[str]:
        return [
            "Data Flow:",
            "",
            "  LoRa Mesh (Preset A)",
            "    |",
            "    v",
            "  meshtasticd #1 (:4403)",
            "    |  Dedup + forward",
            "    v",
            "  MeshForge Preset Bridge",
            "    |",
            "    v",
            "  meshtasticd #2 (:4404)",
            "    |",
            "    v",
            "  LoRa Mesh (Preset B)",
            "",
            "  Bidirectional preset bridging",
        ]

    @staticmethod
    def _diagram_meshcore_bridge(status: TopologyStatus) -> List[str]:
        return [
            "Data Flow:",
            "",
            "  MeshCore Radio",
            "    |  Serial/TCP/BLE",
            "    v",
            "  meshcore_py",
            "    |  Event subscription",
            "    v",
            "  MeshForge Gateway",
            "    |  CanonicalMessage + Router",
            "    +---> meshtasticd → LoRa",
            "    +---> rnsd → RNS Network",
            "",
            "  (Alpha: meshcore-bridge branch)",
        ]

    @staticmethod
    def _diagram_tri_bridge(status: TopologyStatus) -> List[str]:
        return [
            "Data Flow:",
            "",
            "  Meshtastic <--+",
            "                |",
            "  MeshCore   <--+--> MeshForge Hub",
            "                |    (CanonicalMessage)",
            "  RNS/LXMF  <--+",
            "",
            "  All-to-all routing enabled",
            "  (Alpha: meshcore-bridge branch)",
        ]

"""
AREDN Topology Overlay for MeshForge Gateway.

Provides AREDN backhaul topology awareness:
- Discovers AREDN router on local network
- Maps remote MeshForge gateways reachable via AREDN
- Reports link quality for backhaul paths
- Feeds node tracker with reachability information
- Provides routing hints to MessageRouter for AREDN-aware routing

Usage:
    from gateway.aredn_topology import AREDNTopologyOverlay

    overlay = AREDNTopologyOverlay(router_ip="10.10.0.1")
    links = overlay.discover_backhaul_links()
    reach = overlay.get_reachability("!abcd1234")
    hint = overlay.get_routing_hint("!abcd1234")
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from utils.safe_import import safe_import

_AREDNClient, _AREDNScanner, _AREDNNode, _AREDNLink, _HAS_AREDN = safe_import(
    'utils.aredn',
    'AREDNClient', 'AREDNScanner', 'AREDNNode', 'AREDNLink',
)

logger = logging.getLogger(__name__)


class LinkType(Enum):
    """AREDN link type between nodes."""
    RF = "rf"        # 5.8 GHz WiFi RF link
    DTD = "dtd"      # Device-to-Device (Ethernet VLAN 2)
    TUN = "tun"      # VPN tunnel over internet
    UNKNOWN = "unknown"


@dataclass
class AREDNBackhaulLink:
    """A discovered AREDN backhaul link to a remote node."""
    local_aredn_ip: str
    remote_aredn_ip: str
    remote_node_name: str = ""
    link_type: LinkType = LinkType.UNKNOWN
    link_quality: float = 0.0        # 0.0-1.0 (LQ from OLSR)
    neighbor_quality: float = 0.0    # 0.0-1.0 (NLQ from OLSR)
    last_checked: Optional[datetime] = None

    @property
    def is_healthy(self) -> bool:
        """Link quality > 50% is considered healthy."""
        return self.link_quality > 0.5


@dataclass
class GatewayReachability:
    """Reachability information for a mesh node."""
    node_id: str
    via_lora: bool = True
    via_aredn: bool = False
    aredn_router_ip: str = ""
    aredn_link_quality: float = 0.0
    aredn_link_type: LinkType = LinkType.UNKNOWN

    @property
    def has_aredn_path(self) -> bool:
        return self.via_aredn and self.aredn_link_quality > 0.0


@dataclass
class AREDNHealthSummary:
    """Health summary of the AREDN topology overlay."""
    connected: bool = False
    local_router_ip: str = ""
    local_router_name: str = ""
    neighbor_count: int = 0
    healthy_links: int = 0
    degraded_links: int = 0
    remote_gateways: int = 0
    last_scan: Optional[datetime] = None


class AREDNTopologyOverlay:
    """Read-only AREDN topology awareness for the gateway.

    Discovers AREDN routers on the local network and identifies
    remote MeshForge gateways reachable via AREDN backhaul.
    """

    # Services that indicate a MeshForge gateway
    GATEWAY_SERVICES = {"meshtasticd", "rnsd", "meshforge"}

    def __init__(self, router_ip: str = "", auto_detect: bool = True,
                 poll_interval_sec: int = 60):
        """Initialize AREDN topology overlay.

        Args:
            router_ip: AREDN router IP. Empty for auto-detect.
            auto_detect: Try to auto-detect local AREDN router.
            poll_interval_sec: How often to re-scan topology.
        """
        self._router_ip = router_ip
        self._auto_detect = auto_detect
        self._poll_interval = poll_interval_sec
        self._links: List[AREDNBackhaulLink] = []
        self._remote_gateways: Dict[str, str] = {}  # IP -> node_name
        self._lock = threading.Lock()
        self._last_scan: Optional[datetime] = None
        self._connected = False

        if not _HAS_AREDN:
            logger.warning("[AREDN] aredn module not available")
            return

        if not self._router_ip and auto_detect:
            self._router_ip = self._detect_router()

    def _detect_router(self) -> str:
        """Auto-detect local AREDN router.

        Tries common AREDN gateway addresses on the 10.x.x.x network.
        """
        if not _HAS_AREDN:
            return ""

        # Common AREDN default gateway addresses
        candidates = [
            "localnode.local.mesh",  # AREDN mDNS name
            "10.10.0.1",             # Common /28 allocation
            "10.0.0.1",              # First address in 10.x block
        ]

        client = _AREDNClient()
        for addr in candidates:
            try:
                client.host = addr
                info = client.get_sysinfo(hosts=False, services=False,
                                          link_info=False, lqm=False)
                if info:
                    logger.info(f"[AREDN] Auto-detected router at {addr}")
                    return addr
            except Exception:
                continue

        logger.debug("[AREDN] No AREDN router auto-detected")
        return ""

    @property
    def is_connected(self) -> bool:
        """Check if we have a valid AREDN router connection."""
        return self._connected and bool(self._router_ip)

    def discover_backhaul_links(self) -> List[AREDNBackhaulLink]:
        """Discover AREDN backhaul links from the local router.

        Queries the AREDN API for neighbor information and link quality.

        Returns:
            List of discovered backhaul links.
        """
        if not _HAS_AREDN or not self._router_ip:
            return []

        links = []
        try:
            client = _AREDNClient(host=self._router_ip)
            neighbors = client.get_neighbors()

            for neighbor in neighbors:
                link_type = self._classify_link(neighbor)
                lq = getattr(neighbor, 'link_quality', 0.0)
                nlq = getattr(neighbor, 'neighbor_quality', 0.0)

                # Normalize quality to 0.0-1.0
                if lq > 1.0:
                    lq = lq / 100.0
                if nlq > 1.0:
                    nlq = nlq / 100.0

                link = AREDNBackhaulLink(
                    local_aredn_ip=self._router_ip,
                    remote_aredn_ip=getattr(neighbor, 'ip', '') or "",
                    remote_node_name=getattr(neighbor, 'name', '') or "",
                    link_type=link_type,
                    link_quality=lq,
                    neighbor_quality=nlq,
                    last_checked=datetime.now(),
                )
                links.append(link)

            self._connected = True
            self._last_scan = datetime.now()

        except Exception as e:
            logger.error(f"[AREDN] Link discovery failed: {e}")
            self._connected = False

        with self._lock:
            self._links = links

        # Identify remote gateways
        self._identify_remote_gateways()

        return links

    def _classify_link(self, neighbor) -> LinkType:
        """Classify AREDN link type from neighbor data."""
        link_type_str = getattr(neighbor, 'link_type', '') or ""
        link_type_str = link_type_str.lower()

        if "dtd" in link_type_str:
            return LinkType.DTD
        if "tun" in link_type_str or "tunnel" in link_type_str:
            return LinkType.TUN
        if "rf" in link_type_str or "wifi" in link_type_str:
            return LinkType.RF

        return LinkType.UNKNOWN

    def _identify_remote_gateways(self) -> None:
        """Identify which AREDN neighbors are MeshForge gateways.

        Queries each neighbor's service list for rnsd/meshtasticd.
        """
        if not _HAS_AREDN:
            return

        gateways = {}
        for link in self._links:
            if not link.remote_aredn_ip:
                continue

            try:
                client = _AREDNClient(host=link.remote_aredn_ip)
                info = client.get_sysinfo(
                    hosts=False, services=True,
                    link_info=False, lqm=False,
                )
                if not info:
                    continue

                services = info.get('services', []) if isinstance(info, dict) else []
                service_names = set()
                for svc in services:
                    if isinstance(svc, dict):
                        name = svc.get('name', '').lower()
                    else:
                        name = str(svc).lower()
                    service_names.add(name)

                if service_names & self.GATEWAY_SERVICES:
                    gateways[link.remote_aredn_ip] = link.remote_node_name
                    logger.info(
                        f"[AREDN] Found remote gateway: "
                        f"{link.remote_node_name} ({link.remote_aredn_ip})"
                    )

            except Exception as e:
                logger.debug(f"[AREDN] Service query failed for {link.remote_aredn_ip}: {e}")

        with self._lock:
            self._remote_gateways = gateways

    def get_reachability(self, node_id: str) -> GatewayReachability:
        """Get reachability information for a node.

        Currently returns basic AREDN awareness. Future versions
        could map specific Meshtastic nodes to AREDN-connected gateways.

        Args:
            node_id: Mesh node identifier.

        Returns:
            GatewayReachability with AREDN path info.
        """
        reach = GatewayReachability(node_id=node_id, via_lora=True)

        with self._lock:
            if self._remote_gateways:
                # Node might be reachable via any remote gateway
                reach.via_aredn = True
                # Use best link quality among gateway links
                for link in self._links:
                    if link.remote_aredn_ip in self._remote_gateways:
                        if link.link_quality > reach.aredn_link_quality:
                            reach.aredn_router_ip = link.remote_aredn_ip
                            reach.aredn_link_quality = link.link_quality
                            reach.aredn_link_type = link.link_type

        return reach

    def get_health_summary(self) -> AREDNHealthSummary:
        """Get health summary of the AREDN overlay.

        Returns:
            AREDNHealthSummary with connection and link status.
        """
        with self._lock:
            links = list(self._links)
            gateways = dict(self._remote_gateways)

        healthy = sum(1 for l in links if l.is_healthy)
        degraded = len(links) - healthy

        # Get local router name
        local_name = ""
        if _HAS_AREDN and self._router_ip:
            try:
                client = _AREDNClient(host=self._router_ip)
                info = client.get_node_info()
                if info:
                    local_name = getattr(info, 'name', '') or ""
            except Exception:
                pass

        return AREDNHealthSummary(
            connected=self._connected,
            local_router_ip=self._router_ip,
            local_router_name=local_name,
            neighbor_count=len(links),
            healthy_links=healthy,
            degraded_links=degraded,
            remote_gateways=len(gateways),
            last_scan=self._last_scan,
        )

    def get_links(self) -> List[AREDNBackhaulLink]:
        """Get cached backhaul links."""
        with self._lock:
            return list(self._links)

    def get_remote_gateways(self) -> Dict[str, str]:
        """Get discovered remote gateways {ip: name}."""
        with self._lock:
            return dict(self._remote_gateways)

    def get_routing_hint(self, node_id: str) -> Dict[str, Any]:
        """Get AREDN routing hint for a node.

        Returns a routing-friendly dict indicating whether AREDN backhaul
        is available for a given node and how good the path is.

        Args:
            node_id: Mesh node identifier.

        Returns:
            Dict with keys: available, quality, router_ip, link_type, gateway_name.
        """
        reach = self.get_reachability(node_id)
        gateway_name = ""
        if reach.aredn_router_ip:
            with self._lock:
                gateway_name = self._remote_gateways.get(reach.aredn_router_ip, "")

        return {
            "available": reach.has_aredn_path,
            "quality": reach.aredn_link_quality,
            "router_ip": reach.aredn_router_ip,
            "link_type": reach.aredn_link_type.value if reach.aredn_link_type else "unknown",
            "gateway_name": gateway_name,
        }

    def get_all_routing_hints(self) -> Dict[str, Dict[str, Any]]:
        """Get routing hints for all remote gateways.

        Returns:
            Dict mapping gateway IP to routing hint dict.
        """
        with self._lock:
            gateways = dict(self._remote_gateways)
            links = list(self._links)

        hints = {}
        for link in links:
            if link.remote_aredn_ip in gateways:
                hints[link.remote_aredn_ip] = {
                    "available": link.is_healthy,
                    "quality": link.link_quality,
                    "router_ip": link.remote_aredn_ip,
                    "link_type": link.link_type.value,
                    "gateway_name": gateways.get(link.remote_aredn_ip, ""),
                }
        return hints

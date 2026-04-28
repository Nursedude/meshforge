"""AREDN node collection for coverage maps.

Extracted from map_data_collector.py for file size compliance (CLAUDE.md #6).

Expects on the host class:
- self._settings: SettingsManager
- self._make_feature(...): GeoJSON feature builder
- self._is_node_online(last_heard, source): online status check
- self._record_diagnostic(...): per-source diagnostic recorder
- self._info_log_rate_limited(source, message)
- self._fetch_aredn_worldmap_nodes(): provided by PublicDataFallbackMixin
"""

import logging
import socket
import time
import urllib.request
from typing import Dict, List, Optional

from utils.aredn import AREDNClient

logger = logging.getLogger(__name__)


class ARENDataCollectorMixin:
    """Mixin providing AREDN sysinfo + worldmap collection.

    AREDN nodes report location via the sysinfo HTTP API. Local-network
    discovery (`_collect_aredn`) walks links from a reachable AREDN node;
    `_collect_aredn_worldmap` is geographic context — where AREDN nodes
    exist globally — and runs independent of the public_fallback threshold.
    """

    def _collect_aredn(self) -> List[Dict]:
        """Collect nodes from local AREDN mesh network."""
        features: List[Dict] = []
        configured_ips = []
        if self._settings:
            raw = self._settings.get("aredn_node_ips", [])
            configured_ips = [raw] if isinstance(raw, str) else list(raw or [])

        local_node_ip = self._get_aredn_node_ip()
        if not local_node_ip:
            if configured_ips:
                reason = "unreachable"
                msg = (
                    f"AREDN: none of configured IPs {configured_ips} reachable; "
                    "check aredn_node_ips in ~/.config/meshforge/map_settings.json"
                )
            else:
                reason = "not_configured"
                msg = (
                    "AREDN: integration disabled (no aredn_node_ips configured). "
                    "Set aredn_node_ips=[\"10.x.y.z\", ...] in "
                    "~/.config/meshforge/map_settings.json to enable."
                )
            self._info_log_rate_limited("aredn", msg)
            self._record_diagnostic(
                "aredn",
                attempted=len(configured_ips),
                yielded=0,
                reason_if_zero=reason,
                notes=(f"configured: {len(configured_ips)}" if configured_ips else "no IPs configured"),
            )
            return []

        neighbors_tried = 0
        try:
            client = AREDNClient(local_node_ip, timeout=5)
            local_node = client.get_node_info()

            if local_node:
                feature = self._aredn_node_to_feature(local_node)
                if feature:
                    features.append(feature)

                # Walk neighbor links
                for link in local_node.links:
                    if link.ip:
                        neighbors_tried += 1
                        try:
                            neighbor_client = AREDNClient(link.ip, timeout=3)
                            neighbor_node = neighbor_client.get_node_info()
                            if neighbor_node:
                                neighbor_feature = self._aredn_node_to_feature(neighbor_node)
                                if neighbor_feature:
                                    neighbor_feature["properties"]["link_type"] = link.link_type.value
                                    neighbor_feature["properties"]["link_quality"] = link.link_quality
                                    neighbor_feature["properties"]["snr"] = link.snr if link.snr else None
                                    features.append(neighbor_feature)
                        except Exception as e:
                            logger.debug(f"Error fetching AREDN neighbor {link.ip}: {e}")

            if features:
                logger.debug(f"AREDN: {len(features)} nodes with position")

        except Exception as e:
            logger.debug(f"AREDN collection error: {e}")
            self._record_diagnostic(
                "aredn",
                attempted=1 + neighbors_tried,
                yielded=len(features),
                reason_if_zero="unreachable" if not features else None,
                notes=f"{local_node_ip}: {str(e)[:120]}",
            )
            return features

        # Success path — `no_positions` means we reached AREDN but nothing had GPS set.
        reason = None
        if not features:
            reason = "no_positions"
        self._record_diagnostic(
            "aredn",
            attempted=1 + neighbors_tried,
            yielded=len(features),
            reason_if_zero=reason,
            notes=f"local node {local_node_ip}",
        )
        return features

    def _collect_aredn_worldmap(self) -> List[Dict]:
        """Fetch AREDN worldmap nodes (geographic context).

        Wraps `_fetch_aredn_worldmap_nodes` (PublicDataFallbackMixin) so it
        runs independent of the public_fallback threshold. Disabled via
        `enable_aredn_worldmap_fallback=False` in map_settings.json.
        """
        if not self._settings or not self._settings.get("enable_aredn_worldmap_fallback", True):
            self._record_diagnostic(
                "aredn_worldmap",
                attempted=0, yielded=0,
                reason_if_zero="source_disabled",
                notes="set enable_aredn_worldmap_fallback=true",
            )
            return []
        try:
            features = self._fetch_aredn_worldmap_nodes()
        except Exception as e:
            self._record_diagnostic(
                "aredn_worldmap", attempted=0, yielded=0,
                reason_if_zero="unreachable", notes=str(e)[:120],
            )
            return []
        self._record_diagnostic(
            "aredn_worldmap",
            attempted=len(features),
            yielded=len(features),
            reason_if_zero=None if features else "unreachable",
            notes="worldmap.arednmesh.org",
        )
        return features

    def _get_aredn_node_ip(self) -> Optional[str]:
        """Find AREDN node on configured IPs.

        Only probes when `aredn_node_ips` is configured. On a non-AREDN
        box (the 95% case) the previous default-host walk
        (`localnode.local.mesh`, `10.0.0.1`, `10.1.0.1`, `localnode`)
        burned 4-5 s per cache-miss collect for zero yield — opt-in
        is the right default for the NOC view. Operators on AREDN-
        equipped LANs set `aredn_node_ips=["10.x.y.z", ...]` to enable.

        Validates with HTTP API response (not just socket test) to confirm
        the host is actually an AREDN node, not some other service on 8080.
        """
        custom_ips = []
        if self._settings:
            custom_ips = self._settings.get("aredn_node_ips", [])
            if isinstance(custom_ips, str):
                custom_ips = [custom_ips]

        if not custom_ips:
            return None

        for host in custom_ips:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    result = sock.connect_ex((host, 8080))
                    if result != 0:
                        continue
                finally:
                    sock.close()

                url = f"http://{host}:8080/a/sysinfo"
                req = urllib.request.Request(url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=3) as response:
                    data = response.read().decode('utf-8')
                    import json as _json
                    info = _json.loads(data)
                    if isinstance(info, dict) and ('node' in info or 'sysinfo' in info
                                                    or 'meshrf' in info):
                        logger.debug(f"AREDN node confirmed at {host}")
                        return host
                    else:
                        logger.debug(f"Host {host}:8080 responds but not AREDN format")
            except Exception:
                continue
        return None

    def _aredn_node_to_feature(self, node) -> Optional[Dict]:
        """Convert AREDNNode to GeoJSON feature."""
        if not node.has_location():
            return None

        # AREDN uses longer threshold; if we just scanned successfully, treat as fresh.
        aredn_last_heard = time.time()
        is_online = self._is_node_online(aredn_last_heard, source="aredn")

        try:
            is_gateway = int(node.tunnel_count) > 0
        except (TypeError, ValueError):
            is_gateway = False

        feature = self._make_feature(
            node_id=f"aredn_{node.hostname}",
            name=node.hostname,
            lat=node.latitude,
            lon=node.longitude,
            network="aredn",
            is_online=is_online,
            is_gateway=is_gateway,
            hardware=node.model,
            last_heard=aredn_last_heard,
            role=node.mesh_status or "AREDN",
            last_seen="online",
        )
        # Tag provenance — sibling worldmap path sets "aredn_worldmap";
        # this is the local sysinfo path. Frontend filters on `network`
        # today, but a future Data-Sources filter needs `source` set.
        feature["properties"]["source"] = "aredn"
        return feature

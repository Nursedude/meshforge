"""AREDN and RNS data source collectors for MapDataCollector.

Extracted from map_data_collector.py for file size compliance (CLAUDE.md #6).

Provides _MapSourcesMixin with methods:
  _collect_aredn()           — Scan AREDN mesh for nodes with GPS
  _get_aredn_node_ip()       — Find AREDN node on local network
  _aredn_node_to_feature()   — Convert AREDNNode to GeoJSON
  _collect_rns_direct()      — Query rnsd for known destinations
  _load_rns_position_cache() — Load RNS node coordinate cache
  _load_nomadnet_peers()     — Load NomadNet peer cache
  _rns_peer_to_feature()     — Convert NomadNet peer to GeoJSON
  _node_cache_to_feature()   — Convert node cache entry to GeoJSON
  _rns_cache_to_feature()    — Convert RNS cache entry to GeoJSON
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from utils.safe_import import safe_import
from utils.paths import get_real_user_home
from utils.aredn import AREDNClient

# External/optional dependencies
_RNS, _HAS_RNS = safe_import('RNS')
_msgpack, _HAS_MSGPACK = safe_import('msgpack')

logger = logging.getLogger(__name__)


class _MapSourcesMixin:
    """AREDN and RNS data source methods for MapDataCollector.

    Mixed into MapDataCollector to keep file sizes under the 1,500-line limit.
    Methods use self._settings, self._is_node_online(), self._make_feature(),
    and self._is_valid_coordinate() from the main class.
    """

    def _collect_aredn(self) -> List[Dict]:
        """Collect nodes from AREDN mesh network.

        Scans the local AREDN network for nodes with GPS coordinates.
        AREDN nodes may have location data configured by the operator.
        """
        features = []

        # First try to connect to the local AREDN node
        local_node_ip = self._get_aredn_node_ip()
        if not local_node_ip:
            logger.debug("No AREDN node found on local network")
            return []

        try:
            # Get the local node info (may have location)
            client = AREDNClient(local_node_ip, timeout=5)
            local_node = client.get_node_info()

            if local_node:
                feature = self._aredn_node_to_feature(local_node)
                if feature:
                    features.append(feature)

                # Get neighbor nodes through links
                for link in local_node.links:
                    if link.ip:
                        try:
                            neighbor_client = AREDNClient(link.ip, timeout=3)
                            neighbor_node = neighbor_client.get_node_info()
                            if neighbor_node:
                                neighbor_feature = self._aredn_node_to_feature(neighbor_node)
                                if neighbor_feature:
                                    # Add link quality info
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

        return features

    def _get_aredn_node_ip(self) -> Optional[str]:
        """Find AREDN node on local network.

        Checks user-configured IPs first, then common AREDN defaults.
        Configure via map_settings.json: "aredn_node_ips": ["10.54.25.1"]

        Validates with HTTP API response (not just socket test) to confirm
        the host is actually an AREDN node, not some other service on 8080.
        """
        import socket
        import urllib.request

        # User-configured AREDN node IPs (checked first)
        custom_ips = []
        if self._settings:
            custom_ips = self._settings.get("aredn_node_ips", [])
            if isinstance(custom_ips, str):
                custom_ips = [custom_ips]

        # Common AREDN addresses as fallback
        default_hosts = ['localnode.local.mesh', '10.0.0.1', '10.1.0.1', 'localnode']

        for host in custom_ips + default_hosts:
            try:
                # Quick socket pre-check (2s timeout) to avoid slow HTTP timeouts
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    result = sock.connect_ex((host, 8080))
                    if result != 0:
                        continue
                finally:
                    sock.close()

                # Validate with actual HTTP API response
                url = f"http://{host}:8080/a/sysinfo"
                req = urllib.request.Request(url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=3) as response:
                    data = response.read().decode('utf-8')
                    import json as _json
                    info = _json.loads(data)
                    # Verify it looks like an AREDN response
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
        """Convert AREDNNode to GeoJSON feature.

        Args:
            node: AREDNNode object from utils.aredn

        Returns:
            GeoJSON Feature dict or None if no valid location
        """
        # Check for valid location
        if not node.has_location():
            return None

        # Determine online status from scan time — AREDN uses longer threshold
        # If we just scanned it successfully, use current time as last_heard
        aredn_last_heard = time.time()
        is_online = self._is_node_online(aredn_last_heard, source="aredn")

        # Determine if this is a "gateway" type node
        # AREDN nodes with tunnels act as gateways
        try:
            is_gateway = int(node.tunnel_count) > 0
        except (TypeError, ValueError):
            is_gateway = False

        return self._make_feature(
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

    def _collect_rns_direct(self) -> List[Dict]:
        """Collect RNS nodes directly from rnsd shared instance.

        Queries the RNS path table for known destinations when rnsd is running.
        This supplements the temp cache file with live data from rnsd.

        Returns:
            List of GeoJSON features for RNS destinations with stored positions.
        """
        features = []

        # Quick check if rnsd shared instance is available
        try:
            from utils.service_check import check_rns_shared_instance
            if not check_rns_shared_instance():
                logger.debug("rnsd shared instance not available")
                return []
        except ImportError:
            pass  # Proceed without pre-check

        if not _HAS_RNS:
            logger.debug("RNS module not available for direct query")
            return []

        # Load RNS position cache for coordinate lookup
        rns_positions = self._load_rns_position_cache()

        try:
            # Connect as a client to the running rnsd shared instance.
            # Use a temp client-only config to avoid:
            # 1. Creating a default config at /root/.reticulum/ (Path.home() bug MF001)
            # 2. Initializing interfaces that conflict with rnsd's bindings
            import tempfile
            client_config_dir = Path(tempfile.gettempdir()) / "meshforge_rns_client"
            client_config_dir.mkdir(exist_ok=True)
            client_config_file = client_config_dir / "config"
            client_config_file.write_text(
                "[reticulum]\n"
                "  share_instance = Yes\n"
                "  shared_instance_port = 37428\n"
                "  instance_control_port = 37429\n"
            )
            reticulum = _RNS.Reticulum(configdir=str(client_config_dir))

            # Check for known destinations in path table
            if hasattr(_RNS.Transport, 'path_table') and _RNS.Transport.path_table:
                for dest_hash, path_data in _RNS.Transport.path_table.items():
                    try:
                        if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                            hash_hex = dest_hash.hex()
                            node_id = f"rns_{hash_hex[:16]}"

                            # Extract hop count from path tuple if available
                            hops = 0
                            if isinstance(path_data, tuple) and len(path_data) > 1:
                                hops = path_data[1]

                            # Look up position from cache
                            pos = rns_positions.get(hash_hex[:16])
                            lat = pos.get("lat") if pos else None
                            lon = pos.get("lon") if pos else None
                            name = (pos.get("name") if pos else None) or f"RNS:{hash_hex[:8]}"

                            if lat and lon:
                                rns_last_heard = pos.get("last_heard", 0) if pos else 0
                                feature = self._make_feature(
                                    node_id=node_id,
                                    name=name,
                                    lat=lat, lon=lon,
                                    network="rns",
                                    is_online=self._is_node_online(rns_last_heard, source="rns"),
                                    last_heard=rns_last_heard,
                                )
                                features.append(feature)

                    except Exception as e:
                        logger.debug(f"Error processing RNS destination: {e}")

            # Also check NomadNet peer cache if available
            nomadnet_peers = self._load_nomadnet_peers()
            for peer in nomadnet_peers:
                feature = self._rns_peer_to_feature(peer)
                if feature:
                    features.append(feature)

            if features:
                logger.debug(f"RNS direct: {len(features)} nodes with position")
            else:
                # Log how many RNS destinations we found (even without position)
                path_count = len(_RNS.Transport.path_table) if hasattr(_RNS.Transport, 'path_table') and _RNS.Transport.path_table else 0
                if path_count:
                    logger.debug(
                        f"RNS: {path_count} destinations in path table, "
                        f"{len(rns_positions)} have cached positions"
                    )

        except Exception as e:
            logger.debug(f"RNS direct query error: {e}")

        return features

    def _load_rns_position_cache(self) -> Dict[str, Dict]:
        """Load RNS node position cache for coordinate lookup.

        Reads from /tmp/meshforge_rns_nodes.json and node_cache.json
        to build a hash -> {lat, lon, name} mapping.
        """
        positions: Dict[str, Dict] = {}

        # Source 1: RNS temp cache
        rns_cache = Path("/tmp/meshforge_rns_nodes.json")
        if rns_cache.exists():
            try:
                with open(rns_cache) as f:
                    data = json.load(f)
                nodes_list = data if isinstance(data, list) else data.get("nodes", [])
                for node in nodes_list:
                    rns_hash = node.get("id", node.get("rns_hash", ""))
                    if isinstance(rns_hash, str):
                        rns_hash = rns_hash.replace("rns_", "")[:16]
                    lat = node.get("latitude") or node.get("lat")
                    lon = node.get("longitude") or node.get("lon")
                    if lat and lon and rns_hash:
                        positions[rns_hash] = {
                            "lat": lat, "lon": lon,
                            "name": node.get("name", node.get("display_name", "")),
                        }
            except Exception as e:
                logger.debug(f"RNS position cache load error: {e}")

        # Source 2: Node tracker cache (RNS entries)
        cache_path = get_real_user_home() / ".config" / "meshforge" / "node_cache.json"

        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    data = json.load(f)
                nodes_list = data if isinstance(data, list) else data.get("nodes", [])
                for node in nodes_list:
                    if node.get("network") == "rns":
                        rns_hash = node.get("id", node.get("rns_hash", ""))
                        if isinstance(rns_hash, str):
                            rns_hash = rns_hash.replace("rns_", "")[:16]
                        lat = node.get("latitude") or node.get("lat")
                        lon = node.get("longitude") or node.get("lon")
                        if lat and lon and rns_hash:
                            positions[rns_hash] = {
                                "lat": lat, "lon": lon,
                                "name": node.get("name", ""),
                            }
            except Exception:
                pass

        return positions

    def _load_nomadnet_peers(self) -> List[Dict]:
        """Load known peers from NomadNet cache if available."""
        peers = []
        if not _HAS_MSGPACK:
            logger.debug("msgpack not available for NomadNet peer reading")
            return peers
        try:
            nomadnet_dir = get_real_user_home() / '.nomadnetwork'
            peer_file = nomadnet_dir / 'storage' / 'peers'
            if peer_file.exists():
                with open(peer_file, 'rb') as f:
                    data = _msgpack.unpack(f, raw=False)
                    if isinstance(data, dict):
                        for peer_hash, peer_data in data.items():
                            if isinstance(peer_data, dict):
                                peers.append({
                                    'hash': peer_hash.hex() if isinstance(peer_hash, bytes) else peer_hash,
                                    'name': peer_data.get('display_name', ''),
                                    'lat': peer_data.get('latitude'),
                                    'lon': peer_data.get('longitude'),
                                })
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"NomadNet peer loading error: {e}")
        return peers

    def _rns_peer_to_feature(self, peer: Dict) -> Optional[Dict]:
        """Convert NomadNet peer entry to GeoJSON feature."""
        lat = peer.get('lat')
        lon = peer.get('lon')

        if not lat or not lon:
            return None

        peer_hash = peer.get('hash', 'unknown')
        return self._make_feature(
            node_id=f"rns_{peer_hash[:16]}",
            name=peer.get('name', f"RNS:{peer_hash[:8]}"),
            lat=lat, lon=lon,
            network="rns",
            is_online=True,
        )

    def _node_cache_to_feature(self, node: Dict) -> Optional[Dict]:
        """Convert a node cache entry to a GeoJSON feature."""
        lat = node.get("latitude") or node.get("lat")
        lon = node.get("longitude") or node.get("lon")

        if not lat or not lon:
            pos = node.get("position", {})
            if pos:
                lat = pos.get("latitude") or (pos.get("latitudeI", 0) / 1e7)
                lon = pos.get("longitude") or (pos.get("longitudeI", 0) / 1e7)

        if not self._is_valid_coordinate(lat, lon):
            return None

        return self._make_feature(
            node_id=node.get("id", node.get("node_id", "unknown")),
            name=node.get("name", node.get("long_name", "")),
            lat=lat, lon=lon,
            network=node.get("network", "meshtastic"),
            is_online=node.get("is_online", False),
            snr=node.get("snr"),
            battery=node.get("battery", node.get("battery_level")),
            hardware=node.get("hardware", node.get("hardware_model", "")),
            role=node.get("role", ""),
            is_gateway=node.get("is_gateway", False),
            via_mqtt=node.get("via_mqtt", False),
            last_seen=node.get("last_seen", ""),
        )

    def _rns_cache_to_feature(self, node: Dict) -> Optional[Dict]:
        """Convert an RNS node cache entry to a GeoJSON feature."""
        lat = node.get("latitude") or node.get("lat")
        lon = node.get("longitude") or node.get("lon")

        if not lat or not lon:
            pos = node.get("position", {})
            if pos:
                lat = pos.get("latitude", 0)
                lon = pos.get("longitude", 0)

        if not self._is_valid_coordinate(lat, lon):
            return None

        return self._make_feature(
            node_id=node.get("id", node.get("rns_hash", "unknown")),
            name=node.get("name", node.get("display_name", "")),
            lat=lat, lon=lon,
            network="rns",
            is_online=node.get("is_online", False),
            snr=node.get("snr"),
            battery=node.get("battery"),
            hardware=node.get("hardware_model", ""),
            role=node.get("role", ""),
            is_gateway=node.get("is_gateway", False),
            last_seen=node.get("last_seen", ""),
        )

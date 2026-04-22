"""RNS/NomadNet node collection for coverage maps.

Extracted from map_data_collector.py for file size compliance (CLAUDE.md #6).

Expects the following on the host class:
- self._is_valid_coordinate(lat, lon): coordinate validator
- self._make_feature(...): GeoJSON feature builder
- self._is_node_online(last_heard, source): online status check
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

_RNS, _HAS_RNS = safe_import('RNS')
_msgpack, _HAS_MSGPACK = safe_import('msgpack')

logger = logging.getLogger(__name__)


class RNSDataCollectorMixin:
    """Mixin providing RNS data collection methods for MapDataCollector."""

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
                self._record_diagnostic(
                    "rns_direct", attempted=0, yielded=0,
                    reason_if_zero="not_configured",
                    notes="rnsd shared instance unavailable — start rnsd.service",
                )
                return []
        except ImportError:
            pass  # Proceed without pre-check

        if not _HAS_RNS:
            logger.debug("RNS module not available for direct query")
            self._record_diagnostic(
                "rns_direct", attempted=0, yielded=0,
                reason_if_zero="source_disabled",
                notes="RNS python module not installed",
            )
            return []

        # Load RNS position cache for coordinate lookup
        rns_positions = self._load_rns_position_cache()

        try:
            # Connect as a client to the running rnsd shared instance.
            # Use a temp client-only config to avoid:
            # 1. Creating a default config at /root/.reticulum/ (Path.home() bug MF001)
            # 2. Initializing interfaces that conflict with rnsd's bindings
            import tempfile
            from utils.paths import ReticulumPaths
            client_config_dir = Path(tempfile.gettempdir()) / "meshforge_rns_client"
            client_config_dir.mkdir(exist_ok=True)
            client_config_file = client_config_dir / "config"
            lines = [
                "[reticulum]",
                "  share_instance = Yes",
                "  shared_instance_port = 37428",
                "  instance_control_port = 37429",
            ]
            rpc_key = ReticulumPaths.get_shared_rpc_key()
            if rpc_key:
                lines.append(f"  rpc_key = {rpc_key}")
            client_config_file.write_text("\n".join(lines) + "\n")
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

            # Compute path_table size for diagnostic notes (visible in /api/status).
            path_count = 0
            if hasattr(_RNS.Transport, 'path_table') and _RNS.Transport.path_table:
                path_count = len(_RNS.Transport.path_table)

            if features:
                logger.debug(f"RNS direct: {len(features)} nodes with position")
                self._record_diagnostic(
                    "rns_direct",
                    attempted=path_count,
                    yielded=len(features),
                    notes=f"{path_count} path_table entries, {len(rns_positions)} cached positions",
                )
            else:
                # Differentiate: zero destinations (RNS isolated) vs. destinations
                # exist but none have cached GPS.
                if path_count == 0:
                    reason = "unreachable"
                    notes = "rnsd path_table empty — no RNS peers announced yet"
                else:
                    reason = "no_positions"
                    notes = f"{path_count} destinations in path_table but 0 have cached GPS"
                logger.debug(f"RNS direct: {notes}")
                self._record_diagnostic(
                    "rns_direct", attempted=path_count, yielded=0,
                    reason_if_zero=reason, notes=notes,
                )

        except Exception as e:
            logger.debug(f"RNS direct query error: {e}")
            self._record_diagnostic(
                "rns_direct", attempted=0, yielded=len(features),
                reason_if_zero="unreachable" if not features else None,
                notes=str(e)[:160],
            )

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

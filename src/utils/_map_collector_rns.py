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


def _rns_is_initialized() -> bool:
    """Return True if an ``RNS.Reticulum`` instance has already been
    constructed in this process.

    RNS uses a process-wide singleton — ``RNS.Reticulum()`` raises
    ``OSError("Attempt to reinitialise Reticulum, when it was already
    running")`` on the second construction (see
    ``RNS/Reticulum.py:225``). The collector runs every 60s; we must
    init exactly once per process and read ``Transport.path_table``
    directly on subsequent cycles.

    Uses the public ``RNS.Reticulum.get_instance()`` classmethod rather
    than peeking at the name-mangled ``_Reticulum__instance`` attr
    (that was fragile against rns minor-version changes).
    """
    if not _HAS_RNS:
        return False
    try:
        return _RNS.Reticulum.get_instance() is not None
    except Exception:
        return False


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

        # Quick check if rnsd shared instance is available.
        # Must use rnsd's configured instance_name — a box with e.g.
        # "instance_name = volcano ai rns" registers its socket as
        # @rns/volcano ai rns, not @rns/default, and the hardcoded-default
        # precheck would falsely report unavailable.
        from utils.paths import ReticulumPaths
        instance_name = ReticulumPaths.get_configured_instance_name()
        try:
            from utils.service_check import check_rns_shared_instance
            if not check_rns_shared_instance(instance_name=instance_name):
                logger.debug("rnsd shared instance not available (instance_name=%s)", instance_name)
                self._record_diagnostic(
                    "rns_direct", attempted=0, yielded=0,
                    reason_if_zero="not_configured",
                    notes=f"rnsd shared instance unavailable (@rns/{instance_name}) — start rnsd.service",
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
            # Initialize the Reticulum client ONCE per process. Subsequent
            # collect cycles read Transport.path_table directly — it's a
            # class-level singleton and stays live. Calling Reticulum(...)
            # a second time in the same process raises OSError (see the
            # _rns_is_initialized() helper above for the mechanism).
            if not _rns_is_initialized():
                import tempfile
                client_config_dir = Path(tempfile.gettempdir()) / "meshforge_rns_client"
                client_config_dir.mkdir(exist_ok=True)
                client_config_file = client_config_dir / "config"
                lines = [
                    "[reticulum]",
                    "  share_instance = Yes",
                    "  shared_instance_port = 37428",
                    "  instance_control_port = 37429",
                    f"  instance_name = {instance_name}",
                ]
                rpc_key = ReticulumPaths.get_shared_rpc_key()
                if rpc_key:
                    lines.append(f"  rpc_key = {rpc_key}")
                client_config_file.write_text("\n".join(lines) + "\n")
                try:
                    _RNS.Reticulum(configdir=str(client_config_dir))
                except (OSError, ValueError) as e:
                    # Two known cases where init fails but Transport is still
                    # usable and we should not record 'unreachable':
                    #   1. "Attempt to reinitialise Reticulum" — another
                    #      component beat us to init (gateway path).
                    #   2. "signal only works in main thread of the main
                    #      interpreter" — init ran in a ThreadingHTTPServer
                    #      worker thread and failed at signal.signal()
                    #      registration (RNS/Reticulum.py:349). At that point
                    #      Reticulum.__instance is ALREADY set (line 226,
                    #      before the signal call), so get_instance() returns
                    #      a partially-initialized object and Transport is
                    #      running. Ideally pre-warm from the main thread
                    #      prevents this (see MapServer._prewarm_collector),
                    #      but catch it here as a second line of defense.
                    msg = str(e).lower()
                    if "reinitialise" in msg or "main thread" in msg:
                        logger.debug("RNS already partially-initialized (%s) — reusing", e)
                    else:
                        raise

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
                # Differentiate three distinct zero-yield states:
                #   - path_table empty but rnsd reachable -> no_data (healthy,
                #     just nothing announced yet; NOT a fault — the sidebar
                #     badge logic treats 'unreachable' as a warn state and
                #     this situation doesn't warrant that)
                #   - destinations in path_table but none have GPS -> no_positions
                #   - exception path (see except below) -> unreachable
                if path_count == 0:
                    reason = "no_data"
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

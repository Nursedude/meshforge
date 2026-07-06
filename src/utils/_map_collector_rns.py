"""RNS/NomadNet node collection for coverage maps.

Extracted from map_data_collector.py for file size compliance (CLAUDE.md #6).

Expects the following on the host class:
- self._is_valid_coordinate(lat, lon): coordinate validator
- self._make_feature(...): GeoJSON feature builder
- self._is_node_online(last_heard, source): online status check
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

_RNS, _HAS_RNS = safe_import('RNS')
_msgpack, _HAS_MSGPACK = safe_import('msgpack')

logger = logging.getLogger(__name__)


# RNS Transport.path_table entry indices — must match upstream RNS/Transport.py
# IDX_PT_* constants (path_data is a 7-element list/tuple).
_PT_TIMESTAMP = 0
_PT_NEXT_HOP = 1
_PT_HOPS = 2
_PT_RVCD_IF = 5

# Cached snapshot of RNS Transport.path_table for the read-only HTTP
# endpoint (/api/network/rns/paths). Refreshed every time the collector
# walks path_table (currently once per 60s collect cycle in _collect_rns_direct).
# Process-global on purpose — RNS itself is a process singleton, so there's
# exactly one path_table to mirror.
_LAST_PATH_TABLE_SNAPSHOT: Dict[str, Any] = {
    "ts": 0.0,
    "paths": [],
    "available": False,
    "reason": "never_collected",
}
_PATH_TABLE_SNAPSHOT_LOCK = threading.Lock()


# Companion cache for RNS.Transport.interfaces — surfaces per-interface
# RX/TX bytes + bitrate + online state so the operator can answer
# "which interface saw this message" alongside the path-table view.
# Refreshed in the same _collect_rns_direct cycle.
_LAST_INTERFACE_SNAPSHOT: Dict[str, Any] = {
    "ts": 0.0,
    "interfaces": [],
    "available": False,
    "reason": "never_collected",
}
_INTERFACE_SNAPSHOT_LOCK = threading.Lock()


def _interface_name(iface: Any) -> Optional[str]:
    """Best-effort name for an RNS Interface object.

    RNS Interface has a `name` attribute (e.g. "RNodeInterface[/dev/ttyUSB0]"),
    but path_data[IDX_PT_RVCD_IF] may legitimately be None for not-yet-
    reached destinations. Falls back to str() so an unknown shape doesn't
    blow up the snapshot.
    """
    if iface is None:
        return None
    name = getattr(iface, "name", None)
    if isinstance(name, str) and name:
        return name
    try:
        return str(iface)
    except Exception:
        return None


def _snapshot_path_table_inplace() -> None:
    """Re-read RNS Transport.path_table and cache a JSON-serializable view.

    Called from _collect_rns_direct after the existing path_table walk
    (no extra path_table iterations — same hot path). Defensive about
    RNS variant shapes; never raises (snapshot becomes empty + reason
    on failure).

    See `get_cached_path_table_snapshot()` for the consumer.
    """
    global _LAST_PATH_TABLE_SNAPSHOT
    if not _HAS_RNS:
        snap = {
            "ts": time.time(), "paths": [],
            "available": False, "reason": "rns_module_unavailable",
        }
    elif not _rns_is_initialized():
        snap = {
            "ts": time.time(), "paths": [],
            "available": False, "reason": "rns_not_initialized",
        }
    else:
        paths: List[Dict[str, Any]] = []
        try:
            pt = getattr(_RNS.Transport, "path_table", None)
            if pt:
                for dest_hash, path_data in pt.items():
                    try:
                        if not (isinstance(dest_hash, bytes)
                                and len(dest_hash) == 16):
                            continue
                        if not (isinstance(path_data, (list, tuple))
                                and len(path_data) > _PT_RVCD_IF):
                            continue
                        ts_val = path_data[_PT_TIMESTAMP]
                        next_hop = path_data[_PT_NEXT_HOP]
                        hops_val = path_data[_PT_HOPS]
                        iface = path_data[_PT_RVCD_IF]
                        paths.append({
                            "dest_hash": dest_hash.hex(),
                            "hops": int(hops_val) if isinstance(hops_val, int) else None,
                            "next_hop": next_hop.hex() if isinstance(next_hop, bytes) else None,
                            "via_interface": _interface_name(iface),
                            "last_heard": float(ts_val) if isinstance(ts_val, (int, float)) else None,
                        })
                    except Exception:
                        # One malformed entry shouldn't poison the whole snapshot.
                        continue
            snap = {
                "ts": time.time(), "paths": paths,
                "available": True, "reason": None,
            }
        except Exception as e:
            snap = {
                "ts": time.time(), "paths": [],
                "available": False, "reason": f"path_table_read_error: {e!r}",
            }
    with _PATH_TABLE_SNAPSHOT_LOCK:
        _LAST_PATH_TABLE_SNAPSHOT = snap


def get_cached_path_table_snapshot() -> Dict[str, Any]:
    """Return the most recent path_table snapshot (a shallow copy).

    Read-only consumer for HTTP handlers. Returns the same `available:
    False` shape on cold-start (before _collect_rns_direct has run) so
    the endpoint always has a valid JSON contract.
    """
    with _PATH_TABLE_SNAPSHOT_LOCK:
        # Shallow copy is fine — entries are dicts of immutables.
        return dict(_LAST_PATH_TABLE_SNAPSHOT)


def _snapshot_interfaces_inplace() -> None:
    """Capture per-interface RX/TX bytes + state from RNS.Transport.interfaces.

    Each RNS Interface carries:
      * rxb / txb     — RX / TX byte counters (monotonic)
      * online        — bool, whether the interface is currently up
      * bitrate       — bits/sec for the underlying transport
      * HW_MTU        — hardware MTU, if known
      * created       — unix ts when the interface was instantiated
      * __str__       — operator-readable name (e.g. "TCPInterface[hub:4242]",
                        "RNodeInterface[/dev/ttyUSB0]", "AutoInterface[default]")

    The snapshot is cheap to compute (one pass over Transport.interfaces,
    typically ≤10 entries) and answers the operator's "which interface
    is this message flowing through" question without touching the
    bridge (which runs in a separate process).

    Never raises. Empty list with `available: True` on healthy-but-no-
    interfaces (e.g. AutoInterface still discovering peers).
    """
    global _LAST_INTERFACE_SNAPSHOT
    if not _HAS_RNS:
        snap = {
            "ts": time.time(), "interfaces": [],
            "available": False, "reason": "rns_module_unavailable",
        }
    elif not _rns_is_initialized():
        snap = {
            "ts": time.time(), "interfaces": [],
            "available": False, "reason": "rns_not_initialized",
        }
    else:
        interfaces: List[Dict[str, Any]] = []
        try:
            ifaces = getattr(_RNS.Transport, "interfaces", None) or []
            now = time.time()
            for iface in ifaces:
                try:
                    name = _interface_name(iface)
                    kind = type(iface).__name__ if iface is not None else None
                    rxb = getattr(iface, "rxb", None)
                    txb = getattr(iface, "txb", None)
                    online = bool(getattr(iface, "online", False))
                    bitrate = getattr(iface, "bitrate", None)
                    hw_mtu = getattr(iface, "HW_MTU", None)
                    created = getattr(iface, "created", None)
                    age_s = (
                        float(now - created)
                        if isinstance(created, (int, float)) else None
                    )
                    interfaces.append({
                        "name": name,
                        "kind": kind,
                        "online": online,
                        "rxb": int(rxb) if isinstance(rxb, int) else rxb,
                        "txb": int(txb) if isinstance(txb, int) else txb,
                        "bitrate": int(bitrate) if isinstance(bitrate, int) else bitrate,
                        "hw_mtu": int(hw_mtu) if isinstance(hw_mtu, int) else hw_mtu,
                        "age_s": age_s,
                    })
                except Exception:
                    # One malformed interface doesn't poison the snapshot.
                    continue
            snap = {
                "ts": time.time(), "interfaces": interfaces,
                "available": True, "reason": None,
            }
        except Exception as e:
            snap = {
                "ts": time.time(), "interfaces": [],
                "available": False,
                "reason": f"interfaces_read_error: {e!r}",
            }
    with _INTERFACE_SNAPSHOT_LOCK:
        _LAST_INTERFACE_SNAPSHOT = snap


def get_cached_interface_snapshot() -> Dict[str, Any]:
    """Return the most recent RNS interfaces snapshot (shallow copy)."""
    with _INTERFACE_SNAPSHOT_LOCK:
        return dict(_LAST_INTERFACE_SNAPSHOT)


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


def init_rns_singleton() -> bool:
    """Initialize the process-wide RNS Reticulum singleton.

    Idempotent: returns True if already initialized. Returns True on
    successful initialization, False if RNS module isn't installed.
    Raises on unexpected errors (see two-known-OSError cases below).

    MUST be called from the main thread to install signal handlers
    cleanly (Issue #44). Used by both:
      1. Cold-start prewarm in `MapServer._init_rns_main_thread()`
         — runs BEFORE binding `:5000` so worker-thread requests can
         safely reuse the singleton.
      2. Lazy init in `_collect_rns_direct()` — second-line-of-defense
         when the prewarm didn't happen (e.g. legacy callers).

    The two known recoverable OSError cases:
      - "Attempt to reinitialise Reticulum" — another in-process
        component (gateway, NomadNet helper) beat us to init.
      - "signal only works in main thread" — caller violated the
        main-thread requirement; Reticulum.__instance is set anyway
        (line 226, before line 349 signal call) so Transport works.
    """
    if not _HAS_RNS:
        return False
    if _rns_is_initialized():
        return True
    from utils.paths import ReticulumPaths

    # THE canonical client configdir builder (one owner) — was a third
    # hand-rolled copy of this config here, in a DIFFERENT format, invisible
    # to the 8bfa4f3e determinism regression guard and lacking the rpc_key
    # 0600/symlink hardening. Share the helper so a future change (rpc_key
    # handling, a new option) can't drift the map away from the gateway.
    client_config_dir = ReticulumPaths.ensure_rns_client_configdir()

    # Route through the guarded RNS-init chokepoint. require_listener=True is
    # the belt-and-suspenders that keeps the map a pure RNS *consumer*: it
    # never constructs when the @rns shared instance is absent, so it can
    # never win the host role and strand fleet RNS routing (the 2026-05-28
    # ~21h outage; see project_rns_map_host_race). The chokepoint also adds
    # the #68 bounded connect probe (degrade instead of hang the main thread
    # on a wedged rnsd) and the #69 listener-owner preflight, and reuses the
    # singleton if one already exists.
    from utils.rns_init import open_reticulum
    try:
        return open_reticulum(
            str(client_config_dir), require_listener=True,
        ) is not None
    except (OSError, ValueError) as e:
        msg = str(e).lower()
        if "reinitialise" in msg or "main thread" in msg:
            logger.debug("RNS already partially-initialized (%s) — reusing", e)
            return True
        raise


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
            # Init the Reticulum client ONCE per process. Idempotent
            # via init_rns_singleton(); main-thread prewarm in
            # MapServer._init_rns_main_thread() is the preferred
            # callsite — this is the lazy fallback for legacy callers.
            init_rns_singleton()

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

                            if self._is_valid_coordinate(lat, lon):
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

            # Refresh the path_table snapshot cache for the read-only
            # /api/network/rns/paths endpoint. Same cadence as this collect
            # cycle (~60s); no extra path_table walk on HTTP requests.
            _snapshot_path_table_inplace()

            # Companion snapshot — per-interface RX/TX bytes for the
            # /api/network/interfaces endpoint.
            _snapshot_interfaces_inplace()

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

        Reads the RNS nodes cache (MeshForgePaths.rns_nodes_cache_path) and
        node_cache.json to build a hash -> {lat, lon, name} mapping.
        """
        positions: Dict[str, Dict] = {}

        # Source 1: RNS nodes cache (operator-owned; shared path via the writer)
        from utils.paths import MeshForgePaths
        rns_cache = MeshForgePaths.rns_nodes_cache_path()
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
                    if lat is not None and lon is not None and rns_hash:
                        positions[rns_hash] = {
                            "lat": lat, "lon": lon,
                            "name": node.get("name", node.get("display_name", "")),
                            # Stamp freshness so path-table nodes aren't rendered
                            # permanently offline: the cache never carried
                            # last_heard, so _is_node_online(0) was always False.
                            # Coerce to a numeric epoch — `last_seen` is an ISO
                            # string in this cache (MeshNode.to_dict), and an ISO
                            # string reaching `_is_node_online` used to TypeError
                            # → the node got DROPPED.
                            "last_heard": self._coerce_epoch(
                                node.get("last_heard") or node.get("last_seen")
                                or node.get("timestamp") or 0),
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
                        if lat is not None and lon is not None and rns_hash:
                            positions[rns_hash] = {
                                "lat": lat, "lon": lon,
                                "name": node.get("name", ""),
                                "last_heard": self._coerce_epoch(
                                    node.get("last_heard") or node.get("last_seen")
                                    or node.get("timestamp") or 0),
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

        if not self._is_valid_coordinate(lat, lon):
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

        if not self._is_valid_coordinate(lat, lon):
            pos = node.get("position", {})
            if pos:
                # Convert *I forms only when actually present — a missing axis
                # must stay None, not become 0.0 (which validate-accepts as a
                # legit one-axis-zero → a phantom node on the equator/meridian).
                lat_i = pos.get("latitudeI")
                lon_i = pos.get("longitudeI")
                lat = pos.get("latitude")
                if lat is None and lat_i is not None:
                    lat = lat_i / 1e7
                lon = pos.get("longitude")
                if lon is None and lon_i is not None:
                    lon = lon_i / 1e7

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

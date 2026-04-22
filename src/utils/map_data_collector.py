"""
Map Data Collector - Unified node GeoJSON from all available sources.

Collects node data from meshtasticd, MQTT, RNS node tracker, and AREDN,
merges into a single GeoJSON FeatureCollection.

This module provides the data collection logic. For the HTTP server,
see map_data_service.py.

Usage:
    from utils.map_data_collector import MapDataCollector
    collector = MapDataCollector()
    geojson = collector.collect()
"""

import json
import logging
import math
import os
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- Imports ---
from utils.safe_import import safe_import

from utils.paths import get_real_user_home
from utils.common import SettingsManager
from gateway.node_tracker import get_node_tracker
from utils.meshtastic_http import get_http_client
from utils.meshtastic_connection import (
    get_connection_manager, safe_close_interface,
    ConnectionMode, reset_connection_manager,
)
from monitoring.mqtt_subscriber import get_local_subscriber
from utils.aredn import AREDNScanner, AREDNClient

# External/optional dependencies
_RNS, _HAS_RNS = safe_import('RNS')
_msgpack, _HAS_MSGPACK = safe_import('msgpack')


from utils._map_collector_rns import RNSDataCollectorMixin
from utils._map_collector_public import PublicDataFallbackMixin


class MapDataCollector(RNSDataCollectorMixin, PublicDataFallbackMixin):
    """Collects node data from all available sources into unified GeoJSON.

    Sources (tried in order, all merged):
    1. meshtasticd TCP (localhost:4403) - local mesh nodes
    2. MQTT subscriber - global/regional nodes
    3. Node tracker cache - previously discovered RNS + Meshtastic nodes
    4. Last-known cache - persisted state from previous runs

    Settings (in ~/.config/meshforge/map_settings.json):
    - node_cache_max_age_hours: Max age for node_cache.json (default: 48)
    - rns_cache_max_age_hours: Max age for RNS temp cache (default: 1)
    - online_status_threshold_minutes: Minutes since lastHeard to consider online (default: 15)
    """

    # Default cache ages in hours
    DEFAULT_NODE_CACHE_MAX_AGE_HOURS = 48
    DEFAULT_RNS_CACHE_MAX_AGE_HOURS = 24  # Increased from 1 hour
    DEFAULT_ONLINE_THRESHOLD_MINUTES = 15
    # Per-source online thresholds (minutes) — configurable via map_settings.json
    DEFAULT_MESHTASTIC_THRESHOLD_MINUTES = 15
    DEFAULT_MQTT_THRESHOLD_MINUTES = 15
    DEFAULT_RNS_THRESHOLD_MINUTES = 30   # RNS announces less frequently
    DEFAULT_AREDN_THRESHOLD_MINUTES = 60  # AREDN scans are infrequent
    DEFAULT_PUBLIC_FALLBACK_THRESHOLD_MINUTES = 240  # meshmap.net reports less frequently
    # Meshtasticd connection defaults
    DEFAULT_MESHTASTICD_HOST = "localhost"
    DEFAULT_MESHTASTICD_PORT = 4403

    def __init__(self, cache_dir: Optional[Path] = None, enable_history: bool = True):
        if cache_dir:
            self._cache_dir = cache_dir
        else:
            self._cache_dir = get_real_user_home() / ".local" / "share" / "meshforge"

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self._cache_dir / "map_nodes.geojson"
        self._last_collect: Optional[float] = None
        self._cached_geojson: Optional[Dict] = None

        # Serialize concurrent collect() callers. The HTTP server is
        # ThreadingHTTPServer (one thread per request), so two parallel
        # /api/nodes/geojson requests during a cache miss would both race
        # through the source collection and both stomp on per-call state
        # (self._nodes_without_position reset at the top of collect()).
        # Lock here means the second caller waits briefly and then gets
        # the freshly-populated cache from the first caller's run.
        self._collect_lock = threading.Lock()

        # User-configurable cache age settings
        self._settings = SettingsManager(
            "map_settings",
            defaults={
                "node_cache_max_age_hours": self.DEFAULT_NODE_CACHE_MAX_AGE_HOURS,
                "rns_cache_max_age_hours": self.DEFAULT_RNS_CACHE_MAX_AGE_HOURS,
                "online_status_threshold_minutes": self.DEFAULT_ONLINE_THRESHOLD_MINUTES,
                "meshtasticd_host": self.DEFAULT_MESHTASTICD_HOST,
                "meshtasticd_port": self.DEFAULT_MESHTASTICD_PORT,
                "aredn_node_ips": [],  # e.g. ["10.54.25.1", "10.1.0.1"]
                # Operator-assigned positions for nodes that don't self-report GPS
                # (MeshCore advertisements carry no position by protocol design).
                # Shape: {"<node_id_or_pubkey_prefix>": {"lat": 19.4, "lon": -155.3, "note": "Hilo relay"}}
                # Match key is compared against UnifiedNode.id (e.g. "meshcore:abc123...")
                # OR a shorter prefix like "abc123" (first N chars of the pubkey after "meshcore:").
                "meshcore_positions": {},
                # Per-source online thresholds (minutes)
                "meshtastic_threshold_minutes": self.DEFAULT_MESHTASTIC_THRESHOLD_MINUTES,
                "mqtt_threshold_minutes": self.DEFAULT_MQTT_THRESHOLD_MINUTES,
                "rns_threshold_minutes": self.DEFAULT_RNS_THRESHOLD_MINUTES,
                "aredn_threshold_minutes": self.DEFAULT_AREDN_THRESHOLD_MINUTES,
                "public_fallback_threshold_minutes": self.DEFAULT_PUBLIC_FALLBACK_THRESHOLD_MINUTES,
                # Public data fallbacks (disabled by default — opt-in)
                "enable_meshmap_fallback": False,
                "enable_rmap_fallback": False,
                # AREDN worldmap defaults ON so AREDN appears without operator config —
                # matches meshforge-maps :8808 behavior. ~2500 global AREDN nodes.
                "enable_aredn_worldmap_fallback": True,
                "public_fallback_threshold": 3,
                # MeshCore public map (https://map.meshcore.dev) — first-class source,
                # not a fallback. ~30k global MeshCore nodes with GPS. Default ON
                # because local MeshCoreHandler yields no GPS and without this
                # MeshCore is invisible on the map.
                "enable_meshcore_public": True,
                "selected_region": None,
            }
        )

        # Track nodes without GPS for reporting
        self._nodes_without_position: List[Dict] = []
        self._total_nodes_seen: int = 0  # Total from meshtasticd (with + without GPS)

        # Per-source diagnostics — populated by each _collect_* method during collect().
        # Shape: {"source_name": {"attempted": int, "yielded": int, "reason_if_zero": str|None, "notes": str|None}}
        # Reason taxonomy: not_configured | unreachable | no_positions | source_disabled | ok
        self._source_diagnostics: Dict[str, Dict[str, Any]] = {}
        # Rate-limit for actionable INFO logs (source_name -> last-log-timestamp)
        self._last_info_log: Dict[str, float] = {}

        # Node history database for position/state tracking over time
        self._history = None
        if enable_history:
            try:
                from utils.node_history import NodeHistoryDB
                db_path = self._cache_dir / "node_history.db"
                self._history = NodeHistoryDB(db_path=db_path)
            except Exception as e:
                logger.debug(f"Node history disabled: {e}")

    @staticmethod
    def _is_valid_coordinate(lat, lon) -> bool:
        """Validate geographic coordinates.

        Rejects:
        - None values
        - NaN or Infinity
        - Out-of-range (lat must be -90..90, lon must be -180..180)
        - Default zero (both lat AND lon are exactly 0 — unset GPS)

        Accepts:
        - Nodes near the equator/prime meridian where only ONE coord is near zero
        - Any valid coordinate pair within range
        """
        if lat is None or lon is None:
            return False
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(lat) or not math.isfinite(lon):
            return False
        if lat < -90 or lat > 90 or lon < -180 or lon > 180:
            return False
        # Reject default-zero GPS (both exactly 0.0 = unset), but allow
        # nodes where only one axis is near zero (legitimate equator/meridian)
        if lat == 0.0 and lon == 0.0:
            return False
        return True

    def get_node_cache_max_age_seconds(self) -> int:
        """Get max age for node_cache.json in seconds."""
        if self._settings:
            hours = self._settings.get("node_cache_max_age_hours", self.DEFAULT_NODE_CACHE_MAX_AGE_HOURS)
        else:
            hours = self.DEFAULT_NODE_CACHE_MAX_AGE_HOURS
        return int(hours * 3600)

    def get_rns_cache_max_age_seconds(self) -> int:
        """Get max age for RNS temp cache in seconds."""
        if self._settings:
            hours = self._settings.get("rns_cache_max_age_hours", self.DEFAULT_RNS_CACHE_MAX_AGE_HOURS)
        else:
            hours = self.DEFAULT_RNS_CACHE_MAX_AGE_HOURS
        return int(hours * 3600)

    def set_node_cache_max_age_hours(self, hours: int) -> None:
        """Set max age for node_cache.json in hours."""
        if self._settings:
            self._settings.set("node_cache_max_age_hours", hours)
            self._settings.save()
            logger.info(f"Node cache max age set to {hours} hours")

    def set_rns_cache_max_age_hours(self, hours: int) -> None:
        """Set max age for RNS temp cache in hours."""
        if self._settings:
            self._settings.set("rns_cache_max_age_hours", hours)
            self._settings.save()
            logger.info(f"RNS cache max age set to {hours} hours")

    def get_online_threshold_seconds(self) -> int:
        """Get online status threshold in seconds.

        Nodes heard within this threshold are considered online.
        Default: 15 minutes (900 seconds).
        """
        if self._settings:
            minutes = self._settings.get("online_status_threshold_minutes", self.DEFAULT_ONLINE_THRESHOLD_MINUTES)
        else:
            minutes = self.DEFAULT_ONLINE_THRESHOLD_MINUTES
        return int(minutes * 60)

    def set_online_threshold_minutes(self, minutes: int) -> None:
        """Set online status threshold in minutes.

        Args:
            minutes: Consider nodes online if heard within this many minutes.
                    Use higher values for networks with longer update intervals.
        """
        if self._settings:
            self._settings.set("online_status_threshold_minutes", minutes)
            self._settings.save()
            logger.info(f"Online status threshold set to {minutes} minutes")

    def get_source_threshold_seconds(self, source: str) -> int:
        """Get online threshold for a specific network source.

        Per-source thresholds allow different timeout windows per network type:
        - meshtastic: 15 min (frequent heartbeats)
        - mqtt: 15 min (real-time broker)
        - rns: 30 min (announces less frequently)
        - aredn: 60 min (scans are infrequent)

        Falls back to the global online_status_threshold_minutes setting.

        Args:
            source: Network source type ("meshtastic", "mqtt", "rns", "aredn")

        Returns:
            Threshold in seconds
        """
        key = f"{source}_threshold_minutes"
        defaults = {
            "meshtastic": self.DEFAULT_MESHTASTIC_THRESHOLD_MINUTES,
            "mqtt": self.DEFAULT_MQTT_THRESHOLD_MINUTES,
            "rns": self.DEFAULT_RNS_THRESHOLD_MINUTES,
            "aredn": self.DEFAULT_AREDN_THRESHOLD_MINUTES,
            "public_fallback": self.DEFAULT_PUBLIC_FALLBACK_THRESHOLD_MINUTES,
        }
        default = defaults.get(source, self.DEFAULT_ONLINE_THRESHOLD_MINUTES)
        if self._settings:
            minutes = self._settings.get(key, default)
        else:
            minutes = default
        return int(minutes * 60)

    def _is_node_online(self, last_heard: float, source: str = "meshtastic") -> bool:
        """Determine if a node is online based on last_heard timestamp.

        Single source of truth for online status determination.
        Uses per-source thresholds for accurate status across network types.

        Args:
            last_heard: Unix timestamp of last communication (0 or None = unknown)
            source: Network source type for threshold lookup

        Returns:
            True if the node was heard within the source's threshold window
        """
        if not last_heard or last_heard <= 0:
            return False
        threshold = self.get_source_threshold_seconds(source)
        return (time.time() - last_heard) < threshold

    def get_meshtasticd_host(self) -> str:
        """Get meshtasticd host setting."""
        if self._settings:
            return self._settings.get("meshtasticd_host", self.DEFAULT_MESHTASTICD_HOST)
        return self.DEFAULT_MESHTASTICD_HOST

    def get_meshtasticd_port(self) -> int:
        """Get meshtasticd port setting."""
        if self._settings:
            return int(self._settings.get("meshtasticd_port", self.DEFAULT_MESHTASTICD_PORT))
        return self.DEFAULT_MESHTASTICD_PORT

    def set_meshtasticd_connection(self, host: str, port: int) -> None:
        """Set meshtasticd connection parameters.

        Args:
            host: Hostname or IP address of meshtasticd
            port: TCP port (default: 4403)
        """
        if self._settings:
            self._settings.set("meshtasticd_host", host)
            self._settings.set("meshtasticd_port", port)
            self._settings.save()
            logger.info(f"Meshtasticd connection set to {host}:{port}")

    def get_nodes_without_position(self) -> List[Dict]:
        """Get list of nodes that have no GPS position.

        Returns list of dicts with id, name, last_seen, network info.
        Updated after each collect() call.
        """
        return self._nodes_without_position

    def get_source_diagnostics(self) -> Dict[str, Dict[str, Any]]:
        """Per-source collection diagnostics from the most recent collect().

        Each entry: {attempted, yielded, reason_if_zero, notes}.
        Consumed by /api/status to let operators diagnose "why is source X empty"
        without reading source code.
        """
        return dict(self._source_diagnostics)

    def _record_diagnostic(
        self,
        source: str,
        attempted: int,
        yielded: int,
        reason_if_zero: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Record diagnostic for a single source during collect()."""
        if yielded > 0 and reason_if_zero is None:
            reason_if_zero = "ok"
        elif yielded == 0 and reason_if_zero is None:
            reason_if_zero = "no_positions" if attempted > 0 else "unreachable"
        self._source_diagnostics[source] = {
            "attempted": attempted,
            "yielded": yielded,
            "reason_if_zero": reason_if_zero,
            "notes": notes,
        }

    def _info_log_rate_limited(self, source: str, message: str, cooldown_s: float = 300.0) -> None:
        """Emit an INFO log for `source` at most once per cooldown window.

        Prevents one-liner operator guidance from drowning logs on every collect cycle.
        """
        now = time.time()
        last = self._last_info_log.get(source, 0.0)
        if now - last >= cooldown_s:
            logger.info(message)
            self._last_info_log[source] = now

    def collect(self, max_age_seconds: int = 30) -> Dict[str, Any]:
        """Collect nodes from all sources, merge, and return GeoJSON.

        Args:
            max_age_seconds: Use cached data if collected within this window.

        Returns:
            GeoJSON FeatureCollection with all known nodes.
        """
        # Fast path — cache hit, no lock needed. Class attrs are pointer
        # reassignments, atomic under the GIL. Worst case during a cache
        # write is a stale read of _cached_geojson, which is acceptable.
        if (self._cached_geojson and self._last_collect and
                time.time() - self._last_collect < max_age_seconds):
            return self._cached_geojson

        # Slow path — collection happens under the lock so concurrent
        # callers don't double-collect (wasteful) or race on per-call
        # state (_nodes_without_position reset below). The second caller
        # blocks briefly and then sees the first caller's fresh cache.
        with self._collect_lock:
            # Re-check cache inside the lock: while we waited, the holder
            # may have populated it and we can just return their result.
            if (self._cached_geojson and self._last_collect and
                    time.time() - self._last_collect < max_age_seconds):
                return self._cached_geojson

            return self._collect_locked()

    def _collect_locked(self) -> Dict[str, Any]:
        """Actual collection body. Caller MUST hold self._collect_lock."""
        # Reset per-call state so diagnostics/nodes_without_position reflect THIS run only.
        self._nodes_without_position = []
        self._source_diagnostics = {}

        features: Dict[str, Dict] = {}  # id -> feature (dedup by id)

        # Source 0: UnifiedNodeTracker (richest data — includes RNS + Meshtastic)
        # This is the same data source the topology view uses (378 nodes).
        # It includes nodes from RNS path table, meshtasticd, and gateway bridge.
        tracker_unified_features = self._collect_unified_tracker()
        for f in tracker_unified_features:
            fid = f["properties"].get("id", "")
            if fid:
                features[fid] = f

        # Source 1: meshtasticd TCP
        tcp_features = self._collect_meshtasticd()
        for f in tcp_features:
            fid = f["properties"].get("id", "")
            if fid:
                features[fid] = f

        # Source 1.5: Direct USB radio (when meshtasticd not running)
        # Only try this if TCP returned nothing (avoids double-connection)
        direct_radio_features = []
        if not tcp_features:
            direct_radio_features = self._collect_direct_radio()
            for f in direct_radio_features:
                fid = f["properties"].get("id", "")
                if fid:
                    features[fid] = f

        # Source 2: MQTT subscriber (if running)
        mqtt_features = self._collect_mqtt()
        for f in mqtt_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f
            elif fid and fid in features:
                # Merge: prefer newer data
                self._merge_feature(features[fid], f)

        # Source 3: Node tracker cache files
        tracker_features = self._collect_node_tracker()
        for f in tracker_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 4: AREDN mesh network (local scan via sysinfo API)
        aredn_features = self._collect_aredn()
        for f in aredn_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 4.5: AREDN worldmap (public CSV, always runs when enabled —
        # NOT threshold-gated. Provides geographic context alongside local
        # Meshtastic/RNS, not a fill-when-sparse fallback.)
        aredn_worldmap_features = self._collect_aredn_worldmap()
        for f in aredn_worldmap_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 5: RNS direct query (from rnsd path table)
        rns_direct_features = self._collect_rns_direct()
        for f in rns_direct_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 5.5: MeshCore public map (always runs when enabled — NOT gated by
        # feature count threshold, because this is the primary MeshCore visibility path).
        meshcore_public_features = self._collect_meshcore_public()
        for f in meshcore_public_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 6: Public data fallbacks (conditional — only when local data sparse)
        public_features = self._collect_public_fallbacks(
            current_feature_count=len(features),
        )
        for f in public_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 7: Last-known cache (fill gaps)
        if not features:
            cache_features = self._load_cache()
            for f in cache_features:
                fid = f["properties"].get("id", "")
                if fid:
                    features[fid] = f

        # Post-process: promote MeshCore (and other position-less) nodes to map features
        # when the operator has assigned coordinates via map_settings.json.
        promoted = self._apply_operator_positions(features)

        sources = self._get_source_summary(
            tcp_features, mqtt_features, tracker_features, aredn_features,
            direct_radio_features, rns_direct_features, tracker_unified_features,
            public_features,
        )
        sources["meshcore_public"] = len(meshcore_public_features)
        sources["aredn_worldmap"] = len(aredn_worldmap_features)
        if promoted:
            sources["operator_positions"] = promoted
        geojson = {
            "type": "FeatureCollection",
            "features": list(features.values()),
            "properties": {
                "collected_at": datetime.now().isoformat(),
                "source_count": len(features),
                "sources": sources,
                "source_diagnostics": dict(self._source_diagnostics),
                "total_nodes": self._total_nodes_seen,
                "nodes_with_position": len(features),
                "nodes_without_position": self._nodes_without_position,
                "nodes_without_position_count": len(self._nodes_without_position),
                "online_threshold_minutes": self.get_online_threshold_seconds() // 60,
            }
        }

        # Collection summary (INFO — surfaces without --verbose)
        logger.info(
            f"MapDataCollector: {len(features)} nodes "
            f"(unified:{sources.get('unified_tracker', 0)} "
            f"meshtasticd:{sources.get('meshtasticd', 0)} "
            f"direct_radio:{sources.get('direct_radio', 0)} "
            f"mqtt:{sources.get('mqtt', 0)} "
            f"tracker:{sources.get('node_tracker', 0)} "
            f"aredn:{sources.get('aredn', 0)} "
            f"rns_direct:{sources.get('rns_direct', 0)} "
            f"meshcore_public:{sources.get('meshcore_public', 0)} "
            f"public:{sources.get('public_fallback', 0)} "
            f"operator_positions:{promoted}) "
            f"no_position:{len(self._nodes_without_position)}"
        )

        # Cache result
        self._cached_geojson = geojson
        self._last_collect = time.time()
        self._save_cache(geojson)

        # Record to history database
        if self._history and geojson["features"]:
            try:
                self._history.record_observations(geojson["features"])
            except Exception as e:
                logger.debug(f"History recording error: {e}")

        return geojson

    def _collect_unified_tracker(self) -> List[Dict]:
        """Collect nodes from the UnifiedNodeTracker singleton.

        The UnifiedNodeTracker is the richest data source — it merges nodes from
        RNS path table, meshtasticd, and the gateway bridge into a unified view.
        This is the same data the Topology view displays.

        Also appends non-Meshtastic nodes WITHOUT position (primarily MeshCore —
        its advertisements carry no GPS) to self._nodes_without_position, so the
        map UI can render them as a sidebar list and operators can assign
        positions via map_settings.json meshcore_positions.

        Returns:
            List of GeoJSON features for nodes with valid positions.
        """
        try:
            tracker = get_node_tracker()
            all_nodes = tracker.get_all_nodes()
            geojson = tracker.to_geojson()
            features = geojson.get("features", [])

            if features:
                # Enrich with additional properties the map expects
                for f in features:
                    props = f.get("properties", {})
                    if "via_mqtt" not in props:
                        props["via_mqtt"] = False
                    if "hardware" not in props:
                        props["hardware"] = ""
                    if "role" not in props:
                        props["role"] = ""
                    if "source" not in props:
                        props["source"] = "unified_tracker"

            # Capture non-Meshtastic nodes without position. Meshtastic's own
            # no-GPS nodes are handled by _collect_via_http (richer data available there);
            # for MeshCore/RNS/etc. the tracker is the only source.
            with_pos_ids = {n.id for n in tracker.get_nodes_with_position()}
            for node in all_nodes:
                if node.id in with_pos_ids:
                    continue
                if node.network == "meshtastic":
                    continue  # handled by HTTP collector
                try:
                    last_seen = node.get_age_string() if hasattr(node, "get_age_string") else None
                except Exception:
                    last_seen = None
                self._nodes_without_position.append({
                    "id": node.id,
                    "name": getattr(node, "name", node.id),
                    "network": node.network,
                    "is_online": getattr(node, "is_online", False),
                    "last_seen": last_seen,
                })

            # Distinguish three zero-yield cases for operator legibility:
            #   all_nodes=[], features=[]    → tracker simply empty (boot, no peers yet)
            #   all_nodes>0, features=0      → tracked peers exist but none have GPS
            #   (exception path below)       → tracker actually unreachable
            if not all_nodes:
                reason = "no_data"
                notes = "unified tracker empty — no peers known to this box"
            elif not features:
                reason = "no_positions"
                notes = f"{len(all_nodes)} tracked nodes, 0 have GPS"
            else:
                reason = None  # "ok" computed by _record_diagnostic
                notes = f"{len(all_nodes)} tracked total"
            self._record_diagnostic(
                "unified_tracker",
                attempted=len(all_nodes),
                yielded=len(features),
                reason_if_zero=reason,
                notes=notes,
            )
            return features

        except Exception as e:
            logger.debug(f"UnifiedNodeTracker collection error: {e}")
            self._record_diagnostic(
                "unified_tracker",
                attempted=0,
                yielded=0,
                reason_if_zero="unreachable",
                notes=str(e)[:200],
            )
            return []

    def _collect_meshtasticd(self) -> List[Dict]:
        """Collect nodes from meshtasticd.

        Uses configurable host/port (default: localhost:4403 TCP, 9443 HTTP).

        Strategy (ordered by preference):
        1. HTTP API (/json/nodes) — no TCP lock needed, non-blocking
        2. TCP interface via connection manager — needs exclusive lock
        3. CLI parsing — fallback when Python module unavailable
        """
        host = self.get_meshtasticd_host()

        # Strategy 1: HTTP API (preferred — doesn't conflict with gateway bridge)
        features = self._collect_via_http(host)
        if features:
            self._record_diagnostic(
                "meshtasticd",
                attempted=self._total_nodes_seen or len(features),
                yielded=len(features),
                notes="via http",
            )
            return features

        # Strategy 2: TCP interface (needs lock)
        port = self.get_meshtasticd_port()

        # Quick check if TCP port is open before attempting connection
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            if result != 0:
                logger.debug(f"meshtasticd not reachable at {host}:{port}")
                self._record_diagnostic(
                    "meshtasticd",
                    attempted=0, yielded=0,
                    reason_if_zero="unreachable",
                    notes=f"{host}:{port} closed",
                )
                return []
        except OSError:
            self._record_diagnostic(
                "meshtasticd",
                attempted=0, yielded=0,
                reason_if_zero="unreachable",
                notes="socket check failed",
            )
            return []

        features = self._collect_via_tcp_interface()
        if features:
            self._record_diagnostic(
                "meshtasticd",
                attempted=len(features),
                yielded=len(features),
                notes="via tcp",
            )
            return features

        # Strategy 3: Fall back to CLI parsing
        features = self._collect_via_cli()
        if features:
            logger.debug(f"meshtasticd (CLI): {len(features)} nodes with position")

        return features

    def _apply_operator_positions(self, features: Dict[str, Dict]) -> int:
        """Promote position-less nodes to map features using operator-assigned coordinates.

        Reads `meshcore_positions` from map_settings.json. Matches against
        self._nodes_without_position by either the full node id (e.g. "meshcore:abcd…")
        or a prefix of the id after the "network:" part (e.g. "abcd" matches
        "meshcore:abcd1234ef"). Promoted nodes are added to `features` and REMOVED
        from `self._nodes_without_position` (they're no longer "without" — operator fixed that).

        Returns the number of nodes promoted.
        """
        if not self._settings or not self._nodes_without_position:
            return 0
        positions = self._settings.get("meshcore_positions", {}) or {}
        if not positions:
            return 0

        promoted = 0
        remaining: List[Dict] = []
        for entry in self._nodes_without_position:
            nid = entry.get("id", "")
            match = positions.get(nid)
            if match is None:
                # Try prefix match on the body after "network:"
                body = nid.split(":", 1)[1] if ":" in nid else nid
                for key, val in positions.items():
                    if body.startswith(key):
                        match = val
                        break
            if match is None:
                remaining.append(entry)
                continue

            try:
                lat = float(match["lat"])
                lon = float(match["lon"])
            except (KeyError, TypeError, ValueError):
                remaining.append(entry)
                continue

            feature = {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": nid,
                    "name": entry.get("name", nid),
                    "network": entry.get("network", "unknown"),
                    "is_online": entry.get("is_online", False),
                    "is_local": False,
                    "is_gateway": False,
                    "last_seen": entry.get("last_seen"),
                    "source": "operator_positions",
                    "position_source": "operator",
                    "note": match.get("note", ""),
                },
            }
            if nid and nid not in features:
                features[nid] = feature
                promoted += 1
            else:
                remaining.append(entry)

        self._nodes_without_position = remaining
        return promoted

    def _collect_via_http(self, host: str) -> List[Dict]:
        """Collect nodes via meshtasticd's HTTP JSON API.

        Uses GET /json/nodes which returns all known mesh nodes without
        needing the TCP connection lock. This is the preferred collection
        method because it doesn't conflict with the gateway bridge.
        """
        try:
            client = get_http_client(host=host)
            if not client.is_available:
                logger.debug("meshtasticd HTTP API not available")
                return []

            nodes = client.get_nodes()
            if not nodes:
                return []

            features = []
            no_position_nodes = []

            for node in nodes:
                if node.has_position:
                    last_heard = node.last_heard or 0
                    is_online = self._is_node_online(last_heard, source="meshtastic")

                    feature = {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [node.longitude, node.latitude],
                        },
                        "properties": {
                            "id": node.node_id,
                            "name": node.long_name or node.short_name or node.node_id,
                            "short_name": node.short_name,
                            "network": "meshtastic",
                            "hardware": node.hw_model,
                            "snr": node.snr,
                            "last_heard": last_heard,
                            "via_mqtt": node.via_mqtt,
                            "role": node.role or "node",
                            "is_online": is_online,
                            "is_local": getattr(node, 'hops_away', None) == 0,
                            "is_gateway": getattr(node, 'role', '') in ('ROUTER', 'ROUTER_CLIENT'),
                            "hops_away": getattr(node, 'hops_away', None),
                            "altitude": node.altitude,
                            "source": "meshtasticd_http",
                        },
                    }
                    features.append(feature)
                else:
                    no_position_nodes.append({
                        "id": node.node_id,
                        "name": node.long_name or node.short_name or node.node_id,
                        "network": "meshtastic",
                        "hw_model": node.hw_model,
                        "snr": node.snr,
                        "last_heard": node.last_heard,
                    })

            # Append (not overwrite) so entries added by other sources
            # (MeshCore from _collect_unified_tracker, etc.) survive.
            self._nodes_without_position.extend(no_position_nodes)
            self._total_nodes_seen = len(nodes)

            logger.debug(
                f"meshtasticd (HTTP): {len(features)} with GPS, "
                f"{len(no_position_nodes)} without GPS (total: {len(nodes)})"
            )
            return features

        except Exception as e:
            logger.debug(f"HTTP collection error: {e}")
            return []

    def _collect_via_tcp_interface(self) -> List[Dict]:
        """Collect nodes using the meshtastic Python TCP interface.

        Uses MeshtasticConnectionManager for safe locking and cleanup.
        Returns list of GeoJSON features for nodes with valid positions.
        Also populates self._nodes_without_position for nodes lacking GPS.
        """
        features = []
        no_position_nodes = []
        host = self.get_meshtasticd_host()
        port = self.get_meshtasticd_port()
        manager = get_connection_manager(host=host, port=port)

        # Don't block if someone else holds the connection
        if not manager.acquire_lock(timeout=5.0):
            logger.debug("Could not acquire meshtasticd lock (in use)")
            return []

        try:
            manager._wait_for_cooldown()
            interface = manager._create_interface()

            try:
                if hasattr(interface, 'nodes') and interface.nodes:
                    now = time.time()
                    online_threshold = self.get_online_threshold_seconds()
                    total_nodes = len(interface.nodes)

                    for node_id, node_data in interface.nodes.items():
                        feature = self._parse_tcp_node(node_id, node_data, now, online_threshold)
                        if feature:
                            features.append(feature)
                        else:
                            # Track nodes without valid position
                            no_pos_info = self._extract_node_info_without_position(
                                node_id, node_data, now, online_threshold
                            )
                            if no_pos_info:
                                no_position_nodes.append(no_pos_info)

                    # Extend (not overwrite) so entries added by earlier sources
                    # (MeshCore from _collect_unified_tracker, etc.) survive.
                    self._nodes_without_position.extend(no_position_nodes)
                    self._total_nodes_seen = total_nodes

                    logger.debug(
                        f"meshtasticd (TCP): {len(features)} with GPS, "
                        f"{len(no_position_nodes)} without GPS (total: {total_nodes})"
                    )
            finally:
                safe_close_interface(interface)

        except Exception as e:
            logger.debug(f"TCP interface collection error: {e}")
        finally:
            manager.release_lock()

        return features

    def _collect_direct_radio(self) -> List[Dict]:
        """Collect nodes directly from USB radio (serial connection).

        Used when meshtasticd is not running (usb-direct mode).
        MeshForge connects directly to the radio via USB serial.

        Returns list of GeoJSON features for nodes with valid positions.
        """
        # Check if USB device is available
        import glob
        usb_devices = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
        if not usb_devices:
            logger.debug("No USB radio devices found")
            return []

        features = []
        no_position_nodes = []

        # Reset manager to ensure we get SERIAL mode
        # (in case a previous TCP connection left it in TCP mode)
        reset_connection_manager()
        manager = get_connection_manager(mode=ConnectionMode.SERIAL)

        # Don't block if someone else holds the connection
        if not manager.acquire_lock(timeout=5.0):
            logger.debug("Could not acquire radio lock (in use)")
            return []

        try:
            manager._wait_for_cooldown()
            interface = manager._create_interface()

            try:
                if hasattr(interface, 'nodes') and interface.nodes:
                    now = time.time()
                    online_threshold = self.get_online_threshold_seconds()
                    total_nodes = len(interface.nodes)

                    for node_id, node_data in interface.nodes.items():
                        feature = self._parse_tcp_node(node_id, node_data, now, online_threshold)
                        if feature:
                            # Mark as from direct radio
                            feature["properties"]["source"] = "direct_radio"
                            features.append(feature)
                        else:
                            # Track nodes without valid position
                            no_pos_info = self._extract_node_info_without_position(
                                node_id, node_data, now, online_threshold
                            )
                            if no_pos_info:
                                no_position_nodes.append(no_pos_info)

                    # Extend (not overwrite). The `if not self._total_nodes_seen`
                    # gate stays — we still only want to claim _total_nodes_seen
                    # when meshtasticd didn't report, but the no-position list
                    # should always accumulate so MeshCore / RNS entries survive.
                    self._nodes_without_position.extend(no_position_nodes)
                    if not self._total_nodes_seen:
                        self._total_nodes_seen = total_nodes

                    logger.debug(
                        f"Direct radio (USB): {len(features)} with GPS, "
                        f"{len(no_position_nodes)} without GPS (total: {total_nodes})"
                    )
            finally:
                safe_close_interface(interface)

        except Exception as e:
            logger.debug(f"Direct radio collection error: {e}")
        finally:
            manager.release_lock()

        return features

    def _parse_tcp_node(self, node_id: str, data: dict, now: float,
                        online_threshold_seconds: int = 900) -> Optional[Dict]:
        """Parse a single node from the TCP interface nodes dict.

        Handles both float (latitude) and integer (latitudeI) coordinate formats.

        Args:
            node_id: The node ID string
            data: Raw node data from meshtastic interface
            now: Current timestamp
            online_threshold_seconds: Consider online if heard within this many seconds
        """
        position = data.get('position', {})
        if not position:
            return None

        # Extract coordinates - prefer float, fall back to integer / 1e7
        lat = position.get('latitude')
        if lat is None:
            lat_i = position.get('latitudeI')
            lat = lat_i / 1e7 if lat_i is not None else None

        lon = position.get('longitude')
        if lon is None:
            lon_i = position.get('longitudeI')
            lon = lon_i / 1e7 if lon_i is not None else None

        # Skip nodes without valid coordinates
        if not self._is_valid_coordinate(lat, lon):
            return None

        # Extract user info
        user = data.get('user', {})
        device_metrics = data.get('deviceMetrics', {})

        # Determine online status from lastHeard (configurable threshold)
        last_heard = data.get('lastHeard', 0)
        if last_heard and (now - last_heard) <= online_threshold_seconds:
            is_online = True
        elif last_heard:
            is_online = False  # Heard too long ago
        else:
            is_online = False  # Never heard

        # Format last_seen as human-readable
        if last_heard:
            age_seconds = int(now - last_heard)
            if age_seconds < 60:
                last_seen = f"{age_seconds}s ago"
            elif age_seconds < 3600:
                last_seen = f"{age_seconds // 60}m ago"
            elif age_seconds < 86400:
                last_seen = f"{age_seconds // 3600}h ago"
            else:
                last_seen = f"{age_seconds // 86400}d ago"
        else:
            last_seen = "unknown"

        # Format node_id
        node_num = data.get('num', 0)
        if isinstance(node_id, str) and node_id.startswith('!'):
            formatted_id = node_id
        elif node_num:
            formatted_id = f"!{node_num:08x}"
        else:
            formatted_id = str(node_id)

        # Extract environment sensor data from meshtasticd telemetry
        env_metrics = data.get('environmentMetrics', {})

        return self._make_feature(
            node_id=formatted_id,
            name=user.get('longName', '') or user.get('shortName', ''),
            lat=lat,
            lon=lon,
            network='meshtastic',
            is_online=is_online,
            snr=data.get('snr'),
            battery=device_metrics.get('batteryLevel'),
            hardware=user.get('hwModel', ''),
            role=user.get('role', ''),
            is_gateway=user.get('role', '') in ('ROUTER', 'ROUTER_CLIENT'),
            via_mqtt=data.get('viaMqtt', False),
            is_local=(data.get('hopsAway', 99) == 0),
            last_seen=last_seen,
            last_heard=last_heard,
            temperature=env_metrics.get('temperature'),
            humidity=env_metrics.get('relativeHumidity'),
            pressure=env_metrics.get('barometricPressure'),
            channel_utilization=device_metrics.get('channelUtilization'),
            air_util_tx=device_metrics.get('airUtilTx'),
        )

    def _extract_node_info_without_position(self, node_id: str, data: dict, now: float,
                                            online_threshold_seconds: int = 900) -> Optional[Dict]:
        """Extract basic info for a node that has no valid GPS position.

        Returns a dict with id, name, last_seen, etc. for display in a table/list.
        """
        user = data.get('user', {})
        device_metrics = data.get('deviceMetrics', {})

        # Format node_id
        node_num = data.get('num', 0)
        if isinstance(node_id, str) and node_id.startswith('!'):
            formatted_id = node_id
        elif node_num:
            formatted_id = f"!{node_num:08x}"
        else:
            formatted_id = str(node_id)

        # Determine online status from last_heard timestamp
        last_heard = data.get('lastHeard', 0)
        is_online = self._is_node_online(last_heard, source="meshtastic")

        # Format last_seen
        if last_heard:
            age_seconds = int(now - last_heard)
            if age_seconds < 60:
                last_seen = f"{age_seconds}s ago"
            elif age_seconds < 3600:
                last_seen = f"{age_seconds // 60}m ago"
            elif age_seconds < 86400:
                last_seen = f"{age_seconds // 3600}h ago"
            else:
                last_seen = f"{age_seconds // 86400}d ago"
        else:
            last_seen = "unknown"

        name = user.get('longName', '') or user.get('shortName', '')

        return {
            "id": formatted_id,
            "name": name or formatted_id,
            "network": "meshtastic",
            "is_online": is_online,
            "last_seen": last_seen,
            "hardware": user.get('hwModel', ''),
            "role": user.get('role', ''),
            "snr": data.get('snr'),
            "battery": device_metrics.get('batteryLevel'),
            "hops_away": data.get('hopsAway'),
            "via_mqtt": data.get('viaMqtt', False),
        }

    def _collect_via_cli(self) -> List[Dict]:
        """Fall back to CLI parsing when Python TCP interface unavailable."""
        try:
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli()
            if not cli_path:
                logger.debug("meshtastic CLI not found")
                return []

            host = self.get_meshtasticd_host()
            port = self.get_meshtasticd_port()
            host_arg = f"{host}:{port}" if port != 4403 else host

            result = subprocess.run(
                [cli_path, '--host', host_arg, '--info'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                return []
            return self._parse_meshtastic_info(result.stdout)
        except FileNotFoundError:
            logger.debug("meshtastic CLI not found")
        except subprocess.TimeoutExpired:
            logger.debug("meshtastic CLI timed out")
        except Exception as e:
            logger.debug(f"CLI collection error: {e}")
        return []

    def _parse_meshtastic_info(self, output: str) -> List[Dict]:
        """Parse meshtastic --info output for node positions.

        Handles JSON node data that some versions of the CLI output.
        This is a fallback - the TCP interface is preferred.
        """
        features = []
        lines = output.split('\n')

        for line in lines:
            # Try to parse JSON-like node data from --info output
            if '{' in line and ('position' in line.lower() or 'latitude' in line.lower()):
                try:
                    start = line.index('{')
                    data = json.loads(line[start:])
                    if 'position' in data:
                        pos = data['position']
                        lat = pos.get('latitude')
                        if lat is None:
                            lat_i = pos.get('latitudeI')
                            lat = lat_i / 1e7 if lat_i else None
                        lon = pos.get('longitude')
                        if lon is None:
                            lon_i = pos.get('longitudeI')
                            lon = lon_i / 1e7 if lon_i else None

                        if self._is_valid_coordinate(lat, lon):
                            user = data.get('user', {})
                            device_metrics = data.get('deviceMetrics', {})
                            cli_last_heard = data.get('lastHeard', 0)
                            feature = self._make_feature(
                                node_id=data.get('num', data.get('id', 'unknown')),
                                name=user.get('longName', ''),
                                lat=lat, lon=lon,
                                network='meshtastic',
                                is_online=self._is_node_online(cli_last_heard, source="meshtastic"),
                                snr=data.get('snr'),
                                battery=device_metrics.get('batteryLevel'),
                                hardware=user.get('hwModel', ''),
                                role=user.get('role', ''),
                                last_heard=cli_last_heard,
                            )
                            features.append(feature)
                except (json.JSONDecodeError, ValueError, IndexError):
                    continue

        return features

    def _collect_mqtt(self) -> List[Dict]:
        """Collect nodes from MQTT subscriber if available.

        Tries the live subscriber singleton first (best data, includes sensors),
        then falls back to cached GeoJSON file.
        """
        live_connected = False
        live_features: List[Dict] = []
        # Try live subscriber first (has real-time sensor data)
        try:
            subscriber = get_local_subscriber()
            if subscriber.is_connected():
                live_connected = True
                geojson = subscriber.get_geojson()
                live_features = geojson.get("features", [])
                if live_features:
                    logger.debug(f"MQTT live: {len(live_features)} nodes with position")
                    self._record_diagnostic(
                        "mqtt",
                        attempted=len(live_features),
                        yielded=len(live_features),
                        notes="live subscriber",
                    )
                    return live_features
        except Exception as e:
            logger.debug(f"MQTT live collection error: {e}")

        # Fallback: cached MQTT node file
        try:
            mqtt_cache = self._cache_dir / "mqtt_nodes.json"
            if mqtt_cache.exists():
                age = time.time() - mqtt_cache.stat().st_mtime
                if age < 300:  # Less than 5 minutes old
                    with open(mqtt_cache) as f:
                        data = json.load(f)
                    if data.get("type") == "FeatureCollection":
                        cached = data.get("features", [])
                        self._record_diagnostic(
                            "mqtt",
                            attempted=len(cached),
                            yielded=len(cached),
                            notes=f"cached ({int(age)}s old)",
                        )
                        return cached
        except Exception as e:
            logger.debug(f"MQTT cache collection error: {e}")

        self._record_diagnostic(
            "mqtt",
            attempted=0, yielded=0,
            reason_if_zero="unreachable" if live_connected else "not_configured",
            notes="no live subscriber and no fresh cache",
        )
        return []

    def _collect_node_tracker(self) -> List[Dict]:
        """Collect nodes from UnifiedNodeTracker cache files."""
        features = []

        # Check node_cache.json
        cache_path = get_real_user_home() / ".config" / "meshforge" / "node_cache.json"

        if cache_path.exists():
            try:
                age = time.time() - cache_path.stat().st_mtime
                max_age = self.get_node_cache_max_age_seconds()
                if age < max_age:  # Configurable, default 48 hours
                    with open(cache_path) as f:
                        data = json.load(f)

                    # Count nodes for logging
                    total_nodes = 0
                    if isinstance(data, list):
                        total_nodes = len(data)
                        for node in data:
                            feature = self._node_cache_to_feature(node)
                            if feature:
                                features.append(feature)
                    elif isinstance(data, dict) and "nodes" in data:
                        total_nodes = len(data["nodes"])
                        for node in data["nodes"]:
                            feature = self._node_cache_to_feature(node)
                            if feature:
                                features.append(feature)
                    elif isinstance(data, dict):
                        # Dict without "nodes" key - log for debugging
                        logger.debug(f"node_cache.json has dict format without 'nodes' key: {list(data.keys())}")

                    if features:
                        logger.debug(f"node_cache: {len(features)}/{total_nodes} nodes with position")
                else:
                    # Cache too old
                    age_hours = age / 3600
                    max_hours = max_age / 3600
                    logger.debug(f"node_cache.json too old: {age_hours:.1f}h > {max_hours:.1f}h max")
            except json.JSONDecodeError as e:
                logger.warning(f"node_cache.json JSON parse error: {e}")
            except PermissionError as e:
                logger.warning(f"node_cache.json permission denied: {e}")
            except Exception as e:
                logger.debug(f"Node cache read error: {e}")
        else:
            logger.debug(f"node_cache.json not found at: {cache_path}")

        # Check RNS nodes temp file
        rns_cache = Path("/tmp/meshforge_rns_nodes.json")
        if rns_cache.exists():
            rns_count = 0
            try:
                age = time.time() - rns_cache.stat().st_mtime
                max_age = self.get_rns_cache_max_age_seconds()
                if age < max_age:  # Configurable, default 1 hour
                    with open(rns_cache) as f:
                        data = json.load(f)

                    # Handle both list and dict-with-nodes format
                    nodes_list = []
                    if isinstance(data, list):
                        nodes_list = data
                    elif isinstance(data, dict) and "nodes" in data:
                        nodes_list = data["nodes"]

                    for node in nodes_list:
                        feature = self._rns_cache_to_feature(node)
                        if feature:
                            features.append(feature)
                            rns_count += 1

                    if rns_count:
                        logger.debug(f"rns_cache: {rns_count}/{len(nodes_list)} nodes with position")
                else:
                    age_mins = age / 60
                    max_mins = max_age / 60
                    logger.debug(f"RNS cache too old: {age_mins:.0f}m > {max_mins:.0f}m max")
            except Exception as e:
                logger.debug(f"RNS cache read error: {e}")

        return features

    def _collect_aredn(self) -> List[Dict]:
        """Collect nodes from AREDN mesh network.

        Scans the local AREDN network for nodes with GPS coordinates.
        AREDN nodes may have location data configured by the operator.
        """
        features: List[Dict] = []
        configured_ips = []
        if self._settings:
            raw = self._settings.get("aredn_node_ips", [])
            configured_ips = [raw] if isinstance(raw, str) else list(raw or [])

        # First try to connect to the local AREDN node (zero-config via localnode.local.mesh
        # + common defaults is already built into _get_aredn_node_ip).
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
                    "AREDN: no local node reachable (tried localnode.local.mesh + "
                    "defaults). Configure aredn_node_ips in "
                    "~/.config/meshforge/map_settings.json to add a specific IP."
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

        # Success path — reason "no_positions" means we reached AREDN but nothing had GPS set.
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
        """Wrapper around _fetch_aredn_worldmap_nodes that runs independent of
        the public_fallback threshold. AREDN worldmap is geographic context
        (where are AREDN nodes in my region), not a Meshtastic-sparse-fill fallback.

        Disabled via `enable_aredn_worldmap_fallback=False` in map_settings.json.
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

    # MeshCore node type ids from the public map API schema.
    _MESHCORE_NODE_TYPES = {1: "client", 2: "repeater", 3: "room_server"}
    _MESHCORE_MAP_URL = "https://map.meshcore.dev/api/v1/nodes"

    def _collect_meshcore_public(self) -> List[Dict]:
        """Fetch MeshCore nodes from the public map API (map.meshcore.dev).

        MeshCore advertisements carry no GPS locally (MeshCoreHandler adds nodes
        to UnifiedNodeTracker without position → they get filtered out of the map),
        so the only way to render MeshCore with GPS is to pull the public map API.
        ~30k nodes globally, validated coordinates, network='meshcore'.

        Disabled via `enable_meshcore_public=False` in map_settings.json.

        Operator-assigned positions (map_settings.json `meshcore_positions`) still
        take precedence for specific local nodes — see `_apply_operator_positions`.
        """
        if not self._settings or not self._settings.get("enable_meshcore_public", True):
            self._record_diagnostic(
                "meshcore_public",
                attempted=0, yielded=0,
                reason_if_zero="source_disabled",
                notes="set enable_meshcore_public=true in map_settings.json",
            )
            return []

        import urllib.request
        import urllib.error
        features: List[Dict] = []
        attempted = 0
        try:
            req = urllib.request.Request(
                self._MESHCORE_MAP_URL,
                headers={"Accept": "application/json", "User-Agent": "MeshForge/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                # Cap at 64 MB — current global MeshCore map is ~12 MB for ~30k nodes.
                # An 8 MB cap truncates mid-JSON and leaves an "Unterminated string"
                # parse error rather than complete data.
                raw = resp.read(64 * 1024 * 1024)
                data = json.loads(raw.decode("utf-8", errors="replace"))

            if not isinstance(data, list):
                self._record_diagnostic(
                    "meshcore_public", attempted=0, yielded=0,
                    reason_if_zero="unreachable",
                    notes="unexpected response shape (not a list)",
                )
                return []

            attempted = len(data)
            for node in data:
                feature = self._parse_meshcore_public_node(node)
                if feature:
                    features.append(feature)

            if features:
                logger.info(f"MeshCore public map: {len(features)}/{attempted} nodes")

        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug(f"MeshCore public map unavailable: {e}")
            self._record_diagnostic(
                "meshcore_public", attempted=attempted, yielded=len(features),
                reason_if_zero="unreachable" if not features else None,
                notes=str(e)[:120],
            )
            return features

        reason = None
        if not features:
            reason = "no_positions"
        self._record_diagnostic(
            "meshcore_public",
            attempted=attempted, yielded=len(features),
            reason_if_zero=reason,
            notes=self._MESHCORE_MAP_URL,
        )
        return features

    def _parse_meshcore_public_node(self, node: Dict) -> Optional[Dict]:
        """Parse one node from map.meshcore.dev into a GeoJSON feature."""
        lat = node.get("adv_lat")
        lon = node.get("adv_lon")
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        if lat == 0.0 and lon == 0.0:
            return None  # null-island filter

        pubkey = node.get("public_key") or ""
        if not pubkey:
            return None

        name = node.get("adv_name") or pubkey[:16]
        node_type_id = node.get("type", 0)
        node_type = self._MESHCORE_NODE_TYPES.get(node_type_id, "unknown")
        params = node.get("params") or {}
        last_advert = node.get("last_advert")

        is_online = False
        try:
            if last_advert:
                last_ts = float(last_advert)
                is_online = self._is_node_online(last_ts, source="public_fallback")
        except (TypeError, ValueError):
            pass

        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": f"meshcore:{pubkey}",
                "name": name,
                "network": "meshcore",
                "node_type": node_type,
                "is_online": is_online,
                "is_local": False,
                "is_gateway": node_type_id == 2,  # repeater
                "last_heard": last_advert,
                "hardware": "",
                "role": node_type,
                "frequency": params.get("freq"),
                "spreading_factor": params.get("sf"),
                "coding_rate": params.get("cr"),
                "bandwidth": params.get("bw"),
                "source": "meshcore_public_map",
            },
        }

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

    # RNS data collection methods (_collect_rns_direct, _load_rns_position_cache,
    # _load_nomadnet_peers, _rns_peer_to_feature, _node_cache_to_feature,
    # _rns_cache_to_feature) are inherited from RNSDataCollectorMixin
    # in _map_collector_rns.py

    def _make_feature(self, node_id: str, name: str, lat: float, lon: float,
                      network: str = "meshtastic", is_online: bool = False,
                      snr: Optional[float] = None, battery: Optional[int] = None,
                      hardware: str = "", role: str = "",
                      is_gateway: bool = False, via_mqtt: bool = False,
                      is_local: bool = False, last_seen: str = "",
                      last_heard: Optional[float] = None,
                      rssi: Optional[int] = None,
                      temperature: Optional[float] = None,
                      humidity: Optional[float] = None,
                      pressure: Optional[float] = None,
                      pm25: Optional[int] = None,
                      co2: Optional[int] = None,
                      iaq: Optional[int] = None,
                      channel_utilization: Optional[float] = None,
                      air_util_tx: Optional[float] = None,
                      channel_name: str = "",
                      has_encryption: Optional[bool] = None) -> Dict:
        """Create a GeoJSON Feature for a node."""
        props = {
            "id": str(node_id),
            "name": name or str(node_id),
            "network": network,
            "is_online": is_online,
            "is_local": is_local,
            "is_gateway": is_gateway,
            "via_mqtt": via_mqtt,
            "snr": snr,
            "rssi": rssi,
            "battery": battery,
            "last_seen": last_seen or ("online" if is_online else "unknown"),
            "last_heard": last_heard or 0,
            "hardware": hardware,
            "role": role,
        }
        # Add sensor data only when present (avoid cluttering output)
        if temperature is not None:
            props["temperature"] = temperature
        if humidity is not None:
            props["humidity"] = humidity
        if pressure is not None:
            props["pressure"] = pressure
        if pm25 is not None:
            props["pm25"] = pm25
        if co2 is not None:
            props["co2"] = co2
        if iaq is not None:
            props["iaq"] = iaq
        if channel_utilization is not None:
            props["channel_utilization"] = channel_utilization
        if air_util_tx is not None:
            props["air_util_tx"] = air_util_tx
        # Channel/encryption info (Phase 3)
        if channel_name:
            props["channel_name"] = channel_name
        if has_encryption is not None:
            props["has_encryption"] = has_encryption
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]
            },
            "properties": props,
        }

    def _merge_feature(self, existing: Dict, new: Dict) -> None:
        """Merge new feature data into existing.

        - For is_online/last_heard: most recent last_heard wins (freshest data
          determines online status). This prevents stale sources from overriding
          accurate status.
        - For other properties: prefer non-null values (fill gaps).
        """
        new_props = new["properties"]
        ex_props = existing["properties"]

        # Handle is_online via most-recent last_heard
        new_lh = new_props.get("last_heard", 0) or 0
        ex_lh = ex_props.get("last_heard", 0) or 0
        if new_lh > ex_lh:
            ex_props["last_heard"] = new_lh
            if "is_online" in new_props:
                ex_props["is_online"] = new_props["is_online"]

        # Merge other properties (prefer non-null to fill gaps)
        for key, value in new_props.items():
            if key in ("is_online", "last_heard"):
                continue  # Already handled above
            if value is not None and value != "" and value != "unknown":
                existing_val = ex_props.get(key)
                if existing_val is None or existing_val == "" or existing_val == "unknown":
                    ex_props[key] = value

    def _load_cache(self) -> List[Dict]:
        """Load last-known node state from disk cache."""
        if self._cache_file.exists():
            try:
                age = time.time() - self._cache_file.stat().st_mtime
                if age < 86400:  # Less than 24 hours old
                    with open(self._cache_file) as f:
                        data = json.load(f)
                    if data.get("type") == "FeatureCollection":
                        # Mark all cached nodes as potentially offline
                        for feature in data.get("features", []):
                            if age > 900:  # 15 minutes
                                feature["properties"]["is_online"] = False
                                feature["properties"]["last_seen"] = "cached"
                        return data.get("features", [])
            except Exception as e:
                logger.debug(f"Cache load error: {e}")
        return []

    def _save_cache(self, geojson: Dict) -> None:
        """Persist current node state to disk."""
        try:
            with open(self._cache_file, 'w') as f:
                json.dump(geojson, f)
        except Exception as e:
            logger.debug(f"Cache save error: {e}")

    def _get_source_summary(
        self, tcp: List, mqtt: List, tracker: List, aredn: List = None,
        direct_radio: List = None, rns_direct: List = None,
        unified_tracker: List = None, public: List = None,
    ) -> Dict:
        """Summarize which sources contributed data."""
        summary = {
            "unified_tracker": len(unified_tracker) if unified_tracker else 0,
            "meshtasticd": len(tcp),
            "direct_radio": len(direct_radio) if direct_radio else 0,
            "mqtt": len(mqtt),
            "node_tracker": len(tracker),
            "aredn": len(aredn) if aredn else 0,
            "rns_direct": len(rns_direct) if rns_direct else 0,
            "public_fallback": len(public) if public else 0,
        }
        # Flag if HTTP was used (source tag on features)
        if tcp and any(f.get("properties", {}).get("source") == "meshtasticd_http" for f in tcp):
            summary["meshtasticd_via"] = "http"
        elif tcp:
            summary["meshtasticd_via"] = "tcp"
        return summary

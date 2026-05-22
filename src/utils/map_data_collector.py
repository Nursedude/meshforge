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
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import
from utils.paths import get_real_user_home
from utils.common import SettingsManager
from gateway.node_tracker import get_node_tracker
from monitoring.mqtt_subscriber import get_local_subscriber

# External/optional dependencies
_RNS, _HAS_RNS = safe_import('RNS')
_msgpack, _HAS_MSGPACK = safe_import('msgpack')


from utils._map_collector_rns import RNSDataCollectorMixin
from utils._map_collector_public import PublicDataFallbackMixin
from utils._map_collector_meshtastic import MeshtasticDataCollectorMixin
from utils._map_collector_aredn import ARENDataCollectorMixin
from utils._map_collector_meshcore import MeshCorePublicCollectorMixin
from utils.node_history import _origin_priority


class MapDataCollector(
    RNSDataCollectorMixin,
    PublicDataFallbackMixin,
    MeshtasticDataCollectorMixin,
    ARENDataCollectorMixin,
    MeshCorePublicCollectorMixin,
):
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
    # MeshCore public map (~12 MB / 30 k nodes / 30 s timeout) is the dominant
    # collect() cost. Public API contract is "changes hourly", so caching the
    # parsed feature list for 10 min keeps the map fresh enough while sparing
    # every cache-miss collect() the 12 MB refetch.
    DEFAULT_MESHCORE_PUBLIC_CACHE_TTL_SECONDS = 600
    # Cap on any single HTTP-fetching source. With T1.1 caching, MeshCore's
    # original 30 s budget is no longer needed — a 15 s ceiling keeps total
    # collect() worst-case predictable, and a slow MeshCore fetch falls back
    # to stale cache rather than holding _collect_lock for 30 s.
    DEFAULT_SOURCE_TIMEOUT_SECONDS = 15
    # Per-source online thresholds (minutes) — configurable via map_settings.json
    DEFAULT_MESHTASTIC_THRESHOLD_MINUTES = 15
    DEFAULT_MQTT_THRESHOLD_MINUTES = 15
    DEFAULT_RNS_THRESHOLD_MINUTES = 30   # RNS announces less frequently
    DEFAULT_AREDN_THRESHOLD_MINUTES = 60  # AREDN scans are infrequent
    DEFAULT_PUBLIC_FALLBACK_THRESHOLD_MINUTES = 240  # meshmap.net reports less frequently
    # Periodic background refresh — without this, _collect_locked() runs
    # only when /api/nodes/geojson is hit. A box whose map UI isn't
    # visited accumulates no node-history writes; the directory freezes
    # at the last visit. Verified 2026-05-20 on meshanchor-server:
    # 8.5 d stall with the daemon running, because nothing was calling
    # the trigger endpoint. 300 s keeps directory.last_seen well inside
    # the fleet-collector's 60 s poll budget with headroom. Set to 0 in
    # map_settings.json to disable (tests, or boxes that drive collect()
    # from elsewhere).
    DEFAULT_PERIODIC_REFRESH_SECONDS = 300
    # Meshtasticd connection defaults
    DEFAULT_MESHTASTICD_HOST = "localhost"
    DEFAULT_MESHTASTICD_PORT = 4403

    def __init__(self,
                 cache_dir: Optional[Path] = None,
                 enable_history: bool = True,
                 config_dir: Optional[Path] = None):
        if cache_dir:
            self._cache_dir = cache_dir
        else:
            self._cache_dir = get_real_user_home() / ".local" / "share" / "meshforge"

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # Override settings location for tests (None = global CONFIG_DIR).
        self._config_dir_override = config_dir
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

        # Periodic background refresh state — started via
        # start_periodic_refresh() from the daemon entry point so unit
        # tests / CLI usage that construct a collector don't spawn a
        # surprise thread.
        self._periodic_refresh_thread: Optional[threading.Thread] = None
        self._periodic_refresh_stop = threading.Event()
        self._periodic_refresh_interval: float = 0.0

        # /api/nodes/directory response cache (Issue #70). Short TTL +
        # single-flight rebuild keeps concurrent federation polls from
        # paying the 6-10 s json.dumps+gzip cost N times under GIL.
        from utils._response_byte_cache import ResponseByteCache
        self._directory_response_cache = ResponseByteCache(ttl_s=5.0)

        # /api/nodes/geojson response cache (Issue #71 / GitHub #1168).
        # Generalized from #70's pattern — same wedge class, bigger
        # body (47 MB raw, ~35 s cold collect+serialize). Shorter TTL
        # because this endpoint is interactive (browser dashboard
        # refresh, not federation polling), so we want fresh data
        # without burning too many builds under burst load. Keyed by
        # (bbox_str, region_key, preset_key) — each materially alters
        # the response, so collapsing them would serve wrong bytes.
        self._geojson_response_cache = ResponseByteCache(ttl_s=2.0)

        # /api/network/topology response cache (Issue #71 / GitHub #1168).
        # Third instance of the wedge class: ~24 MB body, ~1.4 s every
        # request — no query params, so a single ``None`` cache key
        # covers every caller. Longer TTL because topology changes
        # slowly (gateway-gateway proximity links are computed from
        # positions that move at human scale).
        self._topology_response_cache = ResponseByteCache(ttl_s=5.0)

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
                # MeshCore public map (https://map.meshcore.dev) — global
                # directory of ~40k MeshCore nodes. Default OFF on :5000 because
                # the NOC view is fleet-focused; pulling 40k external nodes adds
                # ~10 s + ~20 MB to every cold collect on Pi-class hardware.
                # Operators wanting global context flip this to true in
                # ~/.config/meshforge/map_settings.json. Local MeshCore nodes
                # without GPS still surface via meshcore_positions overrides.
                "enable_meshcore_public": False,
                "meshcore_public_cache_ttl_seconds": self.DEFAULT_MESHCORE_PUBLIC_CACHE_TTL_SECONDS,
                "source_timeout_seconds": self.DEFAULT_SOURCE_TIMEOUT_SECONDS,
                "selected_region": None,
                # Fleet federation (Issue #49 follow-up). Each box's :5000 is
                # an island unless this is populated — peers expose
                # /api/nodes/directory which we poll and fold into the local
                # geojson response. Local always wins on (network, id) clash.
                #
                # `None` = auto-bootstrap from ~/.config/meshforge/fleet.json
                # on first run, then persist whatever was found (or []).
                # `[]` = explicitly disabled.
                # Operators can edit map_settings.json to override.
                "federation_peers": None,
                "federation_poll_interval_seconds": 60,
                # 30 s aligned with `map_federation.DEFAULT_TIMEOUT` post-
                # Issue #56 — 35 MB directories on Pi-class peers don't
                # fit in the old 5 s budget. Operator can override here
                # if they have a smaller fleet / older directory.
                "federation_timeout_seconds": 30,
                "federation_port": 5000,
                # Drives the periodic _collect_locked() heartbeat — see
                # DEFAULT_PERIODIC_REFRESH_SECONDS comment for the why.
                "periodic_refresh_seconds": self.DEFAULT_PERIODIC_REFRESH_SECONDS,
                # External-bulk geographic filter. External-bulk sources
                # (meshcore_public ~40k worldwide, aredn_worldmap,
                # public_fallback, mqtt_global) ingest the whole planet
                # by default; on a Pi-class fleet box that has no RF
                # reach to 99% of those nodes, the result is a multi-GB
                # node_history.db and a slow geojson cold path. None
                # for the explicit bbox means "auto-derive from
                # operator_position.json + radius"; radius=0 or no
                # operator position disables the filter entirely (fresh
                # installs pass everything until the operator opts in).
                "external_bulk_bbox": None,
                "external_bulk_radius_km": 500,
            },
            config_dir=self._config_dir_override,
            # Stale-default migration (Issue #62). Pre-Issue-#56 fleet
            # boxes have `federation_timeout_seconds: 5` pinned in their
            # JSON because SettingsManager used to persist defaults. Drop
            # the stale 5 so the current 30 takes effect. Operators who
            # deliberately set 5 (rare; smaller fleet, faster fail) need
            # to re-set it post-migration — acceptable tradeoff per the
            # fix-shape decision.
            stale_defaults={
                "federation_timeout_seconds": [5],
            },
        )

        # Bootstrap federation_peers from fleet.json on first run if the key
        # is None (meaning: never seen this setting before). Persist whatever
        # was found so subsequent runs don't re-attempt the bootstrap.
        if self._settings.get("federation_peers") is None:
            bootstrapped = self._bootstrap_federation_peers()
            self._settings.set("federation_peers", bootstrapped)
            self._settings.save()
            if bootstrapped:
                logger.info(
                    f"Federation: bootstrapped {len(bootstrapped)} peers "
                    f"from fleet.json: {bootstrapped}"
                )
            else:
                logger.info("Federation: no fleet.json found; peers=[] (disabled)")

        # Federation collector (lazy — start() called from map_data_service)
        self._federation = None
        self._init_federation()

        # MeshCore public per-source cache. The 30 s outer cache (`collect()`'s
        # `max_age_seconds`) prevents within-burst refetches; this second tier
        # absorbs the cache-miss case so a slow MeshCore fetch doesn't block
        # `_collect_lock` for 30 s on every cache-miss collect.
        self._meshcore_public_cache: Optional[List[Dict]] = None
        self._meshcore_public_cache_ts: Optional[float] = None

        # Track nodes without GPS for reporting
        self._nodes_without_position: List[Dict] = []
        self._total_nodes_seen: int = 0  # Total from meshtasticd (with + without GPS)

        # Per-source diagnostics — populated by each _collect_* method during collect().
        # Shape: {"source_name": {"attempted": int, "yielded": int, "reason_if_zero": str|None, "notes": str|None}}
        # Reason taxonomy: not_configured | unreachable | no_positions | source_disabled | ok
        self._source_diagnostics: Dict[str, Dict[str, Any]] = {}
        # Rate-limit for actionable INFO logs (source_name -> last-log-timestamp)
        self._last_info_log: Dict[str, float] = {}

        # External-bulk bbox-filter state (node count optimization). The
        # bbox is resolved once per collect() cycle and cached so the
        # four external-bulk source lists share one settings+file read.
        # Counts reset at the start of each collect() so /api/status
        # reflects the most recent cycle — same semantics as
        # _source_diagnostics.
        self._cached_external_bulk_bbox: Optional[Dict[str, float]] = None
        self._cached_external_bulk_bbox_ts: float = 0.0
        self._bbox_dropped_counts: Dict[str, int] = {}
        # Federation-skipped-persistence counter (node count opt §B).
        # Incremented for every federated feature filtered out before
        # NodeHistoryDB.record_observations(). Surfaces in /api/status.
        self._federated_skipped_persistence_count: int = 0

        # Node history database for position/state tracking over time
        self._history = None
        if enable_history:
            try:
                from utils.node_history import NodeHistoryDB
                db_path = self._cache_dir / "node_history.db"
                self._history = NodeHistoryDB(db_path=db_path)
            except Exception as e:
                # Was DEBUG; bumped to WARNING because a None history
                # silently strips `history`/`directory` keys from
                # `/api/status` (the dashboard contract). Caught by the
                # 2026-04-30 e2e flake investigation: a concurrent WAL
                # checkpoint on the production DB made the test
                # harness's NodeHistoryDB.__init__ raise database-locked,
                # which got swallowed at DEBUG and looked like a flaky
                # test instead of a contention signal. The proper fix is
                # cache_dir isolation in the e2e harness; this log just
                # makes future failures visible.
                logger.warning(f"Node history disabled: {e}")

    def _bootstrap_federation_peers(self) -> List[str]:
        """Read fleet.json (if present) and derive a peer list with self filtered out.

        Returns [] if fleet.json doesn't exist or doesn't parse — federation
        is opt-in and a missing fleet config is the normal case for
        single-box installs.
        """
        try:
            from utils.map_federation import filter_self_from_peers
            fleet_path = get_real_user_home() / ".config" / "meshforge" / "fleet.json"
            if not fleet_path.exists():
                return []
            with open(fleet_path, "r") as f:
                cfg = json.load(f)
            peers_dict = cfg.get("peers") or {}
            this_host = cfg.get("this_host")
            this_host_lower = str(this_host).lower() if this_host else None

            # Two-pass self-elimination:
            #   1) Skip peer entries whose CONFIG NAME matches this_host —
            #      handles IP-vs-hostname mismatches (the peer's stored
            #      identifier might be its IP, but the name in the dict is
            #      the canonical hostname).
            #   2) Run the runtime hostname filter as a backup, in case
            #      this_host wasn't configured but the host's actual
            #      hostname matches one of the peer ids.
            peer_ids: List[str] = []
            for name, info in peers_dict.items():
                if this_host_lower and name.lower() == this_host_lower:
                    continue
                if isinstance(info, dict) and info.get("ip"):
                    peer_ids.append(info["ip"])
                else:
                    peer_ids.append(name)

            local_names = None
            if this_host:
                from utils.map_federation import get_local_hostnames
                local_names = list(set(get_local_hostnames() + [str(this_host).lower()]))
            return filter_self_from_peers(peer_ids, local_names=local_names)
        except (OSError, json.JSONDecodeError, KeyError, ImportError) as e:
            logger.debug(f"Federation bootstrap from fleet.json failed: {e}")
            return []

    def _load_fleet_peer_names(self) -> Dict[str, str]:
        """Build endpoint→friendly-name mapping from fleet.json.

        Best-effort companion to `_bootstrap_federation_peers`. Returns
        `{ip: name}` (and `{name: name}` when the entry has no IP) so the
        federation collector can stamp `peer_name` on each status without
        re-reading the settings every cycle. Empty dict when fleet.json is
        absent or malformed — federation still works, the `peer_name` field
        just stays None.
        """
        try:
            fleet_path = get_real_user_home() / ".config" / "meshforge" / "fleet.json"
            if not fleet_path.exists():
                return {}
            with open(fleet_path, "r") as f:
                cfg = json.load(f)
            peers_dict = cfg.get("peers") or {}
            out: Dict[str, str] = {}
            for name, info in peers_dict.items():
                if isinstance(info, dict) and info.get("ip"):
                    out[info["ip"]] = name
                else:
                    out[name] = name
            return out
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.debug(f"Federation peer-name load from fleet.json failed: {e}")
            return {}

    def _init_federation(self) -> None:
        """Construct the FederationCollector instance (does not start it).

        start_federation() is called by map_data_service after the warmup
        collect runs, so we don't compete with the cold-start collect for
        I/O budget on Pi-class hardware.
        """
        peers = self._settings.get("federation_peers") or []
        if not peers:
            return
        try:
            from utils.map_federation import FederationCollector
            # Pass node_history db_path so federation can backpressure-skip
            # polls when the WAL is oversize (project_db_recurring_class).
            # NB: _init_federation runs BEFORE self._history is set in
            # __init__, so derive the path the same way the history block
            # does (~line 215) rather than reading self._history.db_path.
            db_path = self._cache_dir / "node_history.db"
            self._federation = FederationCollector(
                peers=peers,
                poll_interval=int(self._settings.get(
                    "federation_poll_interval_seconds", 60
                )),
                # Fallback matches the SettingsManager default (30) — the
                # pre-fix `, 5` here disagreed with `, 30` above, which is
                # exactly the dual-default hazard the Issue #62 fix
                # eliminates. Keep this aligned with the defaults block.
                timeout=float(self._settings.get(
                    "federation_timeout_seconds", 30
                )),
                port=int(self._settings.get("federation_port", 5000)),
                db_path=db_path,
                peer_names=self._load_fleet_peer_names(),
            )
        except ImportError as e:
            logger.warning(f"Federation disabled (import failed): {e}")
            self._federation = None

    def start_federation(self) -> None:
        """Start the federation poll thread. Idempotent. Called by map service."""
        if self._federation:
            self._federation.start()

    def stop_federation(self) -> None:
        """Stop the federation poll thread. Called by map service shutdown."""
        if self._federation:
            self._federation.stop()

    def start_periodic_refresh(
        self, interval_seconds: Optional[float] = None,
    ) -> None:
        """Drive _collect_locked() on a fixed cadence, independent of HTTP demand.

        The cache TTL inside collect() (30 s default) only matters when a
        caller actually invokes collect(). Without a periodic driver,
        boxes whose map UI is unvisited accumulate zero node-history
        writes, freezing /api/nodes/directory at the last visit.
        Verified 2026-05-20 on meshanchor-server (8.5 d stall).

        Interval comes from ``map_settings.periodic_refresh_seconds``
        (default ``DEFAULT_PERIODIC_REFRESH_SECONDS``); pass an explicit
        value to override. 0 or negative disables. Idempotent — a second
        call stops any in-flight thread first.

        Skips ticks while another collect() holds ``_collect_lock`` so a
        slow cycle can't queue up backed-up refresh work.
        """
        if interval_seconds is None:
            interval_seconds = float(self._settings.get(
                "periodic_refresh_seconds",
                self.DEFAULT_PERIODIC_REFRESH_SECONDS,
            ))
        if interval_seconds <= 0:
            return
        self.stop_periodic_refresh()
        self._periodic_refresh_interval = interval_seconds
        self._periodic_refresh_stop.clear()
        self._periodic_refresh_thread = threading.Thread(
            target=self._periodic_refresh_loop,
            daemon=True,
            name="MapDataCollector-periodic-refresh",
        )
        self._periodic_refresh_thread.start()

    def stop_periodic_refresh(self) -> None:
        """Stop the periodic refresh thread. Idempotent."""
        self._periodic_refresh_stop.set()
        thread = self._periodic_refresh_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self._periodic_refresh_thread = None

    def _periodic_refresh_loop(self) -> None:
        while not self._periodic_refresh_stop.is_set():
            # Event.wait satisfies the MF010 lint rule (no time.sleep in
            # daemon loops) and lets stop_periodic_refresh interrupt the
            # wait without waiting for the next tick.
            if self._periodic_refresh_stop.wait(
                self._periodic_refresh_interval
            ):
                return
            if not self._collect_lock.acquire(blocking=False):
                # An on-demand collect() is in flight; skip this tick
                # rather than queue up redundant work behind it.
                continue
            try:
                self._collect_locked()
            except Exception:
                logger.exception("Periodic map data refresh failed")
            finally:
                self._collect_lock.release()

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

    def get_bbox_filter_stats(self) -> Dict[str, Any]:
        """Bbox-filter + federation-skip counters from the most recent collect().

        Surfaced at /api/status.directory.bbox_filter so operators can
        see how much external-bulk traffic the filter is shedding and
        how many federation features are being kept out of the DB.
        Counts reset at the start of each collect() — same semantics
        as get_source_diagnostics.
        """
        return {
            "bbox": (
                dict(self._cached_external_bulk_bbox)
                if self._cached_external_bulk_bbox is not None
                else None
            ),
            "bbox_dropped": dict(self._bbox_dropped_counts),
            "federated_skipped_persistence": (
                self._federated_skipped_persistence_count
            ),
        }

    def _record_diagnostic(
        self,
        source: str,
        attempted: int,
        yielded: int,
        reason_if_zero: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Record diagnostic for a single source during collect().

        Preserves any `latency_ms` previously written by `_timed_collect`.
        Sources that call `_record_diagnostic` directly (without the timing
        wrapper) keep their existing behavior; their latency_ms field is
        absent rather than zero so consumers can distinguish "untimed" from
        "instantaneous".
        """
        if yielded > 0 and reason_if_zero is None:
            reason_if_zero = "ok"
        elif yielded == 0 and reason_if_zero is None:
            reason_if_zero = "no_positions" if attempted > 0 else "unreachable"
        existing = self._source_diagnostics.get(source, {})
        entry = {
            "attempted": attempted,
            "yielded": yielded,
            "reason_if_zero": reason_if_zero,
            "notes": notes,
        }
        if "latency_ms" in existing:
            entry["latency_ms"] = existing["latency_ms"]
        self._source_diagnostics[source] = entry
        # Mirror to Prometheus (Phase D-3). No-op when the optional
        # prometheus_client dep isn't installed. We mirror here rather
        # than at every collector site so a new collector source
        # automatically gets metric coverage by virtue of using the
        # standard diagnostic path.
        try:
            from utils import map_metrics
            map_metrics.record_collect(source, yielded, reason_if_zero or "unknown")
        except ImportError:
            pass

    def _timed_collect(self, source: str, fn, *args, **kwargs):
        """Run `fn` and record its wall-time latency under `source`.

        The collector function still calls `_record_diagnostic` itself; this
        wrapper just stamps `latency_ms` into the diagnostic dict before AND
        after, so the value survives the diagnostic's overwrite. On exception,
        records a synthetic `unreachable` diagnostic with the latency that was
        spent before the exception, then re-raises.
        """
        start = time.perf_counter()
        # Pre-stamp so _record_diagnostic preserves it via the existing-entry
        # carry-over; if the source raises before recording, _collect_locked's
        # except block catches and we still have the timing.
        self._source_diagnostics[source] = {"latency_ms": 0}
        try:
            result = fn(*args, **kwargs)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            entry = self._source_diagnostics.get(source, {})
            entry["latency_ms"] = elapsed_ms
            self._source_diagnostics[source] = entry
            return result
        except Exception:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            entry = self._source_diagnostics.get(source, {})
            entry.setdefault("attempted", 0)
            entry.setdefault("yielded", 0)
            entry.setdefault("reason_if_zero", "unreachable")
            entry.setdefault("notes", "exception during collect")
            entry["latency_ms"] = elapsed_ms
            self._source_diagnostics[source] = entry
            raise

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

    def _apply_external_bulk_bbox(
        self,
        features: List[Dict[str, Any]],
        source_name: str,
    ) -> List[Dict[str, Any]]:
        """Drop out-of-bbox external-bulk features at ingestion.

        External-bulk firehoses (meshcore_public, aredn_worldmap,
        public_fallback) ship the entire planet. On a Pi-class fleet box
        that has no RF reach to 99% of those nodes, the result is a
        multi-GB node_history.db and a slow /api/nodes/geojson cold
        path. Filtering at ingestion to a configurable bbox cuts
        external-bulk volume by 10-50× without touching local-radio
        sources.

        Bbox resolution happens once per collect() cycle (cached on
        self._cached_external_bulk_bbox). None bbox = filter disabled,
        features pass through unchanged. Features missing coordinates
        are dropped too — without a position we can't say they're
        operator-relevant.

        Drop counts surface in /api/status.directory.bbox_filter for
        operator-side observability.
        """
        if self._cached_external_bulk_bbox is None:
            return features
        from utils.geo_filter import is_within_bbox
        bbox = self._cached_external_bulk_bbox
        kept: List[Dict[str, Any]] = []
        dropped = 0
        for f in features:
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates") if isinstance(geom, dict) else None
            # GeoJSON convention: [lon, lat] (longitude first).
            if not (isinstance(coords, (list, tuple)) and len(coords) >= 2):
                dropped += 1
                continue
            lon, lat = coords[0], coords[1]
            if is_within_bbox(lat, lon, bbox):
                kept.append(f)
            else:
                dropped += 1
        if dropped:
            self._bbox_dropped_counts[source_name] = (
                self._bbox_dropped_counts.get(source_name, 0) + dropped
            )
        return kept

    @staticmethod
    def _tag_source_origin(features: List[Dict[str, Any]], origin: str) -> List[Dict[str, Any]]:
        """Stamp `properties.source_origin` on every feature in-place.

        Drives the directory-table tiered retention (Issue #49):
        external-bulk origins (meshcore_public, aredn_worldmap,
        mqtt_global) age out at 7d; locally-RX'd origins (local_radio,
        rns_path_table, aredn_local, mqtt_local) age out at 30d.

        The unified_tracker source produces a mix — its features can be
        either Meshtastic (local_radio) or RNS (rns_path_table); that
        method tags itself per-feature based on `properties.network`.
        Other sources are uniform per call site.

        First-tag-wins: if a feature already carries a source_origin (set
        by a higher-trust collector earlier this cycle), don't overwrite.
        """
        for f in features:
            props = f.get("properties")
            if not isinstance(props, dict):
                continue
            if not props.get("source_origin"):
                props["source_origin"] = origin
        return features

    def _collect_locked(self) -> Dict[str, Any]:
        """Actual collection body. Caller MUST hold self._collect_lock."""
        # Reset per-call state so diagnostics/nodes_without_position reflect THIS run only.
        self._nodes_without_position = []
        self._source_diagnostics = {}
        # Reset bbox-filter counters so /api/status reflects the most
        # recent cycle. Resolve the bbox once per collect() — the
        # operator can change settings or operator_position.json at any
        # time; re-checking each cycle keeps the filter fresh without
        # making every feature loop touch the filesystem.
        self._bbox_dropped_counts = {}
        self._federated_skipped_persistence_count = 0
        try:
            from utils.geo_filter import load_external_bulk_bbox
            self._cached_external_bulk_bbox = load_external_bulk_bbox(self._settings)
        except Exception as e:
            logger.debug(f"external-bulk bbox resolution failed: {e}")
            self._cached_external_bulk_bbox = None
        self._cached_external_bulk_bbox_ts = time.time()

        features: Dict[str, Dict] = {}  # id -> feature (dedup by id)
        collect_start = time.perf_counter()

        # Source 0: UnifiedNodeTracker (richest data — includes RNS + Meshtastic)
        # This is the same data source the topology view uses (378 nodes).
        # It includes nodes from RNS path table, meshtasticd, and gateway bridge.
        # Tagging is per-feature inside _collect_unified_tracker (mixed RNS + Meshtastic).
        tracker_unified_features = self._timed_collect("unified_tracker", self._collect_unified_tracker)
        for f in tracker_unified_features:
            fid = f["properties"].get("id", "")
            if fid:
                features[fid] = f

        # Source 1: meshtasticd TCP
        tcp_features = self._tag_source_origin(
            self._timed_collect("meshtasticd", self._collect_meshtasticd),
            "local_radio",
        )
        for f in tcp_features:
            fid = f["properties"].get("id", "")
            if fid:
                features[fid] = f

        # Source 1.5: Direct USB radio (when meshtasticd not running)
        # Only try this if TCP returned nothing (avoids double-connection)
        direct_radio_features = []
        if not tcp_features:
            direct_radio_features = self._tag_source_origin(
                self._timed_collect("direct_radio", self._collect_direct_radio),
                "local_radio",
            )
            for f in direct_radio_features:
                fid = f["properties"].get("id", "")
                if fid:
                    features[fid] = f

        # Source 2: MQTT subscriber (if running). Local subscriber → mqtt_local
        # tier; the MQTT region/global firehose lives behind the threshold-gated
        # public_fallback source below.
        mqtt_features = self._tag_source_origin(
            self._timed_collect("mqtt", self._collect_mqtt),
            "mqtt_local",
        )
        for f in mqtt_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f
            elif fid and fid in features:
                # Merge: prefer newer data
                self._merge_feature(features[fid], f)

        # Source 3: Node tracker cache files (locally-cached, replayed on cold start)
        tracker_features = self._tag_source_origin(
            self._timed_collect("node_tracker", self._collect_node_tracker),
            "node_tracker",
        )
        for f in tracker_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 4: AREDN mesh network (local scan via sysinfo API)
        aredn_features = self._tag_source_origin(
            self._timed_collect("aredn", self._collect_aredn),
            "aredn_local",
        )
        for f in aredn_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 4.5: AREDN worldmap (public CSV, always runs when enabled —
        # NOT threshold-gated. Provides geographic context alongside local
        # Meshtastic/RNS, not a fill-when-sparse fallback.)
        aredn_worldmap_features = self._apply_external_bulk_bbox(
            self._tag_source_origin(
                self._timed_collect("aredn_worldmap", self._collect_aredn_worldmap),
                "aredn_worldmap",
            ),
            "aredn_worldmap",
        )
        for f in aredn_worldmap_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 5: RNS direct query (from rnsd path table)
        rns_direct_features = self._tag_source_origin(
            self._timed_collect("rns_direct", self._collect_rns_direct),
            "rns_path_table",
        )
        for f in rns_direct_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 5.5: MeshCore public map (always runs when enabled — NOT gated by
        # feature count threshold, because this is the primary MeshCore visibility path).
        meshcore_public_features = self._apply_external_bulk_bbox(
            self._tag_source_origin(
                self._timed_collect("meshcore_public", self._collect_meshcore_public),
                "meshcore_public",
            ),
            "meshcore_public",
        )
        for f in meshcore_public_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 6: Public data fallbacks (conditional — only when local data sparse)
        public_features = self._apply_external_bulk_bbox(
            self._tag_source_origin(
                self._timed_collect(
                    "public_fallback",
                    self._collect_public_fallbacks,
                    current_feature_count=len(features),
                ),
                "public_fallback",
            ),
            "public_fallback",
        )
        for f in public_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 7: Last-known cache (fill gaps). node_tracker tier — the
        # cache is whatever this box has previously seen locally.
        if not features:
            cache_features = self._tag_source_origin(self._load_cache(), "node_tracker")
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
        # Issue #49: surface the persistent node directory in the geojson
        # response so the sidebar can count per-protocol from "what the box
        # knows about" rather than "what's currently positioned". Wrapped:
        # geojson must never 500 because the directory query hiccupped.
        directory_stats = None
        if self._history is not None:
            try:
                directory_stats = self._history.get_directory_stats()
            except Exception as e:
                logger.debug(f"directory stats lookup failed for geojson: {e}")
                directory_stats = None

        # Federation: fold federated peer entries into local features +
        # nodes_without_position. Local always wins on (network, id) clash.
        # Returns the federation summary block for the geojson properties.
        federation_block = self._merge_federation(features, self._nodes_without_position)

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
                "directory": directory_stats,
                "federation": federation_block,
                "online_threshold_minutes": self.get_online_threshold_seconds() // 60,
            }
        }

        # Mirror node inventory to Prometheus (Phase D-3).
        try:
            from utils import map_metrics
            by_network: Dict[str, int] = {}
            for entry in self._nodes_without_position:
                net = entry.get("network", "unknown")
                by_network[net] = by_network.get(net, 0) + 1
            map_metrics.set_node_inventory(
                with_position=len(features),
                without_position_by_network=by_network,
            )
            if directory_stats:
                map_metrics.set_directory_inventory(
                    total=directory_stats.get("total", 0),
                    by_network=directory_stats.get("by_network", {}),
                )
        except ImportError:
            pass

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

        # Per-source latency summary — lets operators see which source is the
        # tall pole on a given box without having to instrument by hand.
        total_ms = int((time.perf_counter() - collect_start) * 1000)
        latency_parts = []
        for src in (
            "unified_tracker", "meshtasticd", "direct_radio", "mqtt",
            "node_tracker", "aredn", "aredn_worldmap", "rns_direct",
            "meshcore_public", "public_fallback",
        ):
            diag = self._source_diagnostics.get(src) or {}
            if "latency_ms" not in diag:
                continue
            note = diag.get("notes") or ""
            cached = "cached" in note.lower() or "stale" in note.lower()
            latency_parts.append(
                f"{src}:{diag['latency_ms']}ms{' (cached)' if cached else ''}"
            )
        logger.info(f"MapDataCollector latencies: total={total_ms}ms — {', '.join(latency_parts)}")

        # Cache result
        self._cached_geojson = geojson
        self._last_collect = time.time()
        self._save_cache(geojson)

        # Record to history database. Pass BOTH positioned features AND
        # position-less nodes (synthesized into geometry-less features)
        # so the directory table (Issue #49) gets a row per node we
        # heard about — including MeshCore adverts and RNS announces
        # that carry no GPS by protocol design. The observation-stream
        # writer in record_observations skips position-less rows itself
        # (it requires lat/lon for the time-series); the directory
        # writer handles them via NULL last_lat/last_lon.
        #
        # Federation memory-only (node count optimization §B): peer-
        # injected features merged by _merge_federation are visible in
        # the response but must NOT flow into NodeHistoryDB. Each fleet
        # box otherwise persists the union of every peer's directory,
        # producing N-way multiplicative inflation. Cold-start sees no
        # federation rows in /api/nodes/directory for ~60s until the
        # first poll; acceptable per design decision.
        if self._history:
            history_features = []
            for f in geojson["features"]:
                props = f.get("properties") or {}
                if props.get("federated") or props.get("federated_from"):
                    self._federated_skipped_persistence_count += 1
                    continue
                history_features.append(f)
            for entry in self._nodes_without_position:
                nid = entry.get("id")
                if not nid:
                    continue
                # Same federation skip on the position-less path —
                # _merge_federation deposits stubs here too.
                if entry.get("federated") or entry.get("federated_from"):
                    self._federated_skipped_persistence_count += 1
                    continue
                # Tier-aware origin tagging mirrors the per-source path:
                # MeshCore via the unified tracker is local-RX (gateway
                # bridge), RNS announces are rns_path_table, everything
                # else falls into local_radio. Position-less features
                # never came through one of the threshold-gated external
                # collectors, so they don't get external_bulk tags.
                net = (entry.get("network") or "").lower()
                if net == "rns":
                    origin = "rns_path_table"
                else:
                    origin = "local_radio"
                history_features.append({
                    "type": "Feature",
                    "geometry": {},  # explicit no-position
                    "properties": {
                        "id": nid,
                        "name": entry.get("name", nid),
                        "network": entry.get("network", "unknown"),
                        "is_online": entry.get("is_online", False),
                        "source_origin": origin,
                    },
                })
            if history_features:
                try:
                    self._history.record_observations(history_features)
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
                    # Tier-aware tagging for the directory (Issue #49):
                    # the unified tracker mixes RNS + Meshtastic; route
                    # each by its protocol so retention applies correctly.
                    if not props.get("source_origin"):
                        net = (props.get("network") or "").lower()
                        if net == "meshtastic":
                            props["source_origin"] = "local_radio"
                        elif net == "rns":
                            props["source_origin"] = "rns_path_table"
                        else:
                            # MeshCore via tracker is local-RX (gateway bridge);
                            # everything else falls into local_radio bucket.
                            props["source_origin"] = "local_radio"

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

    # _collect_meshtasticd lives in MeshtasticDataCollectorMixin (_map_collector_meshtastic.py).

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
                    "source_origin": "operator_positions",
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

    def _merge_federation(self,
                          features: Dict[str, Dict],
                          position_less: List[Dict]) -> Dict[str, Any]:
        """Fold federated peer entries into local features + position_less.

        Local always wins on (network, node_id) collisions — federated
        entries from peers only fill gaps the local box doesn't already
        know about. Federated features carry `federated: True` and
        `federated_from: <peer>` so the frontend can style/filter them.

        Returns a federation summary block for geojson properties:
          - enabled (bool)
          - peers (list of configured peer hostnames)
          - peer_status (list of dicts: hostname, ok, last_sync, ...)
          - last_sync (most recent successful peer sync ts)
          - last_attempt (most recent attempt ts)
          - by_network (dict: network -> count of federated-only entries)
          - total / with_position / without_position (federated-only counts)

        Disabled (no federation collector configured) returns
        `{"enabled": False, ...}` with empty fields so the frontend can
        unconditionally read the block.
        """
        empty_block = {
            "enabled": False, "peers": [], "peer_status": [],
            "last_sync": None, "last_attempt": None, "by_network": {},
            "total": 0, "with_position": 0, "without_position": 0,
        }
        if not self._federation:
            return empty_block

        try:
            snap = self._federation.get_snapshot()
        except Exception as e:
            logger.debug(f"federation snapshot failed: {e}")
            return empty_block

        # Index local entries by (network, id) with source_origin priority,
        # so a federated peer carrying a HIGHER-trust origin can override a
        # lower-trust local entry. Without this, a node first heard by the
        # local meshcore_public collector (priority 30) blocks a federated
        # peer's local_radio observation (priority 100) for the same hash —
        # which defeats the purpose of cross-box federation when a sister
        # box is the only one with the radio. "Local always wins" stays
        # intact at equal priority (the original guarantee against peer
        # noise); a higher-priority federated entry strictly upgrades.
        # Index entry: (priority, feature_key | None, position_less_index | None).
        local_index: Dict[tuple, tuple] = {}
        for fkey, f in features.items():
            props = f.get("properties") or {}
            net = props.get("network")
            nid = props.get("id")
            if net and nid:
                origin = props.get("source_origin")
                local_index[(net, nid)] = (_origin_priority(origin), fkey, None)
        for idx, entry in enumerate(position_less):
            net = entry.get("network")
            nid = entry.get("id")
            if net and nid and (net, nid) not in local_index:
                origin = entry.get("source_origin")
                local_index[(net, nid)] = (_origin_priority(origin), None, idx)

        by_network: Dict[str, int] = {}
        with_pos = 0
        without_pos = 0

        # Track position_less indices to delete after the loop (deleting in-flight
        # would shift indices and break the second `enumerate` pass above).
        position_less_drop: set = set()

        for (net, nid), entry in snap.by_node.items():
            local = local_index.get((net, nid))
            if local is not None:
                local_priority, local_fkey, local_pl_idx = local
                fed_priority = _origin_priority(entry.get("source_origin"))
                if fed_priority <= local_priority:
                    continue  # local-wins — skip federated copy (original behavior)
                # Federated peer carries a higher-trust origin: drop the
                # local stub so the federated entry can take its place.
                if local_fkey is not None:
                    features.pop(local_fkey, None)
                elif local_pl_idx is not None:
                    position_less_drop.add(local_pl_idx)
            by_network[net] = by_network.get(net, 0) + 1

            lat = entry.get("lat")
            lon = entry.get("lon")
            seen_by = entry.get("seen_by_peers") or [entry.get("federated_from")]
            if self._is_valid_coordinate(lat, lon):
                # Federated-only feature: render as map marker. Use a
                # namespaced dict key to avoid clashing with local id-keys.
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [
                            lon, lat,
                            entry.get("altitude") if entry.get("altitude") is not None else 0,
                        ],
                    },
                    "properties": {
                        "id": entry["id"],
                        "name": entry.get("name") or entry["id"],
                        "network": entry["network"],
                        "role": entry.get("role", ""),
                        "hardware": entry.get("hardware", ""),
                        "last_seen": entry.get("last_seen"),
                        "source": "federation",
                        "source_origin": entry.get("source_origin") or "",
                        "federated": True,
                        "federated_from": entry.get("federated_from"),
                        "seen_by_peers": seen_by,
                        # Don't claim freshness — federation only knows
                        # what the peer claimed; let UI mark "stale" by
                        # rendering with reduced opacity.
                        "is_online": False,
                        "is_local": False,
                        "is_gateway": False,
                    },
                }
                features[f"federated:{net}:{nid}"] = feature
                with_pos += 1
            else:
                position_less.append({
                    "id": entry["id"],
                    "name": entry.get("name") or entry["id"],
                    "network": entry["network"],
                    "role": entry.get("role", ""),
                    "hardware": entry.get("hardware", ""),
                    "last_seen": entry.get("last_seen"),
                    "source_origin": entry.get("source_origin") or "",
                    "federated": True,
                    "federated_from": entry.get("federated_from"),
                    "seen_by_peers": seen_by,
                })
                without_pos += 1

        # Drop position_less stubs that got overridden by a higher-priority
        # federated entry (collected during the loop to avoid in-flight
        # index shifts).
        if position_less_drop:
            position_less[:] = [
                p for i, p in enumerate(position_less) if i not in position_less_drop
            ]

        peer_status_list = []
        for s in snap.peer_status.values():
            peer_status_list.append({
                "hostname": s.hostname,
                "ok": s.ok,
                "last_sync": s.last_sync,
                "last_attempt": s.last_attempt,
                "last_error": s.last_error,
                "last_count": s.last_count,
                "last_latency_ms": s.last_latency_ms,
                "consecutive_failures": s.consecutive_failures,
            })

        return {
            "enabled": True,
            "peers": list(self._federation.peers),
            "peer_status": peer_status_list,
            "last_sync": snap.last_sync,
            "last_attempt": snap.last_attempt,
            "by_network": by_network,
            "total": with_pos + without_pos,
            "with_position": with_pos,
            "without_position": without_pos,
        }

    # _collect_via_http, _collect_via_tcp_interface, _collect_direct_radio,
    # _parse_tcp_node, _extract_node_info_without_position, _collect_via_cli,
    # _parse_meshtastic_info live in MeshtasticDataCollectorMixin (_map_collector_meshtastic.py).

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

    # AREDN methods (_collect_aredn, _collect_aredn_worldmap, _get_aredn_node_ip,
    # _aredn_node_to_feature) live in ARENDataCollectorMixin (_map_collector_aredn.py).
    # MeshCore public-map methods (_collect_meshcore_public,
    # _fetch_meshcore_public_uncached, _parse_meshcore_public_node) live in
    # MeshCorePublicCollectorMixin (_map_collector_meshcore.py).
    # RNS methods (_collect_rns_direct, _load_rns_position_cache,
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

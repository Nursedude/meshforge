"""
Node History - SQLite-based node position and state tracking over time.

Records node observations from the MapDataCollector, enabling:
- Position playback on the live map (node trajectory)
- Historical network topology views
- Online/offline patterns over time
- Network growth tracking

Usage:
    from utils.node_history import NodeHistoryDB

    db = NodeHistoryDB()  # Uses default path
    db.record_observations(geojson_features)

    # Get trajectory for a node
    trajectory = db.get_trajectory("!ba4bf9d0", hours=24)

    # Get network snapshot at a point in time
    snapshot = db.get_snapshot(timestamp=time.time() - 3600)
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.db_helpers import connect_tuned
from utils.paths import get_real_user_home
from utils.node_history_config import (
    DEFAULT_RETENTION_SECONDS,
    MIN_RECORD_INTERVAL,
    DEFAULT_HEARTBEAT_SECONDS,
    _LAT_LON_PRECISION,
    DEFAULT_DIRECTORY_RETENTION_LOCAL,
    DEFAULT_DIRECTORY_RETENTION_EXTERNAL,
    DEFAULT_DIRECTORY_MAX_ROWS,
    DEFAULT_DIRECTORY_SIZE_ALARM_BYTES,
    DEFAULT_PRUNE_BATCH_LIMIT,
    DEFAULT_PRUNE_MAX_BATCHES_PER_CYCLE,
    DEFAULT_VACUUM_INTERVAL_SECONDS,
    DEFAULT_VACUUM_DB_SIZE_THRESHOLD_BYTES,
    EXTERNAL_BULK_ORIGINS,
    _ORIGIN_PRIORITY,
    _PROTOCOL_META_MAX_BYTES,
    _origin_priority,
    _should_skip_observation,
    NodeObservation,
)

logger = logging.getLogger(__name__)


class NodeHistoryDB:
    """SQLite database for node position and state history.

    Records node observations over time and provides query methods for
    playback, trajectories, and network snapshots.

    Concurrency contract: DB access (`self._lock`) is thread-safe and the
    WAL-backed read queries are safe to call from any thread. The write path
    `record_observations` is SINGLE-WRITER — its throttle maps
    (`_last_recorded`/`_last_value`) and `_maybe_prune`'s `_last_prune_ts` are
    read-modified OUTSIDE `self._lock`, so it must be driven by exactly one
    writer at a time (today: the collector cycle, serialized by
    `_collect_lock`). A second concurrent writer would race the throttle maps.
    """

    def __init__(self, db_path: Optional[Path] = None,
                 retention_seconds: int = DEFAULT_RETENTION_SECONDS,
                 heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
                 directory_retention_local: int = DEFAULT_DIRECTORY_RETENTION_LOCAL,
                 directory_retention_external: int = DEFAULT_DIRECTORY_RETENTION_EXTERNAL,
                 directory_max_rows: int = DEFAULT_DIRECTORY_MAX_ROWS,
                 prune_batch_limit: int = DEFAULT_PRUNE_BATCH_LIMIT,
                 prune_max_batches_per_cycle: int = DEFAULT_PRUNE_MAX_BATCHES_PER_CYCLE):
        """Initialize node history database.

        Args:
            db_path: Path to SQLite database file.
                     Defaults to ~/.local/share/meshforge/node_history.db
            retention_seconds: How long to keep observations (default 48h).
                Trajectories rarely matter beyond the last day; the `nodes`
                directory table answers the "did we ever hear this node"
                question on a longer horizon.
            heartbeat_seconds: Skip insert when (lat, lon, network) match the
                last recorded value AND we're inside this window. 0 disables
                the value-dedup path (legacy time-only throttle).
            directory_retention_local: Retention for locally-RX'd directory
                rows (own radios, RNS path table, etc.). Default 30d.
            directory_retention_external: Retention for external-bulk
                directory rows (meshcore_public, aredn_worldmap, mqtt_global).
                Default 7d. Bounds firehose sources independently.
            directory_max_rows: Hard count cap on the `nodes` directory
                table. Default 50_000. LRU eviction by last_seen kicks in
                whenever count exceeds the cap.
        """
        if db_path is None:
            db_path = get_real_user_home() / ".local" / "share" / "meshforge" / "node_history.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self.retention_seconds = retention_seconds
        self._heartbeat_seconds = max(0, heartbeat_seconds)
        self.directory_retention_local = max(0, directory_retention_local)
        self.directory_retention_external = max(0, directory_retention_external)
        self.directory_max_rows = max(0, directory_max_rows)
        # 0 disables the per-cycle cap (legacy unbounded prune).
        self.prune_batch_limit = max(0, prune_batch_limit)
        # Multi-batch loop cap; 1 = legacy single-batch-per-cycle.
        self.prune_max_batches_per_cycle = max(1, prune_max_batches_per_cycle)
        self._lock = threading.Lock()
        self._last_recorded: Dict[str, float] = {}  # node_id -> last record time
        # Last (round(lat,6), round(lon,6), network) per node. Pruned in
        # lockstep with _last_recorded.
        self._last_value: Dict[str, Tuple[float, float, str]] = {}
        # Hourly auto-prune cadence. Without this, the DB+WAL grow unbounded
        # — see Issue #44 follow-up where a 14 GB WAL accumulated over 4 days
        # and wedged the service in `jbd2_log_wait_commit` on next startup.
        # 0 disables; tests that want deterministic timing override.
        self._last_prune_ts: float = 0.0
        self._prune_interval_seconds: int = 3600
        # TTL cache for get_stats(). On large observation tables (Issue #52,
        # 3.5M rows on moc1) the COUNT(*)/COUNT(DISTINCT) full scans cost
        # ~14s each on Pi-class SD storage and held self._lock long enough
        # to wedge every other API caller. Stats are observability-only so
        # 60s staleness is acceptable.
        self._stats_cache: Optional[Dict[str, Any]] = None
        self._stats_cache_expires: float = 0.0
        # Bumped 60s → 300s (2026-05-13): status endpoint is observability,
        # not correctness; 5-min staleness is fine and cuts the cache-miss
        # rate by 5×, which directly translates to fewer 14s wall-clock
        # hits on /api/status under disk contention.
        self._stats_cache_ttl: float = 300.0
        # Directory stats cache (added 2026-05-13). Same shape as stats
        # cache. Without this, `/api/status` ran 5 full table scans on
        # the `nodes` table every request, sequentially, and could
        # exceed HTTP timeout under SD contention on fat-DB hosts.
        self._directory_stats_cache: Optional[Dict[str, Any]] = None
        self._directory_stats_cache_expires: float = 0.0
        self._directory_stats_cache_ttl: float = 300.0
        # Directory serialization size monitor (Issue #64). Updated by the
        # HTTP layer after each /api/nodes/directory serialize so
        # get_directory_stats() can surface a size-budget alarm. None
        # until the endpoint has been served at least once. Python int/
        # None assignment is GIL-atomic — no lock needed for these
        # observability-only fields.
        self._last_directory_bytes_raw: Optional[int] = None
        self._last_directory_bytes_compressed: Optional[int] = None
        self._last_directory_serialized_ts: Optional[float] = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Open a tuned SQLite connection via the shared helper.

        See utils.db_helpers.connect_tuned for the WAL + synchronous=NORMAL
        + journal_size_limit + busy_timeout policy. Centralizing here
        ensures every MeshForge SQLite consumer gets the same treatment
        and one place enforces the post-mortem of the 2026-04-26 fleet
        wedge.
        """
        return connect_tuned(self.db_path)

    def _init_db(self) -> None:
        """Create tables and indexes if they don't exist."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS node_observations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        node_id TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        latitude REAL NOT NULL,
                        longitude REAL NOT NULL,
                        altitude REAL,
                        snr REAL,
                        rssi INTEGER,
                        battery INTEGER,
                        is_online INTEGER DEFAULT 1,
                        network TEXT DEFAULT 'meshtastic',
                        hardware TEXT DEFAULT '',
                        role TEXT DEFAULT '',
                        via_mqtt INTEGER DEFAULT 0,
                        name TEXT DEFAULT ''
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_obs_node_id
                    ON node_observations(node_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_obs_timestamp
                    ON node_observations(timestamp)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_obs_node_time
                    ON node_observations(node_id, timestamp)
                """)
                # Migration: backfill the rssi column on pre-existing tables.
                # CREATE IF NOT EXISTS is a no-op on an old DB, so ALTER adds
                # the column (idempotent — guarded by table_info). Existing
                # rows get NULL rssi; new observations capture it.
                obs_cols = {r[1] for r in
                            conn.execute("PRAGMA table_info(node_observations)")}
                if "rssi" not in obs_cols:
                    conn.execute(
                        "ALTER TABLE node_observations ADD COLUMN rssi INTEGER")
                # Directory table — one row per (network, node_id). Long-retention,
                # tier-aware (Issue #49). Survives observation-stream eviction so
                # nodes "stay cached" between long quiet stretches.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS nodes (
                        network        TEXT NOT NULL,
                        node_id        TEXT NOT NULL,
                        first_seen     REAL NOT NULL,
                        last_seen      REAL NOT NULL,
                        last_lat       REAL,
                        last_lon       REAL,
                        last_altitude  REAL,
                        name           TEXT DEFAULT '',
                        role           TEXT DEFAULT '',
                        hardware       TEXT DEFAULT '',
                        source_origin  TEXT DEFAULT '',
                        protocol_meta  TEXT DEFAULT '',
                        obs_count      INTEGER DEFAULT 1,
                        PRIMARY KEY (network, node_id)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_nodes_last_seen
                    ON nodes(last_seen)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_nodes_network
                    ON nodes(network)
                """)
                # Tiny key/value table for persisted maintenance state
                # (currently: last_vacuum_ts for the weekly gated VACUUM
                # in _maybe_prune). MF013-compliant — extension inside
                # the existing DB, not a new DBSpec.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS _meta (
                        key   TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                conn.commit()
            finally:
                conn.close()

    def _meta_get_float(self, key: str, default: float = 0.0) -> float:
        """Read a float from the _meta key/value table.

        Returns ``default`` when the key is missing, the row's value is
        NULL/empty, or the stored string doesn't parse as a float.
        Operations on _meta don't share the prune lock — callers that
        need atomicity must acquire ``self._lock`` themselves.
        """
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM _meta WHERE key = ?", (key,),
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as e:
            logger.debug(f"_meta read failed for {key}: {e}")
            return default
        if not row or row[0] is None:
            return default
        try:
            return float(row[0])
        except (TypeError, ValueError):
            return default

    def _meta_set_float(self, key: str, value: float) -> None:
        """Persist a float to the _meta key/value table."""
        try:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
                    (key, repr(float(value))),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as e:
            logger.debug(f"_meta write failed for {key}: {e}")

    @staticmethod
    def _build_directory_row(feature: Dict[str, Any], now: float) -> Optional[
        Tuple[str, str, float, Optional[float], Optional[float],
              Optional[float], str, str, str, str, str]
    ]:
        """Distill one GeoJSON feature into a directory-table row tuple.

        Returns None if the feature lacks (network, node_id) — those are
        the only required fields. Position is optional so MeshCore adverts
        and RNS announces still produce a directory row with NULL lat/lon.

        last_seen sourcing (Issue #50 — F7): for external-bulk origins
        (meshcore_public, aredn_worldmap, mqtt_global, public_fallback)
        we stamp last_seen from the feature's upstream `last_heard` when
        present and >0. Combined with the MAX-monotonic ON CONFLICT clause,
        this means re-publishing an unchanged upstream record does not
        bump the tier clock, so the 7d external retention can actually fire.
        Local-origin features always use `now` (they were observed by us).

        Tuple shape matches the ON CONFLICT UPSERT below:
          (network, node_id, last_seen, last_lat, last_lon, last_altitude,
           name, role, hardware, source_origin, protocol_meta_json)
        """
        props = feature.get("properties", {}) or {}
        node_id = props.get("id", "") or ""
        if not node_id:
            return None
        network = props.get("network", "meshtastic") or "meshtastic"
        source_origin = props.get("source_origin", "") or ""

        last_seen = now
        if source_origin in EXTERNAL_BULK_ORIGINS:
            upstream = props.get("last_heard")
            try:
                upstream_ts = float(upstream) if upstream is not None else 0.0
            except (TypeError, ValueError):
                upstream_ts = 0.0
            # Cap at `now`: a future-dated upstream timestamp would
            # poison the prune horizon (row never ages out). Also gate
            # on >0 so missing/zero stamps fall back to now.
            if 0.0 < upstream_ts <= now:
                last_seen = upstream_ts

        # Position is optional in the directory.
        last_lat: Optional[float] = None
        last_lon: Optional[float] = None
        last_altitude: Optional[float] = None
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") if isinstance(geom, dict) else None
        if coords and len(coords) >= 2:
            try:
                last_lon = float(coords[0])
                last_lat = float(coords[1])
            except (TypeError, ValueError):
                last_lon = last_lat = None
            if len(coords) >= 3:
                try:
                    last_altitude = float(coords[2])
                except (TypeError, ValueError):
                    last_altitude = None

        # protocol_meta — operator-supplied passthrough. The map collector
        # may stuff per-protocol enrichment here (MeshCore flags + pubkey,
        # AREDN sysinfo blob, RNS hops/iface). Cap at 4 KB to keep one
        # misbehaving source from writing megabyte rows.
        meta = props.get("protocol_meta")
        if meta is None:
            meta_json = ""
        else:
            try:
                meta_json = json.dumps(meta, default=str, separators=(",", ":"))
            except (TypeError, ValueError):
                meta_json = ""
        if len(meta_json.encode("utf-8")) > _PROTOCOL_META_MAX_BYTES:
            # Drop oversized blobs entirely — preserving a truncated JSON
            # produces invalid syntax, and the directory row's other
            # columns already carry the operator-relevant fields.
            meta_json = ""

        return (
            network,
            node_id,
            last_seen,
            last_lat,
            last_lon,
            last_altitude,
            props.get("name", "") or "",
            props.get("role", "") or "",
            props.get("hardware", "") or "",
            source_origin,
            meta_json,
        )

    def _apply_features_to_directory(self, features: List[Dict[str, Any]],
                                     now: float) -> int:
        """UPSERT every feature into the `nodes` directory table.

        Sticky-promotion: source_origin is overwritten only when the
        incoming origin has equal-or-higher priority. A node first seen
        via meshcore_public stays in the 7d tier until the local radio
        actually hears it, at which point the row promotes to local_radio
        (30d tier).

        Position fields update only when the incoming feature carries a
        position. A position-less observation (MeshCore advert) doesn't
        wipe out a previously-recorded GPS fix.

        Returns:
            Number of rows touched (insert + update). Cheap stat for
            telemetry; does not affect the function's primary contract.
        """
        if not features:
            return 0

        # Precompute priority per row in Python — simpler than nesting a
        # CASE WHEN tree in SQL for every known origin. Tuple shape
        # matches the executemany INSERT below.
        rows: List[Tuple[Any, ...]] = []
        for feat in features:
            built = self._build_directory_row(feat, now)
            if built is None:
                continue
            (network, node_id, last_seen, last_lat, last_lon, last_altitude,
             name, role, hardware, source_origin, protocol_meta) = built
            new_priority = _origin_priority(source_origin)
            rows.append((
                network, node_id,
                now,                 # first_seen for INSERT — when WE first
                                     # learned about this node. last_seen may
                                     # be older (upstream stamp), and that's OK.
                last_seen,           # last_seen — upstream-aware (Issue #50)
                last_lat, last_lon, last_altitude,
                name, role, hardware,
                source_origin, protocol_meta,
                new_priority,        # used by ON CONFLICT branch
            ))
        if not rows:
            return 0

        # Single batched UPSERT. ON CONFLICT branch:
        #   - last_seen advances monotonically: MAX(existing, incoming).
        #     External-bulk sources republish their entire dataset every
        #     cycle; without MAX, the row's last_seen would rewrite to NOW
        #     each time and the 7d external retention tier could never
        #     fire (Issue #50 / F7). Combined with `_build_directory_row`
        #     stamping last_seen from the feature's upstream `last_heard`
        #     for external-bulk origins, repeated republishes leave the
        #     tier clock alone.
        #   - position / metadata fields update with COALESCE so a
        #     position-less heartbeat doesn't wipe a known GPS fix.
        #   - source_origin updates only when the incoming origin has
        #     equal-or-higher priority than the row's existing origin.
        #     We compute the existing priority in SQL via a CASE expression
        #     over the known origin tags (kept short — unknown origins map
        #     to 10, the same fallback as _origin_priority()).
        #   - obs_count is an UPSERT counter: ticks on every record_
        #     observations call for this (network, node_id), including
        #     republishes. NOT a count of unique observations. Real
        #     observation counts live in the node_observations table
        #     (joined window-bound by aggregators). Consumers reaching
        #     for "how many times did we observe this node" should query
        #     node_observations, not nodes.obs_count. Pattern-audit
        #     Finding #3 (2026-05-19) — node_rollups previously read
        #     this field thinking it was a real observation count, and
        #     the most_reliable leaderboard inherited the inflation.
        # GENERATED from _ORIGIN_PRIORITY so the SQL existing-row priority can
        # never drift from the Python new-row priority (_origin_priority) — two
        # independent hardcodes WILL diverge (honest_failure_modes #5). Keys are
        # fixed identifiers (no quotes → no injection); the None key can't be a
        # SQL `WHEN` and correctly falls to ELSE 10 (a NULL source_origin never
        # equality-matches). ELSE 10 mirrors _origin_priority's unknown fallback.
        # (QA deferred low/perf, 2026-07-06.)
        existing_case = (
            "CASE nodes.source_origin "
            + " ".join(
                f"WHEN '{origin}' THEN {prio}"
                for origin, prio in _ORIGIN_PRIORITY.items()
                if origin is not None
            )
            + " ELSE 10 END"
        )
        sql = f"""
            INSERT INTO nodes (
                network, node_id, first_seen, last_seen,
                last_lat, last_lon, last_altitude,
                name, role, hardware,
                source_origin, protocol_meta, obs_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(network, node_id) DO UPDATE SET
                last_seen = MAX(nodes.last_seen, excluded.last_seen),
                last_lat = COALESCE(excluded.last_lat, nodes.last_lat),
                last_lon = COALESCE(excluded.last_lon, nodes.last_lon),
                last_altitude = COALESCE(excluded.last_altitude, nodes.last_altitude),
                name = CASE WHEN excluded.name != '' THEN excluded.name ELSE nodes.name END,
                role = CASE WHEN excluded.role != '' THEN excluded.role ELSE nodes.role END,
                hardware = CASE WHEN excluded.hardware != '' THEN excluded.hardware ELSE nodes.hardware END,
                source_origin = CASE
                    WHEN ? >= ({existing_case})
                    THEN excluded.source_origin
                    ELSE nodes.source_origin
                END,
                protocol_meta = CASE WHEN excluded.protocol_meta != '' THEN excluded.protocol_meta ELSE nodes.protocol_meta END,
                obs_count = nodes.obs_count + 1
        """

        touched = 0
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(sql, rows)
                conn.commit()
                touched = len(rows)
            except sqlite3.Error as e:
                logger.error(f"Directory UPSERT failed: {e}")
                touched = 0
            finally:
                conn.close()
        return touched

    def record_observations(self, features: List[Dict[str, Any]]) -> int:
        """Record a batch of node observations from GeoJSON features.

        Skips nodes that were recorded less than MIN_RECORD_INTERVAL ago
        to prevent database flooding from rapid collection cycles.

        Also UPSERTs into the long-retention `nodes` directory table
        (Issue #49) — every feature contributes one directory row,
        independent of the observation-stream throttle. Position-less
        features (MeshCore adverts without GPS, RNS announces) DO write
        a directory row with NULL lat/lon. The directory survives the
        observations table's 48h retention and gives the map a stable
        per-node record across long quiet stretches.

        Args:
            features: List of GeoJSON Feature dicts with node properties.
                Optional `properties.source_origin` selects the retention
                tier; missing tags fall through to a generic priority.

        Returns:
            Number of observations actually recorded into the time-series
            table. (Directory writes are not counted here — the directory
            is a separate persistence layer; query get_directory_stats().)
        """
        now = time.time()
        # Apply directory writes first — even features that fail the
        # observation throttle still represent "we heard from this node",
        # and the directory should reflect that.
        self._apply_features_to_directory(features, now)
        to_insert = []

        for feature in features:
            props = feature.get("properties", {})
            geom = feature.get("geometry", {})
            coords = geom.get("coordinates", [])

            if len(coords) < 2:
                continue

            # Coerce coords to float up front. A source cache can hand us a
            # string ("19.4") or None; left raw, round(lat, 6) below raises and
            # aborts the ENTIRE batch (every node's time-series write lost that
            # cycle, swallowed at DEBUG by the caller). Skip only the bad
            # feature — one malformed entry must not blank the whole pipeline
            # (honest_failure_modes: error isolation).
            try:
                lon = float(coords[0])
                lat = float(coords[1])
            except (TypeError, ValueError):
                continue

            node_id = props.get("id", "")
            if not node_id:
                continue

            # Phase 1 SD-survival fix (2026-05-09): skip observation insert
            # for federated and external-bulk features. Directory still
            # upserts via _apply_features_to_directory above. Drops the
            # observation insert rate on federation receivers from ~75k/hr
            # to a few hundred/hr — the firehose was the structural cause
            # of the multi-day DB bloat the multi-batch prune mitigated.
            if _should_skip_observation(props, props.get("source_origin", "")):
                continue

            # Throttle: skip if recorded recently
            last = self._last_recorded.get(node_id, 0)
            if now - last < MIN_RECORD_INTERVAL:
                continue

            # lon/lat were coerced to float above.
            network = props.get("network", "meshtastic")

            # Value-dedup: skip when (lat, lon, network) match the last
            # recorded value AND we're still inside the heartbeat window.
            # Disabled when heartbeat_seconds == 0.
            if self._heartbeat_seconds > 0:
                rounded = (round(lat, _LAT_LON_PRECISION),
                           round(lon, _LAT_LON_PRECISION),
                           network)
                if (self._last_value.get(node_id) == rounded
                        and (now - last) < self._heartbeat_seconds):
                    continue

            to_insert.append((
                node_id,
                now,
                lat,
                lon,
                None,  # altitude not in standard features
                props.get("snr"),
                props.get("rssi"),
                props.get("battery"),
                1 if props.get("is_online", True) else 0,
                network,
                props.get("hardware", ""),
                props.get("role", ""),
                1 if props.get("via_mqtt", False) else 0,
                props.get("name", ""),
            ))
            self._last_recorded[node_id] = now
            if self._heartbeat_seconds > 0:
                self._last_value[node_id] = (
                    round(lat, _LAT_LON_PRECISION),
                    round(lon, _LAT_LON_PRECISION),
                    network,
                )

        # Prune stale entries to prevent unbounded memory growth
        if len(self._last_recorded) > 10000:
            cutoff = now - self.retention_seconds
            self._last_recorded = {
                k: v for k, v in self._last_recorded.items()
                if v > cutoff
            }
            # Mirror the cull on the value cache so it doesn't outgrow.
            self._last_value = {
                k: v for k, v in self._last_value.items()
                if k in self._last_recorded
            }

        if not to_insert:
            self._maybe_prune(now)
            return 0

        with self._lock:
            conn = self._connect()
            try:
                conn.executemany("""
                    INSERT INTO node_observations
                    (node_id, timestamp, latitude, longitude, altitude,
                     snr, rssi, battery, is_online, network, hardware, role,
                     via_mqtt, name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, to_insert)
                conn.commit()
                inserted = len(to_insert)
            except sqlite3.Error as e:
                logger.error(f"Failed to record observations: {e}")
                inserted = 0
            finally:
                conn.close()

        # Run pruning OUTSIDE the insert lock — it's a separate transaction
        # and holding the insert lock longer just slows the next writer.
        self._maybe_prune(now)
        # Invalidate stats caches so the next get_stats / get_directory_stats
        # call sees the rows we just wrote. The queries are cheap
        # (MAX(rowid) + COUNT FROM nodes); freshness is more valuable than
        # the 300s TTL window here. Closes a write-then-read staleness bug
        # caught by tests/test_map_data_collector_diagnostics.py on
        # 2026-05-13 after the cache was first introduced.
        self._invalidate_stats_caches()
        return inserted

    def _invalidate_stats_caches(self) -> None:
        """Expire the stats and directory-stats TTL caches.

        Called after any write that changes what those queries would
        return — see record_observations() and _maybe_prune() (deletes
        also need to bust the cache, otherwise total_observations stays
        stale until TTL expiry).
        """
        self._stats_cache = None
        self._stats_cache_expires = 0.0
        self._directory_stats_cache = None
        self._directory_stats_cache_expires = 0.0

    def _maybe_prune(self, now: float) -> None:
        """Delete observations + tier-prune directory if hourly cadence reached.

        Called from record_observations on every cycle; the cadence check
        ensures the actual DELETE runs at most once per hour. Skips VACUUM
        (the routine path) — VACUUM rewrites the entire DB which on a Pi
        with a many-hundred-MB DB is multi-minute and not necessary for
        correctness; SQLite reuses freed pages on subsequent inserts.
        Operators wanting full reclaim can still call cleanup() explicitly.

        Two retention bands run inside the same prune cycle:
          1. node_observations — single-tier retention (default 48h),
             driven by self.retention_seconds.
          2. nodes (directory) — tiered retention. External-bulk origins
             prune at directory_retention_external (default 7d); all
             other origins prune at directory_retention_local (default
             30d). After time-based prune, count-cap LRU evicts the
             oldest-last_seen rows until count <= directory_max_rows.
        """
        if self._prune_interval_seconds <= 0:
            return
        if now - self._last_prune_ts < self._prune_interval_seconds:
            return

        # Cadence reached — even if individual prune phases are no-ops
        # (e.g. retention=0), advance the timer so we don't re-enter on
        # every record_observations call this hour.
        self._last_prune_ts = now

        with self._lock:
            conn = self._connect()
            try:
                # Phase 1 — observation-stream prune. Loops up to
                # prune_max_batches_per_cycle batches; each batch is
                # its own commit so WAL pages drain between batches
                # and a long-running reader can never block the whole
                # cycle on a single multi-hundred-MB transaction. The
                # per-batch cap (prune_batch_limit) bounds individual
                # transaction size for Pi-class hardware; the per-cycle
                # cap bounds total work. Pre-2026-05-09 this was a
                # single batch and a 75k inserts/hour firehose
                # accumulated ~65k/hour backlog forever; 12-batch cycles
                # drain 120k/hour, with headroom for bursts.
                deleted_obs = 0
                if self.retention_seconds > 0:
                    cutoff = now - self.retention_seconds
                    if self.prune_batch_limit > 0:
                        for _ in range(self.prune_max_batches_per_cycle):
                            cursor = conn.execute(
                                """
                                DELETE FROM node_observations
                                WHERE rowid IN (
                                    SELECT rowid FROM node_observations
                                    WHERE timestamp < ?
                                    LIMIT ?
                                )
                                """,
                                (cutoff, self.prune_batch_limit),
                            )
                            rc = cursor.rowcount
                            deleted_obs += rc
                            conn.commit()
                            if rc < self.prune_batch_limit:
                                break
                    else:
                        cursor = conn.execute(
                            "DELETE FROM node_observations WHERE timestamp < ?",
                            (cutoff,),
                        )
                        deleted_obs = cursor.rowcount
                        conn.commit()
                    if deleted_obs > 0:
                        cycle_cap = (self.prune_batch_limit
                                     * self.prune_max_batches_per_cycle
                                     if self.prune_batch_limit > 0 else 0)
                        capped = (cycle_cap > 0 and deleted_obs >= cycle_cap)
                        logger.info(
                            f"Node history auto-prune: deleted {deleted_obs} "
                            f"observation rows older than {self.retention_seconds // 3600}h"
                            + (f" (cycle cap reached at {cycle_cap})"
                               if capped else "")
                        )

                # Phase 2 — directory tiered time prune. Same multi-batch
                # shape as Phase 1.
                external_origins = list(EXTERNAL_BULK_ORIGINS)
                deleted_dir = 0
                if self.directory_retention_local > 0 or self.directory_retention_external > 0:
                    placeholders = ",".join("?" * len(external_origins))
                    if self.prune_batch_limit > 0:
                        capped_sql = f"""
                            DELETE FROM nodes
                            WHERE rowid IN (
                                SELECT rowid FROM nodes
                                WHERE
                                    (source_origin IN ({placeholders})
                                        AND last_seen < ?)
                                    OR
                                    (source_origin NOT IN ({placeholders})
                                        AND last_seen < ?)
                                LIMIT ?
                            )
                            """
                        capped_params = [
                            *external_origins,
                            now - self.directory_retention_external,
                            *external_origins,
                            now - self.directory_retention_local,
                            self.prune_batch_limit,
                        ]
                        for _ in range(self.prune_max_batches_per_cycle):
                            cursor = conn.execute(capped_sql, capped_params)
                            rc = cursor.rowcount
                            deleted_dir += rc
                            conn.commit()
                            if rc < self.prune_batch_limit:
                                break
                    else:
                        cursor = conn.execute(
                            f"""
                            DELETE FROM nodes
                            WHERE
                                (source_origin IN ({placeholders})
                                    AND last_seen < ?)
                                OR
                                (source_origin NOT IN ({placeholders})
                                    AND last_seen < ?)
                            """,
                            [
                                *external_origins,
                                now - self.directory_retention_external,
                                *external_origins,
                                now - self.directory_retention_local,
                            ],
                        )
                        deleted_dir = cursor.rowcount
                        conn.commit()

                # Phase 3 — count cap LRU. After time prune, if the
                # directory is still over the hard ceiling, drop the
                # oldest-last_seen rows. Stays single-shot — bounded by
                # excess (typically a few thousand) and runs after tier
                # prune so it sees the post-Phase-2 total.
                cap_evicted = 0
                if self.directory_max_rows > 0:
                    total = conn.execute(
                        "SELECT COUNT(*) FROM nodes"
                    ).fetchone()[0]
                    if total > self.directory_max_rows:
                        excess = total - self.directory_max_rows
                        cursor = conn.execute(
                            """
                            DELETE FROM nodes
                            WHERE rowid IN (
                                SELECT rowid FROM nodes
                                ORDER BY last_seen ASC
                                LIMIT ?
                            )
                            """,
                            (excess,),
                        )
                        cap_evicted = cursor.rowcount
                        conn.commit()

                if deleted_dir > 0 or cap_evicted > 0:
                    logger.info(
                        f"Node directory auto-prune: deleted {deleted_dir} "
                        f"by-tier (local>{self.directory_retention_local // 86400}d, "
                        f"external>{self.directory_retention_external // 86400}d), "
                        f"evicted {cap_evicted} by count-cap (max={self.directory_max_rows})"
                    )

                # Force WAL truncation so long-running readers don't let
                # the WAL file grow unbounded between auto-checkpoints.
                # TRUNCATE is non-blocking — if a reader holds a snapshot
                # it falls through and we'll retry next cycle. Caught live
                # on a federation-enabled fleet box (688 MB WAL with
                # 5.7 GB DB, 2026-05-09) where federation-poll readers
                # prevented the default passive checkpoint from ever
                # truncating.
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error as e:
                    logger.debug(f"WAL checkpoint truncate skipped: {e}")
            except sqlite3.Error as e:
                logger.error(f"Auto-prune failed: {e}")
            finally:
                conn.close()

        # Phase 4 — weekly gated VACUUM (node count optimization §D).
        # The hourly path above skips VACUUM because Pi SD rewrite is
        # multi-minute. Once a week is acceptable, and skipping it
        # entirely is how a 1.95 GB DB stayed invisible until the
        # 2026-04-26 fleet wedge. Gated on (DB size ≥ threshold) AND
        # (time since last VACUUM ≥ interval). VACUUM must run outside
        # any transaction — we drop the prune connection above and
        # open a fresh one. last_vacuum_ts is persisted in _meta so
        # the gate survives daemon restarts.
        try:
            db_size = self.db_path.stat().st_size
        except OSError:
            db_size = 0
        if db_size >= DEFAULT_VACUUM_DB_SIZE_THRESHOLD_BYTES:
            last_vac = self._meta_get_float("last_vacuum_ts", 0.0)
            if (now - last_vac) >= DEFAULT_VACUUM_INTERVAL_SECONDS:
                vacuum_start = time.perf_counter()
                try:
                    with self._lock:
                        conn = self._connect()
                        try:
                            conn.execute("VACUUM")
                        finally:
                            conn.close()
                    self._meta_set_float("last_vacuum_ts", now)
                    try:
                        new_size = self.db_path.stat().st_size
                    except OSError:
                        new_size = db_size
                    logger.info(
                        f"node_history VACUUM completed in "
                        f"{int((time.perf_counter() - vacuum_start) * 1000)}ms: "
                        f"{db_size / 1e6:.1f} MB → {new_size / 1e6:.1f} MB"
                    )
                except sqlite3.Error as e:
                    logger.warning(f"node_history VACUUM failed: {e}")

        # Prune deletes rows from both node_observations and nodes —
        # invalidate stats caches so totals don't lag the new ground truth.
        self._invalidate_stats_caches()

    def get_trajectory(self, node_id: str, hours: float = 24,
                       limit: int = 1000) -> List[NodeObservation]:
        """Get position history for a specific node.

        Args:
            node_id: The node identifier (e.g., "!ba4bf9d0").
            hours: How far back to look (default 24 hours).
            limit: Maximum observations to return.

        Returns:
            List of NodeObservation ordered by time (oldest first).
        """
        cutoff = time.time() - (hours * 3600)

        with self._lock:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("""
                    SELECT * FROM node_observations
                    WHERE node_id = ? AND timestamp >= ?
                    ORDER BY timestamp ASC
                    LIMIT ?
                """, (node_id, cutoff, limit)).fetchall()
                return [self._row_to_observation(row) for row in rows]
            finally:
                conn.close()

    def get_snapshot(self, timestamp: Optional[float] = None,
                     window_seconds: int = 300) -> List[NodeObservation]:
        """Get the most recent observation for each node at a point in time.

        Args:
            timestamp: Unix timestamp for the snapshot (default: now).
            window_seconds: How far back from timestamp to search (default 5 min).

        Returns:
            List of the most recent observation per node within the window.
        """
        if timestamp is None:
            timestamp = time.time()

        window_start = timestamp - window_seconds

        with self._lock:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            try:
                # Get latest observation per node within the window
                rows = conn.execute("""
                    SELECT o.* FROM node_observations o
                    INNER JOIN (
                        SELECT node_id, MAX(timestamp) as max_ts
                        FROM node_observations
                        WHERE timestamp BETWEEN ? AND ?
                        GROUP BY node_id
                    ) latest ON o.node_id = latest.node_id
                        AND o.timestamp = latest.max_ts
                    ORDER BY o.node_id
                """, (window_start, timestamp)).fetchall()
                return [self._row_to_observation(row) for row in rows]
            finally:
                conn.close()

    def get_unique_nodes(self, hours: float = 24) -> List[Dict[str, Any]]:
        """Get summary of unique nodes seen in a time window.

        Args:
            hours: How far back to look.

        Returns:
            List of dicts with node_id, name, observation_count, first_seen, last_seen.
        """
        cutoff = time.time() - (hours * 3600)

        with self._lock:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("""
                    SELECT node_id,
                           MAX(name) as name,
                           COUNT(*) as observation_count,
                           MIN(timestamp) as first_seen,
                           MAX(timestamp) as last_seen,
                           MAX(network) as network
                    FROM node_observations
                    WHERE timestamp >= ?
                    GROUP BY node_id
                    ORDER BY last_seen DESC
                """, (cutoff,)).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def get_trajectory_geojson(self, node_id: str, hours: float = 24) -> Dict[str, Any]:
        """Get trajectory as GeoJSON LineString for map rendering.

        Args:
            node_id: The node identifier.
            hours: How far back to look.

        Returns:
            GeoJSON Feature with LineString geometry and time properties.
        """
        observations = self.get_trajectory(node_id, hours)
        if not observations:
            return {"type": "Feature", "geometry": None, "properties": {"node_id": node_id}}

        coordinates = [[obs.longitude, obs.latitude] for obs in observations]
        timestamps = [obs.timestamp for obs in observations]

        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coordinates,
            },
            "properties": {
                "node_id": node_id,
                "name": observations[-1].name,
                "point_count": len(observations),
                "start_time": timestamps[0],
                "end_time": timestamps[-1],
                "duration_hours": (timestamps[-1] - timestamps[0]) / 3600 if len(timestamps) > 1 else 0,
            }
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics.

        Cached for ``self._stats_cache_ttl`` seconds. All queries now use
        indexes or constant-time lookups so even a cold cache miss
        completes in <100ms on a multi-GB DB.

        2026-05-13 rewrite: the previous `SELECT COUNT(*)` and
        `COUNT(DISTINCT node_id)` were full table scans on
        node_observations and took 14+s on Pi-class SD with a 4.7 GB
        DB. The 60s cache hid the cost on warm calls but the FIRST
        call after restart blocked /api/status past HTTP timeout —
        and racing the prewarm-thread made it worse (multiple
        concurrent full scans on SD). The new queries:

        - total_observations → `MAX(rowid)` (B-tree max, O(log n)).
          High-water mark; differs from exact COUNT(*) after retention
          pruning but the difference is observability-tier acceptable.
        - unique_nodes → `COUNT(*) FROM nodes` (the directory table is
          the canonical unique-nodes list per Issue #49; one row per
          (network, node_id)). Tiny table (~tens of thousands of rows),
          near-instant.
        - oldest/newest timestamp → `MIN/MAX(timestamp)` use
          `idx_obs_timestamp`, O(log n).

        Returns:
            Dict with total_observations, unique_nodes, oldest_record,
            newest_record, db_size_kb, retention_days.
        """
        # Cache fast path. Tuple read is GIL-atomic; even if a concurrent
        # writer mid-update gives us a stale (cache, expires) pair, the
        # worst case is one extra recompute.
        cache = self._stats_cache
        expires = self._stats_cache_expires
        if cache is not None and time.time() < expires:
            return cache

        # Read path runs WITHOUT self._lock — SQLite WAL mode supports
        # concurrent readers without blocking writers, and each call uses
        # its own connection (self._connect()). Holding self._lock here
        # would queue this read behind in-flight federation/collect
        # writes (Issue #52: federation poll inserts 50K rows/cycle and
        # the lock is held for tens of seconds per poll).
        conn = self._connect()
        try:
            # MAX(rowid) — B-tree max, no scan. May overshoot the exact
            # COUNT(*) after retention pruning re-uses rowids, but
            # observability-tier acceptable.
            row = conn.execute(
                "SELECT MAX(rowid) FROM node_observations"
            ).fetchone()
            total = row[0] if row and row[0] is not None else 0
            # Unique nodes from the directory table (Issue #49), which
            # is canonical for per-node identity. Avoids COUNT(DISTINCT)
            # full scan on node_observations.
            unique = conn.execute(
                "SELECT COUNT(*) FROM nodes"
            ).fetchone()[0]

            time_range = conn.execute(
                "SELECT MIN(timestamp), MAX(timestamp) FROM node_observations"
            ).fetchone()

            oldest = time_range[0] if time_range[0] else None
            newest = time_range[1] if time_range[1] else None

            # DB file size
            db_size_kb = 0
            if self.db_path.exists():
                db_size_kb = self.db_path.stat().st_size / 1024

            stats = {
                "total_observations": total,
                "unique_nodes": unique,
                "oldest_record": oldest,
                "newest_record": newest,
                "db_size_kb": round(db_size_kb, 1),
                "retention_days": self.retention_seconds / 86400,
            }
            self._stats_cache = stats
            self._stats_cache_expires = time.time() + self._stats_cache_ttl
            return stats
        finally:
            conn.close()

    def record_directory_serialized_size(
        self,
        raw_bytes: int,
        compressed_bytes: Optional[int],
    ) -> None:
        """Snapshot the most recent /api/nodes/directory response size.

        Called from the HTTP serializer after building the response.
        Observability-only: GIL-atomic field assignments, no lock.
        Surfaced via `get_directory_stats().size_*` so operators see
        directory size growth vs. the alarm threshold without having
        to time `curl | wc -c` themselves.

        Args:
            raw_bytes: Serialized JSON byte count (pre-gzip).
            compressed_bytes: Wire byte count after gzip, or None if
                the client didn't accept gzip / response was below the
                gzip threshold.
        """
        self._last_directory_bytes_raw = int(raw_bytes)
        self._last_directory_bytes_compressed = (
            int(compressed_bytes) if compressed_bytes is not None else None
        )
        self._last_directory_serialized_ts = time.time()
        # Invalidate the stats cache so the next status request sees
        # the fresh size measurement instead of a stale 5-min-old one.
        self._directory_stats_cache = None
        self._directory_stats_cache_expires = 0.0

    def get_directory_stats(self) -> Dict[str, Any]:
        """Aggregate stats for the `nodes` directory table.

        Surfaced in /api/status so operators can see the cached node
        population at a glance: total count, per-network breakdown,
        per-origin breakdown, and the oldest/newest last_seen
        timestamps.

        Cached for ``self._directory_stats_cache_ttl`` seconds (default
        300s). Pre-warming-gate fix (2026-05-13): five sequential full
        scans on the `nodes` table were running on every request, and
        under SD contention with a concurrent writer (e.g. warmup-
        thread history-DB write) the sum exceeded HTTP timeout. Cache
        is observability-only; staleness is fine.
        """
        # Cache fast path — see get_stats() for the GIL-atomic-read
        # rationale.
        cache = self._directory_stats_cache
        expires = self._directory_stats_cache_expires
        if cache is not None and time.time() < expires:
            return cache

        # Read-only path; no self._lock (Issue #52). WAL mode + own
        # connection means we read a consistent snapshot without blocking
        # the federation/collect writers.
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM nodes"
            ).fetchone()[0]

            by_network: Dict[str, int] = {}
            for row in conn.execute(
                "SELECT network, COUNT(*) AS n FROM nodes GROUP BY network"
            ).fetchall():
                by_network[row["network"]] = row["n"]

            by_source_origin: Dict[str, int] = {}
            for row in conn.execute(
                "SELECT source_origin, COUNT(*) AS n FROM nodes "
                "GROUP BY source_origin"
            ).fetchall():
                by_source_origin[row["source_origin"] or ""] = row["n"]

            with_position = conn.execute(
                "SELECT COUNT(*) FROM nodes "
                "WHERE last_lat IS NOT NULL AND last_lon IS NOT NULL"
            ).fetchone()[0]

            time_range = conn.execute(
                "SELECT MIN(last_seen), MAX(last_seen) FROM nodes"
            ).fetchone()
            oldest = time_range[0]
            newest = time_range[1]

            # Size-budget alarm (Issue #64) — surfaced so operators see
            # /api/nodes/directory size growth before the cliff. Raw
            # bytes is the budget axis because some peers may disable
            # gzip; the wire (compressed) bytes are informational. The
            # alarm IS the answer to "how do we know when we've hit
            # 'too late'?" from the reliability backlog.
            raw_bytes = self._last_directory_bytes_raw
            compressed_bytes = self._last_directory_bytes_compressed
            size_alarm = (
                raw_bytes is not None
                and raw_bytes >= DEFAULT_DIRECTORY_SIZE_ALARM_BYTES
            )
            ratio = None
            if raw_bytes is not None and compressed_bytes is not None and raw_bytes > 0:
                ratio = round(raw_bytes / compressed_bytes, 1)

            result = {
                "total": total,
                "with_position": with_position,
                "without_position": total - with_position,
                "by_network": by_network,
                "by_source_origin": by_source_origin,
                "oldest_last_seen": oldest,
                "newest_last_seen": newest,
                "retention_local_days": self.directory_retention_local // 86400,
                "retention_external_days": self.directory_retention_external // 86400,
                "max_rows": self.directory_max_rows,
                "size_bytes_raw": raw_bytes,
                "size_bytes_compressed": compressed_bytes,
                "size_compression_ratio": ratio,
                "size_alarm_threshold_bytes": DEFAULT_DIRECTORY_SIZE_ALARM_BYTES,
                "size_alarm": size_alarm,
                "size_last_serialized_ts": self._last_directory_serialized_ts,
            }
            self._directory_stats_cache = result
            self._directory_stats_cache_expires = time.time() + self._directory_stats_cache_ttl
            return result
        finally:
            conn.close()

    def get_directory_snapshot(self,
                               include_position_less: bool = True
                               ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Read the entire `nodes` directory table.

        Returns a tuple `(features, position_less)` where:
          - features: GeoJSON Feature dicts for nodes with positions.
          - position_less: dicts (id/name/network/last_seen/source_origin/...)
            for nodes without GPS, mirroring the existing
            `nodes_without_position` shape used elsewhere in /api/status.

        Used by the new GET /api/nodes/directory endpoint. Includes nodes
        whose last_seen is older than the observation-stream retention
        — that's the whole point of the directory.
        """
        features: List[Dict[str, Any]] = []
        position_less: List[Dict[str, Any]] = []
        now = time.time()
        # Read-only path; no self._lock (Issue #52). WAL mode + own
        # connection means concurrent federation/collect writers don't
        # block this read. The full-table SELECT also runs faster as a
        # single fetchall() outside any contended lock.
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT network, node_id, first_seen, last_seen,
                       last_lat, last_lon, last_altitude,
                       name, role, hardware,
                       source_origin, protocol_meta, obs_count
                FROM nodes
                """
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            base = {
                "id": row["node_id"],
                "network": row["network"],
                "name": row["name"] or row["node_id"],
                "role": row["role"] or "",
                "hardware": row["hardware"] or "",
                "source_origin": row["source_origin"] or "",
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "last_seen_age_s": max(0.0, now - row["last_seen"]) if row["last_seen"] else None,
                "obs_count": row["obs_count"] or 0,
            }
            if row["last_lat"] is not None and row["last_lon"] is not None:
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [
                            row["last_lon"],
                            row["last_lat"],
                            row["last_altitude"] if row["last_altitude"] is not None else 0,
                        ],
                    },
                    "properties": dict(base),
                }
                features.append(feature)
            elif include_position_less:
                position_less.append(base)
        return features, position_less

    def cleanup(self) -> int:
        """Remove observations older than retention period.

        Returns:
            Number of rows deleted.
        """
        cutoff = time.time() - self.retention_seconds

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM node_observations WHERE timestamp < ?",
                    (cutoff,)
                )
                conn.commit()
                deleted = cursor.rowcount
                if deleted > 0:
                    conn.execute("VACUUM")
                    logger.debug(f"Node history cleanup: deleted {deleted} old observations")
                return deleted
            except sqlite3.Error as e:
                logger.error(f"Cleanup failed: {e}")
                return 0
            finally:
                conn.close()

    def _row_to_observation(self, row: sqlite3.Row) -> NodeObservation:
        """Convert a database row to a NodeObservation."""
        return NodeObservation(
            node_id=row["node_id"],
            timestamp=row["timestamp"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            altitude=row["altitude"],
            snr=row["snr"],
            battery=row["battery"],
            is_online=bool(row["is_online"]),
            network=row["network"],
            hardware=row["hardware"],
            role=row["role"],
            via_mqtt=bool(row["via_mqtt"]),
            name=row["name"],
        )

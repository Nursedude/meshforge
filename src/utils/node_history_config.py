"""Node-history tuning constants, source-origin priority, and value types.

Extracted from ``node_history.py`` on 2026-07-14 to hold that file under the
MF025 size cap (it had drifted to 1,510 lines). This is the "config + types"
half — the tuning knobs, the sticky-promotion priority table, the two
observation-skip helpers, and the ``NodeObservation`` dataclass — none of which
touch SQLite. ``node_history.py`` re-imports every name below, so
``from utils.node_history import <name>`` keeps working for all existing
consumers (map_data_collector, map_federation, cross_protocol_collapse,
provision_role, the test suite); the split is API-preserving.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


# Observation-stream retention: 48h. The `nodes` directory takes over the
# "did we ever hear this node" question, so observations only need to support
# trajectory + playback windows. Cut from 7d on 2026-04-28 (Issue #49).
DEFAULT_RETENTION_SECONDS = 48 * 3600

# Minimum interval between recording the same node (avoid flooding)
MIN_RECORD_INTERVAL = 60  # 1 minute

# Stationary-node heartbeat. When (round(lat,6), round(lon,6), network) match
# the last recorded value, skip the insert until this interval has elapsed.
# Mirrors the meshforge-maps Phase 1 fix (commit b264b60). Stationary nodes
# drop from ~720 rows/day to 24 with the meshcore_public + aredn local sources
# enabled — was ballooning /root/.local/share/meshforge/node_history.db
# at 42K nodes/cycle. Set heartbeat_seconds=0 in ctor to disable (legacy).
DEFAULT_HEARTBEAT_SECONDS = 3600

# Lat/lon comparison precision (decimal degrees). 6 dp ≈ 11 cm — anything
# tighter is GPS noise, anything looser smears co-sited repeaters into one.
_LAT_LON_PRECISION = 6

# Directory-table tiered retention (Issue #49). External-bulk sources
# (MeshCore-public global directory, AREDN worldmap CSV, regional MQTT)
# can flood the table with tens of thousands of rows; locally-RX'd sources
# (own radios, RNS path table) are bounded by what's actually heard.
DEFAULT_DIRECTORY_RETENTION_LOCAL = 30 * 24 * 3600     # 30 days
DEFAULT_DIRECTORY_RETENTION_EXTERNAL = 7 * 24 * 3600   # 7 days
# Hard cap, LRU evict. Lowered 50_000 → 15_000 on 2026-05-22 as part of
# the node count optimization: with the geo-filter shedding 30k+ rows
# from external-bulk firehoses and federation no longer persisting peer
# directories, the realistic upper bound on a regional Pi-class box is
# ~3k total. 15k gives ~5× headroom without LRU thrashing while keeping
# the worst-case /api/nodes/directory body inside the 5s response cache
# budget. Operators with bigger fleets can override via the ctor arg.
DEFAULT_DIRECTORY_MAX_ROWS = 15_000

# Size-budget alarm thresholds (Issue #64). Triggered when the LAST
# /api/nodes/directory response exceeded the budget; surfaced in
# /api/status.directory.size_alarm so operators see the cliff coming
# instead of discovering it via federation timeouts. The threshold is
# in RAW bytes — the federation client now negotiates gzip (Issue #64),
# so wire bytes are 5-10× smaller, but worst case is a peer that
# disables gzip and gets the raw payload. We size the alarm for that
# worst case. 40 MB ≈ 80% of map_federation.DEFAULT_MAX_RESPONSE_BYTES
# (50 MB hard cap) — gives operators ~6 months of growth headroom at
# observed ~3 MB/year per Issue #56.
DEFAULT_DIRECTORY_SIZE_ALARM_BYTES = 40 * 1024 * 1024

# Cap rows deleted per prune BATCH (single transaction). Without this,
# a retention shrink (e.g. observation-stream cut 7d → 48h) on a fleet
# box that's been accumulating for weeks does ONE giant DELETE →
# multi-hundred-MB WAL → multi-minute checkpoint stall on Pi-class
# hardware. Caught live on moc3 (790 MB DB, Pi 3B): first prune after
# the cutover ran for 10+ minutes with a 465 MB WAL. The per-batch
# cap bounds individual transaction size; the per-cycle cap (below)
# bounds how many such batches one cycle runs.
DEFAULT_PRUNE_BATCH_LIMIT = 10_000

# Number of batches a single prune cycle may run. Each batch is its
# own commit so WAL pages drain between batches; total throughput per
# cycle = batch_limit × max_batches. Pre-2026-05-09 this was implicitly
# 1 — caught live on a federation-enabled fleet box where heartbeats
# inserted ~75k observation rows/hour into node_observations while the
# single-batch prune drained only 10k/hour. Net +65k/hr accumulated to
# 5.7 GB before manual intervention. 12 batches × 10k = 120k/hour
# drains the steady-state firehose with headroom; bursts catch up over
# 1-2 cycles instead of stalling forever. Set to 1 to restore the
# legacy single-batch behavior (tests that want deterministic
# per-cycle deletion).
DEFAULT_PRUNE_MAX_BATCHES_PER_CYCLE = 12

# Weekly gated VACUUM (node count optimization §D). The hourly auto-prune
# explicitly skips VACUUM because on a Pi-class SD the full DB rewrite
# can run multi-minute. Once a week is acceptable, and skipping it
# entirely is what made an earlier 1.95 GB DB invisible until the
# 2026-04-26 fleet wedge surfaced it. Gated on (DB file size ≥
# threshold) AND (time since last VACUUM ≥ interval) so small DBs
# never pay the rewrite cost. last_vacuum_ts persists in the _meta
# key/value table so the gate survives daemon restarts.
DEFAULT_VACUUM_INTERVAL_SECONDS = 7 * 24 * 3600        # 7 days
DEFAULT_VACUUM_DB_SIZE_THRESHOLD_BYTES = 200 * 1024 * 1024   # 200 MB

# source_origin tags. The writer derives these from the feature properties;
# the prune query filters on them. Single source of truth so prune SQL and
# tagging logic can't drift.
EXTERNAL_BULK_ORIGINS = frozenset({
    "meshcore_public",   # https://map.meshcore.dev — 40k global
    "aredn_worldmap",    # AREDN worldmap CSV — global
    "mqtt_global",       # MQTT region-wide aggregator (firehose)
    "public_fallback",   # meshmap.net / rmap.world — global Meshtastic firehose
})

# Sticky promotion priority — higher number wins on UPSERT collision.
# A node first seen via meshcore_public stays in the 7d tier until the
# local radio actually hears it (origin promotes to local_radio, tier
# becomes 30d). Reverse demotion does NOT happen.
_ORIGIN_PRIORITY: Dict[str, int] = {
    "local_radio": 100,
    "rns_path_table": 90,
    "aredn_local": 80,
    "mqtt_local": 70,
    "node_tracker": 60,    # local cache replay
    "meshcore_public": 30,
    "aredn_worldmap": 30,
    "mqtt_global": 30,
    "public_fallback": 20,
    "operator_positions": 50,  # operator-overridden coords
    "":  0,
    None: 0,
}

# Hard cap on protocol_meta JSON blob size — prevents a misbehaving source
# from writing a 1 MB row. 4 KB is generous for any single advert/sysinfo.
_PROTOCOL_META_MAX_BYTES = 4 * 1024


def _origin_priority(origin: Optional[str]) -> int:
    """Lookup table for source_origin sticky-promotion ordering."""
    if origin is None:
        return 0
    return _ORIGIN_PRIORITY.get(origin, 10)  # unknown origin: low priority


def _should_skip_observation(props: Dict[str, Any], source_origin: str) -> bool:
    """True when a feature should NOT generate a node_observations row.

    Two cases produce a "skip":
      1. Federation-fetched features. The peer that originally heard the
         node owns its trajectory; duplicating observations on every
         federation receiver multiplies SD write pressure linearly with
         peer count for zero query value (the trajectory query against
         a federated node returns the same data the home box already
         has). Identified by `properties.federated == True` or the
         presence of `properties.federated_from`.
      2. External-bulk origins (meshcore_public, aredn_worldmap,
         mqtt_global, public_fallback). These are global firehoses of
         mostly-stationary nodes — observations on them generate
         heartbeat-driven inserts at upstream poll rate × node count
         (a federation-enabled fleet box, 2026-05-09: 75k inserts/hr)
         for trajectory data that's never queried.

    The `nodes` directory table still UPSERTs in both cases — it's the
    long-tail "did we ever hear this node" record. Only the time-series
    `node_observations` insert is suppressed.
    """
    if props.get("federated") or props.get("federated_from"):
        return True
    if source_origin in EXTERNAL_BULK_ORIGINS:
        return True
    return False


@dataclass
class NodeObservation:
    """A single node observation at a point in time."""
    node_id: str
    timestamp: float
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    snr: Optional[float] = None
    battery: Optional[int] = None
    is_online: bool = True
    network: str = "meshtastic"
    hardware: str = ""
    role: str = ""
    via_mqtt: bool = False
    name: str = ""

"""
Map Federation - aggregate node directories across the fleet.

Each MeshForge box's :5000 map is normally an island: it only knows the
nodes its own RNS hub, MQTT subscriber, AREDN scan, and meshtasticd radio
have heard. In a multi-box fleet that means box A never sees box B's RNS
announces; box C never sees box D's AREDN worldmap; etc.

This module fixes that without requiring a central aggregator. Each box
runs a `FederationCollector` thread that polls peer boxes' public
`/api/nodes/directory` endpoints (Issue #49) every poll_interval seconds
and exposes the merged snapshot. The MapDataCollector reads that snapshot
during its normal collect cycle and folds federated entries into the
geojson response — local always wins on (network, node_id) collisions.

Privacy note: this is intra-fleet only. The endpoints scraped are the
same ones already public on the local network (`/api/nodes/directory`,
unauthenticated). No new exposure surface.

Lifecycle:
    fc = FederationCollector(["host-a", "host-b", "host-c"])
    fc.start()
    ...
    snapshot = fc.get_snapshot()  # thread-safe
    fc.stop()
"""

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.node_history import _origin_priority

logger = logging.getLogger(__name__)


DEFAULT_POLL_INTERVAL = 60       # seconds between full federation polls
DEFAULT_TIMEOUT = 5.0             # per-peer HTTP timeout
DEFAULT_PORT = 5000               # peer map service port
DEFAULT_MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MB hard cap per peer

# Federation backpressure: skip polls when the node_history.db WAL is bigger
# than this. 64 MB is the journal_size_limit set in db_helpers.connect_tuned,
# so anything above means the checkpoint thread is starving and another
# 50k-row republish would worsen the fsync stall. See
# project_db_recurring_class.md, project_meshforge_map_cold_start_wal.md.
DEFAULT_WAL_SKIP_THRESHOLD_BYTES = 64 * 1024 * 1024


def _wal_path_for(db_path: Path) -> Path:
    """SQLite WAL companion path next to the main DB file."""
    return db_path.with_name(db_path.name + "-wal")


def _stat_size(p: Path) -> int:
    """Default stat_fn for FederationCollector — best-effort, 0 on miss."""
    try:
        return p.stat().st_size
    except OSError:
        return 0


@dataclass
class FederationPeerStatus:
    """Per-peer health state. Serialized into the federation API block."""
    hostname: str
    last_sync: Optional[float] = None      # unix ts of last successful sync
    last_attempt: Optional[float] = None   # unix ts of last attempt (success or fail)
    last_error: Optional[str] = None       # error message from last failed attempt
    last_count: int = 0                    # node count from last successful sync
    last_latency_ms: int = 0               # round-trip ms of last successful sync
    consecutive_failures: int = 0
    ok: bool = False                       # last attempt succeeded
    # Friendly fleet name from fleet.json (e.g. the box's hostname alias)
    # when the operator addressed the peer by IP. `hostname` is what the
    # collector actually hits (usually the IP); `peer_name` lets diagnostics
    # line up with the tracer leaderboard and MA fleet rollup, which both
    # use names. `None` when the collector wasn't given a name mapping or
    # the entry is missing from fleet.json.
    peer_name: Optional[str] = None


@dataclass
class FederationSnapshot:
    """Read-only snapshot of federated state at a point in time."""
    by_node: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    peer_status: Dict[str, FederationPeerStatus] = field(default_factory=dict)
    last_sync: Optional[float] = None      # most recent successful peer sync
    last_attempt: Optional[float] = None   # most recent attempt across all peers


def get_local_hostnames() -> List[str]:
    """Best-effort list of names that might match this host in fleet config.

    Returns lowercased candidates: socket.gethostname(), getfqdn(), and
    common aliases. Used to filter self out of the peer list so the
    federation collector doesn't fetch from its own endpoint.
    """
    names = set()
    try:
        h = socket.gethostname()
        if h:
            names.add(h.lower())
    except Exception:
        pass
    try:
        f = socket.getfqdn()
        if f:
            names.add(f.lower())
            # First label, e.g. "moc3.local" -> "moc3"
            first = f.split(".")[0]
            if first:
                names.add(first.lower())
    except Exception:
        pass
    return sorted(names)


def filter_self_from_peers(peers: List[str], local_names: Optional[List[str]] = None) -> List[str]:
    """Drop entries from `peers` that match this host.

    Match is case-insensitive substring (peer == name, or peer contains name,
    or name contains peer) — fleet config sometimes uses a prefixed alias
    (e.g. "meshforge-<host>") while the bare hostname is just "<host>";
    both should self-eliminate.
    """
    if local_names is None:
        local_names = get_local_hostnames()
    if not local_names:
        return list(peers)
    out = []
    for p in peers:
        pl = p.lower()
        skip = False
        for name in local_names:
            if not name:
                continue
            if pl == name or name in pl or pl in name:
                skip = True
                break
        if not skip:
            out.append(p)
    return out


def _peer_url(peer: str, port: int = DEFAULT_PORT, path: str = "/api/nodes/directory") -> str:
    """Build the URL for a peer endpoint. Accepts bare hostname or host:port."""
    if "://" in peer:
        # Operator gave us a full URL; trust it but append path
        base = peer.rstrip("/")
        return f"{base}{path}"
    # Bare hostname (or host:port)
    if ":" in peer:
        return f"http://{peer}{path}"
    return f"http://{peer}:{port}{path}"


def _extract_features(directory_payload: Dict[str, Any], peer: str) -> List[Dict[str, Any]]:
    """Convert a /api/nodes/directory response into a flat list of node dicts.

    Output shape (one per node, positioned or position-less):
        {
          "network": "rns",
          "id": "abc123...",
          "name": "Node Name",
          "lat": 19.4 | None,
          "lon": -155.3 | None,
          "altitude": 0 | None,
          "last_seen": 1700000000.0,
          "source_origin": "rns_path_table",
          "federated_from": "moc3",
        }

    Tolerates missing keys; rejects entries without a `network` and `id`.
    """
    out: List[Dict[str, Any]] = []
    features = directory_payload.get("features") or []
    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        nid = props.get("id")
        net = props.get("network")
        if not nid or not net:
            continue
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") if isinstance(geom, dict) else None
        lat = lon = alt = None
        if isinstance(coords, list) and len(coords) >= 2:
            lon = coords[0]
            lat = coords[1]
            alt = coords[2] if len(coords) >= 3 else None
        out.append({
            "network": net,
            "id": nid,
            "name": props.get("name") or nid,
            "role": props.get("role") or "",
            "hardware": props.get("hardware") or "",
            "lat": lat,
            "lon": lon,
            "altitude": alt,
            "last_seen": props.get("last_seen"),
            "source_origin": props.get("source_origin") or "",
            "federated_from": peer,
        })

    # Position-less entries
    for entry in directory_payload.get("nodes_without_position") or []:
        if not isinstance(entry, dict):
            continue
        nid = entry.get("id")
        net = entry.get("network")
        if not nid or not net:
            continue
        out.append({
            "network": net,
            "id": nid,
            "name": entry.get("name") or nid,
            "role": entry.get("role") or "",
            "hardware": entry.get("hardware") or "",
            "lat": None,
            "lon": None,
            "altitude": None,
            "last_seen": entry.get("last_seen"),
            "source_origin": entry.get("source_origin") or "",
            "federated_from": peer,
        })
    return out


def fetch_peer_directory(
    peer: str,
    timeout: float = DEFAULT_TIMEOUT,
    port: int = DEFAULT_PORT,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> Tuple[List[Dict[str, Any]], FederationPeerStatus]:
    """Fetch one peer's /api/nodes/directory. Returns (entries, status).

    On any failure returns ([], status) with `status.ok=False` and
    `status.last_error` populated. Never raises — federation must
    degrade gracefully when peers are down.
    """
    status = FederationPeerStatus(hostname=peer)
    status.last_attempt = time.time()
    url = _peer_url(peer, port=port)
    started = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MeshForge/federation"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                status.last_error = f"HTTP {resp.status}"
                return [], status
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                status.last_error = f"response > {max_bytes} bytes"
                return [], status
        payload = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, OSError) as e:
        status.last_error = f"{type(e).__name__}: {e}"
        return [], status
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        status.last_error = f"parse: {type(e).__name__}: {e}"
        return [], status
    except Exception as e:
        status.last_error = f"{type(e).__name__}: {e}"
        return [], status

    try:
        entries = _extract_features(payload, peer)
    except Exception as e:
        status.last_error = f"extract: {type(e).__name__}: {e}"
        return [], status

    status.ok = True
    status.last_sync = time.time()
    status.last_count = len(entries)
    status.last_latency_ms = int((time.perf_counter() - started) * 1000)
    status.last_error = None
    return entries, status


class FederationCollector:
    """Background thread that polls peer directories and merges them.

    The merged snapshot is keyed by (network, node_id). When the same
    (network, node_id) appears on multiple peers, the entry with the
    newest `last_seen` wins (with `seen_by_peers` recording all
    federation sources for diagnostics).

    Local nodes (i.e. nodes the local MapDataCollector also knows about)
    are NOT filtered here — that's the integration layer's job in Phase 2.
    """

    def __init__(
        self,
        peers: List[str],
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
        port: int = DEFAULT_PORT,
        max_workers: int = 5,
        db_path: Optional[Path] = None,
        wal_skip_threshold_bytes: int = DEFAULT_WAL_SKIP_THRESHOLD_BYTES,
        stat_fn: Callable[[Path], int] = _stat_size,
        peer_names: Optional[Dict[str, str]] = None,
    ):
        self._peers = list(peers)
        self._poll_interval = max(10, int(poll_interval))
        self._timeout = float(timeout)
        self._port = int(port)
        self._max_workers = max(1, int(max_workers))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # Endpoint → friendly fleet-name lookup. Plumbed in from fleet.json
        # via map_data_collector._init_federation. Empty dict when unknown.
        self._peer_names: Dict[str, str] = dict(peer_names or {})
        self._snapshot = FederationSnapshot(
            peer_status={
                p: FederationPeerStatus(
                    hostname=p, peer_name=self._peer_names.get(p),
                )
                for p in self._peers
            },
        )

        # Backpressure: when node_history.db's WAL is oversize, federation
        # polls would compound the fsync stall. We log the FIRST skip in a
        # streak at WARNING and only log subsequent skips at DEBUG so we
        # don't flood the journal. _consecutive_wal_skips tracks the streak.
        self._db_path = Path(db_path) if db_path is not None else None
        self._wal_skip_threshold_bytes = int(wal_skip_threshold_bytes)
        self._stat_fn = stat_fn
        self._consecutive_wal_skips = 0

    @property
    def peers(self) -> List[str]:
        return list(self._peers)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self._peers:
            logger.info("FederationCollector: no peers configured; not starting thread")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="map-federation"
        )
        self._thread.start()
        logger.info(
            f"FederationCollector started: peers={self._peers} "
            f"interval={self._poll_interval}s timeout={self._timeout}s"
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def get_snapshot(self) -> FederationSnapshot:
        """Return a thread-safe copy of the current federation state."""
        with self._lock:
            return FederationSnapshot(
                by_node=dict(self._snapshot.by_node),
                peer_status={k: FederationPeerStatus(**asdict(v))
                             for k, v in self._snapshot.peer_status.items()},
                last_sync=self._snapshot.last_sync,
                last_attempt=self._snapshot.last_attempt,
            )

    def poll_once(self) -> None:
        """Run one full federation poll cycle. Public for tests + manual triggers."""
        if not self._peers:
            return
        merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
        new_status: Dict[str, FederationPeerStatus] = {}
        attempt_ts = time.time()

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(
                    fetch_peer_directory, peer, self._timeout, self._port
                ): peer
                for peer in self._peers
            }
            for fut in as_completed(futures):
                peer = futures[fut]
                # Carry forward consecutive_failures across cycles
                with self._lock:
                    prior = self._snapshot.peer_status.get(peer)
                    prior_failures = prior.consecutive_failures if prior else 0
                try:
                    entries, status = fut.result()
                except Exception as e:
                    logger.warning(f"FederationCollector: peer {peer} fetch crashed: {e}")
                    status = FederationPeerStatus(
                        hostname=peer, last_attempt=attempt_ts,
                        last_error=f"crash: {type(e).__name__}: {e}",
                        consecutive_failures=prior_failures + 1,
                        peer_name=self._peer_names.get(peer),
                    )
                    new_status[peer] = status
                    continue

                if status.ok:
                    status.consecutive_failures = 0
                else:
                    status.consecutive_failures = prior_failures + 1
                status.peer_name = self._peer_names.get(peer)
                new_status[peer] = status

                for entry in entries:
                    key = (entry["network"], entry["id"])
                    existing = merged.get(key)
                    if existing is None:
                        entry["seen_by_peers"] = [peer]
                        merged[key] = entry
                    else:
                        # Higher source_origin priority wins over newer
                        # last_seen. Without this, a sister peer's
                        # local_radio observation (the box that actually
                        # hears the radio) gets clobbered by a fleet peer
                        # republishing the same hash as meshcore_public
                        # with a fresher firehose-poll timestamp. Tie-
                        # break on newer last_seen preserves the original
                        # behavior when both peers report the same origin.
                        e_priority = _origin_priority(entry.get("source_origin"))
                        x_priority = _origin_priority(existing.get("source_origin"))
                        if e_priority > x_priority:
                            wins = True
                        elif e_priority < x_priority:
                            wins = False
                        else:
                            e_last = entry.get("last_seen") or 0
                            x_last = existing.get("last_seen") or 0
                            wins = e_last > x_last
                        if wins:
                            new_seen = list(existing.get("seen_by_peers", []))
                            if peer not in new_seen:
                                new_seen.append(peer)
                            entry["seen_by_peers"] = new_seen
                            merged[key] = entry
                        else:
                            seen = existing.get("seen_by_peers", [])
                            if peer not in seen:
                                seen.append(peer)
                                existing["seen_by_peers"] = seen

        # Atomic swap
        with self._lock:
            self._snapshot.by_node = merged
            # Preserve hosts that aren't being polled this cycle (shouldn't happen,
            # but defensive)
            for k, v in new_status.items():
                self._snapshot.peer_status[k] = v
            ok_syncs = [s.last_sync for s in new_status.values() if s.ok and s.last_sync]
            if ok_syncs:
                self._snapshot.last_sync = max(ok_syncs)
            self._snapshot.last_attempt = attempt_ts

        successes = sum(1 for s in new_status.values() if s.ok)
        logger.info(
            f"FederationCollector poll: {successes}/{len(self._peers)} peers ok, "
            f"{len(merged)} federated nodes"
        )

    def _wal_oversize(self) -> Optional[int]:
        """Return WAL size in bytes if it exceeds the skip threshold, else None.

        Read-only stat call. Used by _run to skip a poll cycle when the
        node_history DB is mid-fsync-stall — adding another 50k-row
        federation cycle on top of that is the documented cascade trigger.
        """
        if self._db_path is None:
            return None
        wal_size = self._stat_fn(_wal_path_for(self._db_path))
        if wal_size > self._wal_skip_threshold_bytes:
            return wal_size
        return None

    def _run(self) -> None:
        # Stagger first poll slightly so we don't add to startup-thundering-herd
        if self._stop_event.wait(2):
            return
        while not self._stop_event.is_set():
            wal_bytes = self._wal_oversize()
            if wal_bytes is not None:
                self._consecutive_wal_skips += 1
                msg = (
                    f"FederationCollector: skipping poll — node_history WAL "
                    f"is {wal_bytes // (1024 * 1024)} MB "
                    f"(> {self._wal_skip_threshold_bytes // (1024 * 1024)} MB "
                    f"threshold), streak={self._consecutive_wal_skips}. "
                    f"See project_db_recurring_class.md."
                )
                if self._consecutive_wal_skips == 1:
                    logger.warning(msg)
                else:
                    logger.debug(msg)
            else:
                if self._consecutive_wal_skips > 0:
                    logger.info(
                        f"FederationCollector: WAL back below threshold after "
                        f"{self._consecutive_wal_skips} skipped poll(s); resuming."
                    )
                self._consecutive_wal_skips = 0
                try:
                    self.poll_once()
                except Exception as e:
                    logger.error(f"FederationCollector poll crashed: {e}", exc_info=True)
            # _stop_event.wait() respects the bounded interval AND wakes
            # immediately on stop() — consistent with daemon-loop pattern
            # in the rest of the codebase.
            if self._stop_event.wait(self._poll_interval):
                return

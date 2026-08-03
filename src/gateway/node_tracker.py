"""
Unified Node Tracker for RNS and Meshtastic Networks
Tracks nodes from both networks with position and telemetry data.

Enhanced with:
- Multi-service RNS announce parsing (LXMF, Nomad, generic)
- Network topology graph with edge tracking
- Path table change monitoring
"""

import threading
import time
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any
from pathlib import Path
import json

logger = logging.getLogger(__name__)

# Import data models (extracted to reduce file size)
from .node_models import (
    Position, PKIKeyState, PKIStatus,
    AirQualityMetrics, HealthMetrics, DetectionSensor,
    SignalSample, Telemetry, UnifiedNode,
    NODE_STATE_AVAILABLE, RNS_SERVICES_AVAILABLE
)

from utils.node_history_config import (
    DEFAULT_DIRECTORY_RETENTION_LOCAL,
    DEFAULT_DIRECTORY_RETENTION_EXTERNAL,
)
from utils.boundary_timing import timed_boundary
from gateway.bounded_rpc import bounded_call
from utils.safe_import import safe_import

# Import RNS service registry and topology (optional - graceful fallback)
(RNSServiceType, ServiceInfo, AnnounceEvent,
 get_service_registry, RNSServiceRegistry,
 _HAS_RNS_SERVICES) = safe_import(
    '.rns_services',
    'RNSServiceType', 'ServiceInfo', 'AnnounceEvent',
    'get_service_registry', 'RNSServiceRegistry',
    package=__package__
)
(NetworkTopology, get_network_topology, TopologyEvent,
 _HAS_TOPOLOGY) = safe_import(
    '.network_topology',
    'NetworkTopology', 'get_network_topology', 'TopologyEvent',
    package=__package__
)

# Import centralized path utility
from utils.paths import get_real_user_home

# Import event bus for node update events
from utils.event_bus import emit_node_update

class UnifiedNodeTracker:
    """
    Tracks nodes from both RNS and Meshtastic networks.
    Provides unified view for map display and monitoring.

    Enhanced features:
    - Multi-service RNS announce parsing via RNSServiceRegistry
    - Network topology graph via NetworkTopology
    - Path table change monitoring with event logging
    """

    OFFLINE_THRESHOLD = 900  # 15 minutes — consistent with map_data_collector default
    MAX_NODES = 10000  # Prevent unbounded memory growth

    # Cache-write budget (2026-08-03 tracemalloc soak, 18.6 h on moc).
    # The gateway was never leaking: retained growth measured 3.8 MB over the
    # soak, and every growing structure is capped. What it WAS doing is
    # serializing 9.2k nodes — 23.09 MB across two files, one of them
    # pretty-printed — on every 60 s tick: 31.7 GB/day of fsync'd writes per
    # gateway box, on moc3 the same SD card it swaps to.
    #
    # CLEANUP_TICK still drives timeout state at 60 s (node online/offline
    # must stay responsive); only the WRITE moves to the 5-minute cadence the
    # loop's own comment had claimed since it was written.
    CLEANUP_TICK = 60
    # 2026-08-03, revised same day from 300s. The 300s value assumed stop()
    # flushes the cache at shutdown — test_stop_flushes_unconditionally proves
    # it does, and it is NEVER CALLED in production: the systemd unit sends
    # SIGTERM and the process exits without reaching bridge_cli's stop path
    # (measured: zero "Stopping bridge" lines across 24 gateway starts in a
    # day). So the real worst-case loss on restart is a full interval, not
    # zero — a proxy-verified assumption, exactly what calibrated_claims #7
    # warns about.
    #
    # 120s keeps the loss window near the pre-change 60s while the population
    # cap does the heavy lifting: measured on moc, 4.44 MB/pass means 3.2
    # GB/day here versus 33.3 GB/day this morning. Restore a longer interval
    # only once shutdown genuinely flushes.
    CACHE_SAVE_INTERVAL = 120
    # Backstop for a missed dirty marker. The dirty flag is an optimization,
    # never a correctness dependency — if a future mutation path forgets to
    # mark, the cache must go stale for minutes, not forever
    # (honest_failure_modes #9: no permanent silent blindness).
    CACHE_MAX_STALENESS = 1800

    # Population retention (2026-08-03). moc3 held 9,345 RNS announce-space
    # nodes for SIX local Meshtastic radios: the population tracks the
    # reachable Reticulum network, not this box's workload, and every node is
    # serialized into both cache files on every save. Measured age
    # distribution: 2.8% heard inside a day, 16.7% inside a week, 42.6%
    # sitting in the 30-90 day band.
    #
    # The tiers are IMPORTED from the node-directory retention (#49), never
    # re-declared — two consumers of "how long is a node interesting" sharing
    # one constant instead of two hardcodes that WILL drift
    # (honest_failure_modes #5).
    RETENTION_LOCAL = DEFAULT_DIRECTORY_RETENTION_LOCAL        # 30d — our own RF
    RETENTION_EXTERNAL = DEFAULT_DIRECTORY_RETENTION_EXTERNAL  # 7d  — announce firehose

    @classmethod
    def get_cache_file(cls) -> Path:
        """Get the cache file path (evaluated at runtime, not import time)"""
        return get_real_user_home() / ".config" / "meshforge" / "node_cache.json"

    def __init__(self):
        self._nodes: Dict[str, UnifiedNode] = {}
        self._lock = threading.RLock()
        self._callbacks: List[Callable] = []
        self._running = False
        self._stop_event = threading.Event()
        self._cleanup_thread = None
        self._rns_thread = None
        self._reticulum = None
        self._rns_connected = False

        # Cache-write gating. Starts dirty: _load_cache is lossy by design
        # (is_online is forced False, unknown fields dropped), so in-memory
        # state never matches the file at construction. MONOTONIC only — this
        # fleet's Pis are RTC-less and NTP steps the wall clock (#74).
        self._cache_dirty = True
        self._last_cache_save = 0.0

        # Retention pins — hashes that must survive any TTL sweep (the
        # configured propagation node, peer gateways, default LXMF
        # destinations). None means NOBODY HAS TOLD US YET, which keeps TTL
        # eviction inert; see set_retention_pins.
        self._retention_pins: Optional[set] = None
        self._retention_unwired_warned = False

        # Enhanced RNS service tracking
        self._service_registry: Optional[RNSServiceRegistry] = None
        self._network_topology: Optional[NetworkTopology] = None
        if RNS_SERVICES_AVAILABLE:
            self._service_registry = get_service_registry()
            self._network_topology = get_network_topology()
            # Register for topology events
            self._network_topology.register_callback(self._on_topology_event)
            logger.debug("Enhanced RNS service tracking enabled")

        # Load cached nodes
        self._load_cache()

    def start(self):
        """Start the node tracker"""
        self._running = True
        self._stop_event.clear()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        # Start network topology tracking (includes path table monitor)
        if self._network_topology:
            self._network_topology.start()

        # Initialize RNS in the main thread to avoid signal handler issues
        # RNS.Reticulum() sets up signal handlers which only work in main thread
        self._init_rns_main_thread()

        logger.info("Node tracker started")

    def _init_rns_main_thread(self):
        """Initialize RNS from main thread, then start background listener.

        IMPORTANT: MeshForge operates as a CLIENT ONLY - it connects to existing
        rnsd/NomadNet instances but never creates its own RNS instance that would
        bind interfaces and conflict with NomadNet or other RNS services.

        NOTE: RNS.Reticulum() uses signal handlers which ONLY work in the main
        thread. If called from a background thread, it will fail with:
        "signal only works in main thread of the main interpreter"
        """
        # Check if we're in the main thread - RNS signal handlers require it
        import threading as _threading
        current = _threading.current_thread()
        main = _threading.main_thread()
        is_main = current is main
        logger.info(f"Thread check: current={current.name}, main={main.name}, is_main={is_main}")

        if not is_main:
            logger.warning("RNS initialization must be in main thread - skipping node discovery")
            logger.info("RNS node discovery disabled (call start() from main thread to enable)")
            self._rns_connected = False
            return

        # Deferred: importing RNS costs ~180 ms and only this discovery path
        # needs it — at module level it taxed every status_bar/TUI startup.
        RNS, _has_rns = safe_import('RNS')
        if not _has_rns:
            logger.info("RNS module not installed. To enable RNS node discovery:")
            logger.info("  1. Install RNS: pipx install rns")
            logger.info("  2. Start rnsd: sudo systemctl start rnsd")
            logger.info("  3. Restart MeshForge")
            return

        try:
            logger.info("Checking for existing RNS service...")

            # Check if rnsd is already running
            from utils.gateway_diagnostic import find_rns_processes
            rns_pids = find_rns_processes()

            if not rns_pids:
                # No rnsd running - DO NOT initialize our own RNS instance
                # This would bind AutoInterface port and block NomadNet from starting
                logger.info("No rnsd detected - skipping RNS node discovery")
                logger.info("To enable RNS features, start rnsd first: sudo systemctl start rnsd")
                logger.info("MeshForge will operate without RNS node tracking")
                self._rns_connected = False
                return

            # rnsd is running - connect to existing instance as CLIENT ONLY
            logger.info(f"rnsd detected (PID: {rns_pids[0]}), connecting as client...")
            try:
                # Canonical clean-client config (NO interfaces — avoids binding
                # ports rnsd owns) at a FIXED location, SHARED with the bridge
                # connection so the gateway process's RNS singleton resourcepath
                # is deterministic (gw-resourcepath-determinism, 2026-06-27). The
                # helper propagates the box instance_name + rnsd's rpc_key
                # (Issue #37/#40/#41). instance_name is still needed below for
                # the shared-instance preflight + log lines.
                from utils.paths import ReticulumPaths
                client_config_dir = ReticulumPaths.ensure_rns_client_configdir()
                instance_name = ReticulumPaths.get_configured_instance_name()

                # Pre-flight: check if shared instance socket is listening
                try:
                    from utils.service_check import check_rns_shared_instance
                    if not check_rns_shared_instance(instance_name=instance_name):
                        logger.warning(
                            "rnsd PID %d found but shared instance @rns/%s not available "
                            "(may be initializing or hung)", rns_pids[0], instance_name
                        )
                except ImportError:
                    pass  # service_check not available, proceed anyway

                # Connect using client-only config via the guarded chokepoint.
                # require_listener=True keeps node_tracker a pure RNS *consumer*
                # (never becomes the @rns host); the #68 connect probe degrades
                # instead of hanging this MAIN thread on a wedged rnsd. Cold-
                # start RNS attach is genuinely slow (identity load + shared-
                # instance socket open + state sync), so timed_boundary still
                # measures the now-bounded attach time at a higher threshold.
                from utils.rns_init import open_reticulum
                with timed_boundary("rnsd.attach", threshold_s=10.0):
                    self._reticulum = open_reticulum(
                        str(client_config_dir), require_listener=True,
                    )
                if self._reticulum is None:
                    logger.warning(
                        "RNS attach degraded: shared instance @rns/%s absent or "
                        "wedged (#68 fail-open) — node discovery disabled this "
                        "run; will retry on next start.", instance_name)
                    self._rns_connected = False
                    return
                self._rns_connected = True
                logger.info("Connected to existing rnsd instance")

                # Register announce handlers for node discovery
                # We register handlers for specific aspects to get accurate service typing,
                # plus a catch-all handler for unknown service types

                class AspectAnnounceHandler:
                    """Announce handler that passes aspect info to tracker"""
                    def __init__(self, tracker, aspect: str = None):
                        self.tracker = tracker
                        self.aspect_filter = aspect  # None = catch all

                    def received_announce(self, destination_hash, announced_identity, app_data):
                        try:
                            self.tracker._on_rns_announce(
                                destination_hash, announced_identity, app_data,
                                aspect=self.aspect_filter
                            )
                        except Exception as e:
                            logger.error(f"Error handling RNS announce: {e}")

                # Register handlers for known service aspects.
                #
                # IMPORTANT: do NOT also register a None/catch-all handler.
                # RNS aspect filters are NOT exclusive — a None handler
                # receives every announce already covered by the aspect-
                # specific ones, so each LXMF announce was being parsed
                # twice (once with the correct aspect → LXMF_DELIVERY,
                # once via catch-all without aspect → UNKNOWN). The
                # symptom was duplicated `Parsed announce ...` log lines
                # and the node's service_type flapping between
                # LXMF_DELIVERY and UNKNOWN as the second handler
                # overwrote the first. Adding a new aspect to this list
                # is the correct way to broaden coverage; the catch-all
                # is intentionally absent.
                known_aspects = [
                    "lxmf.delivery",       # LXMF messaging (Sideband, NomadNet)
                    "lxmf.propagation",    # LXMF propagation nodes
                    "nomadnetwork.node",   # Nomad Network pages
                ]

                for aspect in known_aspects:
                    RNS.Transport.register_announce_handler(AspectAnnounceHandler(self, aspect))
                    logger.debug(f"Registered announce handler for aspect: {aspect}")
                logger.info(f"Registered {len(known_aspects)} aspect-scoped announce handlers with rnsd")

                # Load known destinations from rnsd (may be empty initially)
                self._load_known_rns_destinations(RNS)

                # Store RNS module reference for background loop
                self._rns_module = RNS

                # Start background loop (will re-check path_table periodically)
                self._rns_thread = threading.Thread(target=self._rns_loop, daemon=True)
                self._rns_thread.start()

                # Schedule delayed re-check after 5 seconds for sync'd data
                def delayed_check():
                    import time
                    time.sleep(5)
                    if self._running and self._rns_connected:
                        logger.debug("Running delayed RNS destination check...")
                        self._load_known_rns_destinations(RNS)

                threading.Thread(target=delayed_check, daemon=True).start()

            except Exception as e:
                logger.warning(f"Could not connect to rnsd: {e}")
                try:
                    from utils.gateway_diagnostic import diagnose_rnsd_connection
                    diagnose_rnsd_connection(rns_pids, error=e)
                except Exception:
                    pass  # diagnostic failure should never block startup
                self._rns_connected = False

        except Exception as e:
            logger.warning(f"Failed to initialize RNS discovery: {e}")
            self._rns_connected = False

    def _rns_loop(self):
        """Background loop for RNS - periodically check for new destinations.

        When connected as a shared instance client, the path_table may not
        be populated immediately. This loop periodically checks for new
        destinations that rnsd has discovered.
        """
        import time
        import RNS

        check_interval = 30  # Check every 30 seconds
        last_check = 0

        while self._running:
            if self._stop_event.wait(1):
                break

            # Periodic check for new RNS destinations
            current_time = time.time()
            if current_time - last_check >= check_interval:
                last_check = current_time
                try:
                    # Re-check path_table for newly discovered routes.
                    # `path_table` is a property — under a wedged rnsd
                    # RPC listener, accessing it has been observed to
                    # block. Snapshot under a hard timeout so a slow
                    # rnsd can't freeze the node-tracker scan thread.
                    new_count = 0
                    path_table_snapshot = bounded_call(
                        "rnsd.path_table",
                        lambda: (
                            dict(RNS.Transport.path_table)
                            if hasattr(RNS.Transport, 'path_table')
                            and RNS.Transport.path_table
                            else {}
                        ),
                        timeout_s=2.0,
                    )
                    if path_table_snapshot:
                        for dest_hash, path_data in path_table_snapshot.items():
                            try:
                                if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                                    node_id = f"rns_{dest_hash.hex()[:16]}"
                                    if node_id not in self._nodes:
                                        hops = 0
                                        if isinstance(path_data, tuple) and len(path_data) > 1:
                                            hops = path_data[1]
                                        node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                        self.add_node(node)
                                        new_count += 1
                                        logger.debug(f"Discovered RNS destination: {dest_hash.hex()[:8]} ({hops} hops)")
                            except Exception as e:
                                logger.debug(f"Error processing path_table entry: {e}")

                    if new_count > 0:
                        logger.info(f"Discovered {new_count} new RNS destinations from path_table")

                except Exception as e:
                    logger.debug(f"Error checking path_table: {e}")

    def stop(self, timeout: float = 5.0):
        """Stop the node tracker and wait for threads to finish

        Args:
            timeout: Seconds to wait for each thread to finish
        """
        logger.info("Stopping node tracker...")
        self._running = False
        self._stop_event.set()

        # Stop network topology tracker
        if self._network_topology:
            self._network_topology.stop(timeout)

        # Wait for cleanup thread to finish
        if hasattr(self, '_cleanup_thread') and self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=timeout)
            if self._cleanup_thread.is_alive():
                logger.warning("Cleanup thread did not stop in time")

        # Wait for RNS thread to finish
        if hasattr(self, '_rns_thread') and self._rns_thread and self._rns_thread.is_alive():
            self._rns_thread.join(timeout=timeout)
            if self._rns_thread.is_alive():
                logger.warning("RNS thread did not stop in time")

        self._save_cache()
        logger.info("Node tracker stopped")

    def add_node(self, node: UnifiedNode):
        """Add or update a node"""
        is_new = False
        with self._lock:
            existing = self._nodes.get(node.id)
            if existing:
                # Merge data
                self._merge_node(existing, node)
            else:
                # Evict oldest offline nodes if at capacity
                if len(self._nodes) >= self.MAX_NODES:
                    self._evict_stale_nodes()
                self._nodes[node.id] = node
                is_new = True
                logger.debug(f"Added new node: {node.id} ({node.name})")

            self._mark_cache_dirty()
            self._notify_callbacks("update", node)

        # Add topology edge for Meshtastic nodes (outside lock to avoid deadlock)
        # This ensures Meshtastic nodes appear in the D3.js topology graph
        if self._network_topology and node.network in ("meshtastic", "both"):
            try:
                self._network_topology.add_edge(
                    source_id="local",
                    dest_id=node.id,
                    hops=node.hops or 0,
                    snr=node.snr,
                    rssi=node.rssi,
                )
            except Exception as e:
                logger.debug(f"Could not add topology edge for {node.id}: {e}")

    def _evict_stale_nodes(self):
        """Evict oldest offline nodes to stay within MAX_NODES. Called under _lock."""
        offline = [
            (nid, n) for nid, n in self._nodes.items()
            if not n.is_online
        ]
        if not offline:
            # All online — evict oldest by last_seen
            offline = list(self._nodes.items())

        # Sort by last_seen ascending (oldest first)
        offline.sort(key=lambda x: x[1].last_seen or datetime.min)

        # Evict 10% to avoid frequent evictions
        evict_count = max(1, len(self._nodes) // 10)
        for nid, _ in offline[:evict_count]:
            del self._nodes[nid]
            self._mark_cache_dirty()

        if evict_count > 0:
            logger.info(f"Evicted {evict_count} stale nodes (capacity: {self.MAX_NODES})")

    def remove_node(self, node_id: str):
        """Remove a node"""
        with self._lock:
            if node_id in self._nodes:
                node = self._nodes.pop(node_id)
                self._mark_cache_dirty()
                self._notify_callbacks("remove", node)
                logger.debug(f"Removed node: {node_id}")

    def get_node(self, node_id: str) -> Optional[UnifiedNode]:
        """Get a node by ID"""
        with self._lock:
            return self._nodes.get(node_id)

    def get_all_nodes(self) -> List[UnifiedNode]:
        """Get all tracked nodes"""
        with self._lock:
            return list(self._nodes.values())

    def get_meshtastic_nodes(self) -> List[UnifiedNode]:
        """Get only Meshtastic nodes"""
        with self._lock:
            return [n for n in self._nodes.values()
                    if n.network in ("meshtastic", "both")]

    def get_rns_nodes(self) -> List[UnifiedNode]:
        """Get only RNS nodes"""
        with self._lock:
            return [n for n in self._nodes.values()
                    if n.network in ("rns", "both")]

    def get_node_by_mesh_id(self, meshtastic_id: str) -> Optional[UnifiedNode]:
        """Get a node by its Meshtastic ID (e.g., !abcd1234)"""
        with self._lock:
            for node in self._nodes.values():
                if node.meshtastic_id == meshtastic_id:
                    return node
            return None

    def get_node_by_short_name(self, short_name: str) -> Optional[UnifiedNode]:
        """Case-insensitive Meshtastic short_name lookup.

        Returns the matching UnifiedNode, or None if absent or ambiguous
        (more than one node shares the same short_name). Ambiguity yields
        None so the caller can fail safe to broadcast rather than guess.
        """
        if not short_name:
            return None
        target = short_name.strip().lower()
        if not target:
            return None
        with self._lock:
            hits = [
                n for n in self._nodes.values()
                if n.meshtastic_id and (n.short_name or "").lower() == target
            ]
        return hits[0] if len(hits) == 1 else None

    def get_node_by_rns_hash(self, rns_hash: bytes) -> Optional[UnifiedNode]:
        """Get a node by its RNS destination hash"""
        with self._lock:
            for node in self._nodes.values():
                if node.rns_hash == rns_hash:
                    return node
            return None

    def get_node_by_meshcore_pubkey(self, pubkey: str) -> Optional[UnifiedNode]:
        """Get a node by its MeshCore public key prefix."""
        with self._lock:
            for node in self._nodes.values():
                if node.meshcore_pubkey and node.meshcore_pubkey == pubkey:
                    return node
            return None

    def get_meshcore_nodes(self) -> list:
        """Get all MeshCore nodes."""
        with self._lock:
            return [n for n in self._nodes.values() if n.network == "meshcore"]

    def get_nodes_with_position(self) -> List[UnifiedNode]:
        """Get nodes that have valid positions"""
        with self._lock:
            return [n for n in self._nodes.values()
                    if n.position and n.position.is_valid()]

    def get_online_nodes(self) -> List[UnifiedNode]:
        """Get online nodes only"""
        with self._lock:
            return [n for n in self._nodes.values() if n.is_online]

    def get_stats(self) -> dict:
        """Get tracker statistics"""
        with self._lock:
            nodes = list(self._nodes.values())
            return {
                "total": len(nodes),
                "meshtastic": sum(1 for n in nodes if n.network in ("meshtastic", "both")),
                "rns": sum(1 for n in nodes if n.network in ("rns", "both")),
                "online": sum(1 for n in nodes if n.is_online),
                "with_position": sum(1 for n in nodes if n.position and n.position.is_valid()),
                "gateways": sum(1 for n in nodes if n.is_gateway),
            }

    def register_callback(self, callback: Callable):
        """Register a callback for node updates"""
        with self._lock:
            self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable):
        """Unregister a callback"""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def _merge_node(self, existing: UnifiedNode, new: UnifiedNode):
        """Merge new node data into existing node"""
        # Update network type if we see it on both
        if existing.network != new.network:
            existing.network = "both"

        # Update identifiers
        if new.meshtastic_id:
            existing.meshtastic_id = new.meshtastic_id
        if new.rns_hash:
            existing.rns_hash = new.rns_hash

        # Update name if we have a better one.
        #
        # A SELF-REPORTED name (one the node announced about itself) always
        # wins: without this, a name recorded once was permanent, so when the
        # propagation parser was fixed on 2026-07-21 the cache kept serving the
        # old mojibake `j^x(` while the gateway logged the correct name. A node
        # must be able to correct what we believe about it.
        #
        # The original rule stays for everything else — an announce whose name
        # could not be parsed falls back to a hash-derived placeholder, and
        # letting that overwrite a good name would be a genuine regression.
        if new.name and (
            getattr(new, "name_is_self_reported", False)
            or not existing.name
            or existing.name.startswith("!")
        ):
            existing.name = new.name
            # Carry the provenance with the value. Without this the flag stayed
            # stale (False) after a self-reported name won, so _save_cache/to_dict
            # served a name whose "is this what the node calls itself?" flag lied
            # (honest_failure_modes #3, the mirror of the 07-21 name-healing fix).
            existing.name_is_self_reported = getattr(new, "name_is_self_reported", False)
        if new.short_name:
            existing.short_name = new.short_name

        # Refresh RNS service classification from this announce.
        #
        # This merge used to ignore service_* entirely, so the field was only
        # ever set when a node was created — an announce from an ALREADY-KNOWN
        # node left it untouched. Combined with the loader dropping it, a
        # node's service_type was unrecoverable once lost, which is why "it
        # heals on the next announce" was false: proven live 2026-07-21, our
        # propagation node's cache entry got a fresh last_seen while
        # service_type stayed None, and the probe that keys on it stayed blind.
        #
        # UNKNOWN never displaces a real classification: RNS aspect filters are
        # not exclusive, and a degraded/catch-all parse must not overwrite a
        # known-good one (the 2026-04 LXMF_DELIVERY<->UNKNOWN flapping). It is
        # still recorded when we know nothing better — that is a real
        # observation, not a downgrade.
        if new.service_type:
            if new.service_type != "UNKNOWN" or not existing.service_type:
                existing.service_type = new.service_type
        if new.service_aspect:
            existing.service_aspect = new.service_aspect
        if new.service_capabilities:
            existing.service_capabilities = list(new.service_capabilities)

        # Update position if newer
        if new.position.is_valid():
            existing.position = new.position

        # Update telemetry if newer
        if new.telemetry.timestamp:
            existing.telemetry = new.telemetry

        # Update metrics with signal quality trending
        if new.snr is not None or new.rssi is not None:
            existing.record_signal_quality(snr=new.snr, rssi=new.rssi)
        if new.hops is not None:
            existing.hops = new.hops

        # Update hardware info
        if new.hardware_model:
            existing.hardware_model = new.hardware_model
        if new.firmware_version:
            existing.firmware_version = new.firmware_version
        if new.role:
            existing.role = new.role

        # PKI/TOFU: route an observed key through the EXISTING node's state
        # machine. from_meshtastic() calls update_pki_status on the throwaway
        # new object, and this merge used to drop it — so the 'PKI KEY
        # CHANGED — potential MITM' transition could only ever fire inside
        # one object's lifetime, i.e. never for an already-known node
        # (2026-07-21 review C1). update_pki_status owns the transitions:
        # first-key TOFU, same-key no-op, changed-key CHANGED.
        if new.pki_status.public_key:
            existing.update_pki_status(new.pki_status.public_key,
                                       is_admin=new.pki_status.is_admin_trusted)

        # MeshCore identity: refresh on every advertisement — a node promoted
        # client→repeater used to keep the stale role for the life of the
        # cache entry (review W1).
        if new.meshcore_pubkey:
            existing.meshcore_pubkey = new.meshcore_pubkey
        if new.meshcore_role:
            existing.meshcore_role = new.meshcore_role
        if new.meshcore_hops is not None:
            existing.meshcore_hops = new.meshcore_hops

        # Favorite flag (BaseUI): set on the throwaway `new` object by
        # from_meshtastic, previously dropped here — a node favorited on the
        # radio AFTER first cache stayed un-favorite forever (07-23 audit,
        # same "unrecoverable once recorded" shape as the name bug). One-way
        # refresh: an un-favorite in a later sweep is absence-of-flag, not a
        # deliberate removal, so only set — never clear — from a merge.
        if new.is_favorite and not existing.is_favorite:
            existing.is_favorite = True
            existing.favorite_updated = new.favorite_updated or datetime.now()

        # Relay-discovery provenance: refresh when the announce carries it
        # (same dropped-on-merge class).
        if new.discovered_via_relay:
            existing.discovered_via_relay = True
        if new.relay_node is not None:
            existing.relay_node = new.relay_node
        if new.next_hop is not None:
            existing.next_hop = new.next_hop

        # Update status
        existing.is_gateway = existing.is_gateway or new.is_gateway
        # is_local: the nodes-db sweep passes is_local=True for our own node;
        # if it was first learned from packets the flag arrived only on the
        # throwaway object (07-23 audit). One-way like is_gateway.
        existing.is_local = existing.is_local or new.is_local
        # Only mark as online if the incoming data is fresh (within threshold)
        if new.last_seen:
            age = (datetime.now() - new.last_seen).total_seconds()
            if age < self.OFFLINE_THRESHOLD:
                existing.update_seen()
            # If new data is stale, don't call update_seen() — preserve existing status
        else:
            existing.update_seen()

    def _notify_callbacks(self, event: str, node: UnifiedNode):
        """Notify registered callbacks and emit to EventBus."""
        for callback in self._callbacks:
            try:
                callback(event, node)
            except Exception as e:
                logger.error(f"Callback error: {e}")

        # Emit to EventBus for decoupled subscribers (status bar, UI, etc.)
        try:
            # Map internal event names to EventBus event_type
            event_type_map = {
                "update": "updated",
                "remove": "lost",
            }
            event_type = event_type_map.get(event, event)

            lat = node.position.latitude if node.position else None
            lon = node.position.longitude if node.position else None

            emit_node_update(
                event_type=event_type,
                node_id=node.id,
                node_name=node.name or "",
                latitude=lat,
                longitude=lon,
                raw_data=node.to_dict() if hasattr(node, 'to_dict') else None,
            )
        except Exception as e:
            logger.debug(f"EventBus node emit failed: {e}")

    def set_retention_pins(self, hashes):
        """Declare the RNS hashes that TTL eviction must never drop.

        Call this even with an EMPTY list — that is the signal "pins have been
        computed and there are none", and it is what arms TTL eviction. Until
        it is called, _evict_expired_nodes is inert.

        The pins exist because eviction is not a neutral act: the
        lxmf_propagation_node_dark probe reports STALE when the configured
        propagation node is present in the cache but quiet, and UNHEARD — read
        as "wrong or truncated hash" — when it is absent entirely. Dropping a
        quiet propagation node would therefore manufacture a false diagnosis
        of a config error, the same shape as the 2026-07-21 false-UNHEARD page.
        """
        pins = set()
        for h in hashes or []:
            if isinstance(h, bytes):
                h = h.hex()
            h = str(h).strip().lower()
            if h:
                pins.add(h)
        self._retention_pins = pins
        logger.debug(f"Retention pins set: {len(pins)} hash(es)")

    def _is_pinned(self, node) -> bool:
        """True when a node must survive TTL eviction regardless of age."""
        if node.is_gateway or node.is_local:
            return True
        pins = self._retention_pins or set()
        h = getattr(node, "rns_hash", None)
        if h:
            try:
                return (h.hex() if isinstance(h, bytes) else str(h)).lower() in pins
            except Exception:
                return False
        return False

    def _retention_seconds(self, node) -> int:
        """Longer tier for our own RF, shorter for the announce firehose."""
        if node.network in ("meshtastic", "both"):
            return self.RETENTION_LOCAL
        return self.RETENTION_EXTERNAL

    def _evict_expired_nodes(self) -> int:
        """Drop nodes past their tier's TTL. Returns the number evicted.

        INERT until set_retention_pins() has been called: an unwired deploy
        must degrade to today's keep-everything behaviour, never to
        'evict with an empty pin set' (honest_failure_modes #4 — the reader
        and writer of the pin list wire together or fail together).

        A node with no last_seen is HELD, not evicted: unknown age is not
        evidence of staleness (#2).
        """
        if self._retention_pins is None:
            if not self._retention_unwired_warned:
                self._retention_unwired_warned = True
                logger.warning(
                    "Node retention is INERT — set_retention_pins() was never "
                    "called, so the announce-space population will grow "
                    "unbounded. Wire it from the gateway config."
                )
            return 0

        now = datetime.now()
        evicted = 0
        with self._lock:
            for nid in [
                nid for nid, n in self._nodes.items()
                if n.last_seen is not None
                and not self._is_pinned(n)
                and (now - n.last_seen).total_seconds() > self._retention_seconds(n)
            ]:
                del self._nodes[nid]
                evicted += 1
            if evicted:
                self._mark_cache_dirty()

        if evicted:
            logger.info(
                f"Retention sweep evicted {evicted} node(s) past TTL "
                f"(local {self.RETENTION_LOCAL // 86400}d / "
                f"external {self.RETENTION_EXTERNAL // 86400}d); "
                f"{len(self._nodes)} remain"
            )
        return evicted

    def _mark_cache_dirty(self):
        """Record that in-memory node state has diverged from the cache file."""
        self._cache_dirty = True

    def _maybe_save_cache(self) -> bool:
        """Write the node cache only if it is due. Returns True if written.

        Two gates, in order: unchanged state is not worth a 20 MB fsync'd
        write, and changed state is not worth writing more than once per
        CACHE_SAVE_INTERVAL. CACHE_MAX_STALENESS is the backstop — a mutation
        path that forgets to mark dirty costs staleness, never permanence.
        """
        elapsed = time.monotonic() - self._last_cache_save
        if self._cache_dirty:
            if elapsed < self.CACHE_SAVE_INTERVAL:
                return False
        elif elapsed < self.CACHE_MAX_STALENESS:
            return False
        self._save_cache()
        return True

    def _cleanup_loop(self):
        """Periodically check node timeouts; write the cache when it is due."""
        while self._running:
            if self._stop_event.wait(self.CLEANUP_TICK):
                break

            with self._lock:
                now = datetime.now()
                for node in self._nodes.values():
                    # Use state machine for timeout checking if available
                    if node._state_machine is not None:
                        if node.check_timeout():
                            self._mark_cache_dirty()
                    elif node.last_seen:
                        # Fallback to simple threshold check
                        age = (now - node.last_seen).total_seconds()
                        if age > self.OFFLINE_THRESHOLD:
                            if node.is_online:
                                self._mark_cache_dirty()
                            node.is_online = False

            # Same pass, so the population sweep is free: we already walked
            # every node above.
            self._evict_expired_nodes()

            # Timeout state is swept every tick; the cache is written at
            # CACHE_SAVE_INTERVAL. This line used to call _save_cache()
            # unconditionally under a comment that said "every 5 minutes" —
            # the comment was the intent, the code was 31.7 GB/day.
            self._maybe_save_cache()

    def _load_cache(self):
        """Load node cache from file"""
        cache_file = self.get_cache_file()
        if not cache_file.exists():
            return

        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)

            for node_data in data.get('nodes', []):
                node = UnifiedNode(
                    id=node_data['id'],
                    network=node_data['network'],
                    name=node_data.get('name', ''),
                    short_name=node_data.get('short_name', ''),
                    meshtastic_id=node_data.get('meshtastic_id'),
                    rns_hash=bytes.fromhex(node_data['rns_hash']) if node_data.get('rns_hash') else None,
                    hardware_model=node_data.get('hardware_model'),
                    role=node_data.get('role'),
                    is_online=False,  # Assume offline until we hear from them
                    # 2026-07-21 review (C2): the loader restored 17 fields
                    # and silently dropped the rest of to_dict()'s output —
                    # the same writer-with-no-reader class as service_type,
                    # enumerated this time. The round-trip test now compares
                    # the FULL serialized shape so the next added field
                    # cannot regress silently.
                    hops=node_data.get('hops'),
                    is_gateway=bool(node_data.get('is_gateway', False)),
                    is_local=bool(node_data.get('is_local', False)),
                    firmware_version=node_data.get('firmware_version'),
                    name_is_self_reported=bool(
                        node_data.get('name_is_self_reported', False)),
                    service_aspect=node_data.get('service_aspect'),
                    service_capabilities=list(
                        node_data.get('service_capabilities') or []),
                    meshcore_pubkey=node_data.get('meshcore_pubkey'),
                    meshcore_role=node_data.get('meshcore_role'),
                    meshcore_hops=node_data.get('meshcore_hops'),
                )
                # Restore first_seen ("known since" must survive a restart);
                # __post_init__ stamps now() when absent.
                if node_data.get('first_seen'):
                    try:
                        node.first_seen = datetime.fromisoformat(node_data['first_seen'])
                    except (ValueError, TypeError):
                        pass
                # Restore telemetry + the PKI/TOFU key baseline. pki_status
                # was the security-relevant twin of the service_type drop:
                # without it every restart erased every node's key baseline,
                # so a key that changed across a restart was silently
                # re-TOFU'd instead of firing the MITM warning (review C1).
                if isinstance(node_data.get('telemetry'), dict):
                    node.telemetry = Telemetry.from_dict(node_data['telemetry'])
                if isinstance(node_data.get('pki_status'), dict):
                    node.pki_status = PKIStatus.from_dict(node_data['pki_status'])
                # Restore last_seen from cache
                if node_data.get('last_seen'):
                    try:
                        node.last_seen = datetime.fromisoformat(node_data['last_seen'])
                    except (ValueError, TypeError):
                        pass
                # Restore position from cache
                pos_data = node_data.get('position')
                if pos_data and isinstance(pos_data, dict):
                    # from_dict keeps the position TIMESTAMP too — without it
                    # position age was unknowable after a restart (review C2).
                    node.position = Position.from_dict(pos_data)
                # Restore signal history from cache
                snr_history = node_data.get('snr_history', [])
                for sample in snr_history:
                    try:
                        ts = datetime.fromisoformat(sample['timestamp'])
                        node.snr_history.append(SignalSample(timestamp=ts, value=sample['value']))
                    except (KeyError, ValueError, TypeError):
                        pass
                rssi_history = node_data.get('rssi_history', [])
                for sample in rssi_history:
                    try:
                        ts = datetime.fromisoformat(sample['timestamp'])
                        node.rssi_history.append(SignalSample(timestamp=ts, value=sample['value']))
                    except (KeyError, ValueError, TypeError):
                        pass
                # Restore current SNR/RSSI values
                if node_data.get('snr') is not None:
                    node.snr = node_data['snr']
                if node_data.get('rssi') is not None:
                    node.rssi = node_data['rssi']
                # Restore state machine from cache if available
                if NODE_STATE_AVAILABLE and node_data.get('state_machine'):
                    try:
                        from .node_state import NodeStateMachine
                        node._state_machine = NodeStateMachine.from_dict(node_data['state_machine'])
                        # Pri-3 (07-23 review): is_online was just forced False
                        # ("not heard since restart"), but the machine may have
                        # persisted an ACTIVE state (e.g. ONLINE) that would make
                        # node.state contradict is_online — a not-yet-heard node
                        # reading as live. Reconcile the live claim to STALE_CACHE
                        # (history preserved); the first real update re-promotes it.
                        node._state_machine.mark_cache_restored()
                    except Exception as e:
                        logger.debug(f"Could not restore state machine: {e}")
                # Restore the RNS service type. to_dict() has always written
                # this; the loader used to drop it, so every restart erased
                # the service type of every known node until it announced
                # again — which for an LXMF propagation node is up to its
                # 360-min interval. A writer with no reader (honest_failure_
                # modes #4); it made probe_lxmf_propagation_node_dark report
                # a healthy configured node as "never heard" (2026-07-21).
                if node_data.get('service_type'):
                    node.service_type = node_data['service_type']
                # Restore favorites from cache (BaseUI 2.7+)
                node.is_favorite = node_data.get('is_favorite', False)
                if node_data.get('favorite_updated'):
                    try:
                        node.favorite_updated = datetime.fromisoformat(node_data['favorite_updated'])
                    except (ValueError, TypeError):
                        pass
                # Relay-discovery provenance (07-23 audit: writer added the
                # same day — reader/writer move together, hfm #4)
                node.discovered_via_relay = bool(
                    node_data.get('discovered_via_relay', False))
                node.relay_node = node_data.get('relay_node')
                node.next_hop = node_data.get('next_hop')
                self._nodes[node.id] = node

            logger.info(f"Loaded {len(self._nodes)} nodes from cache")

        except json.JSONDecodeError as e:
            logger.warning(f"Corrupted node cache: {e}")
            # Backup corrupted file for debugging (matches SettingsManager pattern)
            try:
                backup = cache_file.with_suffix('.json.bak')
                cache_file.rename(backup)
                logger.info(f"Corrupted cache backed up to {backup}")
            except Exception:
                pass  # Backup failure is non-critical
        except Exception as e:
            logger.warning(f"Failed to load node cache: {e}")

    def _save_cache(self):
        """Save node cache to file"""
        try:
            cache_file = self.get_cache_file()
            cache_file.parent.mkdir(parents=True, exist_ok=True)

            with self._lock:
                # Include signal history in cache for persistence
                nodes_data = [n.to_dict(include_signal_history=True) for n in self._nodes.values()]
                # Clear the gate against THIS snapshot, under the same lock
                # that guards it. Clearing after the write instead would drop
                # any mutation that raced the serialization — it would be
                # marked dirty, then un-marked by a write that never contained
                # it, and wait out CACHE_MAX_STALENESS. Restored below if the
                # write fails.
                self._cache_dirty = False

            cache_data = {
                'version': 1,
                'saved_at': datetime.now().isoformat(),
                'nodes': nodes_data
            }

            from utils.paths import atomic_write_text
            # ONE serialization, reused for both files. The indent=2 copy cost
            # 2.8 MB more per write than the compact one and nothing reads a
            # 10 MB file by eye; dumping twice also doubled the transient
            # allocation swing the soak measured (peak 86.5 MB vs current
            # 45.2 MB traced).
            payload = json.dumps(cache_data)
            atomic_write_text(cache_file, payload)
            # The authoritative write landed. Advance the cadence clock HERE,
            # not at the end: the operator-owned copy below is best-effort and
            # must not make a save that already succeeded look like it didn't
            # (feedback_review_your_own_fixes — a failing post-step
            # misreporting a completed publish is the half-state bug that
            # caught us on 69ad7ee).
            self._last_cache_save = time.monotonic()

            # Also save an operator-owned cache for cross-process web-API access.
            # Under ~/.cache/meshforge (not world-writable /tmp) so another local
            # user can't pre-create it with hostile node data. atomic_write_text
            # (mkstemp 0600 + fsync + replace) is symlink-safe AND atomic — the
            # old O_TRUNC fd-dance could expose a torn/empty JSON to the map
            # collectors mid-write. chown_to_operator hands a sudo-created file
            # back so a root first-write can't wedge later operator-uid
            # writers/readers (2026-07-09 review).
            try:
                from utils.paths import (MeshForgePaths, atomic_write_text as _awt,
                                         chown_to_operator)
                cache_path = MeshForgePaths.rns_nodes_cache_path()
                if cache_path.is_symlink():
                    logger.warning(f"Refusing to write to symlink: {cache_path}")
                else:
                    _awt(cache_path, payload)
                    chown_to_operator(cache_path.parent, cache_path)
            except PermissionError as e:
                # A permission failure here silently freezes RNS positions on
                # the map — it must be visible (honest_failure_modes #9).
                if not getattr(self, "_web_cache_perm_warned", False):
                    self._web_cache_perm_warned = True
                    logger.warning(
                        f"Could not save web API cache (permissions — a "
                        f"root-owned leftover? chown it to the operator): {e}")
            except Exception as e:
                logger.debug(f"Could not save web API cache: {e}")

        except Exception as e:
            # A save that raised did not happen: restore the gate so the next
            # tick retries, rather than letting a cleared flag hide the loss
            # until the staleness ceiling (honest_failure_modes #9).
            self._cache_dirty = True
            logger.warning(f"Failed to save node cache: {e}")

    def to_geojson(self) -> dict:
        """Export nodes as GeoJSON for map display"""
        features = []

        for node in self.get_nodes_with_position():
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        node.position.longitude,
                        node.position.latitude
                    ]
                },
                "properties": {
                    "id": node.id,
                    "name": node.name,
                    "network": node.network,
                    "is_online": node.is_online,
                    "is_local": node.is_local,
                    "is_gateway": node.is_gateway,
                    "snr": node.snr,
                    "battery": node.telemetry.battery_level,
                    "last_seen": node.get_age_string(),
                }
            }
            features.append(feature)

        return {
            "type": "FeatureCollection",
            "features": features
        }

    def _load_known_rns_destinations(self, RNS):
        """Load known destinations from RNS path table and identity cache.

        Priority order (most complete first):
        1. RNS.Transport.path_table - complete routing table from rnsd
        2. RNS.Identity.known_destinations - cached identities
        3. RNS.Transport.destinations - local destinations only (fallback)
        """
        try:
            known_count = 0

            # PRIMARY: Check path_table - contains ALL destinations rnsd knows about
            # This is the complete routing table, updated in real-time
            if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
                for dest_hash, path_data in RNS.Transport.path_table.items():
                    try:
                        if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                            node_id = f"rns_{dest_hash.hex()[:16]}"
                            if node_id not in self._nodes:
                                # Extract hop count from path tuple if available
                                hops = 0
                                if isinstance(path_data, tuple) and len(path_data) > 1:
                                    hops = path_data[1]

                                node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                # Store hop count for later use
                                if hasattr(node, 'hops'):
                                    node.hops = hops
                                self.add_node(node)
                                known_count += 1
                                logger.debug(f"Loaded from path_table: {dest_hash.hex()[:8]} ({hops} hops)")
                    except Exception as e:
                        logger.debug(f"Error loading from path_table: {e}")

            # SECONDARY: Check identity known destinations (for any missed in path_table)
            if hasattr(RNS.Identity, 'known_destinations') and RNS.Identity.known_destinations:
                known_dests = RNS.Identity.known_destinations
                # Handle both dict (hash->identity) and list (hashes) formats
                if isinstance(known_dests, dict):
                    dest_hashes = known_dests.keys()
                else:
                    dest_hashes = known_dests

                for dest_hash in dest_hashes:
                    try:
                        if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                            node_id = f"rns_{dest_hash.hex()[:16]}"
                            if node_id not in self._nodes:
                                node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                self.add_node(node)
                                known_count += 1
                                logger.debug(f"Loaded from known_destinations: {dest_hash.hex()[:8]}")
                    except Exception as e:
                        logger.debug(f"Error loading known identity: {e}")

            # TERTIARY: Check Transport.destinations (local only - least useful)
            if hasattr(RNS.Transport, 'destinations') and RNS.Transport.destinations:
                destinations = RNS.Transport.destinations
                if isinstance(destinations, dict):
                    dest_items = destinations.values()
                elif isinstance(destinations, list):
                    dest_items = destinations
                else:
                    dest_items = []

                for dest in dest_items:
                    try:
                        if hasattr(dest, 'hash'):
                            node_id = f"rns_{dest.hash.hex()[:16]}"
                            if node_id not in self._nodes:
                                node = UnifiedNode.from_rns(dest.hash, name="", app_data=None)
                                self.add_node(node)
                                known_count += 1
                    except Exception as e:
                        logger.debug(f"Error loading destination: {e}")

            if known_count > 0:
                logger.info(f"Loaded {known_count} known RNS destinations")
            else:
                logger.debug("No known RNS destinations found (path_table may be empty)")

        except Exception as e:
            logger.debug(f"Could not load known RNS destinations: {e}")

    def _on_rns_announce(self, dest_hash, announced_identity, app_data, aspect: str = None):
        """Handle RNS announce for node discovery.

        Uses the RNS service registry (if available) for multi-service parsing,
        or falls back to legacy LXMF-only parsing.

        Args:
            dest_hash: 16-byte destination hash
            announced_identity: RNS Identity object
            app_data: Raw announce app_data bytes
            aspect: Optional aspect filter from announce handler
        """
        try:
            hash_short = dest_hash.hex()[:8]
            service_info = None
            display_name = ""

            # Use service registry for enhanced parsing if available
            if self._service_registry and RNS_SERVICES_AVAILABLE:
                event = self._service_registry.parse_announce(
                    dest_hash, announced_identity, app_data, aspect
                )
                service_info = event.service_info
                display_name = event.raw_name

                service_type_name = service_info.service_type.name if service_info else "UNKNOWN"
                logger.debug(f"Parsed announce {hash_short}: type={service_type_name}, name={display_name or 'unnamed'}")
            else:
                # Legacy fallback: simple UTF-8 decode
                if app_data:
                    try:
                        display_name = app_data.decode('utf-8', errors='ignore').strip()
                        display_name = ''.join(c for c in display_name if c.isprintable())
                    except Exception as e:
                        logger.debug(f"Could not decode RNS display name: {e}")

            # Create node from announce with service info
            node = UnifiedNode.from_rns(
                dest_hash,
                name=display_name,
                app_data=app_data,
                service_info=service_info,
                aspect=aspect
            )
            self.add_node(node)

            # Update topology edge
            if self._network_topology:
                node_id = f"rns_{dest_hash.hex()[:16]}"
                self._network_topology.add_edge(
                    source_id="local",
                    dest_id=node_id,
                    dest_hash=dest_hash,
                    hops=node.hops or 0,
                )

            service_desc = f"[{node.service_type}]" if node.service_type else ""
            logger.info(f"Discovered RNS node: {hash_short} ({display_name or 'unnamed'}) {service_desc}")

        except Exception as e:
            logger.error(f"Error processing RNS announce: {e}")

    def _on_topology_event(self, event: 'TopologyEvent'):
        """Handle topology change events.

        Updates node information when path table changes are detected.
        """
        if not RNS_SERVICES_AVAILABLE or event.dest_hash is None:
            return

        try:
            node_id = f"rns_{event.dest_hash.hex()[:16]}"

            with self._lock:
                node = self._nodes.get(node_id)
                if node:
                    # Update hop count from topology event
                    if event.new_value is not None and isinstance(event.new_value, int):
                        node.hops = event.new_value
                        node.update_seen()
                        logger.debug(f"Updated node {node_id[:12]} hops: {event.new_value}")

        except Exception as e:
            logger.debug(f"Error handling topology event: {e}")

    # --- Topology API methods ---

    def get_topology_stats(self) -> Optional[Dict[str, Any]]:
        """Get network topology statistics.

        Returns:
            Dict with node_count, edge_count, avg_hops, etc. or None if unavailable
        """
        if self._network_topology:
            return self._network_topology.get_topology_stats()
        return None

    def get_topology(self) -> Optional[Dict[str, Any]]:
        """Get full network topology as dictionary.

        Returns:
            Dict with nodes, edges, and stats or None if unavailable
        """
        if self._network_topology:
            return self._network_topology.to_dict()
        return None

    def trace_path(self, dest_hash: bytes) -> Optional[Dict[str, Any]]:
        """Trace path to a destination through the network.

        Args:
            dest_hash: 16-byte destination hash

        Returns:
            Dict with path info or None if unavailable
        """
        if self._network_topology:
            return self._network_topology.trace_path(dest_hash)
        return None

    def get_recent_topology_events(self, count: int = 50) -> List[Dict[str, Any]]:
        """Get recent topology change events.

        Args:
            count: Maximum number of events to return

        Returns:
            List of event dicts
        """
        if self._network_topology:
            return self._network_topology.get_recent_events(count)
        return []

    def get_service_stats(self) -> Optional[Dict[str, int]]:
        """Get counts of discovered services by type.

        Returns:
            Dict mapping service type names to counts or None if unavailable
        """
        if self._service_registry:
            return self._service_registry.get_stats()
        return None


# Global node tracker instance (singleton)
_node_tracker: Optional[UnifiedNodeTracker] = None


def get_node_tracker() -> UnifiedNodeTracker:
    """Get the global node tracker instance.

    Returns a singleton UnifiedNodeTracker that is shared across the application.
    The tracker is created on first call and reused thereafter.

    Returns:
        UnifiedNodeTracker: The global node tracker instance
    """
    global _node_tracker
    if _node_tracker is None:
        _node_tracker = UnifiedNodeTracker()
    return _node_tracker

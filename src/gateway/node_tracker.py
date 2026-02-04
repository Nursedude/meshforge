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

# Import RNS service registry and topology (optional - graceful fallback)
try:
    from .rns_services import (
        RNSServiceType, ServiceInfo, AnnounceEvent,
        get_service_registry, RNSServiceRegistry
    )
    from .network_topology import (
        NetworkTopology, get_network_topology, TopologyEvent
    )
except ImportError:
    RNSServiceType = None  # type: ignore
    ServiceInfo = None  # type: ignore
    RNSServiceRegistry = None  # type: ignore
    NetworkTopology = None  # type: ignore
    get_network_topology = None  # type: ignore
    TopologyEvent = None  # type: ignore

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        """Fallback for when utils.paths is not in Python path."""
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            return Path(f'/home/{sudo_user}')
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            return Path(f'/home/{logname}')
        return Path('/root')


class UnifiedNodeTracker:
    """
    Tracks nodes from both RNS and Meshtastic networks.
    Provides unified view for map display and monitoring.

    Enhanced features:
    - Multi-service RNS announce parsing via RNSServiceRegistry
    - Network topology graph via NetworkTopology
    - Path table change monitoring with event logging
    """

    OFFLINE_THRESHOLD = 3600  # 1 hour
    MAX_NODES = 10000  # Prevent unbounded memory growth

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

        try:
            import RNS
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
                # Create a client-only config to avoid interface conflicts
                # This prevents RNS from trying to bind ports that rnsd already owns
                import tempfile
                client_config_dir = Path(tempfile.gettempdir()) / "meshforge_rns_client"
                client_config_dir.mkdir(exist_ok=True)
                client_config_file = client_config_dir / "config"

                # Write minimal client-only config (no interfaces, just shared transport)
                client_config_file.write_text("""# MeshForge RNS Client Config (auto-generated)
# This config connects to existing rnsd without creating interfaces

[reticulum]
share_instance = Yes
shared_instance_port = 37428
instance_control_port = 37429
""")

                # Connect using client-only config
                self._reticulum = RNS.Reticulum(configdir=str(client_config_dir))
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

                # Register handlers for known service aspects
                known_aspects = [
                    "lxmf.delivery",       # LXMF messaging (Sideband, NomadNet)
                    "lxmf.propagation",    # LXMF propagation nodes
                    "nomadnetwork.node",   # Nomad Network pages
                ]

                for aspect in known_aspects:
                    RNS.Transport.register_announce_handler(AspectAnnounceHandler(self, aspect))
                    logger.debug(f"Registered announce handler for aspect: {aspect}")

                # Also register a catch-all handler for unknown services
                RNS.Transport.register_announce_handler(AspectAnnounceHandler(self, None))
                logger.info(f"Registered {len(known_aspects) + 1} announce handlers with rnsd")

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
                logger.info("RNS nodes may not appear on map - ensure rnsd is running properly")
                self._rns_connected = False

        except ImportError:
            logger.info("RNS module not installed. To enable RNS node discovery:")
            logger.info("  1. Install RNS: pipx install rns")
            logger.info("  2. Start rnsd: sudo systemctl start rnsd")
            logger.info("  3. Restart MeshForge")
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
                    # Re-check path_table for newly discovered routes
                    new_count = 0
                    if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
                        for dest_hash, path_data in RNS.Transport.path_table.items():
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

        if evict_count > 0:
            logger.info(f"Evicted {evict_count} stale nodes (capacity: {self.MAX_NODES})")

    def remove_node(self, node_id: str):
        """Remove a node"""
        with self._lock:
            if node_id in self._nodes:
                node = self._nodes.pop(node_id)
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

    def get_node_by_rns_hash(self, rns_hash: bytes) -> Optional[UnifiedNode]:
        """Get a node by its RNS destination hash"""
        with self._lock:
            for node in self._nodes.values():
                if node.rns_hash == rns_hash:
                    return node
            return None

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

        # Update name if we have a better one
        if new.name and (not existing.name or existing.name.startswith("!")):
            existing.name = new.name
        if new.short_name:
            existing.short_name = new.short_name

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

        # Update status
        existing.is_gateway = existing.is_gateway or new.is_gateway
        existing.update_seen()

    def _notify_callbacks(self, event: str, node: UnifiedNode):
        """Notify registered callbacks"""
        for callback in self._callbacks:
            try:
                callback(event, node)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def _cleanup_loop(self):
        """Periodically check node timeouts and save cache"""
        while self._running:
            if self._stop_event.wait(60):
                break

            with self._lock:
                now = datetime.now()
                for node in self._nodes.values():
                    # Use state machine for timeout checking if available
                    if node._state_machine is not None:
                        node.check_timeout()
                    elif node.last_seen:
                        # Fallback to simple threshold check
                        age = (now - node.last_seen).total_seconds()
                        if age > self.OFFLINE_THRESHOLD:
                            node.is_online = False

            # Save cache every 5 minutes
            self._save_cache()

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
                )
                # Restore last_seen from cache
                if node_data.get('last_seen'):
                    try:
                        node.last_seen = datetime.fromisoformat(node_data['last_seen'])
                    except (ValueError, TypeError):
                        pass
                # Restore position from cache
                pos_data = node_data.get('position')
                if pos_data and isinstance(pos_data, dict):
                    node.position = Position(
                        latitude=pos_data.get('latitude', 0.0),
                        longitude=pos_data.get('longitude', 0.0),
                        altitude=pos_data.get('altitude', 0.0),
                    )
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
                    except Exception as e:
                        logger.debug(f"Could not restore state machine: {e}")
                # Restore favorites from cache (BaseUI 2.7+)
                node.is_favorite = node_data.get('is_favorite', False)
                if node_data.get('favorite_updated'):
                    try:
                        node.favorite_updated = datetime.fromisoformat(node_data['favorite_updated'])
                    except (ValueError, TypeError):
                        pass
                self._nodes[node.id] = node

            logger.info(f"Loaded {len(self._nodes)} nodes from cache")

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

            cache_data = {
                'version': 1,
                'saved_at': datetime.now().isoformat(),
                'nodes': nodes_data
            }

            from utils.paths import atomic_write_text
            atomic_write_text(cache_file, json.dumps(cache_data, indent=2))

            # Also save to /tmp for web API access (cross-process sharing)
            try:
                tmp_path = '/tmp/meshforge_rns_nodes.json'
                if os.path.islink(tmp_path):
                    logger.warning(f"Refusing to write to symlink: {tmp_path}")
                else:
                    fd = os.open(
                        tmp_path,
                        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                        0o644
                    )
                    with os.fdopen(fd, 'w') as f:
                        json.dump(cache_data, f)
            except Exception as e:
                logger.debug(f"Could not save web API cache: {e}")

        except Exception as e:
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

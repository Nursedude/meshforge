"""
Unified Node Tracker for RNS and Meshtastic Networks
Tracks nodes from both networks with position and telemetry data
"""

import threading
import time
import logging
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
from pathlib import Path
import json

logger = logging.getLogger(__name__)

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


@dataclass
class Position:
    """Geographic position"""
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    precision: int = 5  # decimal places
    timestamp: Optional[datetime] = None

    def is_valid(self) -> bool:
        """Check if position is valid"""
        return (self.latitude != 0.0 or self.longitude != 0.0) and \
               -90 <= self.latitude <= 90 and -180 <= self.longitude <= 180

    def to_dict(self) -> dict:
        return {
            "latitude": round(self.latitude, self.precision),
            "longitude": round(self.longitude, self.precision),
            "altitude": self.altitude,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }


@dataclass
class Telemetry:
    """Node telemetry data"""
    battery_level: Optional[int] = None  # 0-100
    voltage: Optional[float] = None
    temperature: Optional[float] = None  # Celsius
    humidity: Optional[float] = None  # 0-100%
    pressure: Optional[float] = None  # hPa
    air_quality: Optional[int] = None
    uptime: Optional[int] = None  # seconds
    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "battery_level": self.battery_level,
            "voltage": self.voltage,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "pressure": self.pressure,
            "air_quality": self.air_quality,
            "uptime": self.uptime,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }.items() if v is not None}


@dataclass
class UnifiedNode:
    """Represents a node from either RNS or Meshtastic network"""
    # Core identity
    id: str  # Unified identifier (network prefix + hash/id)
    network: str  # "meshtastic", "rns", or "both"
    name: str = ""
    short_name: str = ""

    # Position and telemetry
    position: Position = field(default_factory=Position)
    telemetry: Telemetry = field(default_factory=Telemetry)

    # Network-specific identifiers
    meshtastic_id: Optional[str] = None  # !abcd1234
    rns_hash: Optional[bytes] = None  # 16-byte destination hash

    # Radio metrics
    snr: Optional[float] = None
    rssi: Optional[int] = None
    hops: Optional[int] = None

    # Status
    is_online: bool = False
    is_gateway: bool = False
    is_local: bool = False  # Is this our own node
    last_seen: Optional[datetime] = None
    first_seen: Optional[datetime] = None

    # Hardware info
    hardware_model: Optional[str] = None
    firmware_version: Optional[str] = None
    role: Optional[str] = None

    def __post_init__(self):
        if self.first_seen is None:
            self.first_seen = datetime.now()

    def update_seen(self):
        """Update last seen timestamp"""
        self.last_seen = datetime.now()
        self.is_online = True

    def get_age_string(self) -> str:
        """Get human-readable time since last seen"""
        if not self.last_seen:
            return "Never"

        delta = datetime.now() - self.last_seen
        seconds = delta.total_seconds()

        if seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m ago"
        elif seconds < 86400:
            return f"{int(seconds / 3600)}h ago"
        else:
            return f"{int(seconds / 86400)}d ago"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "network": self.network,
            "name": self.name,
            "short_name": self.short_name,
            "position": self.position.to_dict() if self.position.is_valid() else None,
            "telemetry": self.telemetry.to_dict(),
            "meshtastic_id": self.meshtastic_id,
            "rns_hash": self.rns_hash.hex() if self.rns_hash else None,
            "snr": self.snr,
            "rssi": self.rssi,
            "hops": self.hops,
            "is_online": self.is_online,
            "is_gateway": self.is_gateway,
            "is_local": self.is_local,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "last_seen_ago": self.get_age_string(),
            "hardware_model": self.hardware_model,
            "firmware_version": self.firmware_version,
            "role": self.role,
        }

    @classmethod
    def from_meshtastic(cls, mesh_node: dict, is_local: bool = False) -> 'UnifiedNode':
        """Create from Meshtastic node data"""
        node_id = mesh_node.get('num', 0)
        user = mesh_node.get('user', {})
        position = mesh_node.get('position', {})
        metrics = mesh_node.get('deviceMetrics', {})

        meshtastic_id = f"!{node_id:08x}"

        node = cls(
            id=f"mesh_{meshtastic_id}",
            network="meshtastic",
            name=user.get('longName', meshtastic_id),
            short_name=user.get('shortName', ''),
            meshtastic_id=meshtastic_id,
            is_local=is_local,
            hardware_model=user.get('hwModel'),
            role=user.get('role'),
        )

        # Position
        if position:
            node.position = Position(
                latitude=position.get('latitude', 0) or 0,
                longitude=position.get('longitude', 0) or 0,
                altitude=position.get('altitude', 0) or 0,
                timestamp=datetime.now()
            )

        # Telemetry
        if metrics:
            node.telemetry = Telemetry(
                battery_level=metrics.get('batteryLevel'),
                voltage=metrics.get('voltage'),
                uptime=metrics.get('uptimeSeconds'),
                timestamp=datetime.now()
            )

        # Radio metrics
        node.snr = mesh_node.get('snr')
        node.hops = mesh_node.get('hopsAway')
        node.last_seen = datetime.now()

        return node

    @classmethod
    def from_rns(cls, rns_hash: bytes, name: str = "", app_data: bytes = None) -> 'UnifiedNode':
        """Create from RNS announce/discovery data"""
        hash_hex = rns_hash.hex()

        node = cls(
            id=f"rns_{hash_hex[:16]}",
            network="rns",
            name=name or hash_hex[:8],
            short_name=hash_hex[:4].upper(),
            rns_hash=rns_hash,
        )

        # Parse app_data if available (may contain name, position, etc.)
        if app_data:
            try:
                # LXMF announces include display name
                if len(app_data) > 0:
                    # First byte might be display name length
                    # This varies by application
                    pass
            except Exception:
                pass

        node.last_seen = datetime.now()
        return node


class UnifiedNodeTracker:
    """
    Tracks nodes from both RNS and Meshtastic networks.
    Provides unified view for map display and monitoring.
    """

    CACHE_FILE = get_real_user_home() / ".config" / "meshforge" / "node_cache.json"
    OFFLINE_THRESHOLD = 3600  # 1 hour

    def __init__(self):
        self._nodes: Dict[str, UnifiedNode] = {}
        self._lock = threading.RLock()
        self._callbacks: List[Callable] = []
        self._running = False
        self._cleanup_thread = None
        self._rns_thread = None
        self._reticulum = None
        self._rns_connected = False

        # Load cached nodes
        self._load_cache()

    def start(self):
        """Start the node tracker"""
        self._running = True
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        # Initialize RNS in the main thread to avoid signal handler issues
        # RNS.Reticulum() sets up signal handlers which only work in main thread
        self._init_rns_main_thread()

        logger.info("Node tracker started")

    def _init_rns_main_thread(self):
        """Initialize RNS from main thread, then start background listener"""
        try:
            import RNS
            logger.info("Initializing RNS for node discovery...")

            # Initialize Reticulum - this sets up signal handlers
            # Must be done from main thread
            # If RNS is already running (e.g., rnsd), we'll use the shared instance
            try:
                self._reticulum = RNS.Reticulum()
            except OSError as e:
                # Handle "Address already in use" error (errno 98)
                from utils.gateway_diagnostic import handle_address_in_use_error
                error_info = handle_address_in_use_error(e, logger)

                if error_info['is_address_in_use']:
                    if error_info['can_use_shared']:
                        # An RNS daemon is running, use shared transport
                        logger.info("RNS already running (rnsd), using shared instance")
                        self._reticulum = None  # Use shared transport
                    else:
                        # Port in use but no rnsd - likely a stale process
                        logger.error(f"Cannot initialize RNS: {error_info['message']}")
                        for fix in error_info['fix_options']:
                            logger.info(f"  Fix: {fix}")
                        self._rns_connected = False
                        return
                else:
                    raise
            except Exception as e:
                if "reinitialise" in str(e).lower() or "already running" in str(e).lower():
                    # RNS is already running (rnsd), use shared transport
                    logger.info("RNS already running, using shared instance")
                    self._reticulum = None  # We don't need a direct reference
                else:
                    raise

            # Create announce handler
            class NodeAnnounceHandler:
                def __init__(self, tracker):
                    self.tracker = tracker
                    self.aspect_filter = None

                def received_announce(self, destination_hash, announced_identity, app_data):
                    try:
                        self.tracker._on_rns_announce(destination_hash, announced_identity, app_data)
                    except Exception as e:
                        logger.error(f"Error handling RNS announce: {e}")

            RNS.Transport.register_announce_handler(NodeAnnounceHandler(self))
            self._rns_connected = True
            logger.info("RNS discovery initialized - listening for announces")

            # Load known destinations
            self._load_known_rns_destinations(RNS)

            # Start background loop to keep RNS alive
            self._rns_thread = threading.Thread(target=self._rns_loop, daemon=True)
            self._rns_thread.start()

        except ImportError:
            logger.info("RNS module not installed. To enable RNS node discovery:")
            logger.info("  1. Install RNS: pip install rns")
            logger.info("  2. Configure ~/.reticulum/config with TCPClientInterface")
            logger.info("  3. Restart MeshForge")
        except Exception as e:
            logger.warning(f"Failed to initialize RNS discovery: {e}")
            self._rns_connected = False

    def _rns_loop(self):
        """Background loop to keep RNS connection alive"""
        import time
        while self._running:
            time.sleep(1)

    def stop(self, timeout: float = 5.0):
        """Stop the node tracker and wait for threads to finish

        Args:
            timeout: Seconds to wait for each thread to finish
        """
        logger.info("Stopping node tracker...")
        self._running = False

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
        with self._lock:
            existing = self._nodes.get(node.id)
            if existing:
                # Merge data
                self._merge_node(existing, node)
            else:
                self._nodes[node.id] = node
                logger.debug(f"Added new node: {node.id} ({node.name})")

            self._notify_callbacks("update", node)

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
        self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable):
        """Unregister a callback"""
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

        # Update metrics
        if new.snr is not None:
            existing.snr = new.snr
        if new.rssi is not None:
            existing.rssi = new.rssi
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
        """Periodically mark offline nodes and save cache"""
        while self._running:
            time.sleep(60)

            with self._lock:
                now = datetime.now()
                for node in self._nodes.values():
                    if node.last_seen:
                        age = (now - node.last_seen).total_seconds()
                        if age > self.OFFLINE_THRESHOLD:
                            node.is_online = False

            # Save cache every 5 minutes
            self._save_cache()

    def _load_cache(self):
        """Load node cache from file"""
        if not self.CACHE_FILE.exists():
            return

        try:
            with open(self.CACHE_FILE, 'r') as f:
                data = json.load(f)

            for node_data in data.get('nodes', []):
                # Reconstruct node (simplified - positions may be stale)
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
                self._nodes[node.id] = node

            logger.info(f"Loaded {len(self._nodes)} nodes from cache")

        except Exception as e:
            logger.warning(f"Failed to load node cache: {e}")

    def _save_cache(self):
        """Save node cache to file"""
        try:
            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

            with self._lock:
                nodes_data = [n.to_dict() for n in self._nodes.values()]

            with open(self.CACHE_FILE, 'w') as f:
                json.dump({
                    'version': 1,
                    'saved_at': datetime.now().isoformat(),
                    'nodes': nodes_data
                }, f, indent=2)

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
        """Load known destinations from RNS identity/destination cache"""
        try:
            # Check for known destinations in the transport layer
            known_count = 0

            # Try to access known destinations from Transport
            if hasattr(RNS.Transport, 'destinations') and RNS.Transport.destinations:
                for dest_hash, dest in RNS.Transport.destinations.items():
                    try:
                        if hasattr(dest, 'hash'):
                            node = UnifiedNode.from_rns(dest.hash, name="", app_data=None)
                            self.add_node(node)
                            known_count += 1
                    except Exception as e:
                        logger.debug(f"Error loading destination: {e}")

            # Also check the identity known destinations
            if hasattr(RNS.Identity, 'known_destinations') and RNS.Identity.known_destinations:
                for dest_hash in RNS.Identity.known_destinations:
                    try:
                        if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                            # Check if we already have this node
                            node_id = f"rns_{dest_hash.hex()[:16]}"
                            if node_id not in self._nodes:
                                node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                self.add_node(node)
                                known_count += 1
                    except Exception as e:
                        logger.debug(f"Error loading known identity: {e}")

            if known_count > 0:
                logger.info(f"Loaded {known_count} known RNS destinations")

        except Exception as e:
            logger.debug(f"Could not load known RNS destinations: {e}")

    def _on_rns_announce(self, dest_hash, announced_identity, app_data):
        """Handle RNS announce for node discovery"""
        try:
            # Parse display name from app_data if available
            display_name = ""
            if app_data:
                try:
                    # LXMF announces typically include display name
                    # Try to decode as UTF-8 string
                    display_name = app_data.decode('utf-8', errors='ignore').strip()
                    # Clean up - remove non-printable characters
                    display_name = ''.join(c for c in display_name if c.isprintable())
                except Exception:
                    pass

            # Create node from announce
            node = UnifiedNode.from_rns(dest_hash, name=display_name, app_data=app_data)
            self.add_node(node)

            hash_short = dest_hash.hex()[:8]
            logger.info(f"Discovered RNS node: {hash_short} ({display_name or 'unnamed'})")

        except Exception as e:
            logger.error(f"Error processing RNS announce: {e}")

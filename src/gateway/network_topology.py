"""
Network Topology Graph for RNS/Meshtastic Networks

Provides graph-based representation of mesh network topology with:
- Edge tracking between nodes (with hop counts, metrics)
- Path table change detection and event logging
- Path tracing capabilities
- Topology analysis utilities

Reference: RNS uses destination hashes and hop counts for routing
"""

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Dict, List, Optional, Set, Tuple, Callable, Any

from utils.boundary_timing import timed_boundary
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Optional RNS transport for path table monitoring
_RNS, _HAS_RNS = safe_import('RNS')


class TopologyEventType(Enum):
    """Types of topology change events"""
    NODE_ADDED = auto()
    NODE_REMOVED = auto()
    NODE_UPDATED = auto()
    EDGE_ADDED = auto()
    EDGE_REMOVED = auto()
    EDGE_UPDATED = auto()
    PATH_DISCOVERED = auto()
    PATH_LOST = auto()
    HOP_COUNT_CHANGED = auto()


@dataclass
class TopologyEvent:
    """Represents a change in network topology"""
    event_type: TopologyEventType
    timestamp: datetime = field(default_factory=datetime.now)
    node_id: Optional[str] = None
    dest_hash: Optional[bytes] = None
    old_value: Any = None
    new_value: Any = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.name,
            "timestamp": self.timestamp.isoformat(),
            "node_id": self.node_id,
            "dest_hash": self.dest_hash.hex() if self.dest_hash else None,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "details": self.details,
        }


@dataclass
class NetworkEdge:
    """
    Represents a connection/path between two nodes.

    In RNS, edges are inferred from path table data which shows
    how to reach a destination through the network.
    """
    source_id: str  # Node ID or "local" for our node
    dest_id: str    # Target node ID
    dest_hash: Optional[bytes] = None

    # Path metrics
    hops: int = 0           # Hop count from path table
    interface: str = ""     # RNS interface name (if available)

    # Timing
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)

    # Signal metrics (when available - primarily Meshtastic)
    snr: Optional[float] = None
    rssi: Optional[int] = None

    # Edge state
    is_active: bool = True
    announce_count: int = 0  # Number of announces received on this path

    def update(self, hops: int = None, snr: float = None, rssi: int = None):
        """Update edge metrics"""
        self.last_seen = datetime.now()
        self.last_updated = datetime.now()
        self.announce_count += 1
        self.is_active = True

        if hops is not None:
            self.hops = hops
        if snr is not None:
            self.snr = snr
        if rssi is not None:
            self.rssi = rssi

    def get_weight(self) -> float:
        """Calculate edge weight for pathfinding (lower is better).

        Weight is based on:
        - Hop count (primary factor)
        - SNR/RSSI if available (secondary)
        - Freshness of the path (tertiary)
        """
        weight = float(self.hops + 1)  # Base weight from hops

        # Adjust for signal quality (if available)
        if self.snr is not None:
            # Better SNR = lower weight adjustment
            # SNR typically -20 to +20 dB
            snr_factor = max(0.5, 1.0 - (self.snr / 40.0))
            weight *= snr_factor

        # Penalize stale paths slightly
        age_seconds = (datetime.now() - self.last_seen).total_seconds()
        if age_seconds > 300:  # 5 minutes
            staleness = min(2.0, 1.0 + (age_seconds / 3600))
            weight *= staleness

        return weight

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "dest_id": self.dest_id,
            "dest_hash": self.dest_hash.hex() if self.dest_hash else None,
            "hops": self.hops,
            "interface": self.interface,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "snr": self.snr,
            "rssi": self.rssi,
            "is_active": self.is_active,
            "announce_count": self.announce_count,
            "weight": self.get_weight(),
        }


@dataclass
class PathTableEntry:
    """Snapshot of a path table entry for change detection"""
    dest_hash: bytes
    hops: int
    interface_hash: Optional[bytes] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def __eq__(self, other):
        if not isinstance(other, PathTableEntry):
            return False
        return (self.dest_hash == other.dest_hash and
                self.hops == other.hops and
                self.interface_hash == other.interface_hash)


class PathTableMonitor:
    """
    Monitors RNS path table for changes and emits events.

    Tracks additions, removals, and hop count changes in the
    routing table to provide real-time topology updates.
    """

    def __init__(self, check_interval: float = 10.0):
        self._lock = threading.RLock()
        self._running = False
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

        self._check_interval = check_interval
        self._last_snapshot: Dict[bytes, PathTableEntry] = {}
        self._event_callbacks: List[Callable[[TopologyEvent], None]] = []
        self._event_log: List[TopologyEvent] = []
        self._max_log_size = 1000

    def start(self):
        """Start monitoring the path table"""
        self._running = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info(f"Path table monitor started (interval: {self._check_interval}s)")

    def stop(self, timeout: float = 5.0):
        """Stop monitoring"""
        self._running = False
        self._stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=timeout)
        logger.info("Path table monitor stopped")

    def register_callback(self, callback: Callable[[TopologyEvent], None]):
        """Register callback for topology events"""
        with self._lock:
            self._event_callbacks.append(callback)

    def get_recent_events(self, count: int = 50) -> List[TopologyEvent]:
        """Get recent topology events"""
        with self._lock:
            return list(self._event_log[-count:])

    def _monitor_loop(self):
        """Background loop checking for path table changes"""
        while self._running:
            if self._stop_event.wait(self._check_interval):
                break

            try:
                self._check_path_table()
            except Exception as e:
                logger.debug(f"Path table check error: {e}")

    def _check_path_table(self):
        """Check path table for changes and emit events"""
        if not _HAS_RNS:
            return  # RNS not installed

        try:
            RNS = _RNS

            # Snapshot path_table inside one timed_boundary block. On a
            # shared-instance client this is a mostly-local read, but
            # slowness here flags sync pressure on rnsd or a very large
            # table — exactly what the wedge investigation needs visible.
            with timed_boundary("rnsd.path_table_read"):
                if not hasattr(RNS.Transport, 'path_table') or not RNS.Transport.path_table:
                    return
                path_table_items = list(RNS.Transport.path_table.items())

            current_snapshot: Dict[bytes, PathTableEntry] = {}

            # Build current snapshot
            for dest_hash, path_data in path_table_items:
                if not isinstance(dest_hash, bytes) or len(dest_hash) != 16:
                    continue

                hops = 0
                interface_hash = None

                if isinstance(path_data, tuple):
                    if len(path_data) > 1:
                        hops = path_data[1] if isinstance(path_data[1], int) else 0
                    if len(path_data) > 0 and path_data[0] is not None:
                        # Interface reference - get hash if available
                        try:
                            if hasattr(path_data[0], 'hash'):
                                interface_hash = path_data[0].hash
                        except Exception:
                            pass

                current_snapshot[dest_hash] = PathTableEntry(
                    dest_hash=dest_hash,
                    hops=hops,
                    interface_hash=interface_hash,
                )

            # Compare with last snapshot
            with self._lock:
                # Check for new paths
                for dest_hash, entry in current_snapshot.items():
                    if dest_hash not in self._last_snapshot:
                        self._emit_event(TopologyEvent(
                            event_type=TopologyEventType.PATH_DISCOVERED,
                            dest_hash=dest_hash,
                            new_value=entry.hops,
                            details={"interface_hash": entry.interface_hash.hex() if entry.interface_hash else None},
                        ))
                    else:
                        old_entry = self._last_snapshot[dest_hash]
                        if entry.hops != old_entry.hops:
                            self._emit_event(TopologyEvent(
                                event_type=TopologyEventType.HOP_COUNT_CHANGED,
                                dest_hash=dest_hash,
                                old_value=old_entry.hops,
                                new_value=entry.hops,
                            ))

                # Check for lost paths
                for dest_hash in self._last_snapshot:
                    if dest_hash not in current_snapshot:
                        self._emit_event(TopologyEvent(
                            event_type=TopologyEventType.PATH_LOST,
                            dest_hash=dest_hash,
                            old_value=self._last_snapshot[dest_hash].hops,
                        ))

                # Update snapshot
                self._last_snapshot = current_snapshot

        except Exception as e:
            logger.debug(f"Path table check failed: {e}")

    def _emit_event(self, event: TopologyEvent):
        """Emit a topology event to callbacks and log"""
        # Add to log
        self._event_log.append(event)
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size:]

        # Log significant events
        hash_short = event.dest_hash.hex()[:8] if event.dest_hash else "unknown"
        if event.event_type == TopologyEventType.PATH_DISCOVERED:
            logger.info(f"Path discovered: {hash_short} ({event.new_value} hops)")
        elif event.event_type == TopologyEventType.PATH_LOST:
            logger.info(f"Path lost: {hash_short}")
        elif event.event_type == TopologyEventType.HOP_COUNT_CHANGED:
            logger.debug(f"Hop count changed: {hash_short} {event.old_value} -> {event.new_value} hops")

        # Notify callbacks
        for callback in self._event_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Topology event callback error: {e}")


class NetworkTopology:
    """
    Graph representation of the mesh network topology.

    Maintains nodes and edges with metrics, supports path tracing
    and topology analysis.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._nodes: Dict[str, Dict[str, Any]] = {}  # node_id -> metadata
        self._edges: Dict[Tuple[str, str], NetworkEdge] = {}  # (src, dst) -> edge
        self._adjacency: Dict[str, Set[str]] = defaultdict(set)  # node_id -> neighbors

        # Path table integration
        self._path_monitor = PathTableMonitor()
        self._path_monitor.register_callback(self._on_path_event)

        # Topology event callbacks
        self._callbacks: List[Callable[[TopologyEvent], None]] = []

    def start(self):
        """Start topology tracking"""
        self._path_monitor.start()
        logger.info("Network topology tracker started")

    def stop(self, timeout: float = 5.0):
        """Stop topology tracking"""
        self._path_monitor.stop(timeout)
        logger.info("Network topology tracker stopped")

    def register_callback(self, callback: Callable[[TopologyEvent], None]):
        """Register callback for topology changes"""
        with self._lock:
            self._callbacks.append(callback)

    def add_node(self, node_id: str, metadata: Dict[str, Any] = None):
        """Add or update a node in the topology"""
        with self._lock:
            is_new = node_id not in self._nodes
            self._nodes[node_id] = metadata or {}

            if is_new:
                self._emit_event(TopologyEvent(
                    event_type=TopologyEventType.NODE_ADDED,
                    node_id=node_id,
                    details=metadata or {},
                ))

    def remove_node(self, node_id: str):
        """Remove a node and its edges"""
        with self._lock:
            if node_id not in self._nodes:
                return

            # Remove edges
            edges_to_remove = []
            for (src, dst), edge in self._edges.items():
                if src == node_id or dst == node_id:
                    edges_to_remove.append((src, dst))

            for key in edges_to_remove:
                del self._edges[key]
                src, dst = key
                self._adjacency[src].discard(dst)
                self._adjacency[dst].discard(src)

            del self._nodes[node_id]
            self._emit_event(TopologyEvent(
                event_type=TopologyEventType.NODE_REMOVED,
                node_id=node_id,
            ))

    def add_edge(self, source_id: str, dest_id: str,
                 dest_hash: bytes = None, hops: int = 0,
                 snr: float = None, rssi: int = None,
                 interface: str = "") -> NetworkEdge:
        """Add or update an edge between nodes"""
        with self._lock:
            key = (source_id, dest_id)

            if key in self._edges:
                edge = self._edges[key]
                old_hops = edge.hops
                edge.update(hops=hops, snr=snr, rssi=rssi)

                if hops != old_hops:
                    self._emit_event(TopologyEvent(
                        event_type=TopologyEventType.EDGE_UPDATED,
                        node_id=dest_id,
                        dest_hash=dest_hash,
                        old_value=old_hops,
                        new_value=hops,
                    ))
            else:
                edge = NetworkEdge(
                    source_id=source_id,
                    dest_id=dest_id,
                    dest_hash=dest_hash,
                    hops=hops,
                    interface=interface,
                    snr=snr,
                    rssi=rssi,
                )
                self._edges[key] = edge
                self._adjacency[source_id].add(dest_id)
                self._adjacency[dest_id].add(source_id)

                # Ensure nodes exist
                if source_id not in self._nodes:
                    self._nodes[source_id] = {}
                if dest_id not in self._nodes:
                    self._nodes[dest_id] = {}

                self._emit_event(TopologyEvent(
                    event_type=TopologyEventType.EDGE_ADDED,
                    node_id=dest_id,
                    dest_hash=dest_hash,
                    new_value=hops,
                ))

            return edge

    def get_edge(self, source_id: str, dest_id: str) -> Optional[NetworkEdge]:
        """Get edge between two nodes"""
        with self._lock:
            return self._edges.get((source_id, dest_id))

    def get_neighbors(self, node_id: str) -> Set[str]:
        """Get adjacent nodes"""
        with self._lock:
            return set(self._adjacency.get(node_id, set()))

    def find_path(self, source_id: str, dest_id: str) -> Optional[List[str]]:
        """Find path between two nodes using Dijkstra's algorithm.

        Returns list of node IDs representing the path, or None if no path exists.
        """
        with self._lock:
            if source_id not in self._nodes or dest_id not in self._nodes:
                return None

            if source_id == dest_id:
                return [source_id]

            # Dijkstra's algorithm
            import heapq

            distances: Dict[str, float] = {source_id: 0}
            previous: Dict[str, Optional[str]] = {source_id: None}
            visited: Set[str] = set()
            heap = [(0, source_id)]

            while heap:
                current_dist, current = heapq.heappop(heap)

                if current in visited:
                    continue
                visited.add(current)

                if current == dest_id:
                    # Reconstruct path
                    path = []
                    node = dest_id
                    while node is not None:
                        path.append(node)
                        node = previous.get(node)
                    return list(reversed(path))

                # Check neighbors
                for neighbor in self._adjacency.get(current, set()):
                    if neighbor in visited:
                        continue

                    edge = self._edges.get((current, neighbor))
                    if not edge or not edge.is_active:
                        continue

                    weight = edge.get_weight()
                    new_dist = current_dist + weight

                    if neighbor not in distances or new_dist < distances[neighbor]:
                        distances[neighbor] = new_dist
                        previous[neighbor] = current
                        heapq.heappush(heap, (new_dist, neighbor))

            return None  # No path found

    def trace_path(self, dest_hash: bytes) -> Dict[str, Any]:
        """Trace path to a destination hash through known topology.

        Returns path information including intermediate hops if known.
        """
        with self._lock:
            dest_id = f"rns_{dest_hash.hex()[:16]}"
            result = {
                "dest_hash": dest_hash.hex(),
                "dest_id": dest_id,
                "found": False,
                "path": [],
                "total_hops": 0,
                "details": {},
            }

            # Find edge from local to destination
            edge = self._edges.get(("local", dest_id))
            if edge:
                result["found"] = True
                result["total_hops"] = edge.hops
                result["path"] = ["local", dest_id]
                result["details"] = {
                    "interface": edge.interface,
                    "last_seen": edge.last_seen.isoformat(),
                    "announce_count": edge.announce_count,
                }

            return result

    def get_topology_stats(self) -> Dict[str, Any]:
        """Get topology statistics"""
        with self._lock:
            active_edges = sum(1 for e in self._edges.values() if e.is_active)
            total_hops = sum(e.hops for e in self._edges.values() if e.is_active)

            return {
                "node_count": len(self._nodes),
                "edge_count": len(self._edges),
                "active_edges": active_edges,
                "avg_hops": total_hops / active_edges if active_edges > 0 else 0,
                "max_hops": max((e.hops for e in self._edges.values()), default=0),
            }

    def to_dict(self) -> Dict[str, Any]:
        """Export topology as dictionary"""
        with self._lock:
            return {
                "nodes": list(self._nodes.keys()),
                "edges": [e.to_dict() for e in self._edges.values()],
                "stats": self.get_topology_stats(),
            }

    def get_recent_events(self, count: int = 50) -> List[Dict[str, Any]]:
        """Get recent topology events"""
        events = self._path_monitor.get_recent_events(count)
        return [e.to_dict() for e in events]

    def _on_path_event(self, event: TopologyEvent):
        """Handle path table events and update topology"""
        if event.dest_hash is None:
            return

        dest_id = f"rns_{event.dest_hash.hex()[:16]}"

        if event.event_type == TopologyEventType.PATH_DISCOVERED:
            self.add_edge(
                source_id="local",
                dest_id=dest_id,
                dest_hash=event.dest_hash,
                hops=event.new_value or 0,
            )

        elif event.event_type == TopologyEventType.PATH_LOST:
            key = ("local", dest_id)
            with self._lock:
                if key in self._edges:
                    self._edges[key].is_active = False

        elif event.event_type == TopologyEventType.HOP_COUNT_CHANGED:
            self.add_edge(
                source_id="local",
                dest_id=dest_id,
                dest_hash=event.dest_hash,
                hops=event.new_value or 0,
            )

        # Forward to registered callbacks
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Topology callback error: {e}")

    def _emit_event(self, event: TopologyEvent):
        """Emit topology event to callbacks"""
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Topology event callback error: {e}")


# Global topology instance
_topology: Optional[NetworkTopology] = None


def get_network_topology() -> NetworkTopology:
    """Get the global network topology instance"""
    global _topology
    if _topology is None:
        _topology = NetworkTopology()
    return _topology

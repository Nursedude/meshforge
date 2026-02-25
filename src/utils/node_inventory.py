"""
Node Inventory — track all known mesh nodes with metadata.

Maintains a persistent registry of discovered nodes with their
hardware, firmware, location, and connectivity history. Designed
for operator awareness: "what nodes do I have, and are they healthy?"

Usage:
    from utils.node_inventory import NodeInventory, NodeRecord

    inv = NodeInventory()
    inv.update_node("!abc12345", name="Hilltop-1", hardware="RAK4631",
                    lat=21.3, lon=-157.8)
    inv.update_node("!abc12345", snr=-5.0, rssi=-90)

    # Query
    node = inv.get_node("!abc12345")
    online = inv.get_online_nodes()
    report = inv.format_inventory()

Persistence:
    Stores JSON in ~/.config/meshforge/node_inventory.json
    Auto-saves on every update (debounced).
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Node is "online" if seen within this window
ONLINE_TIMEOUT_SEC = 900  # 15 minutes

# Node is "stale" (candidate for pruning) if not seen for this long
STALE_TIMEOUT_SEC = 7 * 24 * 3600  # 7 days

# Minimum save interval (debounce rapid updates)
SAVE_DEBOUNCE_SEC = 5.0


@dataclass
class NodeRecord:
    """Complete record for a single mesh node."""
    node_id: str
    short_name: str = ""
    long_name: str = ""
    hardware: str = ""
    firmware: str = ""
    role: str = ""  # client, router, gateway, repeater
    owner: str = ""  # callsign or operator name
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt: Optional[float] = None
    last_snr: Optional[float] = None
    last_rssi: Optional[float] = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    update_count: int = 0

    @property
    def is_online(self) -> bool:
        """Node is online if seen within the timeout window."""
        if self.last_seen == 0.0:
            return False
        return (time.time() - self.last_seen) < ONLINE_TIMEOUT_SEC

    @property
    def is_stale(self) -> bool:
        """Node is stale if not seen for a long time."""
        if self.last_seen == 0.0:
            return True
        return (time.time() - self.last_seen) > STALE_TIMEOUT_SEC

    @property
    def status(self) -> str:
        """Current node status."""
        if self.is_online:
            return "online"
        elif self.is_stale:
            return "stale"
        else:
            return "offline"

    @property
    def display_name(self) -> str:
        """Best available display name."""
        if self.long_name:
            return self.long_name
        if self.short_name:
            return self.short_name
        return self.node_id

    @property
    def has_position(self) -> bool:
        """Whether node has GPS coordinates."""
        return self.lat is not None and self.lon is not None

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'NodeRecord':
        """Create from dictionary."""
        # Filter to only known fields
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


class NodeInventory:
    """Persistent registry of known mesh nodes.

    Tracks node metadata, connectivity history, and provides
    query/filter capabilities for NOC operations.
    """

    def __init__(self, path: Optional[Path] = None):
        """Initialize inventory.

        Args:
            path: Path to JSON persistence file. If None, uses
                  default config directory location.
        """
        self._nodes: Dict[str, NodeRecord] = {}
        self._path = path
        self._last_save: float = 0.0
        self._dirty: bool = False

        if self._path is not None:
            self._load()

    def _get_default_path(self) -> Path:
        """Get default persistence path."""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        return config_dir / "node_inventory.json"

    def _load(self) -> None:
        """Load inventory from disk."""
        if self._path is None:
            return
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for node_id, record_data in data.items():
                self._nodes[node_id] = NodeRecord.from_dict(record_data)
            logger.debug(f"Loaded {len(self._nodes)} nodes from {self._path}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load inventory: {e}")

    def _save(self, force: bool = False) -> None:
        """Save inventory to disk (debounced).

        Args:
            force: Skip debounce timer and save immediately.
        """
        if self._path is None:
            return
        if not self._dirty and not force:
            return

        now = time.time()
        if not force and (now - self._last_save) < SAVE_DEBOUNCE_SEC:
            return

        try:
            from utils.paths import atomic_write_text
            data = {nid: node.to_dict() for nid, node in self._nodes.items()}
            atomic_write_text(self._path, json.dumps(data, indent=2))
            self._last_save = now
            self._dirty = False
            logger.debug(f"Saved {len(self._nodes)} nodes to {self._path}")
        except OSError as e:
            logger.warning(f"Failed to save inventory: {e}")

    def update_node(self, node_id: str, **kwargs) -> NodeRecord:
        """Update or create a node record.

        Any keyword argument matching a NodeRecord field will be updated.
        Automatically updates last_seen and update_count.

        Args:
            node_id: Mesh node identifier (e.g., "!abc12345").
            **kwargs: Fields to update (name, hardware, lat, lon, snr, etc.)

        Returns:
            Updated NodeRecord.
        """
        now = time.time()

        if node_id in self._nodes:
            node = self._nodes[node_id]
        else:
            node = NodeRecord(node_id=node_id, first_seen=now)
            self._nodes[node_id] = node

        # Update provided fields
        known_fields = {f.name for f in NodeRecord.__dataclass_fields__.values()}
        for key, value in kwargs.items():
            if key in known_fields and value is not None:
                setattr(node, key, value)

        # Map common aliases
        if 'name' in kwargs and kwargs['name']:
            node.short_name = kwargs['name']
        if 'snr' in kwargs:
            node.last_snr = kwargs['snr']
        if 'rssi' in kwargs:
            node.last_rssi = kwargs['rssi']

        node.last_seen = now
        node.update_count += 1
        self._dirty = True
        self._save()

        return node

    def get_node(self, node_id: str) -> Optional[NodeRecord]:
        """Get a node record by ID.

        Args:
            node_id: Node identifier.

        Returns:
            NodeRecord or None if not found.
        """
        return self._nodes.get(node_id)

    def get_all_nodes(self) -> List[NodeRecord]:
        """Get all node records.

        Returns:
            List of all NodeRecords sorted by last_seen (newest first).
        """
        return sorted(self._nodes.values(),
                      key=lambda n: n.last_seen, reverse=True)

    def get_online_nodes(self) -> List[NodeRecord]:
        """Get nodes currently online.

        Returns:
            List of online NodeRecords.
        """
        return [n for n in self._nodes.values() if n.is_online]

    def get_offline_nodes(self) -> List[NodeRecord]:
        """Get nodes currently offline (but not stale).

        Returns:
            List of offline NodeRecords.
        """
        return [n for n in self._nodes.values()
                if not n.is_online and not n.is_stale]

    def get_stale_nodes(self) -> List[NodeRecord]:
        """Get nodes that haven't been seen for a long time.

        Returns:
            List of stale NodeRecords.
        """
        return [n for n in self._nodes.values() if n.is_stale]

    def search(self, query: str) -> List[NodeRecord]:
        """Search nodes by name, ID, owner, or hardware.

        Args:
            query: Search string (case-insensitive).

        Returns:
            Matching NodeRecords.
        """
        q = query.lower()
        results = []
        for node in self._nodes.values():
            searchable = ' '.join([
                node.node_id,
                node.short_name,
                node.long_name,
                node.owner,
                node.hardware,
                node.role,
            ]).lower()
            if q in searchable:
                results.append(node)
        return sorted(results, key=lambda n: n.last_seen, reverse=True)

    def get_by_role(self, role: str) -> List[NodeRecord]:
        """Get nodes by role.

        Args:
            role: Node role (client, router, gateway, repeater).

        Returns:
            Matching NodeRecords.
        """
        return [n for n in self._nodes.values()
                if n.role.lower() == role.lower()]

    def get_stats(self) -> dict:
        """Get inventory statistics.

        Returns:
            Dict with counts and summary data.
        """
        all_nodes = list(self._nodes.values())
        online = [n for n in all_nodes if n.is_online]
        offline = [n for n in all_nodes if not n.is_online and not n.is_stale]
        stale = [n for n in all_nodes if n.is_stale]
        with_position = [n for n in all_nodes if n.has_position]

        roles = {}
        for node in all_nodes:
            role = node.role or "unknown"
            roles[role] = roles.get(role, 0) + 1

        hardware_types = {}
        for node in all_nodes:
            hw = node.hardware or "unknown"
            hardware_types[hw] = hardware_types.get(hw, 0) + 1

        return {
            'total': len(all_nodes),
            'online': len(online),
            'offline': len(offline),
            'stale': len(stale),
            'with_position': len(with_position),
            'roles': roles,
            'hardware_types': hardware_types,
        }

    def prune_stale(self, max_age_days: int = 30) -> int:
        """Remove nodes not seen for a long time.

        Args:
            max_age_days: Remove nodes not seen for this many days.

        Returns:
            Number of nodes removed.
        """
        cutoff = time.time() - (max_age_days * 24 * 3600)
        to_remove = [
            nid for nid, node in self._nodes.items()
            if node.last_seen > 0 and node.last_seen < cutoff
        ]
        for nid in to_remove:
            del self._nodes[nid]

        if to_remove:
            self._dirty = True
            self._save(force=True)
            logger.info(f"Pruned {len(to_remove)} stale nodes")

        return len(to_remove)

    def remove_node(self, node_id: str) -> bool:
        """Remove a specific node from inventory.

        Args:
            node_id: Node identifier to remove.

        Returns:
            True if node was found and removed.
        """
        if node_id in self._nodes:
            del self._nodes[node_id]
            self._dirty = True
            self._save(force=True)
            return True
        return False

    @property
    def node_count(self) -> int:
        """Total number of tracked nodes."""
        return len(self._nodes)

    def format_inventory(self, include_stale: bool = False) -> str:
        """Format inventory as markdown table.

        Args:
            include_stale: Include stale nodes in output.

        Returns:
            Markdown-formatted inventory table.
        """
        lines = []
        stats = self.get_stats()
        lines.append(f"# Node Inventory ({stats['total']} nodes)\n")
        lines.append(f"Online: {stats['online']} | "
                     f"Offline: {stats['offline']} | "
                     f"Stale: {stats['stale']}\n")

        nodes = self.get_all_nodes()
        if not include_stale:
            nodes = [n for n in nodes if not n.is_stale]

        if not nodes:
            lines.append("*No nodes in inventory.*")
            return "\n".join(lines)

        lines.append("| Node ID | Name | Hardware | Role | Status | "
                     "Last SNR | Last Seen |")
        lines.append("|---------|------|----------|------|--------|"
                     "---------|-----------|")

        for node in nodes:
            name = node.display_name[:20]
            hw = node.hardware[:12] if node.hardware else "—"
            role = node.role or "—"
            status = node.status
            snr = f"{node.last_snr:.1f}" if node.last_snr is not None else "—"

            if node.last_seen > 0:
                age_sec = time.time() - node.last_seen
                if age_sec < 60:
                    last_seen = f"{age_sec:.0f}s ago"
                elif age_sec < 3600:
                    last_seen = f"{age_sec / 60:.0f}m ago"
                elif age_sec < 86400:
                    last_seen = f"{age_sec / 3600:.1f}h ago"
                else:
                    last_seen = f"{age_sec / 86400:.0f}d ago"
            else:
                last_seen = "never"

            lines.append(f"| {node.node_id} | {name} | {hw} | "
                         f"{role} | {status} | {snr} | {last_seen} |")

        return "\n".join(lines)

    def export_json(self) -> str:
        """Export inventory as formatted JSON.

        Returns:
            JSON string of all node records.
        """
        data = {nid: node.to_dict() for nid, node in self._nodes.items()}
        return json.dumps(data, indent=2)

    def flush(self) -> None:
        """Force save any pending changes to disk."""
        if self._dirty:
            self._save(force=True)

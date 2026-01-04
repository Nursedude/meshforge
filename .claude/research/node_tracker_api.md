# Unified Node Tracker API Reference

> Internal API documentation for MeshForge node tracking

---

## Overview

The `UnifiedNodeTracker` provides a unified view of nodes from both Meshtastic and RNS (Reticulum) networks for map display and monitoring.

**Location:** `src/gateway/node_tracker.py`

---

## Data Classes

### Position

Geographic position with validation.

```python
@dataclass
class Position:
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    precision: int = 5  # decimal places
    timestamp: Optional[datetime] = None

    def is_valid(self) -> bool:
        """Check if position has non-zero coords within valid range"""
```

### Telemetry

Node telemetry data.

```python
@dataclass
class Telemetry:
    battery_level: Optional[int] = None  # 0-100
    voltage: Optional[float] = None
    temperature: Optional[float] = None  # Celsius
    humidity: Optional[float] = None  # 0-100%
    pressure: Optional[float] = None  # hPa
    air_quality: Optional[int] = None
    uptime: Optional[int] = None  # seconds
    timestamp: Optional[datetime] = None
```

### UnifiedNode

Represents a node from either network.

```python
@dataclass
class UnifiedNode:
    # Core identity
    id: str              # Unified identifier (network prefix + hash/id)
    network: str         # "meshtastic", "rns", or "both"
    name: str = ""
    short_name: str = ""

    # Position and telemetry
    position: Position
    telemetry: Telemetry

    # Network-specific identifiers
    meshtastic_id: Optional[str] = None  # !abcd1234
    rns_hash: Optional[bytes] = None     # 16-byte destination hash

    # Radio metrics
    snr: Optional[float] = None
    rssi: Optional[int] = None
    hops: Optional[int] = None

    # Status
    is_online: bool = False
    is_gateway: bool = False
    is_local: bool = False
    last_seen: Optional[datetime] = None
    first_seen: Optional[datetime] = None

    # Hardware info
    hardware_model: Optional[str] = None
    firmware_version: Optional[str] = None
    role: Optional[str] = None
```

---

## Factory Methods

### UnifiedNode.from_meshtastic()

Create a node from Meshtastic data.

```python
@classmethod
def from_meshtastic(cls, mesh_node: dict, is_local: bool = False) -> 'UnifiedNode':
    """
    Create from Meshtastic node data dictionary.

    Args:
        mesh_node: Dict with keys: num, user, position, deviceMetrics
        is_local: True if this is our own node

    Returns:
        UnifiedNode with id prefixed "mesh_"
    """
```

**Input Format:**
```python
{
    "num": 0x12345678,
    "user": {
        "longName": "Node Name",
        "shortName": "NODE",
        "hwModel": "TBEAM",
        "role": "ROUTER"
    },
    "position": {
        "latitude": 21.3069,
        "longitude": -157.8583,
        "altitude": 100
    },
    "deviceMetrics": {
        "batteryLevel": 85,
        "voltage": 4.1,
        "uptimeSeconds": 3600
    }
}
```

### UnifiedNode.from_rns()

Create a node from RNS announce data.

```python
@classmethod
def from_rns(cls, rns_hash: bytes, name: str = "", app_data: bytes = None) -> 'UnifiedNode':
    """
    Create from RNS announce/discovery data.

    Args:
        rns_hash: 16-byte destination hash
        name: Display name (from app_data or empty)
        app_data: Raw announce app_data bytes

    Returns:
        UnifiedNode with id prefixed "rns_"
    """
```

---

## UnifiedNodeTracker Class

### Initialization

```python
tracker = UnifiedNodeTracker()
tracker.start()  # Starts cleanup loop and RNS discovery

# When done
tracker.stop()  # Saves cache and stops threads
```

### Node Management

| Method | Description |
|--------|-------------|
| `add_node(node)` | Add or update a node (merges if exists) |
| `remove_node(node_id)` | Remove a node by ID |
| `get_node(node_id)` | Get single node or None |

### Query Methods

| Method | Returns |
|--------|---------|
| `get_all_nodes()` | All tracked nodes |
| `get_meshtastic_nodes()` | Nodes where network is "meshtastic" or "both" |
| `get_rns_nodes()` | Nodes where network is "rns" or "both" |
| `get_nodes_with_position()` | Nodes with valid GPS positions |
| `get_online_nodes()` | Nodes with is_online=True |
| `get_stats()` | Dict with counts: total, meshtastic, rns, online, with_position, gateways |

### Callbacks

```python
def my_callback(event: str, node: UnifiedNode):
    if event == "update":
        print(f"Node updated: {node.name}")
    elif event == "remove":
        print(f"Node removed: {node.id}")

tracker.register_callback(my_callback)
tracker.unregister_callback(my_callback)
```

### Export

```python
geojson = tracker.to_geojson()
# Returns GeoJSON FeatureCollection for map display
```

---

## RNS Discovery

### Automatic Discovery

When `start()` is called, the tracker:

1. Initializes Reticulum (`RNS.Reticulum()`)
2. Registers an announce handler with `RNS.Transport`
3. Listens for announces from all RNS nodes
4. Creates `UnifiedNode` entries for each discovered node

### Announce Handler

```python
class NodeAnnounceHandler:
    aspect_filter = None  # Accept all announces

    def received_announce(self, destination_hash, announced_identity, app_data):
        """Called when an announce is received"""
```

### RNS Node Creation

When an announce is received:

1. Parse `app_data` for display name (UTF-8 string)
2. Create node with `UnifiedNode.from_rns(dest_hash, name, app_data)`
3. Add to tracker (merges with existing if known)

**Note:** RNS announces don't inherently include GPS positions. For position data:
- Use Sideband telemetry sharing
- Parse custom app_data format
- Manual entry in config

---

## Persistence

### Cache File

Location: `~/.config/meshforge/node_cache.json`

Format:
```json
{
    "version": 1,
    "saved_at": "2026-01-04T12:00:00",
    "nodes": [
        {
            "id": "mesh_!12345678",
            "network": "meshtastic",
            "name": "Node Name",
            ...
        }
    ]
}
```

### Auto-Save

- Cache saved every 60 seconds during cleanup loop
- Cache saved on `stop()`
- Nodes loaded on tracker initialization (marked offline)

---

## Thread Safety

All node access uses `threading.RLock`:

```python
with self._lock:
    # Safe node access
```

### Daemon Threads

| Thread | Purpose |
|--------|---------|
| `_cleanup_thread` | Marks old nodes offline, saves cache |
| `_rns_thread` | RNS event loop and discovery |

Both threads exit when `_running` becomes False.

---

## Status Determination

### Online Threshold

```python
OFFLINE_THRESHOLD = 3600  # 1 hour
```

Nodes become offline if `last_seen` is older than threshold.

### Age String

```python
node.get_age_string()  # Returns: "5s ago", "3m ago", "2h ago", "1d ago"
```

---

## Integration with Map Panel

The Map Panel uses the tracker for RNS nodes:

```python
# In MapPanel._refresh_data()
if self.node_tracker:
    rns_nodes = self.node_tracker.get_rns_nodes()
    for rns_node in rns_nodes:
        if rns_node.position and rns_node.position.is_valid():
            # Add to GeoJSON features
```

Meshtastic nodes come from `NodeMonitor` separately.

---

## Error Handling

### RNS Not Installed

```python
except ImportError:
    logger.info("RNS module not installed - RNS node discovery disabled")
```

The tracker continues to function for Meshtastic-only use.

### Connection Failures

- RNS connection failures logged but don't crash tracker
- Cache continues to provide known nodes

---

## Example Usage

```python
from gateway.node_tracker import UnifiedNodeTracker, UnifiedNode

# Start tracker
tracker = UnifiedNodeTracker()
tracker.start()

# Add a Meshtastic node manually
node = UnifiedNode.from_meshtastic(mesh_data)
tracker.add_node(node)

# Query nodes
all_nodes = tracker.get_all_nodes()
stats = tracker.get_stats()
print(f"Total: {stats['total']}, Online: {stats['online']}")

# Export for map
geojson = tracker.to_geojson()

# Cleanup
tracker.stop()
```

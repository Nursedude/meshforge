# Session Notes: Meshtastic Node Visibility Investigation

**Date**: 2026-02-03
**Session ID**: fix-meshtastic-node-visibility-x5MEv
**Objective**: Investigate why some Meshtastic nodes aren't visible and improve telemetry capture

## Root Cause Analysis

### Problem Statement
Some Meshtastic nodes were not appearing in MeshForge's maps, topology graphs, and monitoring displays despite being active in the mesh network.

### Key Discovery: Meshtastic 2.6+ Relay Node Fields

After reviewing the [Meshtastic Mesh Algorithm documentation](https://meshtastic.org/docs/overview/mesh-algo/) and the [Managed Flood Routing blog post](https://meshtastic.org/blog/why-meshtastic-uses-managed-flood-routing/), I discovered that Meshtastic 2.6+ introduced two critical new fields in the packet header:

| Offset | Field | Purpose |
|--------|-------|---------|
| 0x0E | `relay_node` | Last byte of node ID that relayed this packet |
| 0x0F | `next_hop` | Last byte of expected next-hop node for routing |

### Why Nodes Were Missing

1. **Managed Flood Routing Suppression**: Nodes listen before rebroadcasting. If they hear another node already rebroadcasting, they suppress their own rebroadcast. This means many nodes "witness" packets but never send their own telemetry.

2. **SNR-based Prioritization**: Nodes further away (lower SNR) rebroadcast first. Closer nodes hear this and suppress, making them less visible.

3. **Traffic Scaling (>40 nodes)**: In meshes with >40 nodes, NodeInfo/Position/Telemetry intervals are scaled back significantly:
   ```
   ScaledInterval = Interval * (1.0 + ((NumberOfOnlineNodes - 40) * 0.075))
   ```

4. **MeshForge Gap**: The codebase was NOT extracting the `relayNode` field from packets, so relay-only nodes were completely invisible.

## Changes Made

### 1. MQTT Subscriber (`src/monitoring/mqtt_subscriber.py`)

- **Added fields to `MQTTNode` dataclass**:
  - `relay_node: Optional[int]` - Last byte of relay node ID
  - `next_hop: Optional[int]` - Last byte of expected next-hop
  - `discovered_via_relay: bool` - Flag for nodes found via relay activity

- **Added `_discover_relay_node()` method**: Creates placeholder nodes when we see unknown relay nodes (partial ID format: `!????xx` where `xx` is the hex of the last byte)

- **Added `_try_merge_relay_node()` method**: Merges partial relay nodes with full node IDs when the node eventually sends its own telemetry

- **Added stats tracking**:
  - `nodes_discovered_via_relay`
  - `relay_nodes_merged`

- **Updated GeoJSON output** to include relay tracking properties

### 2. RNS Bridge (`src/gateway/rns_bridge.py`)

- **Added relay node extraction** in `_on_meshtastic_receive()`:
  ```python
  relay_node = packet.get('relayNode')
  if relay_node and relay_node > 0:
      self._discover_relay_node(relay_node, from_id, packet)
  ```

- **Added `_discover_relay_node()` method**: Discovers relay nodes and creates topology edges showing relay relationships

### 3. Node Tracker (`src/gateway/node_tracker.py`)

- **Added fields to `UnifiedNode` dataclass**:
  - `discovered_via_relay: bool`
  - `relay_node: Optional[int]`
  - `next_hop: Optional[int]`

- **Updated `from_meshtastic()` method** to extract `relayNode` and `nextHop` fields

### 4. Message Listener (`src/utils/message_listener.py`)

- **Added relay tracking** to message data:
  - `relay_node`
  - `next_hop`

### 5. Traffic Inspector (`src/monitoring/traffic_inspector.py`)

- **Added fields to `MeshPacket` dataclass**:
  - `relay_node: Optional[int]`
  - `next_hop: Optional[int]`

- **Updated `dissect()` method** to extract relay fields from metadata

- **Updated protocol tree** to display relay info in the Routing section

## Impact

These changes enable MeshForge to:

1. **Discover relay-only nodes** that never send their own telemetry
2. **Track relay relationships** in the topology graph
3. **Merge partial IDs** when nodes eventually identify themselves
4. **Display relay path information** in packet inspection
5. **Provide statistics** on relay-discovered nodes

## Testing Notes

- All modified files compile successfully
- Changes follow existing code patterns and security guidelines
- No breaking changes to existing APIs

## Follow-up Recommendations

1. **Web UI Update**: Add visual indicators for relay-discovered nodes (different icon or color)
2. **Map Enhancement**: Show relay paths as dotted lines between nodes
3. **Statistics Dashboard**: Display relay discovery stats in monitoring UI
4. **Documentation**: Update user docs to explain relay node discovery

## Sources

- [Mesh Broadcast Algorithm | Meshtastic](https://meshtastic.org/docs/overview/mesh-algo/)
- [Why Meshtastic Uses Managed Flood Routing | Meshtastic](https://meshtastic.org/blog/why-meshtastic-uses-managed-flood-routing/)
- [Meshtastic 2.6 Preview: MUI and Next-Hop Routing](https://meshtastic.org/blog/meshtastic-2-6-preview/)
- [MeshPacket Structure | DeepWiki](https://deepwiki.com/meshtastic/protobufs/2.1-meshpacket-structure)
- [meshtastic Python library](https://python.meshtastic.org/)

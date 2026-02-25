# Gateway Data Paths Reference

> **Version**: 0.5.4-beta (main branch)
> **Updated**: 2026-02-25
> **Purpose**: Operational reference mapping every transport path through the MeshForge gateway

---

## Overview

The MeshForge gateway bridges incompatible mesh ecosystems using:

- **CanonicalMessage** (`gateway/canonical_message.py`) — Protocol-agnostic intermediate format
- **MessageRouter** (`gateway/message_routing.py`) — Direction-based routing (NOT address-based)
- **6 bridge modes** — Each with distinct transport chains and service requirements

### Fundamental Constraint: Address Incompatibility

| Protocol | Address Format | Size | Example |
|----------|---------------|------|---------|
| Meshtastic | Node ID | 32-bit | `!abcd1234` |
| RNS/LXMF | SHA-256 hash | 128-bit | `a7f3b2...` (32 hex chars) |
| MeshCore | Public key prefix | 16-bit | `0x3f2a` |

These addresses **cannot be mapped** between protocols. The gateway uses **direction-based routing** —
it doesn't translate addresses, it forwards messages from one network to another with protocol-native
addressing on each side. Broadcast messages cross the bridge; DMs require contact mapping
(`gateway/contact_mapping.py`).

---

## Bridge Mode 1: `mqtt_bridge` (Production, Recommended)

**Status**: Production — zero-interference with meshtasticd web client
**Required services**: `meshtasticd`, `mosquitto` (MQTT broker), `rnsd`

### Data Path: Meshtastic → RNS

```
LoRa Radio
  ↓ RF (902-928 MHz US, SF11/BW125)
meshtasticd (:4403 TCP, :443 HTTPS)
  ↓ MQTT publish (JSON)
  ↓ Topic: msh/{region}/2/json/{channel}/{nodeId}
mosquitto MQTT Broker (:1883)
  ↓ MQTT subscribe
MQTTBridgeHandler (gateway/mqtt_bridge_handler.py)
  ↓ JSON decode → packet dict
CanonicalMessage.from_meshtastic(packet)     [canonical_message.py:102]
  ↓
MessageRouter.route(msg)                     [message_routing.py:38]
  ↓ Direction check: meshtastic → rns
  ↓ Dedup check (SHA-256 content hash, 60s window)
CanonicalMessage.to_lxmf()                  [canonical_message.py:~300]
  ↓ LXMF message envelope
RNS/LXMF Transport → rnsd                   [gateway/rns_bridge.py]
  ↓
RNS Network (LoRa/TCP/I2P/Serial)
```

### Data Path: RNS → Meshtastic

```
RNS Network
  ↓
rnsd → LXMF delivery callback               [gateway/rns_bridge.py]
  ↓
CanonicalMessage.from_rns(lxmf_msg)         [canonical_message.py:~250]
  ↓
MessageRouter.route(msg)                     [message_routing.py:38]
  ↓ Direction check: rns → meshtastic
  ↓ Dedup check
CanonicalMessage.to_meshtastic()             [canonical_message.py:~350]
  ↓ Meshtastic packet dict
send_text_direct() via HTTP protobuf         [meshtastic_protobuf_client.py]
  ↓ POST /api/v1/toradio (protobuf)
meshtasticd (:443 HTTPS)
  ↓
LoRa Radio → RF mesh
```

### Key Properties
- **Zero-interference**: MQTT, TCP, and HTTP are independent meshtasticd subsystems
- **Payload limit**: 237 bytes (Meshtastic), truncated with `…` indicator
- **Dedup**: SHA-256 content hash with configurable window (default 60s)
- **Queue**: `PersistentMessageQueue` (SQLite) for offline buffering (`message_queue.py`)

---

## Bridge Mode 2: `message_bridge` (Production, Legacy)

**Status**: Production — legacy TCP mode, blocks web client
**Required services**: `meshtasticd`, `rnsd`

### Data Path: Meshtastic → RNS

```
LoRa Radio
  ↓ RF
meshtasticd (:4403 TCP)
  ↓ TCP stream (protobuf MeshPacket)
MeshtasticHandler (gateway/meshtastic_handler.py)
  ↓ protobuf decode
CanonicalMessage.from_meshtastic(packet)     [canonical_message.py:102]
  ↓
MessageRouter.route(msg)                     [message_routing.py:38]
  ↓
CanonicalMessage.to_lxmf()
  ↓
RNS/LXMF → rnsd → RNS Network
```

### Key Properties
- **Single-client TCP limitation**: meshtasticd supports ONE TCP client — holding this
  connection blocks the web client at :9443
- **Legacy**: Use `mqtt_bridge` for new deployments
- **TX path**: Uses `send_text_direct()` via HTTP to avoid `fromradio` conflicts

---

## Bridge Mode 3: `rns_transport` (Production)

**Status**: Production — RNS over Meshtastic raw transport
**Required services**: `meshtasticd`, `rnsd`

### Data Path

```
RNS Application (NomadNet, LXMF, Sideband)
  ↓ RNS API
rnsd
  ↓ RNS Interface: RNSOverMeshtastic
RNSOverMeshtastic (gateway/rns_transport.py)
  ↓ Fragment RNS packets into LoRa-sized chunks
  ↓ TCP/Serial to meshtasticd
meshtasticd (:4403)
  ↓
LoRa Radio → RF mesh
  ↓
Remote meshtasticd → RNSOverMeshtastic → rnsd → RNS app
```

### Key Properties
- **Raw transport**: No CanonicalMessage — RNS packets are carried verbatim
- **Fragmentation**: RNS packets (up to ~500B) are fragmented to fit LoRa MTU
- **Bidirectional**: Same path in both directions
- **Speed presets**: Configurable via `RNSOverMeshtasticConfig.data_speed` (0-8)
- **NOT a message bridge**: This carries raw RNS traffic, not translated messages

---

## Bridge Mode 4: `mesh_bridge` (Production)

**Status**: Production — bridges two Meshtastic networks with different LoRa presets
**Required services**: Two `meshtasticd` instances (different ports)

### Data Path

```
LoRa Radio (Preset A, e.g., LONG_FAST on :4403)
  ↓ RF
meshtasticd #1 (:4403)
  ↓ TCP/MQTT
MeshtasticPresetBridge (gateway/mesh_bridge.py)
  ↓ Dedup check (content hash)
  ↓ Optional prefix: "[LONG_FAST] "
meshtasticd #2 (:4404)
  ↓
LoRa Radio (Preset B, e.g., SHORT_TURBO)
```

### Key Properties
- **No CanonicalMessage**: Direct Meshtastic-to-Meshtastic forwarding
- **No RNS involvement**: Pure Meshtastic bridging
- **Use case**: Rural LONG_FAST mesh bridged to local SHORT_TURBO high-speed mesh
- **Bidirectional**: Configurable via `MeshtasticBridgeConfig.direction`

---

## Bridge Mode 5: `meshcore_bridge` (Alpha Only)

**Status**: Alpha — `alpha/meshcore-bridge` branch only
**Required services**: MeshCore companion radio (USB/TCP/BLE), `meshtasticd` or `rnsd`

### Data Path: MeshCore → Meshtastic

```
MeshCore Radio (RAK4631, Heltec V3, T-Deck)
  ↓ Serial/TCP/BLE
meshcore_py (Python library)
  ↓ Event: CONTACT_MSG_RECV / CHANNEL_MSG_RECV
MeshCoreHandler (gateway/meshcore_handler.py)
  ↓
CanonicalMessage.from_meshcore(event)        [canonical_message.py:174]
  ↓
MessageRouter.route(msg)                     [message_routing.py:38]
  ↓
CanonicalMessage.to_meshtastic()
  ↓
send_text_direct() → meshtasticd → LoRa
```

### Key Properties
- **Payload limit**: 184 bytes (MeshCore), 160 bytes text
- **MeshCore addressing**: 16-bit public key prefixes, incompatible with Meshtastic/RNS
- **Hop limit**: Up to 64 hops (vs Meshtastic's 7)
- **Connection types**: serial (USB), tcp (WiFi firmware), ble (pending)

---

## Bridge Mode 6: `tri_bridge` (Alpha Only)

**Status**: Alpha — `alpha/meshcore-bridge` branch only
**Required services**: MeshCore radio, `meshtasticd`, `rnsd`

### Data Path

```
Any Protocol (Meshtastic, MeshCore, RNS)
  ↓
Protocol-specific handler
  ↓
CanonicalMessage.from_X()
  ↓
MessageRouter.route(msg)
  ↓ all_to_all direction: forward to ALL other protocols
  ↓
CanonicalMessage.to_Y() for each destination protocol
  ↓
Protocol-specific TX path
```

### Key Properties
- **All three simultaneously**: Messages from any protocol go to both others
- **CanonicalMessage hub**: All translations go through the common format
- **Payload differences**: Each protocol has different size limits; messages are truncated per-target

---

## CanonicalMessage Translation Matrix

Shows which fields survive translation between protocols:

| Field | Mesh→Canon | Canon→Mesh | Core→Canon | Canon→Core | RNS→Canon | Canon→RNS |
|-------|-----------|-----------|-----------|-----------|----------|----------|
| Text content | Yes | Yes (237B max) | Yes | Yes (160B max) | Yes | Yes |
| Source address | Node ID | — | Pubkey prefix | — | Hash | — |
| Dest address | Node ID/broadcast | Node ID | Pubkey/channel | Pubkey | Hash | Hash |
| Broadcast flag | Yes | Yes | Yes (is_channel) | Yes | Yes | Yes |
| Hop count | Yes | — | — | — | — | — |
| SNR/RSSI | metadata | — | — | — | — | — |
| Position | portnum | — | — | — | — | — |
| Telemetry | portnum | — | — | — | — | — |
| Encryption | E2E breaks at bridge | | E2E breaks | | E2E breaks | |

**Critical**: End-to-end encryption **breaks at the bridge boundary**. Messages are decrypted
on ingress and re-encrypted on egress with the destination protocol's keys.

---

## Connection Contention Model

| Transport | Port | Clients | Contention |
|-----------|------|---------|-----------|
| meshtasticd TCP | 4403 | **ONE** | Blocks web client |
| meshtasticd HTTPS | 443 | Multiple | Stateless, no contention |
| meshtasticd MQTT | (broker) | Multiple | Zero contention |
| rnsd | (IPC) | Multiple | Via RNS API |
| MeshCore serial | /dev/ttyUSBx | ONE | Exclusive device |
| MeshCore TCP | 4000 | ONE | Single client |
| MQTT broker | 1883 | Multiple | Standard pub/sub |

**Recommendation**: `mqtt_bridge` mode avoids TCP contention entirely.

---

## Service Dependency Matrix

| Bridge Mode | meshtasticd | rnsd | mosquitto | MeshCore Radio | 2nd meshtasticd |
|-------------|:-----------:|:----:|:---------:|:--------------:|:---------------:|
| `mqtt_bridge` | Required | Required | Required | — | — |
| `message_bridge` | Required | Required | — | — | — |
| `rns_transport` | Required | Required | — | — | — |
| `mesh_bridge` | Required | — | Optional | — | Required |
| `meshcore_bridge` | Optional | Optional | — | Required | — |
| `tri_bridge` | Required | Required | Optional | Required | — |

---

## Persistent Message Queue

**File**: `gateway/message_queue.py`
**Backend**: SQLite at `~/.config/meshforge/gateway_queue.db`

Messages are queued when the destination network is temporarily unavailable:
- Queue depth monitored by `BridgeHealthMonitor`
- Messages expire after configurable TTL
- Used by `mqtt_bridge` and `message_bridge` modes

---

## Latency Budget (Approximate)

| Segment | Typical Latency | Notes |
|---------|----------------|-------|
| LoRa TX/RX (1 hop) | 200-8000ms | Depends on SF/BW preset |
| meshtasticd processing | <50ms | Local daemon |
| MQTT pub/sub | <10ms | Local broker |
| CanonicalMessage conversion | <1ms | Pure Python |
| MessageRouter classification | <1ms | Regex/hash |
| LXMF envelope creation | <5ms | RNS API |
| rnsd processing | <50ms | Local daemon |
| RNS transport (1 hop) | 200-8000ms | Same LoRa, different protocol |

**Total gateway latency** (excluding RF): ~100-200ms
**Total end-to-end** (1 hop each side): ~500-16,000ms

---

## AREDN Router Overlay

When MeshForge nodes sit behind AREDN Mikrotik routers, the data path gains
an additional segment:

```
LoRa → meshtasticd → AREDN Router (10.x.x.x) → WiFi/DtD/Tunnel → Remote AREDN Router → meshtasticd → LoRa
```

AREDN provides:
- **5.8 GHz WiFi backbone** between sites
- **DtD (Device-to-Device)** Ethernet links via VLAN 2
- **VPN tunnels** over internet backhaul

The gateway's `aredn_topology.py` module provides read-only visibility of which nodes
are reachable via AREDN vs direct LoRa. See Workstream 3 in the implementation plan.

---

## File Reference

| Component | File | Key Line(s) |
|-----------|------|-------------|
| CanonicalMessage | `gateway/canonical_message.py` | from_meshtastic:102, from_meshcore:174, from_rns:~250 |
| MessageRouter | `gateway/message_routing.py` | route():~120, _DIRECTION_MAP:55 |
| MQTT Handler | `gateway/mqtt_bridge_handler.py` | Subscribes to meshtasticd MQTT |
| TCP Handler | `gateway/meshtastic_handler.py` | TCP stream to meshtasticd |
| Protobuf TX | `gateway/meshtastic_protobuf_client.py` | send_text_direct() |
| RNS Bridge | `gateway/rns_bridge.py` | Main bridge orchestrator |
| Mesh Bridge | `gateway/mesh_bridge.py` | Preset-to-preset bridging |
| MeshCore Handler | `gateway/meshcore_handler.py` | MeshCore event processing |
| Config | `gateway/config.py` | GatewayConfig:372, all mode configs |
| Queue | `gateway/message_queue.py` | SQLite persistent queue |
| Health | `gateway/bridge_health.py` | BridgeHealthMonitor, DeliveryTracker |
| Transport Registry | `gateway/transport_registry.py` | Machine-readable path definitions |
| Topology Inspector | `gateway/topology_inspector.py` | Runtime topology view |
| Node Models | `gateway/node_models.py` | UnifiedNode:324, Position, Telemetry |
| Node Tracker | `gateway/node_tracker.py` | Node lifecycle management |

---

*Made with aloha for the mesh community — WH6GXZ*

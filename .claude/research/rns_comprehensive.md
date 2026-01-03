# Comprehensive RNS/Reticulum Integration Research

## Table of Contents
1. [Overview](#overview)
2. [Core Components](#core-components)
3. [Python API Reference](#python-api-reference)
4. [LXMF Messaging](#lxmf-messaging)
5. [NomadNet Web Capabilities](#nomadnet-web-capabilities)
6. [Sideband Telemetry](#sideband-telemetry)
7. [Gateway Architecture](#gateway-architecture)
8. [Integration with Meshtastic](#integration-with-meshtastic)
9. [Code Examples](#code-examples)
10. [Resources](#resources)

---

## Overview

**Reticulum Network Stack (RNS)** is a cryptography-based networking protocol designed for unstoppable, resilient mesh networks. It provides:

- **Zero-configuration** globally unique addressing
- **Multi-hop routing** over heterogeneous carriers (LoRa, WiFi, packet radio, serial, I2P)
- **End-to-end encryption** with forward secrecy
- **Initiator anonymity** - no source addresses in packets
- **Extremely low bandwidth operation** - as low as 5 bits/second
- **No kernel modules** - runs entirely in userland

### Key Characteristics
- Asymmetric encryption: X25519 (ECDH) + Ed25519 (signatures)
- Symmetric encryption: AES-256-CBC with PKCS7 padding
- Authentication: HMAC-SHA256
- Link setup: Only 3 packets (297 bytes total)
- Link maintenance: 0.44 bits/second

---

## Core Components

### 1. RNS (Reticulum Network Stack)
**Package:** `rns`
**Purpose:** Core cryptographic networking layer

```bash
pip install rns
```

**Included Utilities:**
| Utility | Purpose |
|---------|---------|
| `rnsd` | System daemon for running as service |
| `rnstatus` | Interface status display |
| `rnpath` | Path lookup and management |
| `rnprobe` | Connectivity diagnostics |
| `rncp` | File transfer program |
| `rnid` | Identity management |
| `rnx` | Remote command execution |

### 2. LXMF (Lightweight Extensible Message Format)
**Package:** `lxmf`
**Purpose:** Secure messaging protocol on top of RNS

```bash
pip install lxmf
```

**Features:**
- Zero-conf message routing
- End-to-end encryption with forward secrecy
- Store-and-forward via propagation nodes
- Only 111 bytes overhead per message
- Works over extremely low bandwidth (5 bits/sec)

### 3. NomadNet
**Package:** `nomadnet`
**Purpose:** Terminal-based messaging + web-like node browser

```bash
pip install nomadnet
```

**Features:**
- Text-based UI for messaging
- **Connectable nodes** that host pages/files
- Server-side scripting (Python, PHP, bash)
- Custom markup language (Micron)
- Works over 300bps radio links
- Daemon mode for headless operation

### 4. Sideband
**Package:** `sbapp`
**Purpose:** Mobile-friendly LXMF client with telemetry

```bash
pip install sbapp
```

**Features:**
- Android, Linux, macOS support
- **P2P telemetry and location sharing**
- Offline maps support
- 20+ built-in sensor types
- Plugin system for custom telemetry
- MQTT export capability
- Headless daemon mode

---

## Python API Reference

### Initializing Reticulum

```python
import RNS

# Initialize Reticulum (must be done first)
reticulum = RNS.Reticulum()

# Or with custom config directory
reticulum = RNS.Reticulum(configdir="/path/to/config")
```

### Identity Management

```python
# Create new identity
identity = RNS.Identity()

# Save identity to file
identity.to_file("/path/to/identity")

# Load identity from file
identity = RNS.Identity.from_file("/path/to/identity")

# Recall identity from destination hash (after receiving announce)
identity = RNS.Identity.recall(destination_hash)
```

### Destination Creation

```python
# Create a destination for receiving
destination = RNS.Destination(
    identity,
    RNS.Destination.IN,      # Direction: IN for receiving
    RNS.Destination.SINGLE,  # Type: SINGLE, GROUP, or PLAIN
    "appname",               # Application name
    "aspect"                 # Aspect name
)

# Set packet callback
def packet_callback(data, packet):
    print(f"Received: {data}")

destination.set_packet_callback(packet_callback)

# Announce destination to network
destination.announce()
```

### Announce Handler

```python
class AnnounceHandler:
    def __init__(self, aspect_filter=None):
        self.aspect_filter = aspect_filter

    @staticmethod
    def received_announce(destination_hash, announced_identity, app_data):
        """Called when matching announce is received"""
        RNS.log(f"Announce from: {RNS.prettyhexrep(destination_hash)}")

        # Recall the identity for later use
        identity = RNS.Identity.recall(destination_hash)

# Register the handler
handler = AnnounceHandler(aspect_filter="myapp")
RNS.Transport.register_announce_handler(handler)
```

### Sending Packets

```python
# Create outgoing destination
out_destination = RNS.Destination(
    remote_identity,
    RNS.Destination.OUT,
    RNS.Destination.SINGLE,
    "appname",
    "aspect"
)

# Send a packet
packet = RNS.Packet(out_destination, b"Hello World")
packet.send()
```

### Link Establishment

```python
# Create a link to remote destination
link = RNS.Link(destination)

# Link callbacks
def link_established(link):
    print("Link established!")
    link.send(b"Connected!")

def link_closed(link):
    print("Link closed")

link.set_link_established_callback(link_established)
link.set_link_closed_callback(link_closed)
```

### Path Management

```python
# Check if path exists
has_path = RNS.Transport.has_path(destination_hash)

# Request path if needed
if not has_path:
    RNS.Transport.request_path(destination_hash)

    # Wait for path
    import time
    while not RNS.Transport.has_path(destination_hash):
        time.sleep(0.1)
```

---

## LXMF Messaging

### LXMRouter Setup

```python
import LXMF
import RNS

# Initialize RNS first
reticulum = RNS.Reticulum()

# Create router
router = LXMF.LXMRouter(storagepath="./lxmf_storage")

# Create identity for sending
identity = RNS.Identity()

# Register delivery identity
source = router.register_delivery_identity(
    identity,
    display_name="My Node",
    stamp_cost=8
)

# Announce our presence
router.announce(source.hash)
```

### Sending Messages

```python
# Create destination from known hash
recipient_hash = bytes.fromhex("abcd1234...")

# Ensure path exists
if not RNS.Transport.has_path(recipient_hash):
    RNS.Transport.request_path(recipient_hash)
    while not RNS.Transport.has_path(recipient_hash):
        time.sleep(0.1)

# Get recipient identity
recipient_identity = RNS.Identity.recall(recipient_hash)

# Create destination
dest = RNS.Destination(
    recipient_identity,
    RNS.Destination.OUT,
    RNS.Destination.SINGLE,
    "lxmf",
    "delivery"
)

# Create and send message
message = LXMF.LXMessage(
    dest,                              # Destination
    source,                            # Source
    "Hello from MeshForge!",           # Content
    "Test Message",                    # Title (optional)
    desired_method=LXMF.LXMessage.DIRECT,
    include_ticket=True
)

router.handle_outbound(message)
```

### Receiving Messages

```python
def delivery_callback(message):
    """Called when message is received"""
    print(f"From: {message.source_hash}")
    print(f"Title: {message.title}")
    print(f"Content: {message.content}")
    print(f"Timestamp: {message.timestamp}")

router.register_delivery_callback(delivery_callback)
```

### Propagation Nodes

```python
# Enable propagation node functionality
router.enable_propagation()

# Set outbound propagation node for store-and-forward
prop_node_hash = bytes.fromhex("...")
router.set_outbound_propagation_node(prop_node_hash)

# Send via propagation (for offline recipients)
message = LXMF.LXMessage(
    dest, source, "Message content",
    desired_method=LXMF.LXMessage.PROPAGATED
)
router.handle_outbound(message)
```

### Delivery Methods

| Method | Description | Encryption |
|--------|-------------|------------|
| `DIRECT` | Link-based delivery | Ephemeral AES-128, forward secrecy |
| `OPPORTUNISTIC` | Single-packet delivery | Per-packet encryption |
| `PROPAGATED` | Store-and-forward via nodes | Full encryption |

---

## NomadNet Web Capabilities

### Node Server Setup

NomadNet nodes can host web-like pages accessible via the text-based browser:

```bash
# Run as daemon (headless server)
nomadnet --daemon

# Run with custom config
nomadnet --config /path/to/config
```

### Page Hosting

Pages are stored in `~/.nomadnet/storage/pages/` using Micron markup:

```micron
>Welcome to My Node

This is a simple page hosted on Reticulum.

>>Features
`[Links work like this]`link_destination
`*Bold text*` and `/italic text/`

>>Dynamic Content
Pages can include server-side scripts!
```

### Server-Side Scripting

Pages can execute Python, bash, or other scripts:

```python
#!/usr/bin/env python3
# File: ~/.nomadnet/storage/pages/dynamic.mu

import datetime
print(f">Current Time")
print(f"The time is: {datetime.datetime.now()}")
```

### Network Access

- **Ctrl+U** - Open URL dialog to connect to nodes
- Node addresses are hex hashes (e.g., `abb3ebcd03cb2388a838e70c001291f9`)

### Integration Possibilities

- Host MeshForge status pages on NomadNet
- Create gateway status dashboard accessible via RNS
- Serve node maps and telemetry via NomadNet pages

---

## Sideband Telemetry

### Telemetry Types

Sideband supports 20+ built-in sensor types:

- **Location** - GPS coordinates, altitude
- **Battery** - Level, charging state
- **Environment** - Temperature, humidity, pressure
- **Custom** - Any arbitrary data via plugins

### Plugin Architecture

```python
# Example telemetry plugin
class LocationPlugin:
    def __init__(self, sideband):
        self.sideband = sideband

    def get_telemetry(self):
        return {
            "type": "location",
            "latitude": 21.3069,
            "longitude": -157.8583,
            "altitude": 10,
            "accuracy": 5
        }
```

### MQTT Export

Sideband can export telemetry to MQTT brokers:

```yaml
# ~/.sideband/config
mqtt:
  enabled: true
  broker: mqtt://localhost:1883
  topic_prefix: sideband/telemetry
```

### Daemon Mode

```bash
# Run as telemetry collector
sideband --daemon
```

---

## Gateway Architecture

### MeshForge Gateway Design

```
┌─────────────────────────────────────────────────────────────────┐
│                    MeshForge Gateway Service                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────┐              ┌─────────────────┐           │
│  │  Meshtastic     │              │  Reticulum      │           │
│  │  Connector      │◄────────────►│  Connector      │           │
│  └────────┬────────┘              └────────┬────────┘           │
│           │                                │                     │
│  ┌────────▼────────┐              ┌────────▼────────┐           │
│  │ Message Queue   │              │ LXMF Router     │           │
│  │ (Meshtastic)    │◄────────────►│ (RNS Messages)  │           │
│  └────────┬────────┘              └────────┬────────┘           │
│           │                                │                     │
│  ┌────────▼────────────────────────────────▼────────┐           │
│  │              Message Translator                   │           │
│  │  - Format conversion                              │           │
│  │  - Address mapping                                │           │
│  │  - Routing rules                                  │           │
│  └────────┬────────────────────────────────┬────────┘           │
│           │                                │                     │
│  ┌────────▼────────┐              ┌────────▼────────┐           │
│  │ Unified Node    │              │ Telemetry       │           │
│  │ Tracker         │              │ Aggregator      │           │
│  └─────────────────┘              └─────────────────┘           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Message Flow

1. **Meshtastic → RNS:**
   ```
   Meshtastic Node → meshtasticd → TCP API → Gateway → LXMF Router → RNS Network
   ```

2. **RNS → Meshtastic:**
   ```
   RNS Network → LXMF Router → Gateway → Meshtastic CLI → LoRa Broadcast
   ```

### Address Mapping

| Meshtastic | RNS |
|------------|-----|
| `!abcd1234` (Node ID) | 16-byte destination hash |
| Channel 0-7 | Destination aspects |
| Broadcast (`!ffffffff`) | Group destinations |

---

## Integration with Meshtastic

### Meshtastic TCP API

```python
import meshtastic
import meshtastic.tcp_interface

# Connect to meshtasticd
interface = meshtastic.tcp_interface.TCPInterface(hostname="localhost")

# Get node info
my_info = interface.getMyNodeInfo()
nodes = interface.nodes

# Send message
interface.sendText("Hello from gateway!", channelIndex=0)

# Receive messages
def on_receive(packet, interface):
    print(f"Received: {packet}")

pub.subscribe(on_receive, "meshtastic.receive")
```

### Unified Node Tracking

```python
class UnifiedNode:
    def __init__(self):
        self.node_id: str          # Unified ID
        self.network: str          # "meshtastic" or "rns"
        self.name: str
        self.position: dict        # lat, lon, alt
        self.last_seen: datetime
        self.telemetry: dict       # battery, env sensors
        self.snr: float
        self.rssi: int

        # Network-specific
        self.meshtastic_id: str    # !abcd1234
        self.rns_hash: bytes       # 16-byte hash
```

### Map Visualization

Both networks on single map:
- **Green markers**: My nodes (Meshtastic + RNS)
- **Blue markers**: Meshtastic nodes
- **Purple markers**: RNS nodes
- **Dashed lines**: Gateway connections

---

## Code Examples

### Complete Gateway Bridge Example

```python
#!/usr/bin/env python3
"""
MeshForge RNS-Meshtastic Gateway Bridge
"""

import RNS
import LXMF
import meshtastic.tcp_interface
import threading
import time
from queue import Queue

class MeshForgeGateway:
    def __init__(self):
        self.running = False
        self.mesh_queue = Queue()
        self.rns_queue = Queue()

        # Initialize RNS
        self.reticulum = RNS.Reticulum()
        self.identity = RNS.Identity()

        # Initialize LXMF Router
        self.lxmf_router = LXMF.LXMRouter(storagepath="./gateway_storage")
        self.lxmf_source = self.lxmf_router.register_delivery_identity(
            self.identity,
            display_name="MeshForge Gateway"
        )
        self.lxmf_router.register_delivery_callback(self._on_lxmf_message)

        # Initialize Meshtastic
        self.mesh_interface = None

    def start(self):
        """Start the gateway"""
        self.running = True

        # Connect to Meshtastic
        self.mesh_interface = meshtastic.tcp_interface.TCPInterface(
            hostname="localhost"
        )

        # Announce on RNS
        self.lxmf_router.announce(self.lxmf_source.hash)

        # Start processing threads
        threading.Thread(target=self._process_mesh_queue, daemon=True).start()
        threading.Thread(target=self._process_rns_queue, daemon=True).start()

        RNS.log("Gateway started")

    def stop(self):
        """Stop the gateway"""
        self.running = False
        if self.mesh_interface:
            self.mesh_interface.close()

    def _on_lxmf_message(self, message):
        """Handle incoming LXMF message"""
        RNS.log(f"RNS message from {message.source_hash.hex()}")
        self.rns_queue.put({
            "source": message.source_hash.hex(),
            "content": message.content,
            "title": message.title
        })

    def _on_mesh_message(self, packet, interface):
        """Handle incoming Meshtastic message"""
        if packet.get("decoded", {}).get("portnum") == "TEXT_MESSAGE_APP":
            self.mesh_queue.put({
                "from": packet.get("fromId"),
                "to": packet.get("toId"),
                "text": packet["decoded"]["payload"].decode()
            })

    def _process_mesh_queue(self):
        """Forward Meshtastic messages to RNS"""
        while self.running:
            try:
                msg = self.mesh_queue.get(timeout=1)
                # Convert and forward to RNS
                # (Implementation depends on routing rules)
                RNS.log(f"Forwarding mesh→RNS: {msg}")
            except:
                pass

    def _process_rns_queue(self):
        """Forward RNS messages to Meshtastic"""
        while self.running:
            try:
                msg = self.rns_queue.get(timeout=1)
                # Forward to Meshtastic
                if self.mesh_interface:
                    self.mesh_interface.sendText(
                        f"[RNS] {msg['content']}",
                        channelIndex=0
                    )
                RNS.log(f"Forwarding RNS→mesh: {msg}")
            except:
                pass

if __name__ == "__main__":
    gateway = MeshForgeGateway()
    gateway.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        gateway.stop()
```

---

## Resources

### Official Documentation
- [Reticulum Manual](https://markqvist.github.io/Reticulum/manual/)
- [LXMF GitHub](https://github.com/markqvist/LXMF)
- [NomadNet GitHub](https://github.com/markqvist/nomadnet)
- [Sideband GitHub](https://github.com/markqvist/Sideband)

### PyPI Packages
- [rns on PyPI](https://pypi.org/project/rns/)
- [lxmf on PyPI](https://pypi.org/project/lxmf/)
- [nomadnet on PyPI](https://pypi.org/project/nomadnet/)
- [sbapp on PyPI](https://pypi.org/project/sbapp/)

### Example Code
- [Reticulum Examples](https://github.com/markqvist/Reticulum/tree/master/Examples)
- [LXMF Example Sender](https://github.com/markqvist/LXMF/blob/master/docs/example_sender.py)

### User's RNS Projects
- [RNS-Meshtastic-Gateway-Tool](https://github.com/Nursedude/RNS-Meshtastic-Gateway-Tool)
- [RNS-Management-Tool](https://github.com/Nursedude/RNS-Management-Tool)

### Test Nodes
- **Dublin Hub:** `abb3ebcd03cb2388a838e70c001291f9`
- **Frankfurt Hub:** `ea6a715f814bdc37e56f80c34da6ad51`

---

---

## LXST - Real-Time Streaming

**Package:** `lxst` (in development)
**Purpose:** Low-latency streaming over RNS

### Features
- Real-time signal streams with <10ms latency
- End-to-end encryption with forward secrecy
- Multi-codec support:
  - Raw/lossless (up to 32 channels, 128-bit precision)
  - OPUS voice (4.5-96 kbps)
  - Codec2 ultra-low-bandwidth (700-3200 bps)
- Dynamic codec switching mid-stream
- In-band signaling for call management
- Signal mixing support

### Use Cases
- Voice calls over mesh
- Two-way radio systems
- Media streaming
- Broadcast systems

### Reference Implementation
- `rnphone` - telephony program included
- Sideband app integration

---

## RNS Over Meshtastic Interface

**Project:** [RNS_Over_Meshtastic](https://github.com/landandair/RNS_Over_Meshtastic)
**Purpose:** Use Meshtastic hardware as RNS transport

### Architecture

```
┌─────────────────┐
│  RNS Stack      │  ← Applications (NomadNet, Sideband, etc.)
├─────────────────┤
│  LXMF Router    │  ← Messaging layer
├─────────────────┤
│  Meshtastic     │  ← Custom RNS interface
│  Interface.py   │
├─────────────────┤
│  Meshtastic     │  ← Standard Meshtastic protocol
│  Radio (LoRa)   │
└─────────────────┘
```

### Configuration

Add to Reticulum config (`~/.reticulum/config`):

```ini
[[Meshtastic Interface]]
type = Meshtastic_Interface
enabled = true
mode = gateway

# Connection method (choose one):
port = /dev/ttyUSB0          # Serial
# ble_port = short_1234      # Bluetooth LE
# tcp_port = 127.0.0.1:4403  # TCP (meshtasticd)

# Radio speed setting
data_speed = 8
```

### Speed Settings

| Speed | Mode | TX Delay |
|-------|------|----------|
| 8 | Short Turbo | 0.4s |
| 6 | Short Fast | 1.0s |
| 5 | Short Slow | 3.0s |
| 4 | Medium Fast | 4.0s |
| 3 | Medium Slow | 6.0s |
| 7 | Long Fast | 12.0s |
| 0 | Long Fast Alt | 8.0s |
| 1 | Long Slow | 15.0s |

### Performance
- ~500 bytes/second throughput
- Coexists with normal Meshtastic traffic
- Multi-hop routing via mesh topology

### MeshForge Integration

This interface allows MeshForge to:
1. Run RNS applications over existing Meshtastic hardware
2. Access RNS network from any Meshtastic node
3. Bridge RNS and Meshtastic messaging
4. Share telemetry between networks

---

## MeshForge Gateway Implementation

### Hybrid Approach

MeshForge can operate in two modes:

**Mode 1: RNS Over Meshtastic**
- Uses Meshtastic_Interface.py
- RNS runs directly over LoRa radio
- Best for dedicated gateway nodes

**Mode 2: Message Bridge**
- Separate RNS and Meshtastic stacks
- Software bridge translates between protocols
- Best for multi-network visibility

### Unified Node Tracker

```python
class UnifiedNode:
    """Represents a node from either network"""
    id: str                    # Unified identifier
    network: str               # "meshtastic" | "rns" | "both"
    name: str
    short_name: str

    # Position
    latitude: float
    longitude: float
    altitude: float
    position_time: datetime

    # Telemetry
    battery_level: int
    voltage: float
    temperature: float
    humidity: float

    # Network-specific
    meshtastic_id: str         # !abcd1234
    rns_hash: bytes            # 16-byte hash
    snr: float
    rssi: int
    hops: int
    last_seen: datetime
```

### Map Integration

Both networks displayed with distinct markers:

| Network | Marker Color | Icon |
|---------|-------------|------|
| My Meshtastic Node | Green | Circle |
| Other Meshtastic | Blue | Circle |
| My RNS Node | Green | Diamond |
| Other RNS | Purple | Diamond |
| Gateway Node | Orange | Star |

---

*Created: 2026-01-03*
*Updated: 2026-01-03*
*Status: Active Research*
*Version: 1.1*

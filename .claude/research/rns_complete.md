# Reticulum Network Stack (RNS) Complete Reference

> Research compiled for MeshForge integration
> Focus: Standalone operation, config management, gateway reliability

## Executive Summary

RNS is a cryptographic mesh networking protocol for unstoppable communications over any transport (LoRa, WiFi, TCP, serial, I2P). MeshForge integrates RNS with Meshtastic to bridge two mesh ecosystems.

---

## 1. RNS Python API

### 1.1 Core Initialization

```python
import RNS
import LXMF

# Initialize Reticulum (singleton - one per process)
reticulum = RNS.Reticulum()  # Uses ~/.reticulum/config

# Custom config directory
reticulum = RNS.Reticulum(configdir="/custom/path")
```

**Critical**: Check if rnsd is running BEFORE initializing:
```python
from utils.gateway_diagnostic import find_rns_processes
if find_rns_processes():
    logger.info("Using existing rnsd daemon")
    return  # Don't initialize our own
```

### 1.2 Identity Management

```python
# Create new identity (X25519 + Ed25519)
identity = RNS.Identity()

# Save/load
identity.to_file("/path/to/identity.key")
identity = RNS.Identity.from_file("/path/to/identity.key")

# Recall from announce
identity = RNS.Identity.recall(destination_hash)
```

### 1.3 Destinations

```python
# Create destination for receiving
destination = RNS.Destination(
    identity,
    RNS.Destination.IN,      # IN=receive, OUT=send
    RNS.Destination.SINGLE,  # SINGLE, GROUP, or PLAIN
    "app_name",
    "aspect"
)

# Set callback
destination.set_packet_callback(lambda data, packet: print(f"Got {len(data)} bytes"))

# Announce to network
destination.announce()
```

### 1.4 Announce Handler

```python
class AnnounceHandler:
    def __init__(self):
        self.aspect_filter = "lxmf.delivery"  # Filter announces

    @staticmethod
    def received_announce(dest_hash, identity, app_data):
        print(f"Discovered: {dest_hash.hex()[:8]}")

RNS.Transport.register_announce_handler(AnnounceHandler())
```

### 1.5 Transport & Paths

```python
# Check path exists
has_path = RNS.Transport.has_path(destination_hash)

# Request path discovery
RNS.Transport.request_path(destination_hash)

# Wait for path
for _ in range(50):
    if RNS.Transport.has_path(destination_hash):
        break
    time.sleep(0.1)
```

### 1.6 Links (Encrypted Tunnels)

```python
link = RNS.Link(destination)

link.set_link_established_callback(lambda l: print("Connected!"))
link.set_link_closed_callback(lambda l: print("Closed"))

link.send(data)  # Encrypted
link.teardown()
```

---

## 2. Config File Structure

Location: `~/.reticulum/config`

### 2.1 Main Sections

```ini
[reticulum]
enable_transport = yes          # Enable message forwarding
share_instance = yes            # Share with other programs
shared_instance_port = 37428
instance_control_port = 37429

[logging]
loglevel = 4                    # 0=Critical to 6=Debug

[interfaces]
  # Interface definitions below
```

### 2.2 Interface Types

**AutoInterface** (zero-config local network):
```ini
[[Default Interface]]
  type = AutoInterface
  enabled = yes
```

**TCPServerInterface** (accept connections):
```ini
[[RNS Server]]
  type = TCPServerInterface
  enabled = yes
  listen_ip = 0.0.0.0
  listen_port = 4242
```

**TCPClientInterface** (connect to server):
```ini
[[Remote Gateway]]
  type = TCPClientInterface
  enabled = yes
  target_host = 192.168.1.100
  target_port = 4242
```

**SerialInterface**:
```ini
[[Serial Link]]
  type = SerialInterface
  enabled = yes
  port = /dev/ttyUSB0
  speed = 115200
```

**RNodeInterface** (LoRa hardware):
```ini
[[LoRa Gateway]]
  type = RNodeInterface
  enabled = yes
  port = /dev/ttyUSB0
  frequency = 903625000
  txpower = 22
  bandwidth = 250000
  spreadingfactor = 7
  codingrate = 5
```

**Meshtastic_Interface**:
```ini
[[Meshtastic Gateway]]
  type = Meshtastic_Interface
  enabled = yes
  tcp_port = 127.0.0.1:4403
  data_speed = 8
  hop_limit = 3
```

### 2.3 Config Validation

```python
def validate_rns_config(config: str) -> Tuple[bool, List[str]]:
    errors = []
    if '[reticulum]' not in config.lower():
        errors.append("Missing [reticulum] section")
    if config.count('[') != config.count(']'):
        errors.append("Mismatched brackets")
    return len(errors) == 0, errors
```

---

## 3. LXMF (Messaging Layer)

### 3.1 Setup

```python
import LXMF

router = LXMF.LXMRouter(storagepath="~/.lxmf_storage")

identity = RNS.Identity()
source = router.register_delivery_identity(identity, display_name="My Node")

router.announce(source.hash)
```

### 3.2 Sending Messages

```python
dest = RNS.Destination(
    recipient_identity,
    RNS.Destination.OUT,
    RNS.Destination.SINGLE,
    "lxmf", "delivery"
)

message = LXMF.LXMessage(
    dest, source,
    "Hello!",
    "Title",
    desired_method=LXMF.LXMessage.DIRECT
)

router.handle_outbound(message)
```

### 3.3 Delivery Methods

| Method | Latency | Use Case |
|--------|---------|----------|
| DIRECT | <1s | Real-time, online recipient |
| OPPORTUNISTIC | 1-30s | Network-wide, single packet |
| PROPAGATED | 30s-hours | Offline recipients |
| AUTO | varies | Let router decide |

### 3.4 Receiving

```python
def on_message(message):
    print(f"From: {message.source_hash.hex()}")
    print(f"Content: {message.content}")

router.register_delivery_callback(on_message)
```

---

## 4. MeshForge Gateway Implementation

### 4.1 Architecture

```
Meshtastic LoRa <--> meshtasticd <--> MeshForge Bridge <--> RNS/LXMF
     (4403)              ^                    |
                         |                    v
                    meshing-around      ~/.reticulum/config
```

### 4.2 Key Files

- `src/gateway/rns_bridge.py` - Main bridge (901 lines)
- `src/gateway/node_tracker.py` - Unified node tracking
- `src/gateway/config.py` - Gateway configuration

### 4.3 Message Flow

```
Mesh -> Queue -> Translate -> LXMF -> RNS Network
RNS -> Queue -> Translate -> Meshtastic API -> Mesh
```

---

## 5. Commands Module Design

### commands/rns.py Structure

```python
# STATUS
get_status()           # RNS daemon status
get_nodes()            # Discovered nodes
get_interfaces()       # Active interfaces

# MESSAGES
send_message()         # Send LXMF message
get_messages()         # Get inbox/outbox
mark_read()            # Mark as read

# CONFIG
read_config()          # Read ~/.reticulum/config
write_config()         # Write config (with backup)
add_interface()        # Add new interface
remove_interface()     # Remove interface
validate_config()      # Validate syntax

# SERVICE
start_rnsd()           # Start daemon
stop_rnsd()            # Stop daemon
restart_rnsd()         # Restart daemon

# DIAGNOSTICS
test_path()            # Test path to node
check_connectivity()   # Test network
get_path_info()        # Path metrics
```

---

## 6. Config File Locations

| Tool | Config Path |
|------|-------------|
| RNS | `~/.reticulum/config` |
| LXMF Storage | `~/.lxmf/` |
| NomadNet | `~/.nomadnetwork/config` |
| Sideband | `~/.config/sideband/` |
| MeshChat | `~/.config/meshchat/` |
| MeshForge | `~/.config/meshforge/` |

---

## 7. Security Considerations

1. **No shell=True** in subprocess
2. **Validate all inputs** before routing
3. **Timeouts** on network operations
4. **Handle rnsd conflicts** gracefully
5. **Encrypt identity files** at rest
6. **Rate limit** message processing
7. **Audit** gateway operations

---

## 8. Implementation Priority

**Phase 1 - Config Management**:
- [ ] Read/write ~/.reticulum/config
- [ ] Interface CRUD operations
- [ ] Config validation
- [ ] Backup/restore

**Phase 2 - Messaging**:
- [ ] LXMF send/receive via commands layer
- [ ] Message storage
- [ ] Delivery confirmation

**Phase 3 - Reliability Testing**:
- [ ] Path testing
- [ ] Latency measurements
- [ ] Link quality tracking

---

## References

- RNS: https://github.com/markqvist/Reticulum
- LXMF: https://github.com/markqvist/LXMF
- NomadNet: https://github.com/markqvist/nomadnet
- meshing-around: https://github.com/SpudGunMan/meshing-around

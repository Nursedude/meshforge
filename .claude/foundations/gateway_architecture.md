# MeshForge Gateway Architecture

## Overview

MeshForge acts as a Network Operations Center (NOC) bridging multiple mesh networks:
- **Meshtastic** (LoRa mesh for low-bandwidth, long-range)
- **Reticulum (RNS)** (encrypted, resilient networking)
- **AREDN** (Amateur Radio Emergency Data Network)

## The Dual-Preset Gateway Architecture

### Problem Statement

Meshtastic networks often need to bridge different presets (Short Turbo, Long Fast, etc.) while also connecting to RNS for wider network access. The challenge:

1. **meshtasticd allows only ONE TCP client** at a time (port 4403)
2. Different presets require different radio hardware/instances
3. RNS integration needs to coexist without conflicts

### Solution: MeshForge as Central Hub

```
┌─────────────────────────────────────────────────────────────────┐
│                    GATEWAY ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  MOC1 (Short Turbo)           MOC2 (Long Fast)                  │
│  High bandwidth, short range  Extended range, lower bandwidth   │
│       ↓ LoRa                       ↓ LoRa                       │
│  meshtasticd:4403             meshtasticd:4404                  │
│       ↓                            ↓                             │
│       └──────────→ MeshForge ←─────┘                            │
│                    Gateway                                       │
│                       ↓                                          │
│              RNS (shared instance)                               │
│                       ↓                                          │
│              TCPInterface → HawaiiNet RNS                        │
│                       ↓                                          │
│              Wider RNS Network                                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Key Principles

1. **MeshForge owns all Meshtastic connections**
   - Single connection manager handles TCP:4403 and TCP:4404
   - No external Meshtastic_Interface.py needed

2. **RNS via shared instance**
   - rnsd runs independently (port 37428)
   - MeshForge connects as client, not transport
   - No TCP:4403 conflict

3. **Different presets serve different purposes**
   - Short Turbo (data_speed=8): Gateway-to-gateway, high bandwidth
   - Long Fast (data_speed=0): Field nodes, extended range
   - Medium Fast (data_speed=4): Balanced option

## Bridge Modes

MeshForge supports three bridge modes:

### 1. Message Bridge (`message_bridge`) - Default
- Translates LXMF messages ↔ Meshtastic text messages
- Works with any Meshtastic node (no special setup)
- Port 1 (TEXT_MESSAGE_APP) for Meshtastic
- LXMF router for RNS

### 2. RNS Transport (`rns_transport`)
- Makes Meshtastic a transport layer for RNS
- Uses Port 256 (PRIVATE_APP)
- Requires Meshtastic_Interface.py on BOTH ends
- Pure RNS-over-Meshtastic

### 3. Mesh Bridge (`mesh_bridge`)
- Bridges two Meshtastic networks on different presets
- Useful for range extension
- Can combine with Message Bridge for RNS connectivity

## Implementation Details

### Connection Manager
```python
from utils.meshtastic_connection import get_connection_manager

# Acquire persistent connection
conn = get_connection_manager(host='localhost', port=4403)
if conn.acquire_persistent(owner='gateway'):
    interface = conn.get_interface()
```

### Detecting Conflicts
```python
# Check if rnsd is already using Meshtastic
from gateway.rns_bridge import is_gateway_running

if is_gateway_running():
    # Don't start another bridge
    pass
```

### Port Allocation
| Service | Port | Purpose |
|---------|------|---------|
| meshtasticd (primary) | 4403 | Main Meshtastic TCP API |
| meshtasticd (secondary) | 4404 | Second preset (if needed) |
| Meshtastic Web UI | 9443 | Browser interface |
| RNS shared instance | 37428 | rnsd communication |
| HamClock | 8080 | Space weather UI |
| MeshChat | 8000 | RNS chat web UI |

## Configuration

### MOC1 (Short Turbo Gateway)
```yaml
# /etc/meshtasticd/config.yaml
Lora:
  Region: US
  ModemPreset: SHORT_TURBO  # data_speed=8

# MeshForge gateway.json
{
  "meshtastic_host": "localhost",
  "meshtastic_port": 4403,
  "bridge_mode": "message_bridge",
  "rns_enabled": true
}
```

### MOC2 (Long Fast Gateway)
```yaml
# /etc/meshtasticd/config.yaml
Lora:
  Region: US
  ModemPreset: LONG_FAST  # data_speed=0

# MeshForge gateway.json
{
  "meshtastic_host": "moc1-ip-address",  # Connect to MOC1
  "meshtastic_port": 4403,
  "bridge_mode": "mesh_bridge"
}
```

### RNS Configuration (on MOC1)
```ini
[reticulum]
enable_transport = True
share_instance = Yes
shared_instance_port = 37428

[interfaces]
[[HawaiiNet RNS]]
    type = TCPClientInterface
    enabled = yes
    target_host = 192.168.86.38
    target_port = 4242

# Note: NO Meshtastic_Interface here
# MeshForge handles Meshtastic bridging
```

## Migration from Meshtastic_Interface.py

If you were using Meshtastic_Interface.py directly:

1. **Disable in RNS config**
   ```bash
   # Comment out or remove:
   # [[Meshtastic Interface]]
   #     type = Meshtastic_Interface
   ```

2. **Start MeshForge gateway instead**
   ```bash
   # In MeshForge Rich CLI: b → 1 (Start Gateway Bridge)
   ```

3. **Benefits**:
   - No TCP:4403 conflict
   - Unified monitoring
   - Works with any Meshtastic node (not just RNS peers)

## Troubleshooting

### "0 B traffic" on Meshtastic interface
- **Cause**: No Meshtastic_Interface.py peer, or wrong port
- **Fix**: Use MeshForge message_bridge mode instead

### "Connection refused" on port 4403
- **Cause**: meshtasticd not running or another client connected
- **Fix**: Check `lsof -i :4403`, restart meshtasticd

### RNS paths not populating
- **Cause**: TCPInterface not bidirectional, ifac_netname mismatch
- **Fix**: Check server config, match ifac_netname

### Meshtastic web UI stuck in "waiting"
- **Cause**: Different issue - waiting for ACK from other Meshtastic nodes
- **Note**: Not related to RNS gateway

## Future Improvements

1. **Multi-preset support**: Single MeshForge managing multiple meshtasticd instances
2. **Auto-discovery**: Detect meshtasticd on network
3. **Proxy mode**: MeshForge proxies all Meshtastic access (web UI goes through MeshForge)
4. **AREDN integration**: Bridge AREDN with Meshtastic/RNS

---

*Document Version: 1.0*
*Last Updated: 2026-01-20*
*Author: Dude AI / WH6GXZ*

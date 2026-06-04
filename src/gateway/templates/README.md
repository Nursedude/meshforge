# MeshForge Gateway Configuration Templates

Pre-configured gateway templates for common bridging scenarios.

## Available Templates

### 1. `meshtastic_rns_bridge.json` - Meshtastic <> RNS Message Bridge

Bridges messages between Meshtastic LoRa mesh and Reticulum (RNS/LXMF) networks.

**Use case**: Connect Meshtastic users to RNS-based applications like NomadNet.

**Requirements**:
- meshtasticd running (default port 4403)
- rnsd running
- Meshtastic radio connected

### 2. `rns_over_meshtastic.json` - RNS Over Meshtastic Transport

Uses Meshtastic LoRa as a transport layer for RNS packets.

**Use case**: Extend RNS network coverage using Meshtastic radios.

**Requirements**:
- meshtasticd running
- RNS configured to use Meshtastic transport

**Speed Presets**:
| data_speed | Preset | B/s | Range |
|------------|--------|-----|-------|
| 8 | SHORT_TURBO | 500 | Short (testing) |
| 6 | SHORT_FAST | 300 | Medium (urban) |
| 4 | MEDIUM_FAST | 100 | Long (suburban) |
| 0 | LONG_FAST | 50 | Maximum (rural) |

### 3. `meshtastic_preset_bridge.json` - LONG_FAST <> SHORT_TURBO Bridge

Bridges two Meshtastic networks with different LoRa presets.

**Use case**: Connect a wide-coverage rural mesh (LONG_FAST) with a high-speed local mesh (SHORT_TURBO).

**Requirements**:
- Two Meshtastic radios (one per preset)
- Either two meshtasticd instances on different ports:
  ```bash
  # Terminal 1 - LONG_FAST radio
  meshtasticd -h localhost -d /dev/ttyUSB0 -p 4403

  # Terminal 2 - SHORT_TURBO radio
  meshtasticd -h localhost -d /dev/ttyUSB1 -p 4404
  ```
- Or one meshtasticd (HAT radio) + one USB radio driven directly by the
  gateway — set on the secondary: `"connection_type": "serial"`,
  `"serial_device": "/dev/ttyUSB0"`. No second meshtasticd needed.
  For the primary, prefer `"connection_type": "mqtt"` (zero contention
  with the :9443 web client).

**Channel scoping** (`mesh_bridge.channels`): optional allow-list of
channel indexes (0-7), enforced in both directions on every connection
type. Empty = bridge all channels. Serial RX hears every channel of its
radio, so without an allow-list a secondary's ch0 text is re-TXed on the
primary's ch0 — which may be a public channel. Forwards preserve channel
index, so allow-listed indexes should carry the same channel (name + PSK)
on both radios. Example: `"channels": [2]` bridges only channel 2.
Malformed entries (non-integers, out of range) refuse loudly at startup.

## Installation

1. Choose a template and copy it to your config directory:

```bash
cp meshtastic_rns_bridge.json ~/.config/meshforge/gateway.json
```

2. Edit the configuration:

```bash
nano ~/.config/meshforge/gateway.json
```

3. Adjust settings for your environment:
   - Host/port for meshtasticd
   - Channel numbers
   - Routing rules
   - Logging preferences

4. Test the configuration:

```bash
# Via MeshForge CLI
python3 -m src.commands.gateway test
```

5. Start the gateway:

```bash
# Via MeshForge CLI
python3 -m src.commands.gateway start
```

## Configuration Reference

### Bridge Modes

| Mode | Description |
|------|-------------|
| `message_bridge` | Translate messages between RNS and Meshtastic |
| `rns_transport` | Use Meshtastic as RNS packet transport |
| `mesh_bridge` | Bridge two Meshtastic presets |

### Common Settings

```json
{
  "enabled": true,           // Enable the gateway
  "auto_start": false,       // Start on MeshForge launch
  "bridge_mode": "...",      // See modes above
  "log_level": "INFO",       // DEBUG, INFO, WARNING, ERROR
  "log_messages": true       // Log bridged message content
}
```

### Routing Rules (message_bridge mode)

```json
{
  "routing_rules": [
    {
      "name": "rule_name",
      "enabled": true,
      "direction": "bidirectional",  // "mesh_to_rns", "rns_to_mesh"
      "source_filter": "regex",      // Filter by source ID
      "dest_filter": "regex",        // Filter by destination ID
      "message_filter": "regex",     // Filter by message content
      "priority": 10                 // Higher = evaluated first
    }
  ]
}
```

## Monitoring

### Check Status

```bash
# Via journalctl (recommended for RPi)
journalctl -t meshforge -f | grep gateway

# Via MeshForge CLI
python3 -m src.commands.gateway status
```

## Troubleshooting

### Gateway Won't Start

1. Check services:
```bash
systemctl status meshtasticd
systemctl status rnsd
```

2. Check ports:
```bash
ss -tlnp | grep -E "4403|4404"
```

### Messages Not Bridging

1. Check routing direction matches message flow
2. Verify regex patterns in filters
3. Enable DEBUG logging for details

### Preset Bridge Loops

If seeing duplicate messages:
1. Increase `dedup_window_sec` (default 60)
2. Add `exclude_filter` pattern for bridged prefixes

---
*Made with aloha for the mesh community*

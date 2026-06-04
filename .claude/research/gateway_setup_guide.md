# RNS-Meshtastic Gateway Setup Guide

> ## ⛔ SUPERSEDED (2026-06-04) — do not follow
>
> This guide predates the composable-bridges refactor, mesh_bridge
> (dual-radio), the MQTT-RX recommended path, Theme-A, and downlink
> injection. Current docs:
> - **Variants + config schema**: `docs/GATEWAY_BRIDGE_CONFIG_GUIDE.md`
> - **Validated templates**: `docs/gateway_config_templates/`
> - **Per-box install recipe**: `docs/GATEWAY_DEPLOYMENT.md`
> - **Architecture/why**: `research/fleet_architecture_2026_06_03.md`
>
> Kept for the historical record only.

> **Created**: 2026-01-11
> **Purpose**: Step-by-step gateway configuration for bridging RNS and Meshtastic networks

---

## Overview

The MeshForge Gateway bridges two mesh ecosystems:
- **Meshtastic**: LoRa mesh network (915/868 MHz)
- **Reticulum (RNS)**: Cryptographic mesh network (multi-transport)

Two bridge modes are available:
1. **Message Bridge**: Translates messages between RNS/LXMF and Meshtastic
2. **RNS Transport**: Uses Meshtastic as a transport layer for RNS packets

---

## Prerequisites

### Required Services

```bash
# Check if meshtasticd is running
systemctl status meshtasticd

# Check if rnsd is running
systemctl status rnsd
```

### Required Ports

| Service | Port | Protocol | Purpose |
|---------|------|----------|---------|
| meshtasticd | 4403 | TCP | Meshtastic daemon |
| rnsd | 37428 | UDP | RNS AutoInterface |
| rnsd | 4242 | TCP | RNS TCP Server |

---

## Step 1: Verify Service Connectivity

### Via CLI
```bash
# Test meshtasticd connection
meshforge gateway test

# Or use Python
python3 -c "
from src.commands.gateway import test_connection
result = test_connection()
print(result.message)
"
```

### Via API
```bash
curl -X POST http://localhost:5000/api/gateway/test
```

---

## Step 2: Configure Gateway

### Configuration File
Location: `~/.config/meshforge/gateway.json`

### Basic Configuration
```json
{
  "enabled": true,
  "auto_start": false,
  "bridge_mode": "message_bridge",
  "default_route": "bidirectional",
  "meshtastic": {
    "host": "localhost",
    "port": 4403,
    "channel": 0
  },
  "rns": {
    "identity_name": "meshforge_gateway",
    "announce_interval": 300
  }
}
```

### Via CLI
```python
from src.commands.gateway import set_config
result = set_config(enabled=True, host="localhost", port=4403)
```

### Via API
```bash
curl -X POST http://localhost:5000/api/gateway/config \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "meshtastic": {"host": "localhost"}}'
```

---

## Step 3: Start the Gateway

### Via CLI
```python
from src.commands.gateway import start_gateway
result = start_gateway()
print(result.message)
```

### Via API
```bash
curl -X POST http://localhost:5000/api/gateway/start
```

### Via GTK UI
1. Open MeshForge
2. Navigate to **RNS Panel** → **Gateway** tab
3. Click **Enable Gateway**

---

## Step 4: Verify Operation

### Check Status
```bash
curl http://localhost:5000/api/gateway/status
```

Response:
```json
{
  "running": true,
  "connected": {
    "meshtastic": true,
    "rns": true
  },
  "stats": {
    "messages_bridged": 42,
    "uptime_seconds": 3600
  }
}
```

### Check Logs
```bash
journalctl -u meshforge -f | grep gateway
```

---

## RNS Over Meshtastic Transport

For using Meshtastic as an RNS transport layer:

### Configure Transport Mode
```json
{
  "bridge_mode": "rns_transport",
  "rns_transport": {
    "enabled": true,
    "connection_type": "tcp",
    "device_path": "localhost:4403",
    "data_speed": 8,
    "hop_limit": 3
  }
}
```

### Speed Presets

| Preset | Speed (B/s) | Range | Use Case |
|--------|-------------|-------|----------|
| 8 (SHORT_TURBO) | 500 | Short | Local testing |
| 6 (SHORT_FAST) | 300 | Medium | Urban mesh |
| 4 (MEDIUM_FAST) | 100 | Long | Suburban |
| 0 (LONG_FAST) | 50 | Maximum | Rural/emergency |

### Start Transport
```bash
curl -X POST http://localhost:5000/api/gateway/transport/start
```

### Monitor Statistics
```bash
curl http://localhost:5000/api/gateway/transport/stats
```

---

## Routing Rules

### Add Custom Rule
```python
from src.commands.gateway import add_routing_rule

add_routing_rule(
    name="emergency_only",
    direction="bidirectional",
    source_filter="^!emergency",  # Regex for node IDs starting with !emergency
    message_filter="SOS|HELP",    # Messages containing SOS or HELP
    priority=100
)
```

### Rule Fields
- `name`: Unique rule identifier
- `direction`: `bidirectional`, `mesh_to_rns`, or `rns_to_mesh`
- `source_filter`: Regex for source node ID
- `dest_filter`: Regex for destination node ID
- `message_filter`: Regex for message content
- `priority`: Higher = evaluated first

---

## Troubleshooting

### Gateway Won't Start

1. **Check services**:
   ```bash
   systemctl status meshtasticd rnsd
   ```

2. **Check port conflicts**:
   ```bash
   ss -tlnp | grep -E "4403|37428|4242"
   ```

3. **Run diagnostics**:
   ```bash
   curl http://localhost:5000/api/gateway/diagnostics
   ```

### Messages Not Bridging

1. **Check routing rules**:
   ```bash
   curl http://localhost:5000/api/gateway/routing-rules
   ```

2. **Verify direction setting**:
   - `bidirectional`: Both ways
   - `mesh_to_rns`: Meshtastic → RNS only
   - `rns_to_mesh`: RNS → Meshtastic only

3. **Check filter patterns**:
   - Empty filter = match all
   - Regex errors are logged as warnings

### High Packet Loss (Transport Mode)

1. **Reduce speed preset**:
   ```bash
   curl -X POST http://localhost:5000/api/gateway/transport/config \
     -d '{"data_speed": 4}'
   ```

2. **Increase hop limit**:
   ```bash
   curl -X POST http://localhost:5000/api/gateway/transport/config \
     -d '{"hop_limit": 5}'
   ```

3. **Check fragment timeout**:
   ```bash
   curl http://localhost:5000/api/gateway/transport/stats
   ```

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/gateway/status` | GET | Gateway status |
| `/api/gateway/start` | POST | Start gateway |
| `/api/gateway/stop` | POST | Stop gateway |
| `/api/gateway/config` | GET/POST | Configuration |
| `/api/gateway/test` | POST | Test connections |
| `/api/gateway/diagnostics` | GET | Run diagnostics |
| `/api/gateway/transport/status` | GET | Transport status |
| `/api/gateway/transport/start` | POST | Start transport |
| `/api/gateway/transport/stop` | POST | Stop transport |
| `/api/gateway/transport/stats` | GET | Transport statistics |

---

## CLI Reference

```python
# Gateway commands
from src.commands.gateway import (
    start_gateway,
    stop_gateway,
    get_gateway_status,
    test_connection,
    diagnose_gateway,

    # Transport commands
    start_transport,
    stop_transport,
    get_transport_status,
    get_transport_stats,
    get_transport_config,
    set_transport_config,
)
```

---

*Made with aloha for the mesh community* 🤙

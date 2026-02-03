# Session Notes: Gateway Bridge WebSocket Fix

**Date**: 2026-02-03
**Branch**: `claude/fix-mesh-message-display-g7Hx8`
**Status**: Pushed - Ready for testing

## Root Cause Identified

**The real issue**: `rnsd` (Reticulum daemon) owns the TCP connection to meshtasticd on port 4403.

```
sudo lsof -i :4403
COMMAND   PID   USER  ...  NAME
rnsd      57773 wh6gxz ... TCP localhost:41342->localhost:4403 (ESTABLISHED)
meshtasti 76286 root   ... TCP *:4403 (LISTEN)
```

Since meshtasticd only supports ONE TCP client, MeshForge's MessageListener couldn't connect when rnsd was already connected.

## Solution Implemented

Added WebSocket broadcast to the **Gateway Bridge** (`src/gateway/rns_bridge.py`):

1. Gateway Bridge starts WebSocket server on startup (port 5001)
2. When receiving TEXT_MESSAGE_APP, broadcasts to WebSocket clients
3. WebSocket server stops on bridge shutdown

This works because the Gateway Bridge:
- Connects to meshtasticd via the connection manager
- Already receives mesh messages via pubsub
- Already stores messages to SQLite via `messaging.store_incoming()`

## Key Insight

**Two architectures, choose one:**

| Architecture | How it works | Message capture |
|--------------|--------------|-----------------|
| Raw rnsd + Meshtastic_Interface.py | rnsd owns TCP, uses plugin for RNS transport | NO text messages (plugin only handles RNS protocol) |
| MeshForge Gateway Bridge | Gateway owns TCP, handles RNS + messages | YES - captures and broadcasts all messages |

## Files Changed

| File | Change |
|------|--------|
| `src/gateway/rns_bridge.py` | +52 lines: WebSocket server lifecycle + broadcast on message receive |

## Testing on Pi

```bash
# After merging PR
cd /opt/meshforge
sudo git pull origin main
sudo pip3 install -r requirements.txt --break-system-packages

# Stop raw rnsd if running (it conflicts)
sudo systemctl stop rnsd

# Run MeshForge with Gateway Bridge
sudo meshforge
# Navigate to: Gateway → Start Gateway Bridge
```

## Notes

- **Branches deleted after merge to main** - normal GitHub workflow
- The previous WebSocket fix (for MessageListener) was correct but couldn't work because rnsd owned the TCP connection
- This fix completes the picture: Gateway Bridge = single owner of meshtasticd connection + broadcasts to all web clients

## Next Session: Local MQTT Integration

**Priority:** Add local MQTT broker support for reliability architecture

### Target Architecture
```
meshtasticd
    ├── TCP:4403 → Gateway Bridge → RNS transport
    └── MQTT → local mosquitto → MeshForge MessageListener
                              → meshing-around
                              → maps/grafana/telemetry/sensors
```

### Tasks
1. **Test current fix** - Verify Gateway Bridge WebSocket works on Pi
2. **Set up mosquitto** - Local MQTT broker on Pi
3. **Configure meshtasticd MQTT** - Publish to local broker
4. **Adapt MQTTNodelessSubscriber** - Point to localhost instead of mqtt.meshtastic.org
5. **Wire MQTT → WebSocket** - MessageListener subscribes to local MQTT, broadcasts to WebSocket

### Files to Modify
- `src/monitoring/mqtt_subscriber.py` - Already has MQTT subscriber, needs local broker config
- `src/utils/message_listener.py` - Could add MQTT mode alongside TCP mode
- meshtasticd config - Enable MQTT publishing

### Benefits
- Multiple consumers (MeshForge, meshing-around, grafana, etc.)
- Decoupled from TCP one-client limitation
- Telemetry/sensor data flows to dashboards
- Reliability - MQTT broker persists messages

### Reference
- Existing repo: meshing-around-meshforge (private channel)
- Public pattern: mqtt.meshtastic.org

### Branch Policy
- Branches deleted after merge to main (normal workflow)

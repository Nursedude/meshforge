# Session Notes: Local MQTT Integration

**Date**: 2026-02-03
**Branch**: `claude/fix-mesh-message-display-StJRJ`
**Status**: Code pushed - Ready for Pi testing

## Pickup Items Status

From previous session:
1. **Test Gateway Bridge WebSocket on Pi** - NOT DONE (requires Pi hardware)
2. **Local MQTT integration code** - DONE (this session)

## Context

Previous session (Gateway WebSocket Fix) PR was merged. This session implements the local MQTT broker architecture for multi-consumer message delivery.

## Problem Solved

The TCP one-client limitation means only one component can connect to meshtasticd at a time:
- If rnsd owns TCP → MessageListener can't connect
- If Gateway Bridge owns TCP → Other tools (meshing-around) can't connect

**Solution**: Use meshtasticd's MQTT publishing to a local mosquitto broker, allowing unlimited consumers.

## Implementation

### 1. MQTTNodelessSubscriber Enhancements
**File**: `src/monitoring/mqtt_subscriber.py`

Added factory functions for easy setup:
```python
# Local broker (recommended for multi-consumer)
subscriber = create_local_subscriber()
subscriber.register_message_callback(my_handler)
subscriber.start()

# Public broker (nodeless monitoring)
subscriber = create_public_subscriber(region="US")
```

Added singleton management:
```python
start_local_subscriber()  # Start global local subscriber
stop_local_subscriber()   # Stop it
get_local_subscriber()    # Get instance
```

### 2. MessageListener MQTT Mode
**File**: `src/utils/message_listener.py`

Added dual-mode support:
```python
# TCP mode (default) - requires exclusive TCP access
listener = MessageListener(mode="tcp")

# MQTT mode - works alongside other TCP consumers
listener = MessageListener(mode="mqtt", mqtt_broker="localhost", mqtt_port=1883)

# Convenience function
start_mqtt_listener(broker="localhost", port=1883)
```

### 3. Documentation
**File**: `.claude/research/local_mqtt_architecture.md`

Complete guide for:
- Installing mosquitto
- Configuring meshtasticd MQTT publishing
- MeshForge configuration
- Testing and troubleshooting

## Architecture Diagram

```
                    ┌─────────────────┐
                    │   meshtasticd   │
                    │  (TCP:4403)     │
                    └────────┬────────┘
                             │ (single TCP client)
                             ▼
                    ┌─────────────────┐
                    │ Gateway Bridge  │◄────┐
                    │ (owns TCP)      │     │
                    └────────┬────────┘     │
                             │ MQTT publish │ WebSocket
                             ▼              │ (port 5001)
                    ┌─────────────────┐     │
                    │ Local Mosquitto │     │
                    │ (port 1883)     │     │
                    └────────┬────────┘     │
              ┌──────────────┼──────────────┤
              │              │              │
              ▼              ▼              ▼
         ┌─────────┐  ┌───────────┐  ┌──────────┐
         │MeshForge│  │meshing-   │  │ Grafana  │
         │Listener │  │around     │  │/InfluxDB │
         └─────────┘  └───────────┘  └──────────┘
```

## Files Changed

| File | Change |
|------|--------|
| `src/monitoring/mqtt_subscriber.py` | +90 lines: Factory functions, local broker defaults, singleton management |
| `src/utils/message_listener.py` | +130 lines: MQTT mode, `_run_mqtt_mode()`, `start_mqtt_listener()` |
| `.claude/research/local_mqtt_architecture.md` | New: Complete setup guide |

## Testing on Pi

### Prerequisites
```bash
# Install mosquitto
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

### Configure meshtasticd
```bash
meshtastic --host localhost --set mqtt.enabled true
meshtastic --host localhost --set mqtt.address localhost
meshtastic --host localhost --set mqtt.json_enabled true
meshtastic --host localhost --ch-index 0 --ch-set uplink_enabled true
```

### Verify
```bash
# Terminal 1: Watch MQTT
mosquitto_sub -h localhost -t 'msh/#' -v

# Terminal 2: Test MeshForge MQTT listener
python3 -c "
from utils.message_listener import start_mqtt_listener, get_listener_status
import time
start_mqtt_listener()
time.sleep(5)
print(get_listener_status())
"
```

## Next Steps (Pi Required)

1. **Test Gateway Bridge WebSocket** - From previous PR (merged)
   - Stop rnsd, start Gateway Bridge, verify WebSocket messages at ws://localhost:5001
2. **Test local MQTT integration** - This session's code
   - Install mosquitto, configure meshtasticd MQTT, verify messages flow
3. **Integration with TUI** - Add MQTT mode option to Monitoring menu
4. **WebSocket bridge** - Optional: MQTT subscriber can also broadcast to WebSocket

## Notes

- MQTT mode and TCP mode are mutually exclusive per listener instance
- Local broker defaults: `localhost:1883` (non-TLS)
- Public broker defaults: `mqtt.meshtastic.org:8883` (TLS)
- Message format is identical between modes for callback compatibility

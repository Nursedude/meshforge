# Local MQTT Architecture for MeshForge

**Date**: 2026-02-03
**Status**: Implementation in progress

## Overview

This document describes the local MQTT broker architecture that enables multiple consumers to receive Meshtastic messages without the TCP one-client limitation.

## The Problem

meshtasticd's TCP server (port 4403) only supports **ONE** client connection at a time:

```
# Only ONE of these can connect:
- rnsd (for RNS transport)
- Gateway Bridge (for RNS + message handling)
- MessageListener (for message display)
- Web client
```

When rnsd owns the connection, no other component can receive messages directly.

## The Solution: Local MQTT Broker

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
                             │              │
              ┌──────────────┼──────────────┤
              │              │              │
              ▼              ▼              ▼
         ┌─────────┐  ┌───────────┐  ┌──────────┐
         │MeshForge│  │meshing-   │  │ Grafana  │
         │Listener │  │around     │  │/InfluxDB │
         └─────────┘  └───────────┘  └──────────┘
```

## Setup Instructions

### 1. Install Mosquitto Broker

```bash
# Debian/Ubuntu/Raspberry Pi
sudo apt update
sudo apt install -y mosquitto mosquitto-clients

# Enable and start
sudo systemctl enable mosquitto
sudo systemctl start mosquitto

# Verify
mosquitto_sub -h localhost -t '#' -v &
mosquitto_pub -h localhost -t 'test' -m 'hello'
# Should see: test hello
```

### 2. Configure meshtasticd MQTT Publishing

```bash
# Enable MQTT on the device
meshtastic --host localhost --set mqtt.enabled true

# Point to local broker
meshtastic --host localhost --set mqtt.address localhost

# Enable JSON mode for human-readable messages
meshtastic --host localhost --set mqtt.json_enabled true

# Set root topic
meshtastic --host localhost --set mqtt.root msh

# Enable uplink on primary channel
meshtastic --host localhost --ch-index 0 --ch-set uplink_enabled true
```

Verify publishing:
```bash
# In terminal 1
mosquitto_sub -h localhost -t 'msh/#' -v

# Send a message from another Meshtastic device
# You should see JSON payloads appear
```

### 3. MeshForge Configuration

Create/edit `~/.config/meshforge/mqtt_nodeless.json`:

```json
{
  "broker": "localhost",
  "port": 1883,
  "use_tls": false,
  "root_topic": "msh/US/2/e",
  "channel": "LongFast",
  "key": "AQ==",
  "regions": ["US"],
  "auto_reconnect": true,
  "reconnect_delay": 5,
  "max_reconnect_delay": 60
}
```

### 4. Topic Structure

meshtasticd publishes to these topics:

```
msh/{region}/2/json/{channel}/{node_id}   # JSON (human-readable)
msh/{region}/2/e/{channel}/{node_id}      # Encrypted (binary)
```

Message types in JSON payload:
- `nodeinfo` - Node identity (long_name, short_name, hardware)
- `position` - GPS coordinates
- `telemetry` - Battery, sensors, environment
- `text` - Text messages

## Code Components

### MQTTNodelessSubscriber Enhancements

The existing `MQTTNodelessSubscriber` already supports:
- Custom broker via config
- TLS/non-TLS connections
- JSON and encrypted message parsing

For local broker, just set `use_tls: false` and `port: 1883`.

### MessageListener MQTT Mode

The `MessageListener` can be enhanced with an MQTT mode:

```python
class MessageListener:
    def __init__(self, mode: str = "tcp", mqtt_broker: str = "localhost"):
        self.mode = mode
        if mode == "mqtt":
            self._setup_mqtt_subscriber(mqtt_broker)
        else:
            self._setup_tcp_listener()
```

This allows the listener to receive messages via MQTT when the TCP port is occupied.

## Benefits

1. **Multiple consumers** - Unlimited MQTT subscribers
2. **Decoupled** - Components don't need to coordinate TCP access
3. **Reliability** - Mosquitto can persist messages
4. **Extensibility** - Easy to add Grafana, InfluxDB, custom scripts
5. **meshing-around compatible** - Standard Meshtastic MQTT format

## Configuration Files

| File | Purpose |
|------|---------|
| `/etc/mosquitto/mosquitto.conf` | Broker config |
| `~/.config/meshforge/mqtt_nodeless.json` | MeshForge MQTT subscriber config |
| `~/.config/meshforge/gateway.json` | Gateway bridge config |

## Testing

```bash
# Terminal 1: Watch all local MQTT traffic
mosquitto_sub -h localhost -t '#' -v

# Terminal 2: Start MeshForge with MQTT subscriber
sudo meshforge
# Navigate to: Monitoring → MQTT → Start Subscriber

# Terminal 3: Send test message via mesh
meshtastic --host localhost --sendtext "Test message"
```

## Troubleshooting

### meshtasticd not publishing
```bash
# Check MQTT settings
meshtastic --host localhost --get mqtt

# Verify enabled
mqtt.enabled: true
mqtt.address: localhost
mqtt.json_enabled: true
```

### Mosquitto not receiving
```bash
# Check mosquitto is running
systemctl status mosquitto

# Check mosquitto logs
journalctl -u mosquitto -f

# Test basic pub/sub
mosquitto_pub -h localhost -t 'test' -m 'hello'
```

### MeshForge not connecting
```bash
# Check config
cat ~/.config/meshforge/mqtt_nodeless.json

# Verify port (1883 for non-TLS, 8883 for TLS)
# Verify use_tls matches port
```

## Related Documents

- `examples/configs/gateway-mqtt.json` - Gateway MQTT config example

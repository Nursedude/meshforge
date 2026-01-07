# Meshtasticd TCP Port Conflicts

## The Problem

**meshtasticd only allows ONE TCP client connection at a time on port 4403.**

When multiple applications try to connect:
- MeshForge GTK (map, radio config)
- meshing-around bot
- meshtastic CLI tools
- meshtastic Python scripts
- NomadNet (via meshtastic interface)
- Web client

Only ONE can be connected. Others get:
- "Connection refused"
- "Waiting for delivery" (messages stuck)
- No incoming messages
- Intermittent disconnections

## Root Cause

From meshtastic-firmware:
```cpp
// Only one TCP client allowed at a time
if (client_connected) {
    new_client.stop();
    return;
}
```

## Symptoms

1. **"Waiting for delivery"** - Message sent but connection lost before ACK
2. **No incoming messages** - Another client has the connection
3. **Map shows no nodes** - Can't connect to get node list
4. **Radio settings fail** - CLI can't connect
5. **Intermittent RNS issues** - Competing for resources

## Current Workarounds

### 1. Kill Competing Clients (Network Diagnostics Panel)
```bash
# MeshForge provides a "Kill Clients" button that runs:
pkill -9 -f 'nomadnet'
pkill -9 -f 'python.*meshtastic'
pkill -9 -f 'lxmf'
```

### 2. Use Serial Instead of TCP
If you have physical access to the device:
```python
# In meshtastic Python code:
interface = meshtastic.serial_interface.SerialInterface()
# Instead of:
interface = meshtastic.tcp_interface.TCPInterface()
```

### 3. Run One Client at a Time
- Stop meshing-around before using MeshForge
- Stop NomadNet meshtastic interface before using web client
- Use systemd to manage which client runs when

### 4. MQTT Instead of Direct Connection
Use MQTT for message routing (doesn't require TCP connection):
```yaml
# In meshtasticd config:
mqtt:
  enabled: true
  address: mqtt.meshtastic.org
  # or local broker
```

## Proper Solution (Future)

### Option A: Connection Broker
A daemon that:
1. Maintains single TCP connection to meshtasticd
2. Multiplexes requests from multiple clients
3. Broadcasts events to all subscribers

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  MeshForge  │────▶│              │     │             │
├─────────────┤     │  Connection  │────▶│ meshtasticd │
│  meshing-   │────▶│    Broker    │     │  (TCP 4403) │
│  around     │     │  (TCP 4404)  │     │             │
├─────────────┤     │              │     │             │
│  CLI tools  │────▶│              │     │             │
└─────────────┘     └──────────────┘     └─────────────┘
```

### Option B: Shared Memory / IPC
- Use Unix sockets for local communication
- SharedMemory for node cache
- One process owns TCP, others read from shared state

### Option C: meshtasticd Enhancement
Request upstream to:
- Allow multiple read-only clients
- Implement pub/sub for events
- Add connection queuing

## For MeshForge Users

### Recommended Setup
1. **Dedicated Mode**: Run ONLY MeshForge OR meshing-around, not both
2. **Service Management**: Use systemd to switch between modes:
   ```bash
   # MeshForge mode
   sudo systemctl stop meshing-around
   sudo python3 src/launcher.py

   # Bot mode
   # Exit MeshForge first
   sudo systemctl start meshing-around
   ```

3. **MQTT for Monitoring**: Use MQTT for passive monitoring while bot runs

### Quick Fix When Stuck
1. Go to Tools → Network Diagnostics
2. Click "Kill Clients"
3. Wait 5 seconds
4. Refresh the map

## Related Issues

- RNS AutoInterface uses UDP multicast (port 29716) - separate issue
- rnsd and MeshForge can coexist (fixed in this update)
- Serial interface works independently (no port conflict)

---
*Last updated: 2026-01-07*
*Issue tracking: Persistent development issue*

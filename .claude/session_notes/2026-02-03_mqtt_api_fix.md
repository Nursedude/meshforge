# Session Notes: MQTT API Fix

**Date**: 2026-02-03
**Branch**: `claude/meshtasticd-testing-6ix6g`
**Status**: Fixes applied - ready for testing

## Context

Continuation of meshtasticd testing session. User is testing the multi-consumer MQTT architecture:
```
meshtasticd
    ├── TCP:4403 → Gateway Bridge → RNS transport
    └── MQTT → local mosquitto → MeshForge MessageListener
                              → meshing-around
                              → future tools
```

## Issues Found & Fixed

### 1. MQTT Mixin API Mismatch
**File**: `src/launcher_tui/mqtt_mixin.py`

The `_start_mqtt_subscriber()` method was using incorrect API:
```python
# WRONG - passing keyword args to a method that expects config dict
self._mqtt_subscriber = MQTTNodelessSubscriber(
    broker=..., port=..., topic=...
)
self._mqtt_thread = threading.Thread(target=self._mqtt_subscriber.run, ...)
```

Fixed to use proper API:
```python
# CORRECT - pass config dict, use start() method
subscriber_config = {
    "broker": broker, "port": port, "root_topic": root_topic, ...
}
self._mqtt_subscriber = MQTTNodelessSubscriber(config=subscriber_config)
self._mqtt_subscriber.start()
```

### 2. Direct `.nodes` Attribute Access
The mixin was accessing `self._mqtt_subscriber.nodes` directly, but the class uses `get_nodes()`, `get_stats()` methods.

Fixed in:
- `_show_mqtt_status()` → use `get_stats()`
- `_show_mqtt_nodes()` → use `get_nodes()`
- `_show_mqtt_stats()` → use `get_stats()`
- `_export_mqtt_data()` → use `get_nodes()`

### 3. Topic Structure for Local Broker
**File**: `src/monitoring/mqtt_subscriber.py`

meshtasticd publishes to `msh/2/json/{channel}/...` (no region prefix), but `create_local_subscriber()` defaulted to `msh/US/2/e` (with region).

Added `LOCAL_ROOT_TOPIC = "msh/2/e"` and updated `create_local_subscriber()` to use it.

## Files Changed

| File | Change |
|------|--------|
| `src/launcher_tui/mqtt_mixin.py` | Fixed API usage throughout |
| `src/monitoring/mqtt_subscriber.py` | Added `LOCAL_ROOT_TOPIC`, updated `create_local_subscriber()` |

## Testing

```bash
# Syntax check - passes
python3 -m py_compile src/launcher_tui/mqtt_mixin.py
python3 -m py_compile src/monitoring/mqtt_subscriber.py

# Import verification - passes
python3 -c "from monitoring.mqtt_subscriber import create_local_subscriber, LOCAL_ROOT_TOPIC; print(LOCAL_ROOT_TOPIC)"
# Output: msh/2/e
```

## Testing on Pi

1. Run TUI: `sudo python3 src/launcher_tui/main.py`
2. Navigate to: Mesh Networks > MQTT Monitor
3. Configure > Use Local Broker
4. Start Subscriber
5. Verify connection and node discovery

## Next Steps

1. Test MQTT subscriber with actual messages flowing through mosquitto
2. Verify node discovery and caching works
3. Test export functionality
4. Commit and push when user confirms working

## Architecture Reminder

```
┌──────────────────────────────────────────────────────────────┐
│                     meshtasticd                               │
│  ┌────────────┐                      ┌─────────────────┐     │
│  │ TCP:4403   │──────────────────────│ MQTT Publisher  │     │
│  │ (1 client) │                      │ (to mosquitto)  │     │
│  └────────────┘                      └─────────────────┘     │
└───────┬──────────────────────────────────────┬───────────────┘
        │                                      │
        │ (exclusive)                          │ (pub/sub)
        ▼                                      ▼
┌───────────────────┐               ┌──────────────────────┐
│  Gateway Bridge   │               │   Local Mosquitto    │
│  └─ RNS transport │               │   (port 1883)        │
│  └─ WebSocket:5001│               └──────────┬───────────┘
└───────────────────┘                          │
                                   ┌───────────┼───────────┐
                                   │           │           │
                                   ▼           ▼           ▼
                              MeshForge   meshing-    Grafana
                              Listener    around      /InfluxDB
```

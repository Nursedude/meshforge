# meshing-around API Research

**Date**: 2026-01-11
**Purpose**: Evaluate meshing-around for MeshForge native messaging integration

## Project Overview

[SpudGunMan/meshing-around](https://github.com/SpudGunMan/meshing-around) is a feature-rich Python bot for Meshtastic networks providing:
- BBS functionality
- Automated responses
- Games and utilities
- Alert integration (NOAA, FEMA, USGS)

## Architecture

### Core Components
```
meshing-around/
├── mesh_bot.py      # Central message router
├── pong_bot.py      # Simple responder
├── config.template  # Configuration
└── modules/         # Pluggable features
    ├── games/       # DopeWars, BlackJack, etc.
    ├── radio.md     # RF monitoring
    └── llm.md       # AI integration
```

### Message Flow
```
Meshtastic Radio
    ↓ (protobuf API)
onReceive callback
    ↓
Packet parsing (from_id, channel, snr, rssi, hop, isDM)
    ↓
Command detection (prefix matching)
    ↓
Handler execution
    ↓
Response via sendText()
```

## Key Technical Details

### Packet Structure
```python
# From onReceive callback
message_from_id  # Sender node ID (!abcd1234)
channel_number   # 0 = DM, 1+ = channels
snr              # Signal-to-noise ratio
rssi             # Signal strength
hop              # Direct/Gateway/MQTT/Flooded
isDM             # True for direct messages
```

### Message Chunking
- Long messages (>160 chars) automatically split
- Ensures delivery across multi-hop networks
- Chunks reassembled at destination

### Command System
```python
# Command dictionary pattern
commands = {
    'ping': lambda: handle_ping(),
    'map': lambda: handle_map(),
    'messages': lambda: handle_messages(),
    # ...
}

# Detection in auto_response()
for cmd in commands:
    if message.startswith(cmd):
        return commands[cmd]()
```

## Integration Options for MeshForge

### Option 1: Direct Import (Not Recommended)
Import meshing-around modules directly.
- **Pros**: Full feature set immediately
- **Cons**: Dependency hell, version conflicts, maintenance burden

### Option 2: API Bridge (Recommended for interop)
Run meshing-around as separate service, bridge via IPC/socket.
- **Pros**: Isolation, can use existing deployment
- **Cons**: Extra process, configuration complexity

### Option 3: Native Implementation (Recommended for MeshForge)
Build `commands/messaging.py` using same patterns.
- **Pros**: Clean integration, unified codebase, our architecture
- **Cons**: Development effort

## Recommended Approach: Option 3

Build native messaging in MeshForge using patterns from meshing-around:

### commands/messaging.py Structure
```python
"""
MeshForge Native Messaging

Unified messaging across Meshtastic and RNS networks.
"""

from .base import CommandResult
from gateway import RNSMeshtasticBridge

def send_message(
    content: str,
    destination: str = None,  # Node ID or broadcast
    network: str = "auto",    # meshtastic, rns, auto
    channel: int = 0          # 0 = DM, 1+ = channels
) -> CommandResult:
    """Send message to mesh network."""
    pass

def get_messages(
    limit: int = 50,
    network: str = "all"
) -> CommandResult:
    """Retrieve recent messages."""
    pass

def get_conversations() -> CommandResult:
    """Get active conversations."""
    pass

def send_broadcast(
    content: str,
    network: str = "all"
) -> CommandResult:
    """Broadcast to all networks."""
    pass
```

### Integration with Gateway
```python
# In gateway/rns_bridge.py
# Already has message callbacks and routing
bridge.register_message_callback(on_message)

# commands/messaging.py wraps this
def send_message(...):
    bridge = get_active_bridge()
    if network == "meshtastic":
        return bridge.send_to_meshtastic(content, destination)
    elif network == "rns":
        return bridge.send_to_rns(content, destination_hash)
```

## Message Storage

meshing-around uses CSV logging. For MeshForge:

```python
# SQLite for message persistence
~/.config/meshforge/messages.db

CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,
    network TEXT,  -- meshtastic, rns
    from_id TEXT,
    to_id TEXT,
    content TEXT,
    channel INTEGER,
    is_dm BOOLEAN,
    snr REAL,
    rssi INTEGER
);
```

## Next Steps

1. Create `commands/messaging.py` stub with basic structure
2. Add message storage model
3. Integrate with existing gateway callbacks
4. Build GTK panel for messaging UI
5. Add TUI messaging interface

## References

- [meshing-around GitHub](https://github.com/SpudGunMan/meshing-around)
- [Meshtastic Python API](https://python.meshtastic.org/)
- [Meshtastic Python Examples](https://github.com/pdxlocations/Meshtastic-Python-Examples)

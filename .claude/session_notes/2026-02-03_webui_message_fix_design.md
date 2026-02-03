# Meshtastic Web UI Inbound Message Fix - Design Document

**Date**: 2026-02-03
**Status**: Implemented
**Priority**: Critical (blocks field work on uConsole/RPi)

## Problem Statement

The Meshtastic Web Client (port 9443) can send messages but doesn't receive inbound messages.

### Symptoms
- Send message → "waiting for delivery" → other nodes receive it ✓
- Other node sends message → local web client doesn't see it ✗
- No ACK displayed in browser

### Root Cause

**The Meshtastic HTTP API only supports ONE client connection at a time.**

From the [transport-http source](https://github.com/meshtastic/web/tree/main/packages/transport-http):
- The web client polls `/api/v1/fromradio` every 3 seconds
- Each poll "consumes" packets from the device
- If another client (MeshForge, another browser, etc.) is also polling, they "fight" for packets
- One consumer gets the packet, the other doesn't

### Related Issues
- [meshtastic/web#575](https://github.com/meshtastic/web/issues/575) - DMs not displaying
- [meshtastic/firmware#4106](https://github.com/meshtastic/firmware/issues/4106) - Messages don't show when multiple clients

## Current Architecture (Broken)

```
┌─────────────┐     ┌─────────────┐
│ Web Client  │────▶│             │
│ (browser)   │ HTTP│ meshtasticd │
└─────────────┘     │   :4403     │
                    │   :9443     │
┌─────────────┐     │             │
│ MeshForge   │────▶│             │
│ Listener    │ TCP └─────────────┘
└─────────────┘

CONFLICT: Both clients poll /api/v1/fromradio
          Packets consumed by whoever polls first
```

## Proposed Solution: MeshForge as Sole Client + WebSocket Broadcast

MeshForge becomes the **single authoritative client** to meshtasticd and provides:
1. WebSocket endpoint for real-time message push to web clients
2. HTTP proxy for the Meshtastic API (optional)

### Target Architecture

```
┌───────────────────────────────────────────────────────┐
│                   Web Browsers                         │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐      │
│  │ Web Client │  │ Web Client │  │ Web Client │      │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘      │
│        │ WS            │ WS            │ WS          │
└────────┼───────────────┼───────────────┼─────────────┘
         │               │               │
         ▼               ▼               ▼
   ┌─────────────────────────────────────────────┐
   │          MeshForge WebSocket Server         │
   │               (Port 5000)                   │
   │                                             │
   │  - Single TCP client to meshtasticd        │
   │  - Receives via pubsub                     │
   │  - Broadcasts to all WebSocket clients     │
   │  - Stores messages in SQLite               │
   │  - Serves web UI with WS client            │
   └────────────────────┬────────────────────────┘
                        │ SINGLE Connection
                        ▼
              ┌────────────────────┐
              │     meshtasticd    │
              │      (Port 4403)   │
              └────────────────────┘
```

## Implementation Plan

### Phase 1: WebSocket Server (Required)

Add WebSocket support to MeshForge's map server:

1. **New endpoint**: `ws://localhost:5000/ws/messages`
2. **On connection**: Send recent messages from SQLite
3. **On message receive**: Broadcast to all connected clients
4. **Protocol**: JSON-encoded message objects

```python
# Pseudo-code
class MessageWebSocket:
    clients = set()

    def on_connect(self, ws):
        self.clients.add(ws)
        # Send last 20 messages
        messages = messaging.get_messages(limit=20)
        ws.send(json.dumps({"type": "history", "messages": messages}))

    def broadcast(self, message):
        for client in self.clients:
            client.send(json.dumps({"type": "message", "data": message}))
```

### Phase 2: Message Listener Integration

The MessageListener already receives via pubsub. Add callback to broadcast:

```python
def _on_message_received(message_data):
    # Store in SQLite (existing)
    messaging.store_incoming(...)

    # NEW: Broadcast to WebSocket clients
    ws_server.broadcast(message_data)
```

### Phase 3: Web UI Updates

Update `web/node_map.html` to use WebSocket instead of polling:

```javascript
// Replace polling with WebSocket
const ws = new WebSocket('ws://' + window.location.host + '/ws/messages');

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'message') {
        state.receivedMessages.unshift(data.data);
        renderReceivedMessages();
        flashNewMessageIndicator();
    } else if (data.type === 'history') {
        state.receivedMessages = data.messages;
        renderReceivedMessages();
    }
};
```

### Phase 4: Native Web Client Proxy (Optional)

For users who want to use the native Meshtastic web client at :9443:

1. Create `/api/v1/fromradio` proxy endpoint in MeshForge
2. Return cached packets from MeshForge's message store
3. Redirect web client to connect to MeshForge instead of meshtasticd

This is more complex and may require modifying the served web files.

## Alternative: Use Existing WebSocket Proxy

[meshtastic-websocket-proxy](https://github.com/liamcottle/meshtastic-websocket-proxy) by Liam Cottle:

```bash
npx @liamcottle/meshtastic-websocket-proxy \
    --meshtastic-host 127.0.0.1 \
    --websocket-port 8080
```

**Pros**: Already exists, well-tested
**Cons**: Another dependency, another service to manage

MeshForge could optionally start this proxy or integrate its functionality.

## Dependencies

- `websockets` Python library (or use built-in `asyncio` WebSocket)
- No changes to meshtasticd required
- Web UI changes are internal to MeshForge

## Testing Plan

1. Start MeshForge with WebSocket server
2. Open web UI in browser
3. Connect WebSocket
4. Send message from another node
5. Verify message appears in web UI within 1-2 seconds
6. Verify no packet loss with multiple browser tabs open

## Files to Modify

1. `src/utils/map_data_service.py` - Add WebSocket server
2. `src/utils/map_http_handler.py` - Add WebSocket upgrade handler
3. `src/utils/message_listener.py` - Add broadcast callback
4. `web/node_map.html` - Replace polling with WebSocket client

## Implementation Notes (2026-02-03)

### Files Modified/Created

1. **`src/utils/websocket_server.py`** (NEW)
   - `MessageWebSocketServer` class with asyncio event loop in background thread
   - Thread-safe `broadcast()` method for pushing messages
   - Message history ring buffer (last 50 messages)
   - Client management with automatic cleanup
   - Singleton pattern with `get_websocket_server()`

2. **`src/utils/map_data_service.py`**
   - Added `enable_websocket` and `websocket_port` parameters to `MapServer`
   - Added `_start_websocket_server()` and `_stop_websocket_server()` methods
   - Added `_register_websocket_callback()` to connect MessageListener to WebSocket
   - Updated `start()`, `start_background()`, and `stop()` to manage WebSocket lifecycle

3. **`src/utils/map_http_handler.py`**
   - Added `/api/websocket/status` endpoint returning WebSocket URL and stats

4. **`web/node_map.html`**
   - Added WebSocket state tracking (`websocket`, `websocketConnected`, `websocketRetries`)
   - Added `initWebSocket()`, `connectWebSocket()`, `handleWebSocketMessage()` functions
   - Added `handleNewMessage()` for real-time message processing
   - Modified `loadReceivedMessages()` to skip polling when WebSocket connected
   - Added fallback polling (every 10s) when WebSocket disconnected
   - Added visual WebSocket connection indicator (green dot = connected)
   - Added CSS for WebSocket indicator

5. **`requirements.txt`**
   - Added `websockets>=12.0` dependency

### Architecture Summary

```
┌─────────────────────────────────────────────────────────────┐
│                    Web Browsers                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │  Browser 1  │  │  Browser 2  │  │  Browser 3  │         │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘         │
│         │ WS             │ WS             │ WS             │
└─────────┼────────────────┼────────────────┼─────────────────┘
          │                │                │
          ▼                ▼                ▼
   ┌─────────────────────────────────────────────────────────┐
   │              MeshForge Map Server                        │
   │                                                          │
   │  ┌─────────────────────┐  ┌─────────────────────┐       │
   │  │  HTTP Server :5000  │  │  WebSocket :5001    │       │
   │  │  (map, API, static) │  │  (real-time msgs)   │       │
   │  └─────────────────────┘  └──────────┬──────────┘       │
   │                                       │                  │
   │  ┌─────────────────────┐             │                  │
   │  │  MessageListener    │─────────────┘                  │
   │  │  (pubsub callback)  │   broadcast()                  │
   │  └──────────┬──────────┘                                │
   └─────────────┼────────────────────────────────────────────┘
                 │ SINGLE Connection
                 ▼
        ┌────────────────────┐
        │     meshtasticd    │
        │      (Port 4403)   │
        └────────────────────┘
```

### WebSocket Protocol

**Server → Client Messages:**
```json
// On connect - message history
{"type": "history", "messages": [...]}

// Connection confirmed
{"type": "connected", "message": "...", "timestamp": "..."}

// New message arrives
{"type": "message", "data": {"from_id": "!abc123", "content": "Hello", ...}}

// Heartbeat response
{"type": "pong", "timestamp": "..."}
```

**Client → Server Messages:**
```json
// Request message history
{"type": "get_history", "limit": 20}

// Heartbeat
{"type": "ping"}

// Get server stats
{"type": "get_stats"}
```

### Testing

1. Start MeshForge: `python3 src/utils/map_data_service.py`
2. Open browser: `http://localhost:5000/`
3. Enable "Messages" filter in map controls
4. Verify green WebSocket indicator appears
5. Send message from another Meshtastic node
6. Verify message appears in real-time (< 1 second)

## References

- [Meshtastic HTTP API](https://meshtastic.org/docs/development/device/http-api/)
- [meshtastic-websocket-proxy](https://github.com/liamcottle/meshtastic-websocket-proxy)
- [transport-http source](https://github.com/meshtastic/web/tree/main/packages/transport-http)
- [Issue #575](https://github.com/meshtastic/web/issues/575) - DMs not displaying

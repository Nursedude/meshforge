# Session Notes: Meshtastic API Proxy - Message Sync/ACK Fix

**Date**: 2026-02-09
**Branch**: `claude/fix-message-sync-ack-kN2Xg`
**Status**: Implemented - Ready for testing

## Problem Statement

The Meshtastic web client (port 9443) shows "waiting for delivery" and can't
see messages from other nodes. This is a persistent issue across multiple
apps, not just MeshForge.

### Root Cause (Deep Research)

The meshtasticd HTTP API (`/api/v1/fromradio`) has a **fundamental
single-client limitation**: each GET request **consumes** the packet from the
device buffer. When multiple clients (web browser, rnsd, MeshForge, etc.)
poll fromradio, they "fight" for packets. The first to poll gets the packet;
others never see it.

This means:
- ACK packets (ROUTING_APP) get consumed by rnsd or other clients
- Web client never receives the ACK → shows "waiting for delivery"
- Inbound messages get consumed by other pollers → web client can't see them

### References
- Meshtastic web client: https://github.com/meshtastic/web
- Transport architecture: `@meshtastic/core` Transport interface
- HTTP transport polls fromradio every 3 seconds
- Web Serial API (Chrome): direct USB serial, bypasses daemon entirely
- PR #998 on meshtastic/web: WebSocket transport (unmerged)
- liamcottle/meshtastic-websocket-proxy: validates the multiplexer approach

## Solution: MeshForge API Proxy

MeshForge now **owns the meshtasticd HTTP API** via a proxy that:

1. **Background poller**: MeshForge is the sole consumer of `/api/v1/fromradio`
2. **Per-client multiplexer**: Each browser tab gets its own packet buffer
3. **Transparent proxy**: Forwards `/api/v1/toradio`, `/json/*` to meshtasticd
4. **Web client serving**: Proxies the meshtastic web client at `/mesh/`

### Architecture

```
Browser(s)
  ↕ (HTTP, per-client sessions)
MeshForge :5000
  /              → NOC Map (node_map.html)
  /mesh/         → Meshtastic web client (proxied from meshtasticd)
  /api/v1/fromradio → multiplexed per-client packets
  /api/v1/toradio   → forwarded to meshtasticd
  /json/*            → proxied from meshtasticd
  ↕ (sole HTTP client)
meshtasticd :9443
```

### Key Design Decisions
- Per-client identification via session cookies + IP
- Ring buffer per client (500 packets max) prevents memory leaks
- Stale client pruning (5-minute timeout)
- Auto-detect meshtasticd port (9443, 443, 80)
- Fallback: direct HTTP client when proxy unavailable
- Packet inspection callbacks for WebSocket/monitoring integration

## Files Changed

| File | Status | Description |
|------|--------|-------------|
| `src/gateway/meshtastic_api_proxy.py` | NEW | Per-client fromradio multiplexer |
| `src/utils/map_http_handler.py` | Modified | Proxy endpoints for fromradio/toradio/json/mesh |
| `src/utils/map_data_service.py` | Modified | Start API proxy alongside map server |

## Testing

1. Start MeshForge with API proxy:
   ```bash
   sudo python3 src/utils/map_data_service.py
   ```
2. Open browser to `http://localhost:5000/mesh/` → Meshtastic web client
3. Open another tab to `http://localhost:5000/` → NOC Map
4. Send message from another node → both UIs should see it
5. Send message from web client → should show "delivered" (not "waiting")
6. Open multiple tabs → all receive messages independently

## Future Work

1. **WebSocket transport**: Build a binary WebSocket proxy (0x94 0xC3 framing)
   so the meshtastic web client can use WebSocket transport instead of HTTP
   polling. This would give sub-second latency and true bidirectional comms.

2. **Packet inspection**: Add protobuf parsing in packet callbacks to log
   message types, track ACKs, and feed into bridge health monitoring.

3. **meshtasticd web server config**: Consider disabling meshtasticd's built-in
   web server and having MeshForge serve everything (Webserver.Enabled: false
   in /etc/meshtasticd/config.yaml).

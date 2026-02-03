# Session Notes: Meshtastic Web UI Setup

**Date**: 2026-02-03
**Branch**: `claude/meshtastic-webui-setup-D4eTO`

## Summary

### Part 1: TUI Web Client Enhancements
Enhanced the Meshtastic Web Client integration in MeshForge TUI to provide:
1. Browser launch functionality (opens meshtasticd Web UI directly)
2. URL display for copying to other devices
3. SSL certificate acceptance guidance (critical for first-time users)
4. Better port connectivity checks

### Part 2: Web UI Inbound Message Fix (CRITICAL BUG FIX)
**Root Cause Found**: Outbound messages worked but inbound messages never appeared because:
1. `MessageListener` was never started when web server started
2. No API endpoint existed for received messages
3. Web UI only polled outbound queue (`/api/messages/queue`), not received messages

**Fix Implemented**:
1. `MapServer` now starts `MessageListener` automatically
2. Added `/api/messages/received` endpoint for inbound messages
3. Added `/api/messages/rx-status` endpoint for listener status
4. Updated web UI to poll and display received messages

## Changes Made

### Part 1: TUI Changes

#### 1. `src/launcher_tui/main.py`

**Modified `_open_web_client()` method** - Complete rewrite with:
- Port check for both localhost and network IP
- Menu with options: Open in Browser, Show URLs, SSL Help, Back
- Better error handling when web client not running

**Added new helper methods**:
- `_launch_web_client_browser(url)` - Opens browser with proper root/sudo handling
- `_show_web_client_urls(local_ip)` - Displays URLs for copying
- `_show_ssl_certificate_help(local_ip)` - Browser-specific SSL acceptance guidance

#### 2. `src/launcher_tui/meshtasticd_config_mixin.py`

**Modified `_show_web_client_info()`** - Now delegates to `_open_web_client()`
to avoid code duplication while maintaining fallback for robustness.

### Part 2: Web UI Inbound Message Fix

#### 3. `src/utils/map_data_service.py`

**Added MessageListener integration to MapServer**:
- New `enable_message_listener` parameter (default True)
- New `_start_message_listener()` method - starts listener on server start
- New `_stop_message_listener()` method - cleanup on shutdown
- Both `start()` and `start_background()` now start the listener

#### 4. `src/utils/map_http_handler.py`

**Added new API endpoints**:
- `GET /api/messages/received` - Returns inbound messages from SQLite database
  - Query params: `limit`, `network`, `since` (for incremental updates)
- `GET /api/messages/rx-status` - Returns MessageListener status

#### 5. `web/node_map.html`

**Updated message panel HTML**:
- Split into "Received (Inbox)" and "Outbound Queue" sections

**Added JavaScript functions**:
- `loadReceivedMessages()` - Polls `/api/messages/received` with incremental loading
- `renderReceivedMessages()` - Displays received messages with from, preview, time
- `renderOutboundQueue()` - Renamed from `renderMessageQueue()`

**Added CSS**:
- `.message-section-title` - Section headers
- `.received-msg` - Received message styling
- `.msg-from`, `.msg-preview`, `.msg-time` - Message elements
- `.message-received` animation - Flash when new messages arrive

## Message Flow Architecture (After Fix)

```
SENDING (works before and after):
  Web UI → POST /api/radio/message → meshtastic.send_message()

RECEIVING (NOW FIXED):
  meshtasticd → pubsub "meshtastic.receive"
             → MessageListener._on_receive()
             → messaging.store_incoming() → SQLite database
             → Web UI polls /api/messages/received
             → renderReceivedMessages() displays them
```

## Testing

```bash
# Syntax check all modified files
python3 -m py_compile src/utils/map_data_service.py
python3 -m py_compile src/utils/map_http_handler.py
python3 -m py_compile src/launcher_tui/main.py
python3 -m py_compile src/launcher_tui/meshtasticd_config_mixin.py

# Test imports
python3 -c "
import sys; sys.path.insert(0, 'src')
from utils.map_data_service import MapServer
from utils.map_http_handler import MapRequestHandler
from commands import messaging
from utils.message_listener import MessageListener
print('All imports OK')
"

# Live test (requires meshtasticd running)
python3 -m utils.map_data_service -p 5000
# Open http://localhost:5000/
# Send a message from another node
# Message should appear in "Received (Inbox)" section
```

## API Endpoints Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/messages/queue` | GET | Pending OUTBOUND messages |
| `/api/messages/received` | GET | RECEIVED inbound messages |
| `/api/messages/rx-status` | GET | MessageListener status |
| `/api/radio/message` | POST | Send message via radio |

## Related Files

- `src/utils/message_listener.py` - Handles pubsub message reception
- `src/commands/messaging.py` - Message storage/retrieval API
- `~/.config/meshforge/messages.db` - SQLite message database

---
**Session Status**: Complete - ready for commit and push

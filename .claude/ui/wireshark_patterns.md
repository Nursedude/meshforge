# Wireshark-Inspired UI Patterns for MeshForge

Research for improving MeshForge's interface based on [Wireshark's UI design](https://www.wireshark.org/docs/wsug_html_chunked/ChUseMainWindowSection.html).

---

## Wireshark's Three-Pane Layout

Wireshark uses a proven three-pane design for network analysis:

```
┌─────────────────────────────────────────────────────────────────────┐
│  [Menu Bar]  [Toolbar]  [Filter: ______________________] [Apply]   │
├─────────────────────────────────────────────────────────────────────┤
│  PACKET LIST (Top Pane)                                             │
│  ┌────┬────────┬───────────┬───────────┬──────────┬───────────────┐│
│  │ No │ Time   │ Source    │ Dest      │ Protocol │ Info          ││
│  ├────┼────────┼───────────┼───────────┼──────────┼───────────────┤│
│  │ 1  │ 0.000  │ 10.0.0.1  │ 10.0.0.2  │ TCP      │ SYN           ││
│  │ 2  │ 0.001  │ 10.0.0.2  │ 10.0.0.1  │ TCP      │ SYN-ACK       ││
│  │ 3  │ 0.002  │ 10.0.0.1  │ 10.0.0.2  │ TCP      │ ACK           ││
│  └────┴────────┴───────────┴───────────┴──────────┴───────────────┘│
├─────────────────────────────────────────────────────────────────────┤
│  PACKET DETAILS (Middle Pane - Tree View)                           │
│  ▶ Frame 1: 74 bytes on wire                                        │
│  ▶ Ethernet II, Src: 00:11:22:33:44:55, Dst: 66:77:88:99:aa:bb     │
│  ▼ Internet Protocol Version 4, Src: 10.0.0.1, Dst: 10.0.0.2       │
│      Version: 4                                                      │
│      Header Length: 20 bytes                                         │
│      Total Length: 60                                                │
│  ▶ Transmission Control Protocol, Src Port: 12345, Dst Port: 80    │
├─────────────────────────────────────────────────────────────────────┤
│  PACKET BYTES (Bottom Pane - Hex View)                              │
│  0000  66 77 88 99 aa bb 00 11 22 33 44 55 08 00 45 00   fw......".3DU..E.│
│  0010  00 3c 1a 2b 40 00 40 06 b1 c3 0a 00 00 01 0a 00   .<.+@.@.........│
│  0020  00 02 30 39 00 50 00 00 00 00 00 00 00 00 50 02   ..09.P........P.│
└─────────────────────────────────────────────────────────────────────┘
```

---

## Applying Wireshark Patterns to MeshForge

### 1. Message Monitor (Three-Pane Layout)

```
┌─────────────────────────────────────────────────────────────────────┐
│  MeshForge  │ Monitor │ Map │ Config │ Tools │    [Filter: ____]   │
├─────────────────────────────────────────────────────────────────────┤
│  MESSAGE LIST                                                        │
│  ┌────┬────────┬───────────┬───────────┬──────────┬───────────────┐│
│  │ #  │ Time   │ From      │ To        │ Type     │ Content       ││
│  ├────┼────────┼───────────┼───────────┼──────────┼───────────────┤│
│  │ 1  │ 14:32  │ !abc123   │ Broadcast │ TEXT     │ Hello mesh    ││
│  │ 2  │ 14:33  │ !def456   │ !abc123   │ POSITION │ 19.7°N 155°W  ││
│  │ 3  │ 14:35  │ RNS:a1b2  │ LXMF      │ MESSAGE  │ [encrypted]   ││
│  └────┴────────┴───────────┴───────────┴──────────┴───────────────┘│
├─────────────────────────────────────────────────────────────────────┤
│  MESSAGE DETAILS (Tree View)                                         │
│  ▶ Frame: Meshtastic LoRa Packet                                    │
│  ▼ Header                                                            │
│      From: !abc1234 (MyNode)                                         │
│      To: ^all (Broadcast)                                            │
│      Hop Limit: 3                                                    │
│      Want ACK: false                                                 │
│  ▼ Payload: TEXT_MESSAGE_APP                                        │
│      Message: "Hello mesh"                                           │
│      [Response Time: 0.234s]                                         │
├─────────────────────────────────────────────────────────────────────┤
│  RAW DATA (Protobuf/Hex)                                            │
│  0000  08 d2 09 10 ff ff ff ff 18 03 22 0b 48 65 6c 6c   ..........".Hell│
│  0010  6f 20 6d 65 73 68                                  o mesh          │
└─────────────────────────────────────────────────────────────────────┘
```

### 2. Node List with Details

```
┌─────────────────────────────────────────────────────────────────────┐
│  NODE LIST                                     [Filter: online____] │
│  ┌──────────┬───────────┬─────────┬────────┬───────┬──────────────┐│
│  │ ID       │ Name      │ Network │ Last   │ Hops  │ Status       ││
│  ├──────────┼───────────┼─────────┼────────┼───────┼──────────────┤│
│  │ !abc1234 │ MyNode    │ Mesh    │ Now    │ 0     │ ● Online     ││
│  │ !def5678 │ Relay1    │ Mesh    │ 2m     │ 1     │ ● Online     ││
│  │ RNS:a1b2 │ NomadNode │ RNS     │ 5m     │ 3     │ ● Online     ││
│  │ !ghi9012 │ BaseNode  │ Mesh    │ 2h     │ 2     │ ○ Stale      ││
│  └──────────┴───────────┴─────────┴────────┴───────┴──────────────┘│
├─────────────────────────────────────────────────────────────────────┤
│  NODE DETAILS                                                        │
│  ▼ Identity                                                          │
│      Node ID: !def5678                                               │
│      Short Name: RLY1                                                │
│      Long Name: Relay1                                               │
│      Hardware: HELTEC_V3                                             │
│  ▼ Position                                                          │
│      Latitude: 19.7297°N                                             │
│      Longitude: 155.0900°W                                           │
│      Altitude: 45m                                                   │
│      [Open in Map]                                                   │
│  ▼ Telemetry                                                         │
│      Battery: 85%                                                    │
│      Voltage: 4.05V                                                  │
│      Channel Util: 12.3%                                             │
│      Air Util TX: 2.1%                                               │
├─────────────────────────────────────────────────────────────────────┤
│  RAW NODE INFO (Protobuf)                                           │
│  { "num": 0xdef5678, "user": { "id": "!def5678", ... } }           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Key UI Patterns to Adopt

### 1. Filter Bar (Display Filters)

Wireshark's filter expressions are powerful and intuitive:

```
┌──────────────────────────────────────────────────────────────────┐
│  [Filter: from:!abc123 AND type:TEXT ] [Apply] [Clear] [Save ▼] │
└──────────────────────────────────────────────────────────────────┘
```

**MeshForge Filter Syntax:**
```
# Node filters
from:!abc123           # Messages from specific node
to:!def456             # Messages to specific node
node:MyNode            # Node by name

# Type filters
type:TEXT              # Text messages
type:POSITION          # Position updates
type:TELEMETRY         # Telemetry data

# Network filters
network:mesh           # Meshtastic only
network:rns            # RNS only
network:*              # Both networks

# Time filters
time:1h                # Last hour
time:today             # Today's messages

# Compound filters
from:!abc123 AND type:TEXT
network:rns OR type:POSITION
NOT type:TELEMETRY
```

### 2. Packet Coloring (Message Coloring)

```
┌─────────────────────────────────────────────────────────────────────┐
│  COLOR RULES                                                         │
├─────────────────────────────────────────────────────────────────────┤
│  ■ Green   │ TEXT messages from my node                             │
│  ■ Blue    │ Incoming TEXT messages                                 │
│  ■ Yellow  │ Position updates                                       │
│  ■ Cyan    │ Telemetry data                                         │
│  ■ Magenta │ RNS/LXMF messages                                      │
│  ■ Red     │ Errors/failures                                        │
│  ■ Gray    │ Routing/admin packets                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### 3. Tree View for Protocol Details

Collapsible tree structure for message details:

```
▶ Meshtastic Packet
    ▶ Header
    ▼ Payload: TEXT_MESSAGE_APP
        Message: "Hello mesh"
        Emoji: false
        Reply To: 0
    ▶ Metadata
        [Hop Count: 2]
        [SNR: -8.5 dB]
        [RSSI: -95 dBm]
```

### 4. Real-Time Updates

Like Wireshark's live capture:

```
┌─────────────────────────────────────────────────────────────────────┐
│  ● LIVE  │ 1,234 messages │ 12 nodes online │ Auto-scroll: ON     │
└─────────────────────────────────────────────────────────────────────┘
```

### 5. Statistics and Graphs

```
┌─────────────────────────────────────────────────────────────────────┐
│  STATISTICS                                                          │
├─────────────────────────────────────────────────────────────────────┤
│  Messages by Type      │  Traffic Over Time                         │
│  ┌──────────────────┐  │  ┌────────────────────────────────────┐   │
│  │ TEXT      ████ 45│  │  │     ▄▄                              │   │
│  │ POSITION  ██   23│  │  │   ▄▄██▄▄    ▄▄                      │   │
│  │ TELEMETRY █    12│  │  │ ▄▄██████▄▄▄▄██▄▄                    │   │
│  │ ADMIN          5│  │  │ ██████████████████▄▄▄▄              │   │
│  └──────────────────┘  │  └────────────────────────────────────┘   │
│                        │   00:00  06:00  12:00  18:00  24:00       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Priority

| Feature | Priority | Complexity | Impact |
|---------|----------|------------|--------|
| Three-pane layout | High | Medium | High - Core UX |
| Filter bar | High | Medium | High - Usability |
| Node tree view | Medium | Low | Medium - Details |
| Message coloring | Medium | Low | Medium - Readability |
| Real-time scroll | Medium | Low | Medium - Live ops |
| Hex view | Low | Low | Low - Debugging |
| Statistics | Low | Medium | Low - Analytics |

---

## GTK4 Implementation Notes

### Widgets to Use

| Wireshark Component | GTK4 Widget |
|---------------------|-------------|
| Packet List | `Gtk.ColumnView` or `Gtk.TreeView` |
| Packet Details | `Gtk.TreeView` with expanders |
| Hex View | `Gtk.TextView` with monospace font |
| Filter Bar | `Gtk.SearchEntry` with completion |
| Panes | `Gtk.Paned` (vertical) |
| Status Bar | `Gtk.Statusbar` or `Adw.StatusPage` |

### libadwaita Enhancements

- `Adw.ViewSwitcher` for tab navigation
- `Adw.Leaflet` for responsive layout on small screens
- `Adw.ToastOverlay` for notifications
- `Adw.ActionRow` for settings

---

## References

- [Wireshark Main Window](https://www.wireshark.org/docs/wsug_html_chunked/ChUseMainWindowSection.html)
- [Packet List Pane](https://www.wireshark.org/docs/wsug_html_chunked/ChUsePacketListPaneSection.html)
- [Packet Details Pane](https://www.wireshark.org/docs/wsug_html_chunked/ChUsePacketDetailsPaneSection.html)
- [Display Filters](https://www.wireshark.org/docs/wsug_html_chunked/ChWorkBuildDisplayFilterSection.html)
- [Wireshark Wiki - Qt Development](https://wiki.wireshark.org/Development/QtShark)

---

*Last updated: 2026-01-05*

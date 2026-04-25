# MeshForge Field Testing QA Plan

> **Purpose**: Structured QA protocol for features with unit tests but no real-world validation
> **Author**: WH6GXZ + Claude Code
> **Created**: 2026-03-03
> **Branch**: `main` (v0.5.7-beta) — MeshCore is an optional gateway handler on `main`; sister NOC `MeshAnchor` for MeshCore-primary work
> **Related**: `.claude/plans/noc_test_plan.md` (lab infrastructure), `README.md` (status table)

---

## Priority 1: Gateway Bridge (MQTT Mode)

The gateway bridge is MeshForge's core mission feature. It has 140+ unit tests
(`tests/test_rns_bridge.py`) but has never bridged a real Meshtastic ↔ RNS message.

### Prerequisites

- [ ] Pi with meshtasticd running (SPI HAT or USB radio)
- [ ] Pi with rnsd running (shared instance, `share_instance = Yes`)
- [ ] mosquitto MQTT broker running on same Pi (`sudo apt install mosquitto`)
- [ ] meshtasticd MQTT publishing enabled (`TUI → Mesh Networks → Gateway Config → MQTT Bridge Settings → Run Setup Guide`)
- [ ] Second device (phone app, another node, or NomadNet on another Pi) to send/receive

### Test Scenarios

#### GW-01: Bridge Startup
1. Launch TUI: `sudo python3 src/launcher_tui/main.py`
2. Navigate: `Mesh Networks → Gateway Config → Start Gateway`
3. **PASS**: Bridge starts, status bar shows gateway "running"
4. **FAIL**: Error dialog, crash, or "service not available" warning

#### GW-02: Meshtastic → RNS Message Delivery
1. Send a text message from Meshtastic app (phone or another node) on the bridge channel
2. Check NomadNet or LXMF client on RNS side for the message
3. **PASS**: Message appears on RNS side within 30 seconds, content intact
4. **FAIL**: Message lost, garbled, or delayed beyond 60 seconds

#### GW-03: RNS → Meshtastic Message Delivery
1. Send an LXMF message from NomadNet or MeshChat targeting the bridge
2. Check Meshtastic app for the bridged message
3. **PASS**: Message appears on Meshtastic side, attributed to bridge
4. **FAIL**: Message lost or not delivered

#### GW-04: Message Queue Persistence
1. While bridge is running, send 3 messages from Meshtastic side
2. Kill the bridge process (`Ctrl+C` or stop via TUI)
3. Restart the bridge
4. **PASS**: Queued messages delivered after restart (check SQLite: `~/.config/meshforge/message_queue.db`)
5. **FAIL**: Messages lost on restart

#### GW-05: Circuit Breaker
1. Start bridge with both services running
2. Stop rnsd: `sudo systemctl stop rnsd`
3. Send a Meshtastic message
4. **PASS**: Circuit breaker trips, message queued, no crash. Log shows WARNING about RNS unavailable.
5. Restart rnsd: `sudo systemctl start rnsd`
6. **PASS**: Circuit breaker recovers, queued message delivered
7. **FAIL**: Bridge crashes, hangs, or doesn't recover

#### GW-06: Zero Interference (Web Client)
1. Start bridge
2. Open meshtasticd web UI at `http://pi-address:9443`
3. Use web UI to view nodes, send a message
4. **PASS**: Web UI works perfectly while bridge is running (MQTT mode, no TCP contention)
5. **FAIL**: Web UI blocked, errors, or "connection in use" message

#### GW-07: Long-Running Stability (Soak Test)
1. Start bridge and leave running for 24+ hours
2. Periodically send messages in both directions
3. Monitor: `TUI → Dashboard` for health status
4. **PASS**: No memory leaks, no crashes, messages continue to flow
5. **FAIL**: OOM, crash, or message delivery stops

### Key Files
- `src/gateway/rns_bridge.py` — Main bridge orchestrator
- `src/gateway/mqtt_bridge_handler.py` — MQTT transport
- `src/gateway/message_queue.py` — SQLite persistence
- `src/gateway/circuit_breaker.py` — Failure isolation
- `src/gateway/bridge_health.py` — Health monitoring

---

## Priority 2: Coverage Maps

Coverage maps have been tested with synthetic data only. They need real GPS
positions from real nodes to validate rendering accuracy.

### Prerequisites

- [ ] MQTT subscriber collecting position data (at least 1 hour of collection)
- [ ] 3+ Meshtastic nodes with GPS reporting positions
- [ ] Browser available to view generated maps

### Test Scenarios

#### MAP-01: Static Coverage Map Generation
1. Ensure MQTT subscriber has collected position data
2. Navigate: `TUI → Maps & Viz → Coverage Map → Generate`
3. **PASS**: HTML file generated, opens in browser showing real node positions
4. **FAIL**: Empty map, wrong positions, or generation error

#### MAP-02: SNR-Based Link Quality
1. Generate coverage map with nodes that have varied SNR values
2. Verify link coloring: green (good SNR) → red (poor SNR)
3. **PASS**: Colors accurately reflect real SNR data from radio
4. **FAIL**: All links same color, or colors don't match actual signal quality

#### MAP-03: Offline Tile Caching
1. Generate a coverage map while online (tiles download)
2. Disconnect internet
3. Reload the map in browser
4. **PASS**: Map tiles load from cache, map renders correctly offline
5. **FAIL**: Blank tiles, "tile not found" errors

#### MAP-04: Live NOC Map
1. Start map server: `TUI → Maps & Viz → Start Map Server`
2. Open `http://localhost:5000` in browser
3. Wait for WebSocket updates (bridge must be running)
4. **PASS**: Nodes appear on map, positions update in real-time
5. **FAIL**: Empty map, no WebSocket connection, or stale data

#### MAP-05: Node Popup Details
1. On live NOC map, click a node marker
2. **PASS**: Popup shows battery, SNR, hardware, altitude from real data
3. **FAIL**: Missing fields, wrong data, or popup doesn't open

### Key Files
- `src/utils/coverage_map.py` — Folium generator
- `web/node_map.html` — Live NOC map frontend
- `src/monitoring/mqtt_subscriber.py` — Data collection

---

## Priority 3: MeshCore (Alpha Branch)

MeshCore integration has 602 unit tests (`test_meshcore_handler.py`) and 684
tri-bridge tests (`test_tribridge_integration.py`), all against mocked meshcore_py.
Real hardware testing requires a companion radio.

### Prerequisites

- [ ] On `main` (MeshCore handler ships on main as of split; alpha branch archived as tag `alpha-archived`)
- [ ] MeshCore companion radio (Heltec/T-Beam with MeshCore firmware)
- [ ] meshcore_py installed: `pip install meshcore` (requires Python 3.10+)
- [ ] Companion radio connected via USB (note device path, e.g., `/dev/ttyUSB1`)
- [ ] meshtasticd + rnsd running for tri-bridge tests

### Test Scenarios

#### MC-01: MeshCore Device Detection
1. Connect companion radio via USB
2. Navigate: `TUI → Mesh Networks → MeshCore → Detect Devices`
3. **PASS**: Device found and listed with correct port
4. **FAIL**: "No devices found" despite radio being connected

#### MC-02: MeshCore Connection
1. Configure device path: `TUI → Mesh Networks → MeshCore → Configure`
2. Enable MeshCore: `TUI → Mesh Networks → MeshCore → Enable`
3. **PASS**: Status shows "Connected", MeshCore handler running
4. **FAIL**: Connection error, timeout, or "device busy"

#### MC-03: MeshCore Node Discovery
1. With MeshCore connected, wait for node advertisements
2. Navigate: `TUI → Mesh Networks → MeshCore → View Nodes`
3. **PASS**: Nodes from MeshCore network listed
4. **FAIL**: Empty list despite known MeshCore nodes in range

#### MC-04: Message Send via MeshCore
1. Send a message from MeshForge TUI to MeshCore network
2. Verify receipt on another MeshCore device
3. **PASS**: Message delivered, content intact (within 160-byte limit)
4. **FAIL**: Message lost, truncated incorrectly, or send error

#### MC-05: Meshtastic → MeshCore Bridge
1. Start gateway bridge with MeshCore enabled
2. Send Meshtastic message from phone app
3. **PASS**: Message appears on MeshCore network with `[MC:pubkey]` prefix
4. **FAIL**: Message not bridged, or bridge crashes

#### MC-06: MeshCore → RNS Bridge
1. With tri-bridge running, send from MeshCore device
2. Check NomadNet or LXMF client
3. **PASS**: Message delivered to RNS side
4. **FAIL**: Message lost in routing

#### MC-07: RadioMode Switching
1. Navigate: `TUI → Mesh Networks → Radio Mode`
2. Switch from MESHTASTIC to DUAL
3. **PASS**: Both Meshtastic and MeshCore handlers active, bridge routes between all 3
4. **FAIL**: Error, handler crash, or one protocol stops working

#### MC-08: USB Disconnect Recovery
1. While MeshCore is connected, unplug the USB cable
2. Wait 10 seconds, then reconnect
3. **PASS**: Auto-reconnect, MeshCore handler resumes operation
4. **FAIL**: Permanent disconnect, requires manual restart

#### MC-09: Message Truncation
1. Send a message > 160 bytes from Meshtastic to MeshCore via bridge
2. **PASS**: Message truncated with `...` indicator, full message logged
3. **FAIL**: Crash, garbled message, or silent truncation without indicator

### Key Files (on `main`)
- `src/gateway/meshcore_handler.py` — Async handler
- `src/gateway/canonical_message.py` — Multi-protocol message format (shared with MeshAnchor)
- `src/gateway/message_routing.py` — 3-way routing classifier
- `src/gateway/meshcore_bridge_mixin.py` — Bridge processing
- `src/core/radio_mode.py` — RadioMode abstraction
- `src/core/meshcore_config.py` — Config manager

---

## Test Results Template

Use this template to record results for each test:

```
## Test: [ID] — [Name]
Date: YYYY-MM-DD
Branch: main
Hardware: [describe Pi model, radio, firmware version]

Result: PASS / FAIL / PARTIAL
Notes:
- [observations]
- [error messages if any]
- [performance notes]

Issues Filed: #[issue number] (if applicable)
```

---

## Execution Order

1. **Gateway (GW-01 through GW-06)** — Validate core mission first
2. **Maps (MAP-01 through MAP-03)** — Needs position data from GW testing
3. **Live NOC Map (MAP-04, MAP-05)** — Needs running bridge from GW testing
4. **MeshCore (MC-01 through MC-09)** — On `main`; needs companion radio
5. **Soak test (GW-07)** — Run last, requires 24+ hours

---

*Made with aloha for the mesh community — WH6GXZ*

# Configuration

Every knob, and where its file lives.

## Configuration

### MeshForge ecosystem `global.ini` (shared identity)

Before loading `daemon.yaml`, the NOC reads `~/.config/meshforge/global.ini`
as a fallback layer.  This file is the single source of truth for values
shared across the ecosystem (NOC, [meshforge-maps](https://github.com/Nursedude/meshforge-maps),
[meshing_around_meshforge](https://github.com/Nursedude/meshing_around_meshforge),
MeshAnchor) — set the MQTT broker, region preset, or operator identity
once and every sister app picks it up.

Layering: `dataclass defaults < deployment profile < global.ini < system daemon.yaml < user daemon.yaml < explicit --config`.
Per-app YAML always wins, so a NOC-only override is a one-liner in
`~/.config/meshforge/daemon.yaml`.

The canonical schema is documented at
[meshing_around_meshforge/docs/global_config.md](https://github.com/Nursedude/meshing_around_meshforge/blob/main/docs/global_config.md).
A minimal example:

```ini
[mqtt]
broker = mqtt.meshtastic.org
port = 1883

[region]
preset = hawaii
home_lat = 19.7
home_lon = -155.1
```

Missing or malformed file → no-op, never raises.

### meshtasticd

MeshForge writes hardware config overlays (never overwrites defaults):

```
/etc/meshtasticd/
├── config.yaml                    # Package default (DO NOT EDIT)
└── config.d/
    ├── lora-*.yaml                # Hardware config (SPI pins, module)
    └── meshforge-overrides.yaml   # Custom overrides
```

LoRa modem presets and frequency slots are applied via the meshtastic
CLI (`--set lora.modem_preset`, `--set lora.channel_num`), not config.d.

### Reticulum

RNS and LXMF are installed from MeshForge-maintained forks
([Nursedude/reticulum](https://github.com/Nursedude/reticulum),
[Nursedude/lxmf](https://github.com/Nursedude/lxmf)), pinned by tag + SHA in
`requirements/rns.txt`. The forks carry reliability fixes for rnsd RPC
fragility (bounded connects, RPC recv timeouts, bounded shutdown) found in
fleet operation; the wire format and crypto are unchanged, so they
interoperate fully with stock RNS networks.

Auto-deploys a working config from `templates/reticulum.conf`:
- AutoInterface (LAN discovery)
- Meshtastic Interface on `127.0.0.1:4403`
- RNode LoRa (optional, for dedicated RNS radio)

**Shared instance detection**: RNS uses abstract Unix domain sockets
(`@rns/default`) on Linux by default — not TCP/UDP port 37428. MeshForge
detects the shared instance via domain socket first, falling back to TCP
then UDP for non-standard configurations. This ensures accurate health
checks across status bar, diagnostics, repair wizard, and gateway pre-flight.

### Prometheus Metrics

MeshForge exports metrics for monitoring with Prometheus and Grafana:

```python
from utils.metrics_export import start_metrics_server

server = start_metrics_server(port=9090)
# Metrics at http://localhost:9090/metrics
```

**TUI Access**: `Tools → Historical Metrics → Prometheus Server → Start Server`

### Grafana Dashboards

Pre-built dashboards are available in `dashboards/`:

| Dashboard | Description |
|-----------|-------------|
| `meshforge-overview.json` | Health scores, service status, message queues |
| `meshforge-nodes.json` | Per-node SNR, RSSI, battery metrics |
| `meshforge-gateway.json` | Gateway connections, message flow |
| `meshforge-infinity.json` | JSON API via Grafana Infinity plugin (no Prometheus required) |
| `meshforge-influxdb.json` | Node trends, signal quality, message activity (InfluxDB) |

**Setup Requirements**:
1. Install Prometheus and Grafana separately
2. Start MeshForge metrics server (port 9090)
3. Add Prometheus scrape target for `localhost:9090`
4. Import dashboards via Grafana UI → Dashboards → Import

See `dashboards/README.md` and `docs/METRICS.md` for full setup instructions.

### Ports

| Port | Service | Owner | Notes |
|------|---------|-------|-------|
| 4403 | meshtasticd TCP API | meshtasticd | Single client limit |
| 1883 | mosquitto MQTT | mosquitto | Multi-consumer (optional) |
| 5000 | MeshForge Map Server | **MeshForge** | Live NOC map + REST API (enumerated in [API Reference](#api-reference)) |
| 5001 | MeshForge WebSocket | **MeshForge** | Real-time message broadcast |
| 8081 | MeshForge Config API | **MeshForge** | RESTful config management |
| 9090 | Prometheus metrics | **MeshForge** | Prometheus + Grafana JSON API |
| 9443 | meshtasticd Web UI | meshtasticd | Protobuf + JSON endpoints |

### API Reference

MeshForge serves REST APIs across three local HTTP servers — the map server,
Prometheus metrics, and the config API (each enumerated below). All APIs are
local-only (LAN/localhost) with CORS enabled for browser access.

#### Map Server (port 5000)

| Method | Endpoint | Returns |
|--------|----------|---------|
| GET | `/api/nodes/geojson` | Unified GeoJSON from all sources (Meshtastic, MQTT, RNS) |
| GET | `/api/nodes/history` | 24-hour node statistics |
| GET | `/api/nodes/trajectory/<id>` | Node movement trail (GeoJSON LineString) |
| GET | `/api/network/topology` | D3.js force-directed graph data |
| GET | `/api/coverage/<lat>/<lon>/<h>` | Terrain-aware RF coverage prediction |
| GET | `/api/los/<lat1>/<lon1>/<lat2>/<lon2>` | Line-of-sight + Fresnel zone analysis |
| GET | `/api/radio/info` | Radio device info (wraps meshtasticd) |
| GET | `/api/radio/nodes` | Nodes from connected radio |
| GET | `/api/radio/channels` | Channel list from radio |
| GET | `/api/radio/status` | Radio connection state |
| POST | `/api/radio/message` | Send message via radio |
| GET | `/api/messages/queue` | Outbound message queue |
| GET | `/api/messages/received` | Received messages |
| GET | `/api/status` | Server health + radio status |

#### Prometheus Metrics (port 9090)

| Method | Endpoint | Returns |
|--------|----------|---------|
| GET | `/metrics` | Prometheus exposition format (~45 metric families) |
| GET | `/api/v1/query` | PromQL query (Grafana compatible) |
| GET | `/api/v1/query_range` | Time-series range query |
| GET | `/api/json/nodes` | Node metrics as JSON |
| GET | `/api/json/status` | System status JSON |

#### Config API (port 8081)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/config[/<path>]` | Read config value(s) |
| PUT | `/config/<path>` | Set config value (validated) |
| DELETE | `/config/<path>` | Reset to default |
| POST | `/config/_reset` | Factory reset all config |
| GET | `/config/_audit` | Change audit log |

#### Protobuf Transport (via meshtasticd port 9443)

MeshForge's `MeshtasticProtobufClient` communicates with meshtasticd's
protobuf endpoints for full device control without consuming the TCP
connection (port 4403):

| Operation | Protocol | Description |
|-----------|----------|-------------|
| Config read/write | AdminMessage | All 8 device + 13 module config sections |
| Channel management | AdminMessage | Get/set channels 0-7 |
| Owner management | AdminMessage | Get/set device name |
| Neighbor info | NEIGHBORINFO_APP | Parse neighbor tables from mesh broadcasts |
| Device metadata | AdminMessage | Firmware version, capabilities, hardware model |
| Traceroute | TRACEROUTE_APP | Multi-hop route discovery with SNR |
| Position request | POSITION_APP | Request GPS position from remote nodes |
| Event polling | FromRadio stream | Background thread dispatches events via callbacks |

---


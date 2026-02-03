# MeshForge

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
</p>

<p align="center">
  <strong>Turnkey Mesh Network Operations Center</strong><br>
  <em>Meshtastic + Reticulum + AREDN — One Box, One Interface</em>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshforge"><img src="https://img.shields.io/badge/version-0.5.0--beta-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.9+-yellow.svg" alt="Python"></a>
  <a href="https://github.com/Nursedude/meshforge/actions"><img src="https://img.shields.io/badge/tests-2963%20functions-brightgreen.svg" alt="Tests"></a>
</p>

<p align="center">
  <a href="https://nursedude.substack.com">Development Blog</a> |
  <a href="https://github.com/Nursedude/meshforge/issues">Report Issues</a> |
  <a href="#contributing">Contribute</a>
</p>

---

## What is MeshForge?

**MeshForge turns a Raspberry Pi into a mesh network operations center.**

Plug in a LoRa radio, run the installer, and you get:
- A **gateway** bridging Meshtastic and Reticulum networks
- **Live NOC maps** showing Meshtastic AND RNS nodes on one map
- **Coverage maps** with SNR-based link quality
- **RF engineering tools** for site planning
- **AI diagnostics** that work offline

### The Vision

Modern mesh networks are fragmented. Meshtastic nodes can't talk to Reticulum nodes. AREDN operates on a different layer entirely. Each ecosystem has its own tools, its own interfaces, its own learning curve.

**MeshForge unifies them.**

One interface to monitor Meshtastic, Reticulum, and AREDN. One gateway to bridge messages between incompatible meshes. One toolkit for RF planning, diagnostics, and field operations. All running on a $35 Raspberry Pi that you can SSH into from anywhere.

This is the first open-source tool to bridge Meshtastic (LoRa mesh) with Reticulum (encrypted transport layer). No cloud dependencies. No subscriptions. Just a box that makes mesh networks work together.

```bash
sudo python3 src/launcher_tui/main.py
```

**Built for:** HAM operators, emergency comms teams, off-grid builders, preppers, and mesh enthusiasts who want professional-grade network visibility without the complexity.

---

## Quick Start

```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
sudo bash scripts/install_noc.sh    # Full install
```

Or if you already have meshtasticd:
```bash
sudo python3 src/launcher_tui/main.py
```

RF tools only (no sudo, no radio):
```bash
python3 src/standalone.py
```

---

## Upgrading MeshForge

### Before You Upgrade

**1. Check your current version:**
```bash
python3 -c "from src.__version__ import __version__; print(__version__)"
```

**2. Backup your configuration (recommended):**
```bash
# Backup meshtasticd configs
sudo cp -r /etc/meshtasticd/config.d ~/meshforge-backup-configs/

# Backup Reticulum config
cp -r ~/.reticulum ~/meshforge-backup-rns/

# Backup MeshForge settings
cp -r ~/.config/meshforge ~/meshforge-backup-settings/ 2>/dev/null || true
```

### Standard Upgrade (Git Pull)

For installations cloned from GitHub:

```bash
cd /path/to/meshforge    # Usually /opt/meshforge or ~/meshforge

# Check for local changes
git status

# Pull latest changes
sudo git pull origin main

# If you have local modifications, stash them first:
# git stash
# sudo git pull origin main
# git stash pop
```

### Switch Branches

The `main` and `alpha` branches are now synchronized. Both contain all features.

```bash
# Check current branch
git branch

# Switch if needed (both are equivalent)
git checkout main
sudo git pull origin main
```

### Fresh Install Upgrade

If upgrading from a very old version or encountering issues:

```bash
# Backup existing installation
sudo mv /opt/meshforge /opt/meshforge.old

# Fresh clone
sudo git clone https://github.com/Nursedude/meshforge.git /opt/meshforge
cd /opt/meshforge

# Re-run installer if dependencies changed
sudo bash scripts/install_noc.sh

# Restore custom configs if needed
sudo cp ~/meshforge-backup-configs/* /etc/meshtasticd/config.d/
```

### Post-Upgrade Verification

After upgrading, verify the installation:

```bash
# Check new version
python3 -c "from src.__version__ import __version__; print(__version__)"

# Verify TUI launches
sudo python3 src/launcher_tui/main.py

# Check services are running
systemctl status meshtasticd
systemctl status rnsd
```

### Troubleshooting Upgrades

| Issue | Solution |
|-------|----------|
| `Permission denied` | Use `sudo git pull` |
| `Local changes would be overwritten` | `git stash` before pull, `git stash pop` after |
| Python import errors | Re-run `sudo bash scripts/install_noc.sh` |
| Service won't start | Check logs: `journalctl -u meshtasticd -n 50` |
| Config file conflicts | Restore from backup or regenerate via TUI |
| `meshtastic` module errors | See "Python Library Conflicts" below |

#### Python Library Conflicts

On some systems (especially Raspberry Pi OS with externally-managed Python), the `meshtastic` library may fail to install due to version conflicts. If you see errors like "externally-managed-environment" or module import failures:

```bash
# Force reinstall meshtastic (use with caution)
pip install meshtastic --break-system-packages --ignore-installed

# Alternative: use a virtual environment
python3 -m venv ~/.meshforge-venv
source ~/.meshforge-venv/bin/activate
pip install meshtastic
```

Note: The `--break-system-packages` flag bypasses PEP 668 protections. Only use this if you understand the implications for your system Python environment.

### Version History

See the full changelog in `src/__version__.py` or run:
```bash
python3 -c "from src.__version__ import show_version_history; show_version_history()"
```

---

## What Works (v0.5.0-beta)

| Category | Capabilities | Status |
|----------|-------------|--------|
| **Radio Management** | Install/configure meshtasticd, LoRa presets, channels, SPI/USB auto-detect | Stable |
| **TUI Interface** | Installer, service control, device config wizard (name, region, TX power, MQTT), gateway config, diagnostics | Stable |
| **Multi-Mesh Gateway** | Meshtastic ↔ RNS bridge, persistent message queue (SQLite), WebSocket broadcast | Stable |
| **MQTT Architecture** | Local mosquitto broker, multi-consumer support, setup wizard | Stable |
| **Traffic Inspector** | Wireshark-grade packet visibility, protocol dissection, multi-hop path tracing | Stable |
| **NomadNet/RNS** | Config editor, interface templates, rnstatus/rnpath, identity management | Stable |
| **Network Monitoring** | MQTT node tracking, live logs, port inspection, service health | Stable |
| **Coverage Maps** | Interactive Folium maps, SNR-based link quality, offline tile caching | Stable |
| **Live NOC Map** | Browser view with WebSocket updates, node markers, signal heatmap | Beta |
| **RF Engineering** | Link budget, Fresnel zone, path loss, site planning, space weather | Stable |
| **AI Diagnostics** | Offline knowledge base (20+ topics), rule-based troubleshooting | Stable |
| **AI PRO Mode** | Claude API integration, log analysis, predictive diagnostics | Stable (requires API key) |
| **Config API** | RESTful configuration management with NGINX reliability patterns | Stable |
| **AREDN** | Node discovery, link quality, service enumeration | Stable |
| **Prometheus Metrics** | HTTP endpoint on port 9090, metrics exporter | Stable |
| **Grafana Dashboards** | Pre-built JSON dashboards, manual import required | Dashboards Ready |
| **uConsole AIO V2** | Hardware detection, GPIO power control, meshtasticd auto-config | Code Ready (hardware Q2 2026) |

### Roadmap

| Feature | Target | Status |
|---------|--------|--------|
| Historical playback (Live Map Phase 6) | Q2 2026 | Planned |
| Packet decode (protobuf + RNS frames) | Q2 2026 | Planned |
| SDR spectrum analysis (RTL-SDR) | Q2 2026 | Planned |
| GPS tracking + GPX export | Q2 2026 | Planned |
| NanoVNA antenna integration | Q2 2026 | Alpha |
| Firmware flashing | Q3 2026 | Alpha (high risk) |

### Known Limitations

| Feature | Limitation | Workaround |
|---------|-----------|------------|
| **Live NOC Map** | Node trails require historical data | Enable MQTT subscriber for data collection |
| **Grafana** | Dashboards require manual import | See `dashboards/README.md` for instructions |
| **TCP:4403** | Only one client can connect | Use MQTT path for multi-consumer scenarios |
| **WebSocket** | Requires Gateway Bridge or MQTT bridge | Start one of the bridges first |

*Goal: Complete network operations visibility with historical analysis.*

---

## Architecture

```mermaid
graph TB
    subgraph User Interfaces
        TUI[Terminal UI<br>SSH-friendly, raspi-config style]
        BROWSER[Browser Maps<br>Live Leaflet.js NOC view]
        CLI[Standalone CLI<br>Zero-dependency RF tools]
    end

    subgraph MeshForge Core
        LAUNCHER[Launcher<br>Auto-detect display]
        GATEWAY[Gateway Bridge<br>Message routing + SQLite queue]
        MONITOR[MQTT Subscriber<br>Nodeless node tracking]
        TRAFFIC[Traffic Inspector<br>Packet capture + path tracing]
        MAPS[Coverage Maps<br>Folium + offline tiles]
        RF[RF Engine<br>Link budget, Fresnel, path loss]
        DIAG[Diagnostics<br>Rule engine + knowledge base]
        AI[AI Assistant<br>Standalone + PRO modes]
    end

    subgraph External Services
        MESHTASTICD[meshtasticd<br>LoRa radio daemon]
        RNSD[rnsd<br>Reticulum transport]
        AREDN_NET[AREDN<br>IP mesh network]
        MQTT[MQTT Broker<br>Node telemetry]
        NOAA[NOAA SWPC<br>Space weather]
    end

    subgraph Hardware
        SPI[SPI HAT<br>Meshtoad, MeshAdv]
        USB[USB Radio<br>Heltec, T-Beam, RAK]
        SDR[RTL-SDR<br>Spectrum analysis]
        UCONSOLE[uConsole AIO V2<br>LoRa+SDR+GPS all-in-one]
    end

    TUI --> LAUNCHER
    TUI --> BROWSER
    LAUNCHER --> GATEWAY
    LAUNCHER --> MONITOR
    LAUNCHER --> MAPS
    LAUNCHER --> RF
    LAUNCHER --> DIAG
    DIAG --> AI

    GATEWAY --> MESHTASTICD
    GATEWAY --> RNSD
    MONITOR --> MQTT
    TRAFFIC --> GATEWAY
    TRAFFIC --> MONITOR
    MAPS --> MONITOR
    RF --> NOAA

    MESHTASTICD --> SPI
    MESHTASTICD --> USB
    MESHTASTICD --> UCONSOLE
    SDR --> UCONSOLE

    style TUI fill:#2d5016,color:#fff
    style BROWSER fill:#2d5016,color:#fff
    style CLI fill:#2d5016,color:#fff
    style GATEWAY fill:#1a3a5c,color:#fff
    style TRAFFIC fill:#3a1a5c,color:#fff
    style AI fill:#5c1a3a,color:#fff
    style UCONSOLE fill:#5c4a1a,color:#fff
```

### Data Flow: Multi-Mesh Bridge

```mermaid
sequenceDiagram
    participant M as Meshtastic Node
    participant D as meshtasticd
    participant G as MeshForge Gateway
    participant R as rnsd (Reticulum)
    participant N as RNS Node

    M->>D: LoRa packet (protobuf)
    D->>G: TCP:4403 (mesh packet)
    G->>G: Classify, queue (SQLite)
    G->>R: LXMF message
    R->>N: RNS transport

    N->>R: RNS reply
    R->>G: LXMF delivery
    G->>D: TCP:4403 (mesh packet)
    D->>M: LoRa broadcast
```

### MQTT Multi-Consumer Architecture

MeshForge supports dual data paths from meshtasticd:

```
meshtasticd
    ├── TCP:4403 → Gateway Bridge → RNS transport → WebSocket:5001
    │              (exclusive - one client)
    │
    └── MQTT → mosquitto:1883 → MQTT Subscriber (MeshForge)
                              → meshing-around
                              → Grafana/InfluxDB
                              → other consumers (unlimited)
```

**Key insight**: TCP:4403 allows only one client, but MQTT supports unlimited subscribers.

**Setup via TUI**:
- MQTT Setup Wizard: `Configuration → Service Config → MQTT Setup`
- MQTT Monitor: `Mesh Networks → MQTT Monitor → Configure → Use Local Broker`
- WebSocket Bridge: `MQTT Monitor → WebSocket Bridge` (for web UI without Gateway Bridge)

### Design Principles

- **TUI is a dispatcher** — selects what to run, not how to run it
- **Services run independently** — MeshForge connects, never embeds
- **Standard Linux tools** — `systemctl`, `journalctl`, `meshtastic`, `rnstatus`
- **Config overlays** — writes to `config.d/`, never overwrites defaults
- **Graceful degradation** — missing dependencies disable features, don't crash

---

## AI Intelligence

MeshForge includes two tiers of AI-powered network diagnostics:

### Standalone Mode (No Internet Required)
- 20+ topic knowledge base covering mesh networking fundamentals
- Rule-based diagnostic engine with pattern matching
- Structured troubleshooting guides for common issues
- Confidence scoring on diagnoses
- Works completely offline — ideal for field deployment

### PRO Mode (Claude API)
- Natural language troubleshooting ("Why is my node offline?")
- Log file analysis with suggested actions
- Context-aware responses (knows your network topology)
- Predictive issue detection
- Expertise-level adaptation (novice → expert)
- Falls back to Standalone when API unavailable

```python
from utils.claude_assistant import ClaudeAssistant

assistant = ClaudeAssistant()  # Auto-detects mode
response = assistant.ask("Node !abc123 has -15dB SNR, is that okay?")
print(response.answer)
print(response.suggested_actions)
```

---

## uConsole: All-In-One Field Unit

MeshForge has first-class support for the [HackerGadgets uConsole AIO V2](https://hackergadgets.com/products/uconsole-aio-v2) — a portable mesh operations terminal:

| Component | Capability |
|-----------|-----------|
| **SX1262 LoRa** | 860-960MHz, 22dBm, native Meshtastic via SPI |
| **RTL-SDR** | RTL2832U + R860, 100KHz-1.74GHz spectrum |
| **GPS/GNSS** | Multi-constellation (GPS/BDS/GLONASS) |
| **RTC** | PCF85063A with battery backup |
| **Ethernet** | RJ45 Gigabit (wired AREDN backhaul) |

Auto-detection, GPIO power control, and meshtasticd config generation are implemented. Hardware arrives Q2 2026.

---

## Hardware

**Minimum:** Raspberry Pi 3B+ or Pi Zero 2W + any Meshtastic radio

| Component | Options |
|-----------|---------|
| **Computer** | Raspberry Pi 4/5 (recommended), Pi 3B+, Pi Zero 2W |
| **OS** | Raspberry Pi OS Bookworm 64-bit, Debian 12+, Ubuntu 22.04+ |
| **Radio (SPI)** | Meshtoad, MeshAdv-Pi-Hat, Waveshare SX1262 |
| **Radio (USB)** | Heltec V3, T-Beam, RAK4631 |

**Cost:** ~$90 (Pi 4 + SPI HAT)

---

## Coverage Maps

Interactive network visualization powered by Folium and Leaflet.js:

### Static Coverage Maps (Stable)

- **Node markers** with status, battery, RSSI, hardware info
- **SNR-based link coloring** — green (excellent) → red (marginal)
- **Coverage radius estimation** based on LoRa preset
- **Offline tile caching** — works without internet in the field
- **Multiple tile layers** — OpenStreetMap, Terrain, Satellite
- **Heatmap generation** — node density visualization
- **GeoJSON import/export** — interoperate with other tools

```python
from utils.coverage_map import CoverageMapGenerator

gen = CoverageMapGenerator(offline=True)
gen.add_nodes_from_geojson(node_data)
gen.generate("field_coverage.html")  # Opens in any browser
```

### Live NOC Map (Beta)

Real-time browser-based network operations view at `http://localhost:8080`:

**Working Features**:
- **WebSocket updates** — real-time node position refresh (requires bridge running)
- **Node markers** — color-coded by status (online/stale/offline)
- **Signal heatmap** — toggle SNR-based heat visualization
- **Node popup details** — battery, SNR, hardware, altitude
- **Node list** — click to focus map on node

**In Development**:
- **Node trails** — requires historical data collection (enable MQTT subscriber)
- **Network topology** — D3.js force-directed graph view
- **Alert system** — visual notifications for node events

**Access**:
```bash
# Via TUI: Maps → Start Map Server
# Or directly:
sudo python3 src/utils/map_data_service.py
# Open http://localhost:8080 in browser
```

**Data Sources**:
- Gateway Bridge → WebSocket:5001 (real-time)
- MQTT Subscriber → mosquitto:1883 (multi-consumer)
- MQTT → WebSocket Bridge (connects MQTT to web UI)

---

## Project Structure

```
src/
├── launcher_tui/          # Terminal UI (primary interface)
│   ├── main.py            # NOC dispatcher + menus
│   ├── backend.py         # whiptail/dialog abstraction
│   └── *_mixin.py         # Feature modules (RF, channels, AI, system)
├── gateway/               # Multi-mesh bridge
│   ├── rns_bridge.py      # Meshtastic ↔ RNS transport
│   ├── message_queue.py   # Persistent SQLite queue
│   └── node_tracker.py    # Unified node discovery
├── monitoring/            # Network monitoring
│   ├── mqtt_subscriber.py # Nodeless MQTT node tracking
│   ├── traffic_inspector.py # Wireshark-grade packet capture
│   └── path_visualizer.py # Multi-hop path tracing
├── utils/                 # Core utilities
│   ├── rf.py              # RF calculations (well-tested)
│   ├── coverage_map.py    # Folium map generator + tile cache
│   ├── config_api.py      # RESTful configuration API
│   ├── diagnostic_engine.py # Rule-based AI diagnostics
│   ├── diagnostic_rules.py  # Diagnostic rule definitions
│   ├── claude_assistant.py  # AI assistant (Standalone + PRO)
│   ├── knowledge_base.py   # Core knowledge base class
│   ├── knowledge_content.py # 20+ mesh networking topics
│   ├── shared_health_state.py # Cross-component health tracking
│   ├── metrics_export.py   # Prometheus/JSON metrics export
│   ├── uconsole.py        # uConsole AIO V2 hardware profile
│   ├── aredn.py           # AREDN mesh client
│   └── paths.py           # Sudo-safe path resolution
├── standalone.py          # Zero-dependency RF tools
└── __version__.py         # Version tracking

dashboards/                # Grafana monitoring dashboards
├── meshforge-overview.json  # Health, services, queues
├── meshforge-nodes.json     # Per-node RF metrics
└── meshforge-gateway.json   # Gateway bridge status

templates/
└── gateway-pair/          # Multi-preset bridging templates
    ├── node-a.yaml        # First gateway node config
    └── node-b.yaml        # Second gateway node config
```

---

## Configuration

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

Auto-deploys a working config from `templates/reticulum.conf`:
- AutoInterface (LAN discovery)
- Meshtastic Interface on `127.0.0.1:4403`
- RNode LoRa (optional, for dedicated RNS radio)

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

**Setup Requirements**:
1. Install Prometheus and Grafana separately
2. Start MeshForge metrics server (port 9090)
3. Add Prometheus scrape target for `localhost:9090`
4. Import dashboards via Grafana UI → Dashboards → Import

See `dashboards/README.md` and `docs/METRICS.md` for full setup instructions.

### Ports

| Port | Service | Notes |
|------|---------|-------|
| 4403 | meshtasticd TCP API | Single client limit |
| 1883 | mosquitto MQTT | Multi-consumer (optional) |
| 5001 | MeshForge WebSocket | Real-time messages |
| 8080 | MeshForge Web UI | Maps, node browser |
| 9090 | Prometheus metrics | Optional |
| 9443 | meshtasticd Web UI | Official Meshtastic UI |

---

## Contributing

```bash
python3 -m pytest tests/ -v      # Run tests
python3 scripts/lint.py --all    # Security linter
```

**Code rules:** No `shell=True`, no bare `except:`, use `get_real_user_home()` not `Path.home()`.

See [CLAUDE.md](CLAUDE.md) for details.

---

## Development Branches

MeshForge uses a unified development model:

| Branch | Purpose | Status |
|--------|---------|--------|
| `main` | Production-ready, all features | **Stable** |
| `alpha` | Synchronized with main | Unified |

All features are now in `main`. The `alpha` branch tracks `main` for users who prefer the alpha naming convention.

```bash
# Install (main branch)
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
sudo bash scripts/install_noc.sh
```

### Quick Update

```bash
cd /opt/meshforge && sudo git pull origin main
```

See [Upgrading MeshForge](#upgrading-meshforge) for complete instructions including backup and troubleshooting.

### Gateway Configurations

| Configuration | Setup | Status |
|---------------|-------|--------|
| **moc1** | USB LongFast ↔ Short Turbo | Stable |
| **moc2** | HAT Short Turbo ↔ LongFast (two-radio) | Stable |
| **moc3** | HAT LongFast ↔ TBD | Planned |
| **VolcanoAI** | USB LongFast ↔ RNS (desktop) | Stable |

Gateway templates available in `templates/gateway-pair/` for multi-preset bridging.

---

## Resources

| Resource | Link | Relation |
|----------|------|----------|
| Development Blog | [nursedude.substack.com](https://nursedude.substack.com) | Project updates |
| Meshtastic Docs | [meshtastic.org/docs](https://meshtastic.org/docs/) | Primary radio network |
| Reticulum Network | [reticulum.network](https://reticulum.network/) | Bridge target (encrypted transport) |
| AREDN Mesh | [arednmesh.org](https://www.arednmesh.org/) | Monitoring integration |
| RTL-SDR | [rtl-sdr.com](https://www.rtl-sdr.com/) | Spectrum analysis (planned) |
| uConsole AIO V2 | [hackergadgets.com](https://hackergadgets.com/products/uconsole-aio-v2) | Field hardware (Q2 2026) |
| MeshCore | [meshcore.co](https://meshcore.co/) | Future research |

---

## License

GPL-3.0 — See [LICENSE](LICENSE)

---

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="32" height="32"/><br>
  <strong>MeshForge</strong><br>
  <em>Made with aloha for the mesh community</em><br>
  WH6GXZ | Hawaii
</p>

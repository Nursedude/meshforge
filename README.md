# MeshForge

**LoRa Mesh Network Development & Operations Suite**

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
  <br/>
  <strong>Build. Test. Deploy. Bridge. Monitor.</strong>
</p>

[![Version](https://img.shields.io/badge/version-0.4.5--beta-blue.svg)](https://github.com/Nursedude/meshforge)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-yellow.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%20%7C%20Linux-orange.svg)](https://www.raspberrypi.org/)
[![Tests](https://img.shields.io/badge/tests-195%20passing-brightgreen.svg)](tests/)

**The first open-source tool to bridge Meshtastic and Reticulum (RNS) mesh networks.**

```
+-----------------------------------------------------------------------+
|  MESHTASTIC  <---->  MESHFORGE  <---->  RETICULUM (RNS)               |
|   (LoRa)              (Bridge)           (Cryptographic Mesh)         |
|                                                                       |
|  * 915 MHz LoRa        * Unified Map      * Multi-transport           |
|  * Simple setup        * Gateway Config   * E2E encryption            |
|  * Wide adoption       * RF Tools         * 64+ hop routing           |
+-----------------------------------------------------------------------+
```

MeshForge is a **Network Operations Center (NOC)** for heterogeneous off-grid mesh networks. It unifies two powerful but incompatible mesh ecosystems—**Meshtastic** and **Reticulum**—into a single manageable system.

### What Makes It Different?

| Problem | MeshForge Solution |
|---------|-------------------|
| Meshtastic and RNS can't talk to each other | **Gateway bridge** routes messages between networks |
| Managing two separate node lists | **Unified map** shows all nodes on one interactive display |
| Complex config files scattered everywhere | **GUI editors** for meshtasticd, RNS interfaces, and gateway |
| "Will my link work?" guesswork | **RF tools** calculate LOS, Fresnel zones, path loss |
| Setup troubleshooting is painful | **Diagnostic wizard** identifies and fixes common issues |

### Who Is It For?

- **HAM Radio Operators** - Reliable emergency comms with cryptographic security
- **Network Operators** - Manage city-scale mesh infrastructure
- **RF Engineers** - Analyze propagation and plan deployments
- **Researchers** - Deploy sensor networks in remote areas
- **Developers** - Build apps on unified Meshtastic + RNS mesh

> **Note**: Formerly "Meshtasticd Interactive Installer". The old repo is deprecated.

---

## Table of Contents

- [What is MeshForge?](#what-is-meshforge)
- [Upgrade Path](#upgrade-path)
- [Support Levels](#support-levels)
- [RNS-Meshtastic Gateway](#rns-meshtastic-gateway)
- [AREDN Integration](#aredn-integration)
- [MeshForge University](#meshforge-university)
- [Quick Start](#quick-start)
- [Interfaces](#interfaces)
- [Features](#features)
- [Frequency Slot Calculator](#frequency-slot-calculator)
- [RF Engineering Tools](#rf-engineering-tools)
- [Plugin System](#plugin-system)
- [Simulation Mode](#simulation-mode)
- [Supported Hardware](#supported-hardware)
- [Installation](#installation)
- [Project Structure](#project-structure)
- [Version History](#version-history)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## What is MeshForge?

MeshForge follows the **Build → Test → Deploy → Monitor** lifecycle:

| Phase | What You Can Do |
|-------|-----------------|
| **BUILD** | Install meshtasticd, configure RNS interfaces, set up gateway bridges |
| **TEST** | RF line-of-sight analysis, frequency slot calculation, diagnostic wizard |
| **DEPLOY** | Activate configs, manage systemd services, enable boot persistence |
| **BRIDGE** | Route messages between Meshtastic LoRa and RNS cryptographic networks |
| **MONITOR** | Unified node map, real-time telemetry, message routing status |

### Technical Benefits

| Feature | Benefit |
|---------|---------|
| **Bidirectional Gateway** | Meshtastic nodes talk to NomadNet, Sideband, LXMF apps |
| **Position Sharing** | GPS coordinates flow between both networks |
| **End-to-End Encryption** | Reticulum's cryptographic identity layer secures messages |
| **Multi-Transport** | RNS works over LoRa, TCP, UDP, I2P, serial—simultaneously |
| **Extensible Plugins** | Add MeshCore, MQTT, or custom integrations |

---

## Upgrade Path

### Upgrading MeshForge

```bash
# Navigate to your MeshForge directory
cd meshforge

# Pull latest changes
git fetch origin
git pull origin main

# Check for dependency updates
pip3 install --upgrade rich textual flask meshtastic --break-system-packages

# Verify the upgrade
python3 -c "from src.__version__ import __version__; print(f'MeshForge v{__version__}')"

# Restart if running as service
sudo systemctl restart meshforge  # If you have it set up as a service
```

### Version Upgrade Notes

| From | To | Notes |
|------|-----|-------|
| v3.x | v4.x | Project renamed from "Meshtasticd Installer" to "MeshForge" |
| v4.0 | v4.1 | Node map added, new dependencies (leaflet.js bundled) |
| v4.1 | v4.2 | RNS gateway added, install RNS ecosystem: `pip3 install rns lxmf` |
| v4.2 | v4.3 | AREDN integration, no new dependencies |

### Migrating from Old Repository

If you were using `Meshtasticd_interactive_UI`:

```bash
# Backup your configs
cp -r ~/.config/meshforge ~/.config/meshforge.backup

# Clone new repo
git clone https://github.com/Nursedude/meshforge.git

# Your settings will be preserved in ~/.config/meshforge/
```

---

## Support Levels

MeshForge uses a **tiered support system** to communicate feature maturity:

| Level | Status | Description |
|-------|--------|-------------|
| **Core** | ✅ Integrated | Features with tests, GUI panels, full functionality |
| **Extensible** | 🔧 Plugin Stubs | Architecture ready, awaiting implementation |
| **Future** | 📋 Planned | On roadmap, not yet implemented |

### ✅ Fully Integrated (Core)

| Feature | Tests | Description |
|---------|-------|-------------|
| **GTK Desktop UI** | ✅ | Full tabbed interface with all panels |
| **Web UI** | ✅ | Browser-based interface with password auth |
| **Terminal TUI** | ✅ | SSH-friendly terminal interface |
| **Meshtastic Integration** | ✅ | Install, configure, monitor meshtasticd |
| **Radio Configuration** | ✅ | Full device settings with freq calculator |
| **Hardware Detection** | ✅ | USB, SPI HAT, I2C device detection |
| **Node Monitor** | ✅ | Real-time node tracking (no sudo) |
| **RF Tools** | 13 tests | Haversine, Fresnel, FSPL, Earth bulge |
| **Gateway Diagnostic** | 18 tests | AI-like troubleshooting for RNS/Meshtastic |
| **Network Diagnostics** | ✅ | UDP/TCP/IPv6 listeners, port mapping, multicast |
| **Security Validation** | 24 tests | Input validation, subprocess safety |

### 🔧 Plugin Stubs (Extensible)

| Plugin | Type | Status | Description |
|--------|------|--------|-------------|
| **mqtt-bridge** | Integration | Stub | MQTT for Home Assistant/Node-RED |
| **meshcore** | Protocol | Stub | MeshCore protocol (64 hops) |
| **meshing-around** | Integration | Stub | Bot framework (games, alerts) |

### 📋 Planned (Future)

| Feature | Priority | Notes |
|---------|----------|-------|
| LXMF/NomadNet UI | High | RNS messaging integration |
| Node flashing | Medium | Flash firmware to USB devices |
| NanoVNA plugin | Medium | Antenna SWR, Smith chart |

---

## Quick Start

```bash
# Clone the repository
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# Install dependencies (Raspberry Pi / Debian / Ubuntu)
sudo apt update
sudo apt install -y python3-pip python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1

# Install Python dependencies
pip3 install rich textual flask --break-system-packages

# Launch MeshForge (choose your interface)
sudo python3 src/launcher.py          # Auto-detect best interface
sudo python3 src/main_gtk.py          # GTK Desktop UI
sudo python3 src/main_web.py          # Web UI (browser)
sudo python3 src/main_tui.py          # Terminal UI (SSH)
python3 src/standalone.py             # Zero-dependency mode
```

---

## Interfaces

MeshForge provides **multiple interfaces for different use cases**:

| Interface | Command | Best For | Requires |
|-----------|---------|----------|----------|
| **Auto Launcher** | `sudo python3 src/launcher.py` | Auto-selects best UI | - |
| **GTK Desktop** | `sudo python3 src/main_gtk.py` | Pi with display, VNC | GTK4 |
| **Web UI** | `sudo python3 src/main_web.py` | Remote browser access | Flask |
| **Terminal TUI** | `sudo python3 src/main_tui.py` | SSH, headless | Textual |
| **Standalone** | `python3 src/standalone.py` | Zero dependencies | Python only |
| **Monitor** | `python3 -m src.monitor` | Quick node check | None |

---

## RNS-Meshtastic Gateway

MeshForge provides the **first open-source gateway between Meshtastic and Reticulum (RNS)**.

### Gateway Architecture

```
Meshtastic Network  <--TCP:4403-->  MeshForge Gateway  <--RNS/LXMF-->  Reticulum Network
   (LoRa 915MHz)                      (Bridge Node)                     (Multi-path)
        |                                  |                                 |
  Meshtastic Nodes               Unified Node Tracker              NomadNet, Sideband
```

### Gateway Features

| Feature | Description |
|---------|-------------|
| **Bidirectional Messaging** | Route messages between networks |
| **Unified Node Map** | All nodes on one interactive map |
| **Telemetry Sharing** | Share position and battery data |
| **Configuration GUI** | Full graphical settings editor |
| **RNS Config Editor** | Built-in ~/.reticulum/config editor |

---

## AREDN Integration

MeshForge provides **comprehensive AREDN integration** for high-bandwidth amateur radio mesh networking:

**Components:**
- Hardware Database (10+ supported devices)
- MikroTik Configuration Wizard
- Network Simulator (RF modeling)

### Supported AREDN Hardware

| Manufacturer | Models | Band |
|--------------|--------|------|
| **MikroTik** | hAP ac3, hAP ac2, mANTBox 52, LHG 5 | 5GHz |
| **Ubiquiti** | NanoStation M5, Rocket M5, LiteBeam 5AC | 5GHz |
| **GL.iNet** | AR750S (Slate) | 2.4/5GHz |

---

## MeshForge University

MeshForge includes an **integrated learning platform** (reserved for pro edition):

**9 Courses:**
1. Introduction to Meshtastic
2. Understanding LoRa Technology
3. Mesh Network Architecture
4. RF Propagation Fundamentals
5. Advanced Deployment Strategies
6. Security Best Practices
7. Troubleshooting Guide
8. Building Custom Hardware
9. Amateur Radio Compliance & Part 97

*Progress tracking, interactive quizzes, and hands-on labs.*

---

## Features

### Installation & Setup
- **One-click meshtasticd installation** from official repos
- **Multiple release channels**: stable, beta, daily, alpha
- **Config templates** for common hardware setups

### Hardware Detection
- **USB LoRa devices**: CH340/CH341, CP2102, ESP32-S3, nRF52840
- **SPI HAT detection**: Waveshare, Adafruit, MeshAdv
- **I2C scanning**: OLED displays, sensors, GPS

### Radio Configuration
- **Full radio settings panel** with all Meshtastic options
- **Device/Position/Power/MQTT/Telemetry settings**

### Service Management
- **Start/Stop/Restart** meshtasticd service
- **Live logs** with journalctl integration
- **Boot persistence** management

---

## Frequency Slot Calculator

Uses the same **djb2 hash algorithm** as Meshtastic firmware:

```python
def djb2_hash(channel_name):
    h = 5381
    for c in channel_name:
        h = ((h << 5) + h) + ord(c)
    return h & 0xFFFFFFFF

slot = djb2_hash(channel_name) % num_channels
frequency = freq_start + (bandwidth / 2000) + (slot * bandwidth / 1000)
```

| Channel Name | Region | Preset | Slot | Frequency |
|-------------|--------|--------|------|-----------|
| LongFast | US | LONG_FAST | 20 | 906.875 MHz |
| MediumSlow | US | MEDIUM_SLOW | 52 | 914.875 MHz |

---

## RF Engineering Tools

**Core (Integrated, 13 tests, no dependencies):**
- Haversine distance
- Fresnel radius
- FSPL calculation
- Earth bulge

**Plugin (Optional, requires external APIs):**
- Site Planner API
- External elevation data
- Coverage heatmaps
- APRS integration

*Core functions work offline with no dependencies.*

### Core Calculations

| Function | Formula | Use Case |
|----------|---------|----------|
| **Haversine** | Great-circle distance | Node-to-node range |
| **Fresnel** | `17.3 × √(d/4f)` meters | Clearance planning |
| **FSPL** | `20log(d) + 20log(f) + 20log(4π/c)` dB | Link budget |
| **Earth Bulge** | `d²/12.75` meters | Long-distance LOS |

---

## Plugin System

**PluginManager API:**
- `register(plugin_class)` - Add plugin to registry
- `activate(name)` - Enable plugin
- `deactivate(name)` - Disable plugin
- `list_by_type(type)` - Filter by category

**Plugin Types:** PanelPlugin (UI), IntegrationPlugin, ToolPlugin (utilities), ProtocolPlugin

### Example Plugins

| Plugin | Type | Description |
|--------|------|-------------|
| **aredn_advanced** | Panel | AREDN hardware database, network simulation |
| **rf_calculator** | Tool | Essential RF calculations |
| **band_plan** | Tool | Amateur radio band plan reference |

---

## Simulation Mode

MeshForge includes a **hardware simulator** for testing without physical devices:

| Mode | Description |
|------|-------------|
| **Disabled** | Real hardware only (default) |
| **RF Only** | Simulate RF path calculations |
| **Mesh Network** | Full simulated mesh with nodes |
| **Full** | Complete hardware simulation |

### Simulated Node Presets

| Preset | Nodes | Description |
|--------|-------|-------------|
| **Hawaii Islands** | 8 | Inter-island links (Hilo, Kona, Maui, Oahu) |
| **Generic Test** | 5 | Basic test nodes |

---

## Supported Hardware

### Raspberry Pi
| Model | Status |
|-------|--------|
| Pi 5, Pi 4, Pi 3 | ✅ Full Support |
| Pi Zero 2 W | ✅ Full Support |
| Pi Zero W | ⚠️ Limited |

### USB LoRa Devices
| Device | Chip | Status |
|--------|------|--------|
| MeshToad, MeshTadpole | ESP32-S3 | ✅ Auto-detected |
| Heltec V3 | ESP32-S3 | ✅ Auto-detected |
| RAK4631, MeshStick | nRF52840 | ✅ Auto-detected |
| T-Beam | ESP32 | ✅ Auto-detected |

### SPI LoRa HATs
| HAT | Chip | Status |
|-----|------|--------|
| MeshAdv-Pi-Hat, MeshAdv-Mini | SX1262 | ✅ Supported |
| Waveshare SX126x | SX1262/SX1268 | ✅ Supported |
| Adafruit RFM9x | RFM95/96 | ✅ Supported |

---

## Installation

### Prerequisites

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install GTK4 (for desktop UI)
sudo apt install -y python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1

# Install Python packages
pip3 install rich textual flask meshtastic --break-system-packages
```

### Clone and Run

```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# Verify installation
python3 -c "from src.__version__ import __version__; print(f'MeshForge v{__version__}')"

# Launch
sudo python3 src/launcher.py
```

### Enable SPI/I2C (for HATs)

```bash
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0
sudo reboot
```

---

## Project Structure

```
meshforge/
├── src/
│   ├── launcher.py           # Smart interface launcher
│   ├── main_gtk.py           # GTK Desktop entry point
│   ├── main_web.py           # Web UI entry point
│   ├── main_tui.py           # Terminal TUI entry point
│   ├── standalone.py         # Zero-dependency entry point
│   ├── __version__.py        # Version info and changelog
│   │
│   ├── gtk_ui/               # GTK4 Desktop Interface
│   │   ├── app.py            # Main application
│   │   └── panels/           # Feature panels
│   │
│   ├── gateway/              # RNS-Meshtastic Bridge
│   │   ├── rns_bridge.py     # Gateway bridge service
│   │   └── node_tracker.py   # Unified node tracking
│   │
│   ├── university/           # Learning platform
│   │   └── courses.py        # 9 courses
│   │
│   ├── utils/                # Shared utilities
│   │   ├── rf.py             # RF calculations
│   │   ├── rf_fast.pyx       # Cython-optimized RF
│   │   ├── common.py         # Centralized settings
│   │   ├── auto_review.py    # Code review system
│   │   └── aredn_hardware.py # AREDN integration
│   │
│   └── plugins/              # Plugin system
│       ├── mqtt_bridge.py    # MQTT (stub)
│       └── meshcore.py       # MeshCore (stub)
│
├── plugins/examples/         # Example plugins
├── tests/                    # 195 tests
├── assets/                   # Icons and images
├── ARCHITECTURE.md           # AI self-audit report
└── README.md
```

---

## Version History

### v0.4.5-beta (2026-01-08) - Current

- **FIX**: NomadNet 'Address already in use' - node_tracker is now client-only
- **FIX**: RNS config dialog validates before save (prevents broken configs)
- **FIX**: Web Client Mode toggle to prevent meshtasticd interference
- **FIX**: PEP 668 installer handling for Debian Bookworm
- **NEW**: Setup Wizard with service/conflict detection
- **NEW**: Safe config editing with validation, backup, atomic save
- **NEW**: 11 TDD tests for config reliability
- **IMPROVED**: Multi-channel display in Radio Config (all 8 channels)
- **IMPROVED**: Simplified ASCII borders for better terminal compatibility

### v0.4.3-beta (2026-01-07)

- **NEW**: Network Diagnostics panel with UDP/TCP/IPv6 listener display
- **NEW**: Quick-action buttons (Kill Clients, Stop RNS, Watch API)
- **NEW**: NomadNet launch from GTK with proper terminal wrapper
- **NEW**: VOACAP HF Propagation tools
- **IMPROVED**: Standalone boot mode with zero external dependencies

### v4.2.x (2026-01-03-04)

- **NEW**: Unified Node Map (Meshtastic + RNS)
- **NEW**: RNS Configuration Editor with templates
- **NEW**: Gateway Configuration Dialog
- **SECURITY**: Fixed command injection vulnerabilities

### v4.1.x (2026-01-03)

- **NEW**: Mesh Network Map with Leaflet.js
- **NEW**: RF Line of Sight Calculator
- **NEW**: Version Checker & Updates tab

### v4.0.x (2026-01-03)

- **REBRAND**: Project renamed to MeshForge
- **NEW**: Frequency Slot Calculator
- **SECURITY**: Replaced os.system(), removed shell=True

### v3.x (2025-12-30 - 2026-01-02)

- GTK4 and Textual TUI interfaces
- Config file manager, system diagnostics

### v2.x - v1.x (2025-11-15 - 2025-12-29)

- Initial release through service management

---

## Roadmap

### Completed ✅
- [x] GTK4 Desktop UI
- [x] Unified Node Map
- [x] RNS-Meshtastic Gateway
- [x] AREDN Integration
- [x] Amateur Radio Compliance course
- [x] Standalone boot mode
- [x] MeshChat web interface integration
- [x] Network Diagnostics panel

### In Progress 🔧
- [ ] Node firmware flashing
- [ ] VOACAP propagation predictions

### Planned 📋
- [x] NomadNet launch from GTK (completed 2026-01-07)
- [ ] NanoVNA plugin for antenna tuning
- [ ] MQTT dashboard
- [ ] Coverage analytics

---

## Contributing

Contributions welcome! See `ARCHITECTURE.md` for codebase overview.

```bash
# Run tests
python3 -m pytest tests/ -v

# Verify syntax
python3 -m py_compile src/**/*.py
```

### Development Notes

**Public vs Template Repository:**
- **Public**: Standard open-source repo. Best for active development with contributors.
- **Template**: For creating new projects from this base. Adds "Use this template" button.

**Recommendation**: Keep as **Public** for now. Template is useful when MeshForge becomes a stable framework others want to fork as a starting point.

---

## License

GPL-3.0 - See [LICENSE](LICENSE) for details.

---

## Resources

- [Meshtastic Documentation](https://meshtastic.org/docs/)
- [Reticulum Network Stack](https://reticulum.network/)
- [AREDN Documentation](https://www.arednmesh.org/)

---

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="32" height="32"/><br>
  <b>MeshForge</b> - Build. Test. Deploy. Bridge. Monitor.<br>
  Made with aloha for the mesh community<br>
  <sub>- nurse dude (wh6gxz)</sub>
</p>

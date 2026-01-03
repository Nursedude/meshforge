# MeshForge

```
╔╦╗╔═╗╔═╗╦ ╦╔═╗╔═╗╦═╗╔═╗╔═╗
║║║║╣ ╚═╗╠═╣╠╣ ║ ║╠╦╝║ ╦║╣
╩ ╩╚═╝╚═╝╩ ╩╚  ╚═╝╩╚═╚═╝╚═╝
 LoRa Mesh Network Development & Operations Suite
```

**Build. Test. Deploy. Monitor.**

[![Version](https://img.shields.io/badge/version-4.1.0-blue.svg)](https://github.com/Nursedude/meshforge)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-yellow.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%20%7C%20Linux-orange.svg)](https://www.raspberrypi.org/)

A professional-grade toolkit for developing, testing, and managing Meshtastic/LoRa mesh networks on Raspberry Pi and Linux systems.

> **Note**: This project was formerly known as "Meshtasticd Interactive Installer". The old repository is deprecated - please use this one.

---

## Table of Contents

- [What is MeshForge?](#what-is-meshforge)
- [Quick Start](#quick-start)
- [Interfaces](#interfaces)
- [Features](#features)
- [Frequency Slot Calculator](#frequency-slot-calculator)
- [Lightweight Monitor (No Sudo)](#lightweight-monitor-no-sudo)
- [Supported Hardware](#supported-hardware)
- [Installation](#installation)
- [Usage Examples](#usage-examples)
- [Project Structure](#project-structure)
- [Version History](#version-history)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## What is MeshForge?

MeshForge is a comprehensive suite of tools for LoRa mesh network operations:

| Pillar | Description |
|--------|-------------|
| **BUILD** | Install meshtasticd, configure hardware, set up radios |
| **TEST** | Calculate frequencies, plan links, validate configurations |
| **DEPLOY** | Activate configs, manage services, enable boot persistence |
| **MONITOR** | Track nodes, view messages, watch system health |

Originally created to simplify meshtasticd installation on Raspberry Pi, MeshForge has evolved into a full **Network Operations Center** for LoRa mesh networks.

### Who is it for?

- **Network Engineers** setting up mesh infrastructure
- **Developers** building Meshtastic applications
- **Hobbyists** experimenting with LoRa radios
- **Emergency Comms Teams** deploying off-grid networks
- **Educators** teaching mesh networking concepts

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
pip3 install rich textual flask

# Launch MeshForge (choose your interface)
sudo python3 src/launcher.py          # Auto-detect best interface
sudo python3 src/main_gtk.py          # GTK Desktop UI
sudo python3 src/main_web.py          # Web UI (browser)
sudo python3 src/main_tui.py          # Terminal UI (SSH)
sudo python3 src/main.py              # Rich CLI
```

---

## Interfaces

MeshForge provides multiple interfaces for different use cases:

| Interface | Command | Best For | Requires |
|-----------|---------|----------|----------|
| **Auto Launcher** | `sudo python3 src/launcher.py` | Auto-selects best UI | - |
| **Web UI** | `sudo python3 src/main_web.py` | Remote browser access | Flask |
| **GTK Desktop** | `sudo python3 src/main_gtk.py` | Pi with display, VNC | GTK4, libadwaita |
| **Terminal TUI** | `sudo python3 src/main_tui.py` | SSH, headless systems | Textual |
| **Rich CLI** | `sudo python3 src/main.py` | Scripting, minimal systems | Rich |
| **Monitor** | `python3 -m src.monitor` | Quick node check | None (no sudo!) |

### Web UI

Access MeshForge from any device on your network:

```bash
# Start on default port 8080
sudo python3 src/main_web.py

# With password protection
sudo python3 src/main_web.py --password yourpassword

# Custom port
sudo python3 src/main_web.py --port 9000

# Check status / stop
sudo python3 src/main_web.py --status
sudo python3 src/main_web.py --stop
```

Then open `http://your-pi-ip:8080` in your browser.

### GTK Desktop UI

Modern libadwaita interface with tabbed navigation:

```bash
sudo python3 src/main_gtk.py

# Keyboard shortcuts:
# F11     - Toggle fullscreen
# Escape  - Exit fullscreen
# Ctrl+Q  - Quit
```

---

## Features

### Installation & Setup
- **One-click meshtasticd installation** from official Meshtastic repos
- **Multiple release channels**: stable, beta, daily, alpha
- **Automatic dependency resolution**
- **Config templates** for common hardware setups

### Hardware Detection
- **Auto-detect USB LoRa devices**: CH340/CH341, CP2102, ESP32-S3, nRF52840
- **SPI HAT detection**: Waveshare, Adafruit, MeshAdv
- **I2C device scanning**: OLED displays, sensors, GPS
- **Interface enablement**: SPI, I2C via raspi-config

### Radio Configuration
- **Full radio settings panel** with all Meshtastic options
- **Device settings**: Role, region, preset, hop limit
- **Position settings**: GPS mode, fixed position, broadcast intervals
- **Power settings**: TX power, low power mode
- **MQTT settings**: Server, encryption, JSON output
- **Telemetry settings**: Device metrics, environment sensors

### Service Management
- **Start/Stop/Restart** meshtasticd service
- **View live logs** with journalctl integration
- **Enable/disable** boot persistence
- **Status monitoring** with uptime tracking

### System Monitoring
- **Real-time dashboard**: CPU, memory, disk, temperature
- **Node list** with hardware info and last heard times
- **Message sending**: Broadcast and direct messages
- **Connection status** indicator

---

## Frequency Slot Calculator

MeshForge includes a **Frequency Slot Calculator** that uses the same djb2 hash algorithm as the Meshtastic firmware. This helps you understand which frequency your channel will use.

### How It Works

1. Enter a **channel name** (e.g., "LongFast", "MyMesh")
2. Select your **region** (US, EU_868, EU_433, etc.)
3. Select your **modem preset** (LONG_FAST, MEDIUM_SLOW, etc.)
4. Click **Calculate** to see:
   - Hash value (djb2)
   - Frequency slot number
   - Total available slots
   - Center frequency in MHz

### Example Calculations

| Channel Name | Region | Preset | Slot | Frequency |
|-------------|--------|--------|------|-----------|
| LongFast | US | LONG_FAST | 20 | 906.875 MHz |
| MediumSlow | US | MEDIUM_SLOW | 52 | 914.875 MHz |
| ShortFast | US | SHORT_FAST | 68 | 918.875 MHz |
| MyMesh | US | LONG_FAST | 41 | 912.125 MHz |

### Algorithm

```python
def djb2_hash(channel_name):
    h = 5381
    for c in channel_name:
        h = ((h << 5) + h) + ord(c)
    return h & 0xFFFFFFFF

slot = djb2_hash(channel_name) % num_channels
frequency = freq_start + (bandwidth / 2000) + (slot * bandwidth / 1000)
```

---

## Lightweight Monitor (No Sudo)

Need a quick way to check your mesh network without root access? Use the standalone monitor:

```bash
# Quick node list (no sudo required!)
python3 -m src.monitor

# Continuous monitoring
python3 -m src.monitor --watch

# JSON output for scripting
python3 -m src.monitor --json

# Connect to remote meshtasticd
python3 -m src.monitor --host 192.168.1.100 --port 4403

# Custom update interval
python3 -m src.monitor --watch --interval 10
```

### Features
- **No root/sudo required** - runs as regular user
- **Connects via TCP** to meshtasticd (port 4403)
- **Real-time node updates** with last heard times
- **JSON output** for integration with other tools
- **Config persistence** in `~/.config/meshtastic-monitor/`

---

## Supported Hardware

### Raspberry Pi Models
| Model | Status | Notes |
|-------|--------|-------|
| Pi 5 | ✅ Full Support | Best performance |
| Pi 4 | ✅ Full Support | Recommended |
| Pi 3 | ✅ Full Support | Good performance |
| Pi Zero 2 W | ✅ Full Support | Compact option |
| Pi Zero W | ⚠️ Limited | Works, slower |
| Pi 400 | ✅ Full Support | Desktop form factor |

### USB LoRa Devices
| Device | Chip | Status |
|--------|------|--------|
| MeshToad | ESP32-S3 | ✅ Auto-detected |
| MeshTadpole | ESP32-S3 | ✅ Auto-detected |
| MeshStick | nRF52840 | ✅ Auto-detected |
| Heltec V3 | ESP32-S3 | ✅ Auto-detected |
| RAK4631 | nRF52840 | ✅ Auto-detected |
| T-Beam | ESP32 | ✅ Auto-detected |

### SPI LoRa HATs
| HAT | Chip | Status |
|-----|------|--------|
| MeshAdv-Pi-Hat | SX1262 | ✅ Supported |
| MeshAdv-Mini | SX1262 | ✅ Supported |
| Waveshare SX126x | SX1262/SX1268 | ✅ Supported |
| Adafruit RFM9x | RFM95/96 | ✅ Supported |

### I2C Devices
- **Displays**: SSD1306, SH1106 OLED
- **Sensors**: BME280, BME680
- **GPS**: NEO-6M, NEO-7M, NEO-8M

---

## Installation

### Prerequisites

**Raspberry Pi OS (Bookworm) or Debian 12+**

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install GTK4 and libadwaita (for desktop UI)
sudo apt install -y python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1

# Install Python packages
pip3 install rich textual flask meshtastic --break-system-packages
```

### Clone and Run

```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# Test the installation
python3 -c "from src.__version__ import __version__; print(f'MeshForge v{__version__}')"

# Launch
sudo python3 src/launcher.py
```

### Enable SPI and I2C (for HATs)

```bash
# Enable SPI
sudo raspi-config nonint do_spi 0

# Enable I2C
sudo raspi-config nonint do_i2c 0

# Reboot to apply
sudo reboot
```

---

## Usage Examples

### Install meshtasticd

```bash
# Launch MeshForge
sudo python3 src/launcher.py

# Navigate to: Install/Update → Install meshtasticd
# Select release channel (stable recommended)
# Follow prompts
```

### Configure a Radio

```bash
# In MeshForge GTK or Web UI:
# 1. Go to "Radio Config" tab
# 2. Click "Load Current Config"
# 3. Adjust settings (region, preset, name, etc.)
# 4. Click "Apply" for each setting
# 5. Restart service when prompted
```

### Monitor Your Mesh

```bash
# Quick check (no sudo)
python3 -m src.monitor

# Output:
# ╔═══════════════════════════════════════════════════════════╗
# ║         Meshtastic Node Monitor (No Sudo Required)        ║
# ╚═══════════════════════════════════════════════════════════╝
#
# Connecting to localhost:4403...
# Connected! My node: !abcd1234
# Node count: 5
#
#   ID           Name             Short  Hardware           Battery  Last Heard
#   ----         ----             -----  --------           -------  ----------
#   abcd1234     MyNode           MYND   TBEAM              85%      2m ago *
#   ef567890     Neighbor1        NBR1   HELTEC_V3          --       15m ago
```

### Calculate Frequency Slot

```bash
# In MeshForge GTK UI:
# 1. Go to "Radio Config" tab
# 2. Scroll to "Frequency Slot Calculator"
# 3. Enter channel name, select region/preset
# 4. Click "Calculate Frequency"
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
│   ├── main.py               # Rich CLI entry point
│   ├── monitor.py            # Lightweight node monitor (no sudo)
│   ├── __version__.py        # Version info and changelog
│   │
│   ├── gtk_ui/
│   │   ├── app.py            # MeshForgeApp GTK application
│   │   └── panels/
│   │       ├── dashboard.py  # System dashboard
│   │       ├── radio_config.py # Radio configuration + freq calculator
│   │       ├── hardware.py   # Hardware detection
│   │       ├── install.py    # Installation panel
│   │       └── ...
│   │
│   ├── config/
│   │   ├── hardware.py       # Hardware detection logic
│   │   ├── radio_config.py   # CLI-based radio config
│   │   └── config_file_manager.py # YAML config management
│   │
│   ├── monitoring/
│   │   └── node_monitor.py   # Node tracking via TCP
│   │
│   ├── services/
│   │   └── systemd.py        # Service management
│   │
│   ├── tools/
│   │   ├── network.py        # Network diagnostics
│   │   └── rf_tools.py       # RF calculations
│   │
│   └── utils/
│       ├── system.py         # System utilities
│       ├── cli.py            # CLI path detection
│       └── emoji.py          # Terminal emoji support
│
├── templates/                 # Config templates
├── .claude/                   # Development notes
└── README.md
```

---

## Version History

### v4.1.0 (2026-01-03) - Map, Updates & RF Tools
- **NEW**: Mesh Network Map with Leaflet.js
  - Interactive map showing node positions
  - Color-coded markers (green=my node, blue=online, orange=stale, gray=offline)
  - Click nodes for detailed popups (battery, SNR, hardware, altitude)
  - Auto-zoom to fit all nodes
- **NEW**: RF Line of Sight Calculator
  - Built-in LOS calculator with elevation data from Open-Elevation API
  - Calculates: distance, earth bulge, Fresnel zone, FSPL
  - Web-based visualization with Chart.js elevation profile
  - Shows terrain, LOS line, 60% Fresnel zone, earth curvature
  - Status indicators: Clear/Marginal/Obstructed
- **NEW**: Settings Panel with Dark Mode
  - Dark/Light mode toggle with instant theme switching
  - Compact mode for smaller screens
  - Auto-refresh and interval settings
  - Settings persisted to ~/.config/meshforge/settings.json
- **NEW**: In-App Config File Editor
  - Full YAML editor built into Config File Manager
  - Save and Revert buttons with change tracking
  - Unsaved changes indicator
  - Option to use terminal editor (nano) if preferred
- **NEW**: Version Checker & Updates tab
  - Check installed versions of meshtasticd, CLI, firmware
  - Compare against latest available versions
  - Update availability notifications
- **NEW**: Desktop Integration
  - `.desktop` launcher for Raspberry Pi menu (Internet, System Tools)
  - SVG icon for application menu
  - Install script: `sudo ./scripts/install-desktop.sh`
  - Terminal-based sudo authentication for reliable launching
- **NEW**: Site Planner Integration
  - Opens [site.meshtastic.org](https://site.meshtastic.org/) for RF coverage planning
  - Button in Tools → RF Tools section
  - Uses ITM/Longley-Rice model with NASA SRTM terrain data
- **NEW**: Frequency Slot Calculator Redesign
  - All 22 Meshtastic regions supported
  - Channel Preset dropdown for quick slot selection
  - Auto-calculated frequency display
- **FIX**: Device role parsing now handles numeric enum values from CLI
  - Correctly maps 0=CLIENT, 1=CLIENT_MUTE, 2=ROUTER, etc.
- **NEW**: `/api/nodes/full` endpoint with rich node data

### v4.0.1 (2026-01-03) - Security & Features
- **SECURITY**: Replaced `os.system()` with `subprocess.run()`
- **SECURITY**: Removed `shell=True` from subprocess calls
- **SECURITY**: Fixed bare except clauses across 12 files
- **NEW**: Frequency Slot Calculator with djb2 hash algorithm
- **NEW**: Region and modem preset selection for frequency calculation
- **IMPROVED**: Better exception handling with specific types

### v4.0.0 (2026-01-03) - MeshForge Rebrand
- **REBRAND**: Project renamed to MeshForge
- **NEW**: Professional suite branding
- **NEW**: Enhanced Radio Config parsing
- **NEW**: USB LoRa device detection
- **NEW**: Serial port detection for GPS

### v3.2.7 (2026-01-02)
- Web UI with live dashboard
- Node and message tabs
- Password authentication option

### v3.x (2025-12-30 - 2026-01-02)
- GTK4 and Textual TUI interfaces
- Config file manager
- System diagnostics

### v2.x (2025-12-29)
- Service management
- CLI integration
- Channel presets

### v1.x (2025-11-15)
- Initial release

---

## Roadmap

### ✅ v4.2 - Auto-Update System (Completed in v4.1)
- [x] Component version detection (meshtasticd, CLI, firmware)
- [x] Update notifications in Updates tab
- [ ] One-click updates with rollback

### ✅ v4.3 - Desktop Integration (Completed in v4.1)
- [x] `.desktop` launcher for Pi menu
- [x] SVG application icon
- [ ] System tray icon
- [ ] Autostart on boot option

### v4.4 - Site Planner API Integration
- [x] Link to site.meshtastic.org (completed)
- [x] Built-in RF Line of Sight calculator
- [ ] Embed Site Planner in WebKitGTK view
- [ ] Auto-populate node positions from mesh
- [ ] Local Docker deployment option

### v4.5 - RNS/Reticulum Integration
- [ ] RNS Management panel (install, configure, monitor rnsd)
- [ ] LXMF/NomadNet/MeshChat integration
- [ ] RNS-Meshtastic Gateway setup wizard
- [ ] RNS nodes on mesh map (if gateway active)
- [ ] RNODE device detection and configuration

### v4.6 - Node Flashing
- [ ] Flash Meshtastic firmware to USB devices
- [ ] Firmware version management
- [ ] Backup/restore node configs

### v5.0 - Network Operations
- [x] Map visualization of mesh topology (completed)
- [ ] MQTT integration for multi-node monitoring
- [ ] Message history and analytics
- [ ] Coverage analytics with Site Planner API

---

## Contributing

Contributions are welcome! Please see `.claude/session_notes.md` for development patterns and architecture notes.

### Development Setup

```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# Create virtual environment (optional)
python3 -m venv venv
source venv/bin/activate

# Install dev dependencies
pip install rich textual flask meshtastic

# Run tests
python3 -m py_compile src/**/*.py
```

---

## License

GPL-3.0 - See [LICENSE](LICENSE) for details.

---

## Resources

- [Meshtastic Documentation](https://meshtastic.org/docs/)
- [Meshtastic GitHub](https://github.com/meshtastic)
- [Meshtastic Firmware](https://github.com/meshtastic/firmware)
- [MtnMesh Community](https://mtnme.sh/)

---

## Acknowledgments

- The [Meshtastic](https://meshtastic.org/) project and community
- Contributors and testers
- [heypete](https://github.com/heypete) for frequency calculator inspiration

---

<p align="center">
  <b>MeshForge</b> - Build. Test. Deploy. Monitor.<br>
  Made with aloha for the mesh community
</p>

# MeshForge

**LoRa Mesh Network Operations Center**

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
</p>

[![Version](https://img.shields.io/badge/version-0.4.5--beta-blue.svg)](https://github.com/Nursedude/meshforge)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-yellow.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-714%20passing-brightgreen.svg)](tests/)

**The first open-source tool to bridge Meshtastic and Reticulum (RNS) mesh networks.**

```
MESHTASTIC  <---->  MESHFORGE  <---->  RETICULUM
  (LoRa)            (Bridge)         (Multi-path)
```

---

## Quick Start

```bash
# Clone and run
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# Install dependencies
pip3 install rich textual flask --break-system-packages

# Launch (pick one)
sudo python3 src/launcher.py      # Auto-detect best UI
python3 src/standalone.py         # Zero dependencies
```

**That's it.** The launcher auto-selects the best interface for your system.

---

## What It Does

| Problem | Solution |
|---------|----------|
| Meshtastic and RNS can't communicate | **Gateway bridge** routes messages between networks |
| Two separate node databases | **Unified map** shows all nodes together |
| Complex config files | **GUI editors** for meshtasticd, RNS, and gateway |
| "Will my link work?" | **RF tools** calculate LOS, Fresnel, path loss |
| Setup troubleshooting | **Diagnostics** identify and fix issues |

---

## Interfaces

| Interface | Command | Best For |
|-----------|---------|----------|
| **Auto** | `sudo python3 src/launcher.py` | Let MeshForge choose |
| **GTK** | `sudo python3 src/main_gtk.py` | Desktop/VNC |
| **Web** | `sudo python3 src/main_web.py` | Browser access |
| **TUI** | `sudo python3 src/main_tui.py` | SSH/headless |
| **Rich CLI** | `sudo python3 src/main.py` | Terminal menus |
| **Standalone** | `python3 src/standalone.py` | Zero deps |

---

## Features

### Core (Fully Integrated)

- **Service Management** - Start/stop/restart meshtasticd, view logs
- **Config Editor** - Manage YAML configs with templates
- **Hardware Detection** - USB, SPI HAT, I2C auto-detection
- **Node Monitor** - Real-time tracking (viewer mode, no sudo)
- **RF Tools** - Haversine, Fresnel, FSPL, earth bulge calculations
- **Connection Manager** - Handles meshtasticd's single-connection limit
- **Network Diagnostics** - UDP/TCP listeners, port scanning

### Gateway (RNS Bridge)

- **Bidirectional** - Messages flow between Meshtastic and RNS
- **Position Sharing** - GPS coordinates sync across networks
- **Unified Map** - All nodes on one interactive display

### Extensible

- **AREDN Integration** - Hardware database, network tools
- **HamClock** - Solar/HF propagation data
- **Plugin System** - Add custom integrations

---

## Supported Hardware

### Raspberry Pi
Pi 5, Pi 4, Pi 3, Zero 2 W - Full support

### USB LoRa Devices
- **ESP32-S3**: MeshToad, MeshTadpole, Heltec V3
- **nRF52840**: RAK4631, MeshStick
- **ESP32**: T-Beam, T-Echo

### SPI HATs
- MeshAdv-Pi-Hat, MeshAdv-Mini (SX1262)
- Waveshare SX126x
- Adafruit RFM9x

---

## Installation

### Raspberry Pi / Debian

```bash
# System packages
sudo apt update
sudo apt install -y python3-pip python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1

# Python packages
pip3 install rich textual flask meshtastic --break-system-packages

# Enable SPI/I2C for HATs
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0
```

### Run

```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
sudo python3 src/launcher.py
```

---

## Project Structure

```
meshforge/
├── src/
│   ├── launcher.py        # Smart interface launcher
│   ├── main.py             # Rich CLI interface
│   ├── main_gtk.py         # GTK Desktop
│   ├── main_web.py         # Web UI
│   ├── main_tui.py         # Terminal TUI
│   ├── standalone.py       # Zero-dependency mode
│   │
│   ├── gtk_ui/panels/      # GTK feature panels
│   ├── tui/                # Textual TUI
│   ├── gateway/            # RNS-Meshtastic bridge
│   └── utils/              # RF tools, connection manager
│
├── tests/                  # 714 tests
└── assets/                 # Icons
```

---

## Development

```bash
# Run tests
python3 -m pytest tests/ -v

# Check syntax
python3 -m py_compile src/**/*.py

# Verify version
python3 -c "from src.__version__ import __version__; print(__version__)"
```

See `CLAUDE.md` for development guidelines.

---

## Version History

### v0.4.5-beta (Current)
- Connection manager with retry logic and cache fallback
- TUI dashboard improvements with auto-refresh
- ASCII-safe borders for terminal compatibility
- Emoji fallback system for Raspberry Pi terminals
- HamClock diagnostic tools

### v0.4.3-beta
- Network diagnostics panel
- NomadNet launch from GTK
- Standalone boot mode

### v0.4.x
- Unified node map
- RNS configuration editor
- Gateway configuration dialog
- Security fixes

---

## Contributing

Contributions welcome! Please:
1. Run tests before submitting
2. Follow existing code style
3. Add tests for new features

---

## License

GPL-3.0 - See [LICENSE](LICENSE)

---

## Resources

- [Meshtastic Docs](https://meshtastic.org/docs/)
- [Reticulum Network](https://reticulum.network/)
- [AREDN Mesh](https://www.arednmesh.org/)

---

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="32" height="32"/><br>
  <b>MeshForge</b> - Build. Test. Deploy. Monitor.<br>
  Made with aloha for the mesh community<br>
  <sub>WH6GXZ</sub>
</p>

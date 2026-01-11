# MeshForge

**LoRa Mesh Network Operations Center**

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshforge"><img src="https://img.shields.io/badge/version-0.4.6--beta-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.9+-yellow.svg" alt="Python"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/tests-779%20passing-brightgreen.svg" alt="Tests"></a>
</p>

<p align="center">
  <strong>The first open-source tool to bridge Meshtastic and Reticulum mesh networks.</strong>
</p>

---

## Why MeshForge?

Meshtastic and Reticulum (RNS) are powerful mesh networks, but they can't talk to each other. MeshForge bridges that gap.

| Problem | MeshForge Solution |
|---------|-------------------|
| Meshtastic and RNS can't communicate | Gateway bridge routes messages between networks |
| Two separate node databases | Unified map shows all nodes together |
| Complex config files | GUI editors for meshtasticd, RNS, and gateway |
| "Will my link work?" | RF tools calculate line-of-sight, Fresnel zones, path loss |
| Setup troubleshooting | Diagnostics identify and fix issues |

---

## Quick Start

```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
pip3 install rich textual flask --break-system-packages
sudo python3 src/launcher.py
```

The launcher auto-detects your environment and picks the best interface.

---

## Choose Your Interface

| Interface | Command | Best For |
|-----------|---------|----------|
| Auto | `sudo python3 src/launcher.py` | Let MeshForge decide |
| TUI (raspi-config style) | `sudo python3 src/launcher_tui.py` | SSH / headless (recommended) |
| VTE Wrapper | `python3 src/launcher_vte.py` | Desktop with proper taskbar icon |
| GTK Desktop | `sudo python3 src/main_gtk.py` | Full graphical interface |
| Web UI | `sudo python3 src/main_web.py` | Browser access |
| Standalone | `python3 src/standalone.py` | Zero dependencies |

**Desktop Integration**: After install, run `meshforge vte` for best taskbar icon support.

---

## Features

**Core Tools**
- Service management for meshtasticd (start/stop/restart, logs)
- Full radio configuration (presets, channels, hardware profiles)
- YAML config editor with templates
- Hardware detection (USB, SPI HAT, I2C)
- Real-time node monitoring
- RF calculations (Haversine, Fresnel, FSPL, earth bulge)

**Radio Configuration (TUI)**
- Radio presets (SHORT_TURBO → LONG_SLOW)
- Full 8-channel configuration with individual editing
- Frequency Slot Calculator (djb2 hash algorithm)
- Gateway templates (Standard, Turbo, MtnMesh)
- Hardware config selection from available.d/

**Network & Diagnostics (TUI)**
- System diagnostics (services, hardware, logs, resources)
- Network tools (ping, port scan, device discovery)
- Site planner (range estimator, antenna guidelines)

**Gateway Bridge**
- Bidirectional Meshtastic-to-RNS messaging
- Position sharing across networks
- Unified node tracking

**Integrations**
- AREDN hardware database
- HamClock solar/propagation data
- MQTT connectivity

---

## Supported Hardware

**Raspberry Pi**: Pi 5, Pi 4, Pi 3, Zero 2 W

**USB LoRa Devices**
- ESP32-S3: MeshToad, MeshTadpole, Heltec V3
- nRF52840: RAK4631, MeshStick
- ESP32: T-Beam, T-Echo

**SPI HATs**: MeshAdv-Pi-Hat, Waveshare SX126x, Adafruit RFM9x

---

## Architecture

MeshForge follows a **configuration over installation** philosophy. It connects to services - it doesn't embed them.

**Layers**

- **UI Layer** - GTK4, Web, TUI, CLI (all share the commands layer)
- **Commands Layer** - Unified API: `meshtastic.py`, `gateway.py`, `rns.py`, `service.py`, `hardware.py`
- **Gateway Layer** - Bridge logic: `rns_bridge.py`, `node_tracker.py`, `config.py`
- **External Services** - meshtasticd, rnsd, HamClock, MQTT (run independently)

**Design Principles**
- All operations go through `src/commands/` for consistency
- Services run independently - MeshForge monitors and configures
- Viewer mode (no sudo) vs Admin mode (sudo required)
- Graceful degradation when dependencies are missing

---

## Installation

```bash
# Raspberry Pi / Debian
sudo apt update
sudo apt install -y python3-pip python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1 \
    gir1.2-vte-2.91 libvte-2.91-gtk4-0  # For VTE taskbar icon

pip3 install rich textual flask meshtastic --break-system-packages

# Enable SPI/I2C for HATs
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0

# Run
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
sudo python3 src/launcher_tui.py  # Recommended: raspi-config style TUI
```

**Desktop Integration**
```bash
sudo ./scripts/install-desktop.sh  # Installs icons, menu entries, VTE dependencies
meshforge vte  # Launch with proper taskbar icon
```

---

## Contributing

We welcome contributions! Before submitting:

1. Run tests: `python3 -m pytest tests/ -v`
2. Use `get_real_user_home()` instead of `Path.home()` for user paths
3. Add tests for new features
4. Use the commands layer for new operations

See `CLAUDE.md` for development guidelines and `.claude/foundations/persistent_issues.md` for common pitfalls.

---

## Resources

- [Meshtastic Documentation](https://meshtastic.org/docs/)
- [Reticulum Network](https://reticulum.network/)
- [AREDN Mesh](https://www.arednmesh.org/)

---

## License

GPL-3.0 - See [LICENSE](LICENSE)

---

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="32" height="32"/><br>
  <strong>MeshForge</strong><br>
  Build. Test. Deploy. Monitor.<br>
  <sub>Made with aloha for the mesh community | WH6GXZ</sub>
</p>

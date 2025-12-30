# Meshtasticd Interactive Installer & Manager

An interactive installer, updater, and comprehensive configuration tool for meshtasticd on Raspberry Pi OS and compatible Linux systems.

**Version 3.0.1** | [Changelog](#version-history)

## What's New in v3.0.1

- **Launcher Wizard** - Interactive wizard to select your preferred interface
- **Fixed Log Following** - Logs now update properly in GTK4 and TUI
- **Improved Navigation** - All menus have Back (0) and Main Menu (m) options
- **Better Shortcuts** - Logical keyboard shortcuts (q=quit, ?=help)
- **RPi Compatibility** - Proper pip install with --break-system-packages

## What's New in v3.0.0

- **GTK4 Graphical Interface** - Modern desktop UI with libadwaita design
- **Textual TUI** - Full-featured terminal UI for SSH/headless access
- **Config File Manager** - Select YAML from `/etc/meshtasticd/available.d`
- **Service Management** - Start/stop/restart with live log viewing
- **Three UI Options** - Choose GTK4, Textual TUI, or Rich CLI

## UI Options

| Interface | Command | Best For |
|-----------|---------|----------|
| **Wizard** | `sudo meshtasticd-installer` | Auto-detect and select best interface |
| **GTK4 GUI** | `sudo python3 src/main_gtk.py` | Pi with display, VNC, desktop |
| **Textual TUI** | `sudo python3 src/main_tui.py` | SSH, headless, Raspberry Pi Connect |
| **Rich CLI** | `sudo meshtasticd-cli` | Fallback, minimal environments |

## Features

### Installation & Management
- **Interactive Installation**: Guided setup for meshtasticd daemon
- **Version Management**: Install/update stable, beta, daily, or alpha builds
- **Official Repositories**: Uses OpenSUSE Build Service for latest builds
- **Virtual Environment**: Isolated Python dependencies (fixes PEP 668 errors)
- **OS Detection**: Automatic detection of 32-bit/64-bit Raspberry Pi OS and other Linux boards
- **Board Detection**: Reads exact model from device tree (Pi 2/3/4/5/Zero/Zero 2W/etc.)
- **Dependency Management**: Automatically fix deprecated dependencies
- **Error Handling**: Comprehensive debugging and troubleshooting tools
- **Automatic Update Notifications**: Get notified when updates are available
- **Terminal Compatibility**: Works on all terminals with automatic emoji/ASCII fallback

#### Available Build Channels
- **stable/beta** - Latest stable releases from `network:Meshtastic:beta` (recommended)
- **daily** - Cutting-edge daily builds from `network:Meshtastic:daily`
- **alpha** - Experimental alpha builds from `network:Meshtastic:alpha`

### Config File Manager (New in v3.0)
- **Browse available.d** - View all YAML configs from meshtasticd package
- **Activate configs** - Copy to config.d with one click
- **Edit with nano** - Direct editing in terminal (always returns to app)
- **Apply changes** - Automatic daemon-reload and service restart
- **Preview files** - See config content before activating

### Service Management (New in v2.2)
- **Start/Stop/Restart** - Control meshtasticd service
- **Live Logs** - View and follow journalctl output
- **Boot Control** - Enable/disable service on startup
- **Daemon Reload** - Reload systemd after config changes

### Quick Status Dashboard
Real-time monitoring at a glance:
- **Service Status**: Running/stopped state with uptime information
- **System Health**: CPU temperature, memory usage, disk space
- **Network Status**: IP address, internet connectivity
- **Configuration Status**: Active config file and template
- **Quick Actions**: Refresh, view logs, restart service, check updates

### Channel Presets
Pre-configured channel setups for common use cases:
- **Default Meshtastic** - Standard LongFast configuration
- **MtnMesh Community** - MediumFast with slot 20
- **Emergency/SAR** - Maximum range for emergency operations
- **Urban High-Density** - ShortFast for city networks
- **Private Group** - Custom encrypted channels
- **Multi-Channel** - Multiple channels for organizations
- **Long Range** - Maximum distance configuration
- **Repeater/Router** - Infrastructure node setup

### Configuration Templates
Ready-to-use hardware and use-case templates:
- MeshAdv-Mini (SX1262/SX1268 HAT)
- MeshAdv-Mini 400MHz variant
- Waveshare SX1262
- Adafruit RFM9x
- **MtnMesh Community**
- **Emergency/SAR**
- **Urban High-Speed**
- **Repeater Node**

### Hardware Support
- **Hardware Detection**: Auto-detect USB and SPI LoRa modules
- **MeshToad/MeshTadpole Support**: Specialized detection for MtnMesh devices
- **Power Warnings**: Alerts for high-power modules (900mA+ devices)

### Radio Configuration
- **Modem Presets**: All official Meshtastic presets
  - **MediumFast** (MtnMesh community standard, Oct 2025)
  - LongFast (Default Meshtastic)
  - ShortFast, MediumSlow, LongModerate, etc.
- **Channel Slot Configuration**: Interactive slot selection (e.g., slot 20 for LongFast)
- **Region Selection**: All supported regulatory regions
- **TX Power Configuration**: 0-30 dBm with device-specific recommendations
- **Hop Limit Settings**: Network size optimization

### Module Configuration
Interactive configuration for all Meshtastic modules:
- **MQTT** - Bridge mesh to internet
- **Serial** - Serial communication
- **External Notification** - LED/buzzer control
- **Store & Forward** - Message caching
- **Range Test** - Network testing
- **Telemetry** - Device/environment monitoring
- **Canned Messages** - Quick message templates
- **Audio** - Voice communication
- **Remote Hardware** - GPIO control
- **Neighbor Info** - Network topology
- **Detection Sensor** - GPIO sensors

## Supported Platforms

- **Raspberry Pi OS** (32-bit armhf)
- **Raspberry Pi OS** (64-bit arm64)
- **Raspbian** Bookworm and newer
- **Debian-based** Linux distributions on ARM/x86_64
- **Terminal Compatibility**: Works perfectly on:
  - Direct console (HDMI/serial)
  - SSH sessions (automatic ASCII mode)
  - Raspberry Pi Connect (desktop or terminal)
  - Screen/tmux
  - Any terminal emulator

## Supported Hardware

### Raspberry Pi Models
- **Raspberry Pi 5** (Latest model with full support)
- **Raspberry Pi 4** (All memory variants)
- **Raspberry Pi 3** (B, B+, A+)
- **Raspberry Pi 2**
- **Raspberry Pi Zero 2 W** (Recommended for portable nodes)
- **Raspberry Pi Zero W**
- **Raspberry Pi Zero**
- **Raspberry Pi 400** (Desktop all-in-one)

### USB LoRa Modules
- **MeshToad** (CH341, 1W, 900mA peak) - MtnMesh device
- **MeshTadpole** (CH341 variant)
- **MeshStick** (Official Meshtastic device)
- CH340/CH341-based modules
- CP2102-based modules (Silicon Labs)
- FT232-based modules (FTDI)

### SPI LoRa HATs
- **MeshAdv-Mini** (SX1262/SX1268, +22dBm, GPS, Temp sensor, PWM fan)
- **MeshAdv-Pi-Hat** (1W High Power, SX1262/SX1268, +33dBm, GPS)
- **MeshAdv-Pi v1.1** (Standard HAT)
- **Waveshare SX126X** (SX1262)
- **Adafruit RFM9x** (SX1276)
- **Elecrow LoRa RFM95** (SX1276)
- **PiTx LoRa** (SX1276)

All HATs include complete GPIO pin configurations and hardware-specific settings.

## Installation

Choose your preferred installation method:

### Quick Install (Recommended)

**One-liner installation** - Downloads, installs, and launches the interactive installer automatically:

```bash
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo bash
```

This will:
- Update package lists (`apt-get update`)
- Prompt for optional system upgrade (you can respond with y/n)
- Install all required dependencies
- Clone the repository to `/opt/meshtasticd-installer`
- **Create Python virtual environment** (fixes PEP 668 externally-managed-environment errors)
- Install Python dependencies in isolated venv
- Create the `meshtasticd-installer` command
- **Automatically launch the interactive installer**

**Installation Options:**

Skip the interactive prompts and automatically upgrade system packages:
```bash
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo UPGRADE_SYSTEM=yes bash
```

Skip the system upgrade entirely (faster installation):
```bash
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo SKIP_UPGRADE=yes bash
```

**To run the installer later manually:**
```bash
sudo meshtasticd-installer
```

---

### UI-Specific Dependencies

**For GTK4 Graphical Interface:**
```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1
```

**For Textual TUI:**
```bash
pip install textual
```

---

### Web-Based Installer

**Perfect for beginners** - Install through your browser:

```bash
# Clone the repository
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI

# Start web installer
sudo python3 web_installer.py
```

Then open your browser and visit: `http://<raspberry-pi-ip>:8080`

The web interface provides:
- Visual system information
- One-click installation buttons
- Real-time installation progress
- Easy access from any device on your network

---

### Docker Installation

**For containerized deployments**:

```bash
# Clone repository
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI

# Build and run with Docker Compose
docker-compose up -d

# Or build and run manually
docker build -t meshtasticd-installer .
docker run -it --privileged -v /dev:/dev meshtasticd-installer
```

**Docker Web Installer:**
```bash
docker run -d -p 8080:8080 --privileged -v /dev:/dev meshtasticd-installer web
```

---

### Manual Installation

**For advanced users who want full control**:

```bash
# 1. Install system dependencies
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv git

# 2. Clone repository
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI

# 3. Create virtual environment and install dependencies (Recommended)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Run installer (choose one)
sudo ./venv/bin/python src/main_gtk.py   # GTK4 GUI (with display)
sudo ./venv/bin/python src/main_tui.py   # Textual TUI (SSH/headless)
sudo ./venv/bin/python src/main.py       # Rich CLI (fallback)
```

## Usage

### Interactive Mode (Choose Your UI)
```bash
# GTK4 Graphical Interface (requires display)
sudo python3 src/main_gtk.py

# Textual TUI (works over SSH, Raspberry Pi Connect)
sudo python3 src/main_tui.py

# Rich CLI (original interface)
sudo python3 src/main.py
```

### Command Line Options
```bash
# Install latest stable version
sudo python3 src/main.py --install stable

# Install beta version
sudo python3 src/main.py --install beta

# Update existing installation
sudo python3 src/main.py --update

# Configure device
sudo python3 src/main.py --configure

# Show status dashboard
sudo python3 src/main.py --dashboard

# Show version information
sudo python3 src/main.py --version

# Check system and dependencies
sudo python3 src/main.py --check

# Debug mode
sudo python3 src/main.py --debug
```

## Main Menu Overview

### GTK4 / Textual TUI
```
Sidebar Navigation:
- Dashboard          <- Real-time system monitoring
- Service Management <- Start/stop/restart/logs
- Install / Update   <- Install meshtasticd
- Config File Manager <- Select YAML, edit with nano
- Meshtastic CLI     <- Run CLI commands
- Hardware Detection <- Detect SPI/I2C devices
```

### Rich CLI (main.py)
```
Main Menu:
1. Quick Status Dashboard
2. Service Management
3. Install meshtasticd
4. Update meshtasticd
5. Configure device
6. Channel Presets
7. Configuration Templates
8. Config File Manager
c. Meshtastic CLI Commands
9. Check dependencies
h. Hardware detection
d. Debug & troubleshooting
0. Exit
```

## Quick Start Examples

### View Status Dashboard
```bash
sudo python3 src/main_tui.py
# Dashboard tab shows service status, version, config, hardware
```

### Manage Config Files
```bash
sudo python3 src/main_tui.py
# Go to Config tab
# Select a YAML from available.d
# Click Activate, then Edit with nano
# Click Apply & Restart Service
```

### Run Meshtastic CLI Commands
```bash
sudo python3 src/main_tui.py
# Go to CLI tab
# Click --info, --nodes, or enter custom command
```

### Apply Channel Preset
```bash
sudo python3 src/main.py
# Select option 6 (Channel Presets)
# Choose a preset (e.g., MtnMesh Community)
```

## Requirements

- Python 3.7+
- Root/sudo access (for GPIO, SPI, and system package management)
- Internet connection (for downloading packages)

### Optional Dependencies
- **GTK4 + libadwaita** - For graphical interface
- **Textual** - For terminal UI
- **nano** - For config file editing

## Project Structure

```
Meshtasticd_interactive_UI/
├── src/
│   ├── main.py                    # Rich CLI entry point
│   ├── main_gtk.py                # GTK4 GUI entry point (NEW)
│   ├── main_tui.py                # Textual TUI entry point (NEW)
│   ├── __version__.py             # Version control
│   ├── dashboard.py               # Quick Status Dashboard
│   ├── gtk_ui/                    # GTK4 interface (NEW)
│   │   ├── app.py                 # Main GTK4 application
│   │   └── panels/                # UI panels
│   │       ├── dashboard.py       # Dashboard panel
│   │       ├── service.py         # Service management
│   │       ├── config.py          # Config file manager
│   │       ├── cli.py             # Meshtastic CLI
│   │       ├── install.py         # Install/update
│   │       └── hardware.py        # Hardware detection
│   ├── tui/                       # Textual TUI (NEW)
│   │   └── app.py                 # Textual application
│   ├── installer/
│   │   ├── meshtasticd.py         # Meshtasticd installation logic
│   │   ├── dependencies.py        # Dependency management
│   │   ├── version.py             # Version management
│   │   └── update_notifier.py     # Update notifications
│   ├── config/
│   │   ├── config_file_manager.py # YAML selector + nano (NEW)
│   │   ├── lora.py                # LoRa configuration
│   │   ├── radio.py               # Radio configuration
│   │   ├── device.py              # Device configuration
│   │   ├── hardware.py            # Hardware detection
│   │   ├── modules.py             # Module configuration
│   │   ├── spi_hats.py            # SPI HAT configuration
│   │   ├── yaml_editor.py         # YAML editor
│   │   └── channel_presets.py     # Channel presets
│   ├── services/                  # Service management (NEW)
│   │   └── service_manager.py     # Systemd controls
│   ├── cli/                       # Meshtastic CLI wrapper (NEW)
│   │   └── meshtastic_cli.py      # CLI commands
│   └── utils/
│       ├── system.py              # System utilities
│       ├── emoji.py               # Emoji/ASCII fallback
│       ├── logger.py              # Logging and debugging
│       ├── env_config.py          # Environment configuration
│       └── cli.py                 # CLI interface
├── templates/
│   ├── config.yaml                # Main config template
│   └── available.d/               # Hardware templates
├── scripts/
│   ├── install_armhf.sh           # 32-bit installation script
│   ├── install_arm64.sh           # 64-bit installation script
│   └── setup_permissions.sh       # GPIO/SPI permissions setup
├── requirements.txt
└── README.md
```

## Version History

### v3.0.0 (2025-12-30)
- **NEW: GTK4 graphical interface** - Modern libadwaita design with dashboard
- **NEW: Textual TUI** - Full-featured terminal UI for SSH/headless access
- **Config File Manager** - Select YAML from available.d, edit with nano
- **Service Management panel** - Start/stop/restart with live logs
- **Meshtastic CLI panel** - Integrated CLI commands
- **Hardware Detection panel** - Detect SPI/I2C devices
- **Reboot Persistence** - Installer auto-restarts after reboot
- **Three UI options** - GTK4 (display), Textual TUI (SSH), Rich CLI (fallback)
- **Auto-detect display** - Suggests appropriate UI based on environment

### v2.3.0 (2025-12-30)
- Added Config File Manager - select YAML from /etc/meshtasticd/available.d
- Uses official meshtasticd package yaml files
- Integrated nano editor for direct config file editing
- Copy selected config to config.d for activation
- Auto daemon-reload and service restart after config changes

### v2.2.0 (2025-12-30)
- Added Service Management menu (start/stop/restart/status/logs)
- Added Meshtastic CLI commands menu with full CLI integration
- Enhanced config.yaml editor with all hardware sections
- Added LoRa module presets (MeshAdv-Mini, Waveshare, Ebyte E22, etc.)
- Service management: view logs by time, follow logs, boot control

### v2.1.2 (2025-12-30)
- Added interactive config.yaml editor
- Simplified emoji detection - uses stdout UTF-8 encoding
- All menus now have Back option to return to previous menu

### v2.1.1 (2025-12-30)
- Fixed version comparison for non-standard versions
- Improved emoji detection for Raspberry Pi local console
- Updated config.yaml template with complete configuration
- Added reboot prompt after SPI/I2C changes

### v2.1.0 (2025-12-30)
- Fixed packaging 25.0 conflict - use pipx for meshtastic CLI isolation
- Added pipx support for isolated meshtastic CLI installation
- Added .env configuration file support

### v2.0.2 (2025-12-30)
- Enhanced Raspberry Pi OS compatibility - Default to ASCII on Raspberry Pi OS
- Improved emoji detection - Automatic SSH session detection
- Better OS and board detection - Reads exact model from device tree

### v2.0.1 (2025-12-30)
- Fixed Python externally-managed-environment error - PEP 668 compliance
- Virtual environment support - Isolated Python dependencies
- Emoji fallback system - ASCII alternatives for terminals without UTF-8

### v2.0.0 (2025-12-29)
- Added Quick Status Dashboard
- Added Interactive Channel Configuration with presets
- Added Automatic Update Notifications
- Added Configuration Templates for common setups
- Improved UI with better navigation and help
- Added version control system
- New templates: Emergency/SAR, Urban, MtnMesh, Repeater

### v1.2.0 (2025-12-15)
- Added device configuration support
- Added SPI HAT configuration (MeshAdv-Mini)
- Improved hardware detection

### v1.1.0 (2025-12-01)
- Added modem preset configuration
- Added channel slot configuration
- Added module configuration

### v1.0.0 (2025-11-15)
- Initial release
- Basic installation support
- LoRa configuration
- Hardware detection

## License

GPL-3.0 (inherited from meshtastic/python)

## Contributing

Contributions welcome! Please open an issue or PR.

## Community Resources

### Official Meshtastic
- [Meshtastic Documentation](https://meshtastic.org/docs/)
- [Meshtastic Python Library](https://github.com/meshtastic/python)
- [Linux Native Hardware Guide](https://meshtastic.org/docs/hardware/devices/linux-native-hardware/)
- [LoRa Configuration](https://meshtastic.org/docs/configuration/radio/lora/)
- [Module Configuration](https://meshtastic.org/docs/configuration/module/)

### MtnMesh Community (Mountain Mesh)
- [MtnMe.sh](https://mtnme.sh/) - Community guides and resources
- [MediumFast Migration Guide](https://mtnme.sh/mediumfast/)
- [MeshToad Device Info](https://mtnme.sh/devices/MeshToad/)
- [Configuration Best Practices](https://mtnme.sh/config/)

### Hardware
- [MeshAdv-Pi-Hat](https://github.com/chrismyers2000/MeshAdv-Pi-Hat) - 1W High-Power LoRa/GPS Pi HAT by Chris Myers
- [FemtoFox](https://github.com/femtofox) - Linux-based Meshtastic node with Foxbuntu OS ([Getting Started](https://github.com/femtofox/femtofox/wiki/Getting-Started))

### Other Tools
- [Meshtasticd Configuration Tool](https://github.com/chrismyers2000/Meshtasticd-Configuration-Tool) by Chris Myers

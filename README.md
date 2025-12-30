# Meshtasticd Interactive Installer & Manager

An interactive installer, updater, and comprehensive configuration tool for meshtasticd on Raspberry Pi OS.

**Version 2.0.0** | [Changelog](#version-history)

## What's New in v2.0.0

- **Quick Status Dashboard** - Real-time monitoring of service, system health, and network status
- **Interactive Channel Presets** - Pre-configured channel setups for common use cases
- **Automatic Update Notifications** - Get notified when new versions are available
- **Configuration Templates** - Ready-to-use templates for Emergency/SAR, Urban, MtnMesh, and Repeater setups
- **Version Control** - Track installer version history and changes

## Features

### Installation & Management
- **Interactive Installation**: Guided setup for meshtasticd daemon
- **Version Management**: Install/update stable, beta, daily, or alpha builds
- **Official Repositories**: Uses OpenSUSE Build Service for latest builds
- **OS Detection**: Automatic detection of 32-bit/64-bit Raspberry Pi OS
- **Dependency Management**: Automatically fix deprecated dependencies
- **Error Handling**: Comprehensive debugging and troubleshooting tools
- **Automatic Update Notifications**: Get notified when updates are available

#### Available Build Channels
- **stable/beta** - Latest stable releases from `network:Meshtastic:beta` (recommended)
- **daily** - Cutting-edge daily builds from `network:Meshtastic:daily`
- **alpha** - Experimental alpha builds from `network:Meshtastic:alpha`

### Quick Status Dashboard
Real-time monitoring at a glance:
- **Service Status**: Running/stopped state with uptime information
- **System Health**: CPU temperature, memory usage, disk space
- **Network Status**: IP address, internet connectivity
- **Configuration Status**: Active config file and template
- **Quick Actions**: Refresh, view logs, restart service, check updates

### Channel Presets (New in v2.0)
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
- **MtnMesh Community** (New)
- **Emergency/SAR** (New)
- **Urban High-Speed** (New)
- **Repeater Node** (New)

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

- Raspberry Pi OS (32-bit armhf)
- Raspberry Pi OS (64-bit arm64)
- Raspbian Bookworm and newer

## Supported Hardware

### Raspberry Pi Models
- Raspberry Pi Zero 2W, 3, 4, Pi 400, Pi 5

### USB LoRa Modules
- **MeshToad** (CH341, 1W, 900mA peak) - MtnMesh device
- **MeshTadpole** (CH341 variant)
- **MeshStick** (Official Meshtastic device)
- CH340/CH341-based modules
- CP2102-based modules (Silicon Labs)
- FT232-based modules (FTDI)

### SPI LoRa HATs
- MeshAdv-Mini
- MeshAdv-Pi v1.1
- Adafruit RFM9x
- Elecrow LoRa RFM95
- Waveshare SX126X
- PiTx LoRa

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
- Install Python dependencies
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
sudo apt-get install -y python3 python3-pip git

# 2. Clone repository
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI

# 3. Install Python dependencies
sudo python3 -m pip install -r requirements.txt

# 4. Run installer
sudo python3 src/main.py
```

## Usage

### Interactive Mode
```bash
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

```
Main Menu:
1. Quick Status Dashboard     <- NEW: Real-time system monitoring
2. Install meshtasticd
3. Update meshtasticd
4. Configure device
5. Channel Presets            <- NEW: Pre-configured channel setups
6. Configuration Templates    <- NEW: Ready-to-use config files
7. Check dependencies
8. Hardware detection
9. Debug & troubleshooting
0. Exit
```

## Quick Start Examples

### View Status Dashboard
```bash
sudo python3 src/main.py
# Select option 1 (Quick Status Dashboard)
```

### Apply Channel Preset
```bash
sudo python3 src/main.py
# Select option 5 (Channel Presets)
# Choose a preset (e.g., MtnMesh Community)
```

### Apply Configuration Template
```bash
sudo python3 src/main.py
# Select option 6 (Configuration Templates)
# Choose a template (e.g., Emergency/SAR)
```

### Complete Radio Setup (Recommended)
```bash
sudo python3 src/main.py
# Select option 4 (Configure device)
# Select option 1 (Complete Radio Setup)
# Choose MediumFast preset
# Use slot 20 for compatibility with MtnMesh community
```

### Configure MQTT Bridge
```bash
sudo python3 src/main.py
# Select option 4 (Configure device)
# Select option 5 (Module Configuration)
# Select option 1 (MQTT Module)
```

## Requirements

- Python 3.7+
- Root/sudo access (for GPIO, SPI, and system package management)
- Internet connection (for downloading packages)

## Project Structure

```
Meshtasticd_interactive_UI/
├── src/
│   ├── main.py                    # Main entry point
│   ├── __version__.py             # Version control (NEW)
│   ├── dashboard.py               # Quick Status Dashboard (NEW)
│   ├── installer/
│   │   ├── __init__.py
│   │   ├── meshtasticd.py        # Meshtasticd installation logic
│   │   ├── dependencies.py        # Dependency management
│   │   ├── version.py             # Version management
│   │   └── update_notifier.py     # Update notifications (NEW)
│   ├── config/
│   │   ├── __init__.py
│   │   ├── lora.py               # LoRa configuration
│   │   ├── radio.py              # Radio configuration
│   │   ├── device.py             # Device configuration
│   │   ├── hardware.py           # Hardware detection
│   │   ├── modules.py            # Module configuration
│   │   ├── spi_hats.py           # SPI HAT configuration
│   │   └── channel_presets.py    # Channel presets (NEW)
│   └── utils/
│       ├── __init__.py
│       ├── system.py             # System utilities
│       ├── logger.py             # Logging and debugging
│       └── cli.py                # CLI interface
├── templates/
│   ├── config.yaml               # Main config template
│   └── available.d/
│       ├── meshadv-mini.yaml     # MeshAdv-Mini template
│       ├── meshadv-mini-400mhz.yaml
│       ├── waveshare-sx1262.yaml
│       ├── adafruit-rfm9x.yaml
│       ├── mtnmesh-community.yaml # MtnMesh template (NEW)
│       ├── emergency-sar.yaml     # Emergency/SAR template (NEW)
│       ├── urban-highspeed.yaml   # Urban template (NEW)
│       └── repeater-node.yaml     # Repeater template (NEW)
├── scripts/
│   ├── install_armhf.sh          # 32-bit installation script
│   ├── install_arm64.sh          # 64-bit installation script
│   └── setup_permissions.sh      # GPIO/SPI permissions setup
├── tests/
├── docs/
├── requirements.txt
└── README.md
```

## Version History

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

### Other Tools
- [Meshtasticd Configuration Tool](https://github.com/chrismyers2000/Meshtasticd-Configuration-Tool) by Chris Myers

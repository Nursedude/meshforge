# Meshtasticd Interactive Installer & Manager

An interactive installer, updater, and comprehensive configuration tool for meshtasticd on Raspberry Pi OS.

## Features

### Installation & Management
- **Interactive Installation**: Guided setup for meshtasticd daemon
- **Version Management**: Install/update stable or beta versions
- **OS Detection**: Automatic detection of 32-bit/64-bit Raspberry Pi OS
- **Dependency Management**: Automatically fix deprecated dependencies
- **Error Handling**: Comprehensive debugging and troubleshooting tools

### Hardware Support
- **Hardware Detection**: Auto-detect USB and SPI LoRa modules
- **MeshToad/MeshTadpole Support**: Specialized detection for MtnMesh devices
- **Power Warnings**: Alerts for high-power modules (900mA+ devices)

### Radio Configuration
- **Modem Presets**: All official Meshtastic presets
  - **MediumFast** ⭐ (MtnMesh community standard, Oct 2025)
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
- **MeshToad** (CH341, 1W, 900mA peak) ⭐ MtnMesh device
- **MeshTadpole** (CH341 variant)
- **MeshStick** (Official Meshtastic device)
- CH340/CH341-based modules
- CP2102-based modules (Silicon Labs)
- FT232-based modules (FTDI)

### SPI LoRa HATs
- **MeshAdv-Mini** - LoRa/GPS HAT with SX1262/SX1268, +22dBm, GPS, Temp Sensor ([GitHub](https://github.com/chrismyers2000/MeshAdv-Mini))
- MeshAdv-Pi v1.1
- Adafruit RFM9x
- Elecrow LoRa RFM95
- Waveshare SX126X
- PiTx LoRa

## Installation

Choose your preferred installation method:

### 🚀 Quick Install (Recommended)

**One-liner installation** - Downloads, installs, and launches the interactive installer automatically:

```bash
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_IU/main/install.sh | sudo bash
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
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_IU/main/install.sh | sudo UPGRADE_SYSTEM=yes bash
```

Skip the system upgrade entirely (faster installation):
```bash
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_IU/main/install.sh | sudo SKIP_UPGRADE=yes bash
```

**To run the installer later manually:**
```bash
sudo meshtasticd-installer
```

---

### 🌐 Web-Based Installer

**Perfect for beginners** - Install through your browser:

```bash
# Clone the repository
git clone https://github.com/Nursedude/Meshtasticd_interactive_IU.git
cd Meshtasticd_interactive_IU

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

### 🐳 Docker Installation

**For containerized deployments**:

```bash
# Clone repository
git clone https://github.com/Nursedude/Meshtasticd_interactive_IU.git
cd Meshtasticd_interactive_IU

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

### 📦 Manual Installation

**For advanced users who want full control**:

```bash
# 1. Install system dependencies
sudo apt-get update
sudo apt-get install -y python3 python3-pip git

# 2. Clone repository
git clone https://github.com/Nursedude/Meshtasticd_interactive_IU.git
cd Meshtasticd_interactive_IU

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

# Check system and dependencies
sudo python3 src/main.py --check

# Debug mode
sudo python3 src/main.py --debug
```

## Requirements

- Python 3.7+
- Root/sudo access (for GPIO, SPI, and system package management)
- Internet connection (for downloading packages)

## Project Structure

```
Meshtasticd_interactive_IU/
├── src/
│   ├── main.py                 # Main entry point
│   ├── installer/
│   │   ├── __init__.py
│   │   ├── meshtasticd.py     # Meshtasticd installation logic
│   │   ├── dependencies.py     # Dependency management
│   │   └── version.py          # Version management
│   ├── config/
│   │   ├── __init__.py
│   │   ├── lora.py            # LoRa configuration
│   │   ├── device.py          # Device configuration
│   │   └── hardware.py        # Hardware detection
│   └── utils/
│       ├── __init__.py
│       ├── system.py          # System utilities
│       ├── logger.py          # Logging and debugging
│       └── cli.py             # CLI interface
├── scripts/
│   ├── install_armhf.sh       # 32-bit installation script
│   ├── install_arm64.sh       # 64-bit installation script
│   └── setup_permissions.sh   # GPIO/SPI permissions setup
├── tests/
├── docs/
├── requirements.txt
└── README.md
```

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

## Quick Start Examples

### Complete Radio Setup (Recommended)
```bash
sudo python3 src/main.py
# Select option 3 (Configure device)
# Select option 1 (Complete Radio Setup)
# Choose MediumFast preset
# Use slot 20 for compatibility with MtnMesh community
```

### Install with Beta Version
```bash
sudo python3 src/main.py --install beta
```

### Configure MQTT Bridge
```bash
sudo python3 src/main.py
# Select option 3 (Configure device)
# Select option 4 (Module Configuration)
# Select option 1 (MQTT Module)
```

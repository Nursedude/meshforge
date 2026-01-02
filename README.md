# Meshtasticd Interactive Installer & Manager

An interactive installer, updater, and comprehensive configuration tool for meshtasticd on Raspberry Pi OS and compatible Linux systems.

**Version 3.2.7** | [Changelog](#version-history)

## What's New in v3.2.7

### Web UI - Browser-Based Interface
Access meshtasticd manager from any browser on your network!

- **Dashboard** - Service status, CPU, Memory, Disk, Temperature with live updates
- **Service Control** - Start/Stop/Restart meshtasticd from browser
- **Config Management** - Activate/deactivate YAML configurations
- **Hardware Detection** - View SPI, I2C, and connected devices
- **Radio Info** - See connected radio details (firmware, hardware, region)
- **System Monitor** - View top processes and system stats
- **Optional Authentication** - Password protection with `--password` flag
- **Custom Port** - Use `--port` to change from default 8080

```bash
# Start web UI (default port 8080)
sudo python3 src/main_web.py

# Custom port
sudo python3 src/main_web.py --port 9000

# With password protection
sudo python3 src/main_web.py --password mysecretpassword

# Access from browser
# http://localhost:8080/
# http://your-pi-ip:8080/
```

**Security Note:** For remote access over the internet, use a VPN (WireGuard, Tailscale)
or SSH tunnel rather than exposing the port directly.

## What's New in v3.2.6

### System Monitor & Daemon Control
- **System Monitor** - Live CPU, Memory, Disk, Temperature with progress bars
- **htop Integration** - Open htop in terminal, Show Processes button
- **Daemon Control** - `--status` and `--stop` commands for GTK app
- **Better Service Detection** - Uses systemctl, pgrep, and TCP port checks
- **No Fork Warning** - Daemon mode uses subprocess instead of fork()

```bash
# Start daemon
sudo python3 src/main_gtk.py -d

# Check status
python3 src/main_gtk.py --status

# Stop daemon
sudo python3 src/main_gtk.py --stop
```

## What's New in v3.2.5

### GTK App Stability & Keyboard Shortcuts
- **Escape Key** - Exit fullscreen mode
- **F11** - Toggle fullscreen mode
- **Ctrl+Q** - Quit application
- **No More Connection Spam** - Removed "Connection reset by peer" error messages
- **Better Error Handling** - Radio Config shows clear messages when meshtasticd not running

## What's New in v3.2.4

### Radio Configuration Improvements (GTK UI)
- **Connected Radio Info** - Shows Node ID, Name, Hardware, Firmware, Region, Modem Preset
- **Daemon Mode** - Run GTK app in background with `--daemon` or `-d` flag
- **MQTT Settings Display** - Shows server, username, encryption, JSON, TLS status
- **JSON Parsing Fixes** - Firmware/hardware fields now extract values correctly

```bash
# Run GTK app in background (returns terminal control)
sudo python3 src/main_gtk.py --daemon

# Or use short flag
sudo python3 src/main_gtk.py -d
```

## What's New in v3.2.3

### Node Monitoring Module (No Sudo Required!)
- **Standalone Monitor** - `python3 -m src.monitor` works without root
- **Interactive Setup** - Configure host/port with `--setup` flag
- **Watch Mode** - Continuous monitoring with `--watch`
- **JSON Output** - Machine-readable output with `--json` for scripting
- **Config Persistence** - Saves settings to `~/.config/meshtastic-monitor/`
- **Fast Failure** - Socket pre-check for quick connection error detection

```bash
# Quick start
python3 -m src.monitor --setup      # Configure host interactively
python3 -m src.monitor              # View nodes (uses saved config)
python3 -m src.monitor --watch      # Continuous monitoring
python3 -m src.monitor --json       # JSON output for scripts
```

### Developer Documentation
- **DEVELOPMENT.md** - Critical patterns and methods for contributors
  - Textual @work decorator rules
  - GTK4 GLib.idle_add threading pattern
  - Meshtastic CLI integration patterns
  - Common pitfalls and solutions

## What's New in v3.2.2

### Radio Configuration Panel (GTK UI)
- **Full Radio Settings** - Configure all radio parameters from the GUI
- **Real-time Node Count** - Status bar shows connected nodes
- **Position Settings** - Set fixed position with `--setlat/--setlon` format
- **Auto-load Config** - Current settings load when panel opens

### Bug Fixes
- GTK status bar now shows nodes and uptime correctly
- Dashboard config count checks both `.yaml` and `.yml` files
- Hardware detection Enable buttons work with sudo

## What's New in v3.2.1

### Hardware Configuration (Main Menu → `w`)
- **SPI Configuration** - Enable/disable SPI interface via raspi-config
- **I2C Configuration** - Enable/disable I2C interface
- **Serial Port Setup** - Configure UART for GPS modules and serial devices
- **SPI Overlay Management** - Add dtoverlay=spi0-0cs for LoRa HATs
- **Hardware Detection** - Detect SPI, I2C, Serial, and GPIO devices
- **Device Selection** - Select and configure known Meshtastic hardware:
  - LoRa 900MHz 30dBm/22dBm SX1262 modules
  - LoRa 868MHz EU modules
  - Waveshare displays (1.44", 2.8")
  - I2C OLED displays
  - Serial GPS modules
- **Config File Copy** - Copy configs from available.d to config.d
- **YAML Config Editor** - Edit config files with validation
- **Safe Reboot** - Checks for running applications before rebooting:
  - Detects open editors (nano, vim, emacs)
  - Shows active SSH sessions
  - Checks for apt/dpkg locks
  - Graceful meshtasticd shutdown
  - Countdown with cancel option

### Channel Configuration Enhancements
- **Full Channel Add/Modify** - Complete channel editor with all settings
- **PSK Key Management** - Generate/enter 256-bit, 128-bit, or custom keys
- **MQTT Per-Channel** - Configure uplink/downlink for each channel
- **Position Precision** - Set location sharing accuracy per channel
- **Existing Channel Detection** - Pre-fills values when editing

## What's New in v3.2.0

### Network Tools (Main Menu → `n`)
- **Ping Test** - ICMP connectivity testing
- **TCP Port Test** - Check if ports are open
- **Meshtastic TCP Test** - Test port 4403 connectivity
- **Network Interfaces** - View interface configuration
- **Routing Table** - Display network routes
- **DNS Lookup** - Resolve hostnames
- **Active Connections** - Show listening ports
- **Network Scan** - Discover devices on LAN
- **Find Meshtastic Devices** - Scan for port 4403

### RF Tools (Main Menu → `r`)
- **Link Budget Calculator** - Full TX/RX analysis with EIRP, path loss, margin
- **FSPL Calculator** - Free Space Path Loss at any distance/frequency
- **Fresnel Zone Calculator** - Required clearance for reliable links
- **LoRa Preset Comparison** - All presets with data rate, sensitivity, range
- **Range Estimator** - Estimate max range for given parameters
- **Time-on-Air Calculator** - Calculate packet transmission time
- **Detect LoRa Radio** - Find SPI/GPIO radio hardware
- **SPI/GPIO Status** - Check interface availability
- **Frequency Band Reference** - Regional bands and power limits

### MUDP Tools (Main Menu → `m`)
- **Monitor UDP Traffic** - Run `mudp` CLI to watch mesh packets
- **Listen to Multicast** - Join 224.0.0.69:4403 and display packets
- **View UDP Sockets** - Show active UDP listeners
- **Send Test Packet** - Transmit test UDP datagram
- **UDP Echo Test** - Test echo server connectivity
- **Multicast Join Test** - Verify multicast group joining
- **MUDP Configuration** - View PubSub topics and send functions
- **Install/Update MUDP** - pip install mudp package

### Tool Manager (Main Menu → `g`)
- **View Installed Tools** - Status of all network/RF tools
- **Install/Update Tools** - Manage mudp, meshtastic, nmap, etc.
- **Check for Updates** - Query PyPI for latest versions
- **Install All Missing** - One-click install of all tools

### Research Documentation
- **RESEARCH.md** - Bibliography with MUDP, TCP API, RF tools references

## What's New in v3.1.0

### System Diagnostics (Main Menu → `t`)
- **Network Connectivity** - Ping tests, DNS resolution, internet/HTTPS checks
- **Mesh Network Diagnostics** - Node count, activity, API status
- **System Health** - CPU, memory, disk, temperature monitoring
- **LoRa/Radio Diagnostics** - SPI, device detection, radio status
- **GPIO/SPI/I2C Status** - Interface availability and device scanning
- **Service Diagnostics** - Installation status, logs, error detection
- **Full Diagnostic Report** - Comprehensive health score

### Site Planner (Main Menu → `p`)
- **Coverage Tools** - Links to Meshtastic Site Planner, Radio Mobile, HeyWhatsThat
- **Link Budget Calculator** - Calculate theoretical range with custom parameters
- **Preset Range Estimates** - Expected ranges for all modem presets
- **Antenna Guidelines** - Selection guide with gain and mounting tips
- **Frequency/Power Reference** - Regional bands, power limits, conversions

## What's New in v3.0.6

- **Fixed Meshtastic CLI Detection** - Now properly finds CLI installed via pipx

## What's New in v3.0.4

- **Uninstaller** - Remove meshtasticd and components interactively (Main Menu → `u`)
- **Progress Indicators** - Visual progress bars during installations
- **Launcher Saves Preference** - Save your preferred UI and auto-launch next time
  - Use `--wizard` flag to force wizard and change preference
- **Edit Existing Channels** - Select a channel and modify with pre-filled values
- **Consistent Navigation** - `m` = Main Menu everywhere, `0` = Back

## What's New in v3.0.2

- **Enhanced Channel Configuration** - Full interactive setup with Role, PSK, and MQTT settings
- **PSK Key Generation** - Generate secure 256-bit or 128-bit encryption keys
- **MQTT Channel Settings** - Configure uplink/downlink per channel
- **Auto-Install Meshtastic CLI** - Automatically installs via pipx when needed
- **Device Configuration Saves** - All settings apply directly to your LoRa device
- **Existing Channel Detection** - Reads current config from device before editing

## What's New in v3.0.1

- **Launcher Wizard** - Interactive wizard to select your preferred interface
- **Fixed Log Following** - Logs now update properly in GTK4 and TUI
- **Improved Navigation** - All menus have Back (0) and Main Menu (m) options

## What's New in v3.0.0

- **GTK4 Graphical Interface** - Modern desktop UI with libadwaita design
- **Textual TUI** - Full-featured terminal UI for SSH/headless access (Raspberry Pi Connect friendly)
- **Config File Manager** - Select YAML from `/etc/meshtasticd/available.d`, edit with nano
- **Service Management** - Start/stop/restart with live log viewing
- **Meshtastic CLI Integration** - Run CLI commands from the UI
- **Reboot Persistence** - Installer auto-restarts after system reboot
- **Four UI Options** - Choose Web, GTK4, Textual TUI, or Rich CLI based on your setup

## UI Options

| Interface | Command | Best For |
|-----------|---------|----------|
| **Web UI** | `sudo python3 src/main_web.py` | Remote access via browser, any device on network |
| **Wizard** | `sudo meshtasticd-installer` | Auto-detect and select best interface |
| **GTK4 GUI** | `sudo python3 src/main_gtk.py` | Pi with display, VNC, Raspberry Pi Connect desktop |
| **GTK4 GUI (Background)** | `sudo python3 src/main_gtk.py -d` | Same as above, but returns terminal control |
| **Textual TUI** | `sudo python3 src/main_tui.py` | SSH, headless, Raspberry Pi Connect terminal |
| **Rich CLI** | `sudo meshtasticd-cli` | Fallback, minimal environments |
| **Node Monitor** | `python3 -m src.monitor` | Lightweight monitoring (no sudo required) |

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
- **Long Range** - Maximum distance configuration
- **Repeater/Router** - Infrastructure node setup

### Configuration Templates
Ready-to-use hardware and use-case templates:
- MeshAdv-Mini (SX1262/SX1268 HAT)
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
- **Modem Presets**: All official Meshtastic presets (ShortTurbo, ShortFast, MediumFast, LongFast, etc.)
- **Channel Slot Configuration**: Interactive slot selection
- **Region Selection**: All supported regulatory regions
- **TX Power Configuration**: 0-30 dBm with device-specific recommendations

## Supported Platforms

- **Raspberry Pi OS** (32-bit armhf, 64-bit arm64)
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
- Raspberry Pi 5, 4, 3, 2, Zero 2 W, Zero W, Zero, 400

### USB LoRa Modules
- MeshToad, MeshTadpole, MeshStick, CH340/CH341, CP2102, FT232

### SPI LoRa HATs
- MeshAdv-Mini, MeshAdv-Pi-Hat, Waveshare SX126X, Adafruit RFM9x

## Installation

### Quick Install (Recommended)

```bash
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo bash
```

### UI-Specific Dependencies

**For GTK4 Graphical Interface:**
```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1
```

**For Textual TUI:**
```bash
# On Raspberry Pi / Debian (outside virtual environment):
sudo pip install --break-system-packages --ignore-installed textual

# In a virtual environment or on other systems:
pip install textual
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

```bash
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Choose your UI:
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
t. System Diagnostics
p. Site Planner
n. Network Tools
r. RF Tools
m. MUDP Tools
g. Tool Manager
9. Check dependencies
h. Hardware detection
w. Hardware Configuration (SPI, Serial, GPIO)
d. Debug & troubleshooting
u. Uninstall
q. Exit
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
│   ├── main_gtk.py                # GTK4 GUI entry point
│   ├── main_tui.py                # Textual TUI entry point
│   ├── monitor.py                 # Node monitor entry point (no sudo)
│   ├── launcher.py                # Interface wizard
│   ├── __version__.py             # Version control
│   ├── dashboard.py               # Quick Status Dashboard
│   ├── monitoring/                # Node monitoring module (NEW in v3.2.3)
│   │   ├── __init__.py            # Module exports
│   │   └── node_monitor.py        # NodeMonitor class (TCP interface)
│   ├── gtk_ui/                    # GTK4 interface
│   │   ├── app.py                 # Main GTK4 application
│   │   └── panels/                # UI panels
│   │       ├── dashboard.py       # Dashboard panel
│   │       ├── service.py         # Service management
│   │       ├── config.py          # Config file manager
│   │       ├── cli.py             # Meshtastic CLI
│   │       ├── install.py         # Install/update
│   │       ├── radio_config.py    # Radio configuration (NEW in v3.2.2)
│   │       └── hardware.py        # Hardware detection
│   ├── tui/                       # Textual TUI
│   │   └── app.py                 # Textual application
│   ├── installer/
│   │   ├── meshtasticd.py         # Meshtasticd installation logic
│   │   ├── dependencies.py        # Dependency management
│   │   ├── version.py             # Version management
│   │   └── update_notifier.py     # Update notifications
│   ├── config/
│   │   ├── config_file_manager.py # YAML selector + nano
│   │   ├── lora.py                # LoRa configuration
│   │   ├── radio.py               # Radio configuration
│   │   ├── device.py              # Device configuration
│   │   ├── hardware.py            # Hardware detection
│   │   ├── hardware_config.py     # SPI/Serial/GPIO config (NEW)
│   │   ├── modules.py             # Module configuration
│   │   ├── spi_hats.py            # SPI HAT configuration
│   │   ├── yaml_editor.py         # YAML editor
│   │   └── channel_presets.py     # Channel presets
│   ├── tools/                     # System tools (NEW)
│   │   ├── network_tools.py       # TCP/IP, ping, scanning
│   │   ├── rf_tools.py            # Link budget, LoRa analysis
│   │   ├── mudp_tools.py          # UDP, multicast, MUDP
│   │   └── tool_manager.py        # Tool install/update
│   ├── services/                  # Service management
│   │   └── service_manager.py     # Systemd controls
│   ├── cli/                       # Meshtastic CLI wrapper
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

### v3.2.5 (2026-01-02)
- **Keyboard Shortcuts** - Escape exits fullscreen, F11 toggles fullscreen, Ctrl+Q quits
- **FIX**: Connection error spam removed from GTK app
- **FIX**: Radio Config checks meshtasticd connectivity before loading
- **FIX**: Better error messages when service not running
- **FIX**: SUDO_USER path detection for meshtastic CLI

### v3.2.4 (2026-01-02)
- **Connected Radio Info** - New section showing node details in Radio Config panel
- **Daemon Mode** - `--daemon`/`-d` flag for GTK app to run in background
- **MQTT Settings** - Parses and displays server, username, encryption status
- **FIX**: Radio info JSON parsing extracts firmware/hardware correctly
- **FIX**: Firmware field no longer shows entire JSON object
- **FIX**: Rebroadcast mode parsing in config loader

### v3.2.3 (2026-01-02)
- **Node Monitoring Module** - Sudo-free monitoring via TCP interface
- **Monitor Entry Point** - `python3 -m src.monitor` works without root
- **Interactive Setup** - Configure host/port with `--setup` flag
- **Watch Mode** - Continuous monitoring with `--watch`
- **JSON Output** - Machine-readable output with `--json`
- **Config Persistence** - Saves to `~/.config/meshtastic-monitor/`
- **DEVELOPMENT.md** - Critical patterns for contributors
- **FIX**: TUI @work decorator errors
- **FIX**: Fast connection failure detection

### v3.2.2 (2026-01-02)
- **Radio Configuration Panel** - Full radio settings in GTK UI
- **Real-time Node Count** - Status bar shows connected nodes
- **FIX**: GTK status bar nodes and uptime
- **FIX**: Dashboard config detection (.yaml/.yml)

### v3.2.1 (2026-01-01)
- **Hardware Configuration** - SPI, I2C, Serial, GPIO setup
- **Safe Reboot** - Checks for running processes before reboot
- **Device Selection** - Configure known Meshtastic hardware

### v3.2.0 (2026-01-01)
- **Network Tools** - TCP/IP diagnostics, ping, port scanning
- **RF Tools** - Link budget, FSPL, Fresnel zone calculators
- **MUDP Tools** - Meshtastic UDP monitoring
- **Tool Manager** - Install/update all tools

### v3.0.3 (2025-12-31)
- **Edit Existing Channels** - Modify channels with pre-filled current values
- **Consistent Navigation** - `m` = Main Menu, `0` = Back in all menus
- **Better Emoji Support** - Improved detection for SSH and RPi terminals
- **Region Selection** - Now includes back/menu navigation options

### v3.0.2 (2025-12-31)
- **Enhanced Channel Configuration** - Full interactive setup with Role, PSK, and MQTT
- **PSK Key Generation** - Generate secure 256-bit or 128-bit encryption keys
- **MQTT Channel Settings** - Configure uplink/downlink per channel
- **Auto-Install Meshtastic CLI** - Automatically installs via pipx when needed
- **Device Configuration Saves** - All settings apply directly to LoRa device
- **Existing Channel Detection** - Reads current config from device before editing
- **Modem Presets Apply** - Selected presets now save to device

### v3.0.1 (2025-12-30)
- **Launcher Wizard** - Interactive wizard to select your preferred interface
- **Fixed Log Following** - Logs now update properly in GTK4 and TUI
- **Improved Navigation** - All menus have Back (0) and Main Menu (m) options
- **Better Shortcuts** - Logical keyboard shortcuts (q=quit, ?=help)
- **RPi Compatibility** - Proper pip install with --break-system-packages

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

### v1.x
- Initial release with basic installation and configuration

## License

GPL-3.0

## Community Resources

- [Meshtastic Documentation](https://meshtastic.org/docs/)
- [MtnMesh Community](https://mtnme.sh/)
- [MeshAdv-Pi-Hat](https://github.com/chrismyers2000/MeshAdv-Pi-Hat)

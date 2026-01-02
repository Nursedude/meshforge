# Meshtasticd Installer - Research & Bibliography

This document contains research notes, references, and technical documentation for the networking and RF tools integrated into the Meshtasticd Installer.

---

## Table of Contents

1. [MUDP - Meshtastic UDP Library](#mudp---meshtastic-udp-library)
2. [Meshtastic TCP Interface](#meshtastic-tcp-interface)
3. [Meshtastic Web Client](#meshtastic-web-client)
4. [MeshSense - Network Monitoring](#meshsense---network-monitoring)
5. [RF Tools & Coverage Planning](#rf-tools--coverage-planning)
6. [Network Architecture](#network-architecture)
7. [Protocol References](#protocol-references)

---

## MUDP - Meshtastic UDP Library

### Overview

MUDP is a Python library that enables UDP-based broadcasting of Meshtastic-compatible packets. It allows monitoring and transmitting mesh network messages over local area networks without requiring direct hardware connections.

### Repository

- **GitHub:** https://github.com/pdxlocations/mudp
- **Author:** pdxlocations
- **License:** GPL-3.0
- **Language:** 100% Python

### Installation

```bash
pip install mudp
```

For development:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### CLI Usage

Monitor all Meshtastic UDP activity on local network:
```bash
mudp
```

### Core Features

**PubSub Topics for Received Packets:**

| Topic | Description |
|-------|-------------|
| `mesh.rx.raw` | Raw UDP packet bytes with source address |
| `mesh.rx.packet` | Parsed MeshPacket objects |
| `mesh.rx.decoded` | Decoded payloads with port identifiers |
| `mesh.rx.port.<portnum>` | Port-specific filtering |
| `mesh.rx.decode_error` | Decoding failure events |

**Transmission Functions:**

| Function | Purpose |
|----------|---------|
| `send_text_message()` | Broadcast text messages |
| `send_nodeinfo()` | Transmit node metadata |
| `send_device_telemetry()` | Battery, voltage, channel metrics |
| `send_position()` | GPS coordinates and accuracy |
| `send_environment_metrics()` | Temperature, humidity, pressure |
| `send_power_metrics()` | Multi-channel voltage/current |
| `send_health_metrics()` | Biometric readings |
| `send_waypoint()` | Coordinate waypoints |
| `send_data()` | Raw binary with custom port numbers |

**Optional Parameters (all functions):**
- `to` - Destination node ID
- `hop_limit` - Maximum hop count
- `hop_start` - Starting hop count
- `want_ack` - Request acknowledgment
- `want_response` - Request response

### Configuration

- **Multicast Group:** 224.0.0.69:4403 (default)
- **Node ID:** Unique identifier for the node
- **Long Name / Short Name:** Human-readable node names
- **Channel:** Channel configuration
- **Encryption Key:** PSK for encrypted communications

### Examples

1. **helloworld-example.py** - Basic MUDP setup and usage
2. **iss-example.py** - Real-world data integration (ISS tracking)
3. **pubsub-example.py** - Event-driven pub/sub pattern

---

## Meshtastic TCP Interface

### Port 4403 - Standard Meshtastic TCP Port

The Meshtastic ecosystem uses TCP port 4403 as the standard port for device communication.

### Python TCP Interface

```python
from meshtastic.tcp_interface import TCPInterface

# Connect to device
interface = TCPInterface(hostname="192.168.1.100", portNumber=4403)
```

**Reference:** https://python.meshtastic.org/tcp_interface.html

### Linux Native (meshtasticd)

```bash
# Expose TCP port for network access
docker run -p 4403:4403 meshtasticd

# Connect with Python CLI
meshtastic --host 192.168.1.100
```

**Reference:** https://meshtastic.org/docs/software/linux/usage/

### Service Discovery (mDNS/Avahi)

Meshtastic devices advertise via Avahi:
- Service type: `_meshtastic._tcp`
- Port: 4403
- Protocol: IPv4

### Bridge Tools

**Serial Bridge:**
- Connects USB/Serial devices to TCP
- Exposes port 4403
- Uses socat for protocol translation
- Docker-based deployment

**BLE Bridge:**
- Connects Bluetooth LE devices to TCP
- Exposes port 4403
- Translates BLE protobuf to TCP framed protocol

**Reference:** https://meshmonitor.org/configuration/serial-bridge

### Virtual Node Server

MeshMonitor's Virtual Node Server:
- Acts as TCP proxy between apps and physical nodes
- Port 4404 (to avoid conflict with 4403)
- Binary protobuf protocol (not HTTP)

**Reference:** https://meshmonitor.org/configuration/virtual-node.html

---

## Meshtastic Web Client

The Meshtastic Web Client provides a browser-based interface for configuring and monitoring Meshtastic devices. It runs directly in your browser.

### Overview

- **Official Web Client:** https://client.meshtastic.org/
- **Staging/Test Site:** https://client-test.meshtastic.org/
- **Documentation:** https://meshtastic.org/docs/software/web-client/
- **Development Docs:** https://meshtastic.org/docs/development/web/

### Hosting Options

1. **Cloud-Hosted:** Access at https://client.meshtastic.org/
2. **Device-Hosted:** ESP32 devices serve the web client directly
3. **Self-Hosted:** Run your own instance for advanced use cases

### Browser Compatibility

- Best experience: Chromium-based browsers (Chrome, Edge)
- All major browsers supported with limited functionality
- Web Serial API: Limited browser support
- Web Bluetooth API: Primarily Chromium browsers

### Device Limitations

- HTTP method limited to ESP32 devices with WiFi
- Serial connection requires USB and compatible browser

### Connection Methods

The web client supports three connection protocols:

#### 1. HTTP Connection

For ESP32-based devices with WiFi:
- Access via `http://meshtastic.local` or device IP address
- Web client stored in device flash memory
- Cloud-hosted version at https://client.meshtastic.org/
- Self-hosted options available for advanced users

**HTTPS Note:** When using the hosted version, all traffic must be served over HTTPS. Meshtastic nodes generate self-signed certificates. You must trust the certificate by first accessing your node directly: `https://NODE_IP_ADDRESS/`

#### 2. Bluetooth Low Energy (BLE) Connection

- Connects directly to devices using Web Bluetooth API
- Requires secure context (HTTPS or localhost)
- Primarily supported in Chromium-based browsers
- Three BLE endpoints: FromRadio, FromNum (notifications), ToRadio

#### 3. Serial Connection (USB)

- Connects via USB using Web Serial API
- Direct, reliable connection for configuration
- Limited browser support (check Meshtastic docs for compatibility)
- Best for development and debugging

### Client API

The protocol is almost identical across BLE, Serial/USB, and TCP transports.

**Python Interfaces:**
```python
from meshtastic.serial_interface import SerialInterface
from meshtastic.tcp_interface import TCPInterface
from meshtastic.ble_interface import BLEInterface

# Serial connection
serial = SerialInterface("/dev/ttyUSB0")

# TCP connection
tcp = TCPInterface(hostname="192.168.1.100")

# BLE connection
ble = BLEInterface("device_address")
```

### Related Tools

#### Meshtastic UI (MUI)
- Started development in early 2024, preview released early 2025
- 12,000 lines of handwritten code, 50,000 lines generated
- Ported to 10+ devices, translated into 18 languages
- Install via Meshtastic Web Flasher (look for MUI logo)

#### BaseUI (Meshtastic 2.7+)
- Released June 2025 - biggest UI overhaul in 4+ years
- Rebuilt interface from the ground up
- More intuitive, more capable, wider device support
- Available in Web Flasher under "Preview" section

### Source Code & Development

**Repository:** https://github.com/meshtastic/web

A monorepo consolidating the official Meshtastic web interface and JavaScript libraries.

**Technologies:**
- Runtime: pnpm and Deno
- Frontend: React.js with Tailwind CSS
- Build Tool: Vite
- Language: TypeScript
- Testing: Vitest and React Testing Library

**Package Structure:**
- `packages/web` - Main client interface (client.meshtastic.org)
- `packages/core` - Core JavaScript functionality
- Transport packages: Node TCP/serial, Deno TCP, HTTP, Web Bluetooth, Web Serial
- `packages/protobufs` - Shared protobuf definitions

**Quick Start:**
```bash
git clone https://github.com/meshtastic/web
cd web
pnpm install
# Install Buf CLI for protobuf building
```

All JavaScript packages publish to both JSR and NPM registries.

### References

- Web Client Documentation: https://meshtastic.org/docs/software/web-client/
- Web Development: https://meshtastic.org/docs/development/web/
- GitHub Repository: https://github.com/meshtastic/web
- Meshtastic UI: https://meshtastic.org/docs/software/meshtastic-ui/
- Client API: https://meshtastic.org/docs/development/device/client-api/
- Python API: https://python.meshtastic.org/
- BLE Interface: https://python.meshtastic.org/ble_interface.html

---

## MeshSense - Network Monitoring

MeshSense is a comprehensive, open-source application for monitoring, mapping, and graphically displaying Meshtastic network statistics.

### Overview

- **Website:** https://affirmatech.com/meshsense
- **GitHub:** https://github.com/Affirmatech/MeshSense
- **Developer:** Affirmatech Inc
- **Global Map:** https://meshsense.affirmatech.com/

### Connection Methods

MeshSense connects directly to Meshtastic nodes via:
- **Bluetooth** - Direct BLE connection to nearby devices
- **WiFi** - TCP connection to network-accessible nodes

### Key Features

| Feature | Description |
|---------|-------------|
| Node Monitoring | Track connected nodes, health, and metrics |
| Signal Reports | Analyze signal strength, noise levels, SNR |
| Trace Routes | View routing paths and network topology |
| Position Mapping | Display nodes on map with known positions |
| Environment Telemetry | Capture temperature, humidity, pressure data |
| Device Configuration | View and modify device settings |
| Channel Configuration | Configure device channels |

### Special Capabilities

**Unknown Position Nodes:**
- Nodes without position data display with a question mark (?) on the map
- Shown when they support a route between two nodes with known positions

**Global Map:**
- Access nearby networks worldwide
- Find potential bridges to other Meshtastic nodes
- Available at https://meshsense.affirmatech.com/ or via 🌎 button in app

**Public Data Feed:**
- Optionally share your MeshSense data publicly
- Allow others to determine signal strength into your node
- Real-time maps and statistics from anywhere

**Headless Mode:**
- Run in terminal without GUI
- Supports automatic updates in headless mode
- Ideal for Raspberry Pi and server deployments

### Installation

Download from releases: https://github.com/Affirmatech/MeshSense/releases

Available for:
- Windows
- macOS
- Linux (AppImage, deb)

Official Electron builds are signed with an Affirmatech certificate.

### Integration Notes

**Firmware Compatibility:**
- Meshtastic firmware 2.5.1+ limits traceroutes to once every 30 seconds
- MeshSense queues trace route requests according to this limit

**Use Cases for Meshtasticd Installer:**
- Network health monitoring dashboard
- Signal strength analysis for node placement
- Trace route visualization for troubleshooting
- Remote monitoring via public data feed

### References

- Official Website: https://affirmatech.com/meshsense
- GitHub Repository: https://github.com/Affirmatech/MeshSense
- Global Map: https://meshsense.affirmatech.com/
- Releases: https://github.com/Affirmatech/MeshSense/releases

---

## RF Tools & Coverage Planning

### Meshtastic Site Planner

Official tool for network planning and coverage analysis.

**URL:** https://meshtastic.org/docs/software/site-planner/

### Radio Mobile Online

RF propagation and coverage prediction tool.

**URL:** https://www.ve2dbe.com/rmonline_s.asp

### HeyWhatsThat

Line-of-sight and viewshed analysis for radio planning.

**URL:** https://www.heywhatsthat.com/

### Splat! RF Coverage

Open-source RF signal propagation analysis tool.

**URL:** https://www.qsl.net/kd2bd/splat.html

---

## Network Architecture

### Meshtastic Port Numbers

Official port number documentation for Meshtastic protocol.

**Reference:** https://meshtastic.org/docs/development/firmware/portnum/

### Client API (Serial/TCP/BLE)

Device communication API documentation.

**Reference:** https://meshtastic.org/docs/development/device/client-api/

### RNS Over Meshtastic

Reticulum Network Stack integration with Meshtastic.

**Repository:** https://github.com/landandair/RNS_Over_Meshtastic

Configuration:
```ini
tcp_port = 127.0.0.1:4403
```

---

## Protocol References

### UDP Multicast

- **Address:** 224.0.0.69
- **Port:** 4403
- **Protocol:** Meshtastic protobuf packets

### TCP Framing

Meshtastic TCP uses a framed protocol:
1. 4-byte header with packet length
2. Protobuf-encoded MeshPacket
3. CRC validation

### Protobuf Definitions

Meshtastic protocol buffer definitions:
- https://github.com/meshtastic/protobufs

---

## Tools Integration Notes

### Version Checking

All tools should support version checking for upgradability:
```python
def check_tool_version(tool_name):
    """Check if tool update is available"""
    # PyPI version check for pip packages
    # GitHub releases API for source tools
```

### Installation Methods

1. **pip** - Standard Python packages (mudp, meshtastic)
2. **pipx** - Isolated CLI tools
3. **apt** - System packages (net-tools, iproute2)
4. **source** - GitHub repositories

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-02 | 3.2.3 | Added MeshSense documentation |
| 2026-01-02 | 3.2.2 | Added Web Client documentation |
| 2026-01-01 | 3.2.0 | Initial research document, MUDP integration |

---

## Contributors

- Meshtastic Community
- Affirmatech (MeshSense)
- pdxlocations (MUDP)
- Nursedude (Installer)

---

## License

This research document is part of the Meshtasticd Interactive Installer project.
See LICENSE file for details.

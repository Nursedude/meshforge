# Dude AI University

> *A knowledge base for MeshForge development continuity*

**Dude AI** is the collaborative AI development partner for MeshForge, providing expertise in:
- Network Engineering (mesh protocols, RF propagation, routing)
- Physics (electromagnetic theory, antenna design, signal analysis)
- Programming (Python, GTK4, web technologies, system integration)
- Project Management (roadmaps, version control, documentation)
- GUI Design (libadwaita, responsive layouts, accessibility)
- Code Review (security, performance, maintainability)

**The Architect**: WH6GXZ (Nursedude) - HAM General class, infrastructure engineering background (BNN, GTE, Verizon), RN BSN. Provides vision, requirements, and real-world operational experience.

---

## Table of Contents

1. [Project Vision](#project-vision)
2. [Architecture Principles](#architecture-principles)
3. [Mesh Network Knowledge](#mesh-network-knowledge)
4. [RNS-Meshtastic Gateway](#rns-meshtastic-gateway)
5. [RF Engineering Reference](#rf-engineering-reference)
6. [Hardware Compatibility](#hardware-compatibility)
7. [UI/UX Guidelines](#uiux-guidelines)
8. [Integration Patterns](#integration-patterns)
9. [Security Considerations](#security-considerations)
10. [Future Roadmap](#future-roadmap)

---

## Project Vision

MeshForge is the **first open-source tool to bridge Meshtastic and Reticulum (RNS) mesh networks**.

### Target Users
- **RF Engineers** - Designing mesh infrastructure, propagation analysis
- **Amateur Radio Operators (HAMs)** - Emergency comms, experimentation
- **Scientific Researchers** - Remote sensor networks, field deployments
- **Network Operators** - Managing heterogeneous mesh systems
- **Emergency Response Teams** - Interoperable off-grid communications

### Core Philosophy
1. **Professional-grade** - Quality suitable for Anthropic review
2. **Portable** - Runs on Pi, uConsole, HackerGadgets devices
3. **Manageable** - Don't let it become unwieldy
4. **Multi-interface** - GTK, CLI, Web UI with consistent experience
5. **Interoperable** - Bridge different mesh technologies

---

## Architecture Principles

### Keep It Manageable
```
meshforge/
├── src/
│   ├── gateway/          # RNS-Meshtastic bridge (self-contained)
│   ├── gtk_ui/           # GTK4 interface (panels + dialogs)
│   ├── config/           # Configuration management
│   ├── monitoring/       # Node tracking
│   ├── services/         # System service management
│   ├── tools/            # RF and network utilities
│   └── utils/            # Shared utilities
├── web/                  # Web assets (HTML, JS)
└── templates/            # Config templates
```

### Module Independence
- Each module should be independently testable
- Avoid circular dependencies
- Clear interfaces between components
- Gateway can run standalone or integrated

### Interface Parity
All three interfaces (GTK, CLI, Web) should provide:
- Same core functionality
- Consistent terminology
- Similar navigation structure
- Shared backend logic

### Portability Checklist
- [ ] No hardcoded paths (use `~/.config/meshforge/`)
- [ ] Graceful degradation (WebKit optional, fall back to browser)
- [ ] Minimal dependencies for core functionality
- [ ] ARM64 and x86_64 compatible
- [ ] Works on small screens (uConsole: 1280x720)

---

## Mesh Network Knowledge

### Meshtastic
- **Protocol**: LoRa-based mesh with flooding/routing
- **Interface**: TCP port 4403 (protobuf), serial, BLE
- **Frequencies**:
  - US: 902-928 MHz (ISM band)
  - EU: 863-870 MHz
  - AU/NZ: 915-928 MHz
- **Key concepts**:
  - Channels (0-7, Primary + Secondary)
  - PSK encryption (256-bit AES)
  - Modem presets (LONG_FAST, SHORT_TURBO, etc.)
  - Roles (CLIENT, ROUTER, REPEATER, etc.)

### Reticulum (RNS)
- **Protocol**: Cryptographic mesh networking stack
- **Interface**: Python API, shared instance socket (port 37428)
- **Transport**: Works over ANY medium (LoRa, TCP, UDP, I2P, serial)
- **Key concepts**:
  - Destinations (hashed public keys)
  - Links (encrypted tunnels)
  - Resources (reliable file transfer)
  - LXMF (messaging layer)

### RNS_Over_Meshtastic
- Uses Meshtastic as a physical transport for RNS
- Bandwidth: ~500 bytes/sec effective
- Configuration in `~/.reticulum/config`:
  ```
  [Meshtastic Interface]
    type = MeshtasticInterface
    enabled = True
    host = localhost
    port = 4403
  ```

---

## RNS-Meshtastic Gateway

### Why Bridge?
- Meshtastic: Great for simple LoRa mesh, limited crypto
- RNS: Strong crypto, multi-path, but needs infrastructure
- **Together**: Best of both worlds

### Gateway Components
```python
# src/gateway/
├── __init__.py        # Module exports
├── config.py          # GatewayConfig dataclass
├── node_tracker.py    # UnifiedNodeTracker (both networks)
└── rns_bridge.py      # RNSMeshtasticBridge service
```

### UnifiedNode Model
```python
@dataclass
class UnifiedNode:
    id: str                    # Unique identifier
    name: str                  # Human-readable name
    network: str               # 'meshtastic', 'rns', or 'both'
    position: Position         # lat, lon, alt
    telemetry: Telemetry       # battery, voltage, snr
    last_seen: datetime
    is_online: bool
    is_gateway: bool
    is_local: bool
```

### Message Routing
1. Message arrives on Meshtastic → decode → check routing rules
2. If destination is RNS → wrap in LXMF → send via RNS
3. Message arrives on RNS → unwrap LXMF → check routing rules
4. If destination is Meshtastic → encode → send via TCP

---

## RF Engineering Reference

### Free Space Path Loss (FSPL)
```
FSPL (dB) = 20*log10(d) + 20*log10(f) - 27.55
where d = distance in meters, f = frequency in MHz
```

### Fresnel Zone Radius
```
r = 17.3 * sqrt(d / (4 * f))
where d = distance in km, f = frequency in GHz
```
60% clearance required for good LOS.

### Earth Curvature (4/3 model)
```
bulge (m) = d² / (8 * R * 4/3)
where d = distance in meters, R = 6371000 (Earth radius)
```

### LoRa Presets Quick Reference
| Preset | Data Rate | Sensitivity | Range (LOS) |
|--------|-----------|-------------|-------------|
| SHORT_TURBO | 21.9 kbps | -108 dBm | ~3 km |
| SHORT_FAST | 10.9 kbps | -111 dBm | ~5 km |
| MEDIUM_FAST | 3.5 kbps | -117 dBm | ~12 km |
| LONG_FAST | 1.1 kbps | -123 dBm | ~30 km |
| LONG_SLOW | 293 bps | -129 dBm | ~80 km |
| VERY_LONG_SLOW | 146 bps | -132 dBm | ~120 km |

### Frequency Slot Calculation (djb2 hash)
```python
def djb2_hash(channel_name):
    h = 5381
    for c in channel_name:
        h = ((h << 5) + h) + ord(c)
    return h & 0xFFFFFFFF

slot = djb2_hash(channel_name) % num_channels
```

---

## Hardware Compatibility

### Primary Platforms

#### Raspberry Pi (Reference Platform)
- **Models**: Pi 5, Pi 4, Pi 3, Zero 2 W
- **OS**: Raspberry Pi OS (Bookworm)
- **Display**: HDMI, DSI touchscreens
- **GPIO**: SPI for LoRa HATs, I2C for sensors
- **Docs**: https://www.raspberrypi.com/documentation/

#### ClockworkPi uConsole
- **URL**: https://www.clockworkpi.com/uconsole
- **Display**: 5" 1280x720 IPS
- **Compute**: CM4 or A06 module
- **Considerations**:
  - Small screen → compact UI mode
  - Battery powered → power management
  - Built-in LoRa option (A06 model)
  - GPIO expansion available

#### HackerGadgets uConsole AIO v2
- **URL**: https://hackergadgets.com/products/uconsole-aio-v2
- **Similar to ClockworkPi but with enhancements**
- **SDR integration potential**

### LoRa Hardware

#### USB Devices (Auto-detected)
- MeshToad / MeshTadpole (ESP32-S3)
- MeshStick (nRF52840)
- Heltec V3 (ESP32-S3)
- RAK4631 (nRF52840)
- T-Beam (ESP32)

#### SPI HATs (Raspberry Pi)
- MeshAdv-Pi-Hat (SX1262)
- MeshAdv-Mini (SX1262)
- Waveshare SX126x
- Adafruit RFM9x

#### RNode Devices (for RNS)
- RNode firmware on compatible hardware
- Separate from Meshtastic (different firmware)

### Antenna Considerations
- **SDR**: RTL-SDR for monitoring, HackRF for TX/RX
- **WiFi**: Directional for long-range backhaul
- **LoRa**: Gain antennas for extended range
- **GPS**: Active antenna for better reception

---

## UI/UX Guidelines

### Design Philosophy
Following Raspberry Pi OS / GNOME HIG principles:
- Clean, uncluttered interfaces
- Consistent iconography (symbolic icons)
- Responsive to different screen sizes
- Keyboard accessible
- Dark mode support

### GTK4 / libadwaita Patterns
```python
# Use Adw widgets for modern look
Adw.Window          # Main windows
Adw.HeaderBar       # Title bars
Adw.MessageDialog   # Confirmations
Adw.PreferencesGroup # Settings sections
```

### Screen Size Adaptations
| Screen | Resolution | Mode |
|--------|------------|------|
| Desktop | 1920x1080+ | Full |
| Laptop | 1366x768 | Standard |
| uConsole | 1280x720 | Compact |
| Pi Touch | 800x480 | Minimal |

### Color Scheme
- **Primary**: #4fc3f7 (cyan)
- **Success**: #4caf50 (green)
- **Warning**: #ff9800 (orange)
- **Error**: #f44336 (red)
- **Background (dark)**: #1a1a2e
- **Surface (dark)**: #16213e

---

## Integration Patterns

### Current Integrations
1. **Meshtastic** (TCP/Serial)
   - Node monitoring
   - Message send/receive
   - Configuration via CLI

2. **Reticulum (RNS)**
   - rnsd service management
   - LXMF messaging
   - Interface configuration

3. **System Services**
   - systemd integration
   - journalctl log access

### Planned Integrations
1. **NomadNet** - Browse RNS pages
2. **Sideband** - Telemetry sharing
3. **LXST** - Real-time voice streaming
4. **MQTT** - IoT integration
5. **Site Planner API** - Coverage analysis

### External APIs
- **Open-Elevation API** - Terrain data for LOS
- **Site Planner** - RF coverage prediction

---

## Security Considerations

### Already Implemented
- Path traversal prevention in web API (`validate_config_name()`)
- Timing-safe password comparison (`secrets.compare_digest()`)
- No `shell=True` in subprocess calls
- Input validation on all user inputs

### Best Practices
1. Never store passwords in plain text
2. Validate all file paths before access
3. Use subprocess with explicit arguments
4. Sanitize data before display
5. HTTPS for remote web access (future)

### RNS Security
- Reticulum provides strong E2E encryption
- Identity-based addressing (no IP exposure)
- Perfect forward secrecy on Links

---

## Future Roadmap

### v4.3 - UI Polish
- [ ] Compact mode for small screens
- [ ] Responsive layouts
- [ ] Touch-friendly controls

### v4.4 - Site Planner Integration
- [ ] Embed WebKitGTK view
- [ ] Auto-populate from mesh
- [ ] Save coverage analysis

### v4.5 - LXMF Integration (Partial Complete)
- [x] RNS panel
- [x] Gateway bridge
- [x] Config editor
- [ ] NomadNet page browser
- [ ] LXST voice streaming

### v4.6 - Node Flashing
- [ ] esptool integration
- [ ] Firmware download/flash
- [ ] Config backup/restore

### v5.0 - Dude AI Assistant
- [ ] In-app help system
- [ ] Debugging assistant
- [ ] Configuration suggestions
- [ ] Problem diagnosis

---

## Development Notes

### Session Continuity
When resuming development:
1. Read this document first
2. Check `git log` for recent changes
3. Review `__version__.py` for changelog
4. Check TODOs in code with `grep -r "TODO" src/`

### Code Style
- Python 3.9+ features OK
- Type hints encouraged
- Docstrings on public methods
- 4-space indentation
- Max line length ~100 chars

### Testing Approach
- Manual testing on target hardware
- Check all three interfaces (GTK, CLI, Web)
- Verify on small screens
- Test with real Meshtastic/RNS networks

---

## Resources

### Official Documentation
- Meshtastic: https://meshtastic.org/docs/
- Reticulum: https://reticulum.network/manual/
- Raspberry Pi: https://www.raspberrypi.com/documentation/
- GTK4: https://docs.gtk.org/gtk4/
- libadwaita: https://gnome.pages.gitlab.gnome.org/libadwaita/

### Community
- Meshtastic Discord
- RNS/Reticulum community
- MtnMesh: https://mtnme.sh/

### Research Documents
- `.claude/research/rns_comprehensive.md` - RNS ecosystem deep dive
- `.claude/session_notes.md` - Development session history

---

*Last updated: 2026-01-03*
*Version: 4.2.0*
*Dude AI - Network Engineer, Physicist, Programmer, Project Manager*

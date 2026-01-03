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
   - [Self-Healing Network Principles](#self-healing-network-principles)
4. [RNS-Meshtastic Gateway](#rns-meshtastic-gateway)
5. [RF Engineering Reference](#rf-engineering-reference)
   - [LoRa Technical Deep Dive](#lora-technical-deep-dive)
6. [Hardware Compatibility](#hardware-compatibility)
7. [UI/UX Guidelines](#uiux-guidelines)
8. [Integration Patterns](#integration-patterns)
   - [HamClock Integration](#hamclock-integration)
   - [Ham Radio Map Resources](#ham-radio-map-resources)
9. [Security Considerations](#security-considerations)
10. [Future Roadmap](#future-roadmap)
    - [ML/AI Research for Dude AI](#mlai-research-for-dude-ai)

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

### Self-Healing Network Principles

MeshForge networks should embody self-healing characteristics:

#### Core Concepts
- **Automatic Fault Detection**: Continuously monitor node health and connectivity
- **Dynamic Rerouting**: When a node fails, automatically find alternative paths
- **No Human Intervention**: Recovery happens in real-time without operator action
- **Adaptive Optimization**: Network continuously tunes itself for best performance

#### Implementation in Mesh Networks
```
┌─────────┐    X    ┌─────────┐
│ Node A  │─────────│ Node B  │  ← Link fails
└────┬────┘         └─────────┘
     │                   ▲
     │   ┌─────────┐     │
     └──►│ Node C  │─────┘       ← Auto-reroute via C
         └─────────┘
```

#### Key Technologies
1. **Slot-based protocols**: Local neighbor synchronization
2. **Hop distance calculation**: Find shortest path to gateway
3. **Digital twin simulation**: Test recovery strategies safely
4. **AI/ML prediction**: Anticipate failures before they occur

#### Design Goals for MeshForge
- [ ] Node health monitoring with predictive alerts
- [ ] Automatic path recalculation on failure
- [ ] Mesh topology visualization showing link quality
- [ ] Historical reliability metrics per node/link

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

### LoRa Technical Deep Dive

#### Spreading Factor (SF)
The spreading factor is selectable from SF5 to SF12:
- **SF7**: Shortest time on air, highest data rate, lowest sensitivity
- **SF12**: Longest time on air, lowest data rate, best sensitivity (-20 dB below noise floor)
- Each step up **doubles** the time on air for same data
- **Orthogonal**: Different SFs don't interfere on same frequency

#### Bandwidth Options
| Bandwidth | Use Case | Notes |
|-----------|----------|-------|
| 125 kHz | Standard LoRaWAN | Best sensitivity |
| 250 kHz | Faster transfer | Reduced range |
| 500 kHz | Maximum speed | Shortest range |
| 7.8-62.5 kHz | Narrow band | Extended range, special use |

#### Coding Rate (Forward Error Correction)
- CR 4/5 to 4/8 available
- Higher CR = more reliable in interference, slower data
- Does NOT increase range, only reliability

#### Sub-Noise Performance
LoRa demodulates signals **-7.5 to -20 dB below noise floor**. This is key to its long-range capability.

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
6. **HamClock** - Propagation and space weather

### HamClock Integration

[HamClock](https://www.clearskyinstitute.com/ham/HamClock/) is a powerful ham radio dashboard that provides:

#### Features to Integrate
| Feature | MeshForge Use |
|---------|---------------|
| **VOACAP Propagation** | Predict MUF/TOA for long-range LoRa links |
| **DRAP Map** | D-layer absorption affects HF, correlates with ionospheric conditions |
| **Gray Line Indicator** | Enhanced propagation at twilight boundaries |
| **Solar Flux/A-Index** | Space weather affects all RF propagation |
| **DX Cluster Spots** | Active frequency usage, potential interference |
| **Satellite Tracking** | LEO satellite pass predictions |

#### Integration Approach
```
┌──────────────────┐     HTTP/REST     ┌──────────────────┐
│    MeshForge     │◄─────────────────►│    HamClock      │
│   (Node Map)     │                   │   (Propagation)  │
└──────────────────┘                   └──────────────────┘
         │                                      │
         └───────────► Combined Dashboard ◄─────┘
```

- HamClock has **web browser control** - accessible via HTTP
- Embed HamClock data in MeshForge dashboard
- Overlay propagation predictions on node map
- Alert when solar events may affect mesh links

#### Hardware
- Runs on Raspberry Pi (same as MeshForge target)
- Can share display or run headless with web access
- Inovato Quadra4K as dedicated HamClock appliance

### Ham Radio Map Resources

For MeshForge's node map and propagation features:

| Resource | Use Case |
|----------|----------|
| **ARRL Amateur Radio Map** | Grid squares, CQ/ITU zones, DXCC prefixes |
| **IZ8WNH.it Repeater Locator** | Interactive repeater database |
| **Geochron Digital Atlas 4K** | Real-time propagation visualization |
| **QRZ.com Grid Square Map** | Maidenhead grid reference |
| **PSKReporter** | Real-time digital mode propagation |
| **WSPRnet** | Weak signal propagation network |

#### Grid Square / Maidenhead Locator
```python
def latlon_to_grid(lat, lon):
    """Convert lat/lon to 6-character Maidenhead grid"""
    lon += 180
    lat += 90
    field = chr(int(lon / 20) + ord('A')) + chr(int(lat / 10) + ord('A'))
    square = str(int((lon % 20) / 2)) + str(int(lat % 10))
    subsq = chr(int((lon % 2) * 12) + ord('a')) + chr(int((lat % 1) * 24) + ord('a'))
    return field + square + subsq

# Example: latlon_to_grid(21.3069, -157.8583) → "BL11bh" (Honolulu)
```

#### Contest/Award Features to Consider
- Display grid square on node map popups
- Calculate grid square from node position
- Show CQ/ITU zones for international contacts
- DXCC prefix lookup for callsigns

### External APIs
- **Open-Elevation API** - Terrain data for LOS
- **Site Planner** - RF coverage prediction
- **HamClock** - Propagation/space weather (local or remote)
- **QRZ.com** - Callsign lookup (requires API key)
- **HamDB** - Free callsign database API

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

#### ML/AI Research for Dude AI

**Potential Technologies:**

| Technology | Platform | Use Case |
|------------|----------|----------|
| **Claude API** | Cross-platform | Conversational help, config suggestions |
| **Ollama** | Local (Pi 5) | Offline AI assistance |
| **Apple MLX** | macOS/Apple Silicon | Fast local inference |
| **Core ML** | iOS/macOS | On-device signal processing |
| **TensorFlow Lite** | Pi/Linux | Edge ML for predictions |

**Application Areas:**

1. **Predictive Maintenance**
   - Monitor node health metrics (battery, SNR, uptime)
   - Predict node failures before they happen
   - Suggest optimal maintenance windows

2. **Network Optimization**
   - Analyze traffic patterns
   - Suggest optimal routing configurations
   - Identify bottleneck nodes

3. **Anomaly Detection**
   - Detect unusual node behavior
   - Identify potential security issues
   - Alert on degraded link quality

4. **Conversational Interface**
   ```
   User: "Why can't I reach Node-Alpha?"
   Dude AI: "Node-Alpha was last seen 2 hours ago.
            The link to Node-Beta (its relay) shows
            degraded SNR (-15dB). Check for obstruction
            or try increasing TX power."
   ```

5. **Time Series Analysis**
   - Battery discharge prediction
   - Link quality trending
   - Solar/propagation correlation

**Privacy-Preserving Approach:**
- On-device inference preferred (Ollama, TF Lite)
- No mesh data sent to cloud without consent
- Local models for sensitive operations
- Cloud AI only for non-sensitive help queries

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

### Technical References
- [LoRa Spreading Factors](https://www.thethingsnetwork.org/docs/lorawan/spreading-factors/) - TTN documentation
- [LoRa Parameters](https://unsigned.io/understanding-lora-parameters/) - Bitrate calculator
- [Meshtastic Radio Settings](https://meshtastic.org/docs/overview/radio-settings/) - Official docs
- [Self-Healing Networks](https://link.springer.com/chapter/10.1007/978-3-031-75608-5_25) - Academic research
- [HamClock](https://www.clearskyinstitute.com/ham/HamClock/) - Ham radio dashboard
- [Apple MLX Framework](https://machinelearning.apple.com/research/exploring-llms-mlx-m5) - ML on Apple Silicon

---

*Last updated: 2026-01-03*
*Version: 4.2.0 (knowledge base rev 2)*
*Dude AI - Network Engineer, Physicist, Programmer, Project Manager*

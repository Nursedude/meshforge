# RNS/Reticulum Integration Research

## Overview

MeshForge can become a unified **Mesh Network Operations Center** by integrating:
1. Meshtastic (LoRa mesh - current)
2. Reticulum Network Stack (RNS - encrypted mesh)
3. Gateway bridging between networks

## Source Repositories

### RNS-Meshtastic-Gateway-Tool
- **Purpose**: Bridges Reticulum Network Stack with Meshtastic hardware
- **Features**:
  - AI-powered diagnostics for signal quality
  - Self-updating via git synchronization
  - Cross-platform (Windows 11, Raspberry Pi)
- **Hardware**: RAK4631, RAK13302, T-Beam, Heltec V3
- **Key Modules**: ai_methods.py, git_manager.py, launcher.py

### RNS-Management-Tool
- **Purpose**: Comprehensive installer/manager for Reticulum ecosystem
- **Manages**:
  - RNS (Reticulum Network Stack) - cryptographic networking
  - LXMF (Lightweight Extensible Message Format)
  - NomadNet - terminal messaging client
  - MeshChat - web messaging interface
  - Sideband - mobile LXMF client
  - RNODE devices - 21+ board support
- **Features**:
  - Interactive menu-driven interface
  - Automatic version detection
  - Smart dependency management
  - Configuration backups with restore
  - Service management (start/stop/restart/monitor)
  - RNODE auto-detection and firmware flashing

## Meshtastic APIs

### Native HTTP API (ESP32 devices)
- Endpoints: `/api/v1/fromradio`, `/api/v1/toradio`
- Uses protobuf binary format
- Supports HTTP and HTTPS (self-signed certs)

### meshttpd (TCP REST API)
- Python-based REST API for Meshtastic over TCP
- **Endpoints**:
  | Endpoint | Method | Function |
  |----------|--------|----------|
  | `/api/mesh/send_message` | POST | Send messages |
  | `/api/mesh/get_device_telemetry` | GET | Device metrics |
  | `/api/mesh/get_environment_telemetry` | GET | Sensor data |
  | `/api/mesh/get_last_messages` | GET | Message cache |
  | `/api/mesh/nodes` | GET | List nodes |
  | `/api/mesh/status` | GET | Connection status |

### Meshtastic Python API
- `meshtastic` Python package
- TCP connection to meshtasticd (port 4403)
- Full device control and monitoring

## Integration Plan for MeshForge

### Phase 1: UI Improvements
- [ ] Dark mode toggle (GTK, Web, TUI)
- [ ] Improved TUI with better navigation
- [ ] Unified theme system

### Phase 2: RNS Management Panel
- [ ] Install/update RNS, LXMF, NomadNet, MeshChat
- [ ] Service management for rnsd
- [ ] RNODE device detection and setup
- [ ] Configuration editor

### Phase 3: Gateway Integration
- [ ] RNS-Meshtastic gateway setup wizard
- [ ] Bridge status monitoring
- [ ] Message routing visualization
- [ ] AI diagnostics integration

### Phase 4: Unified API
- [ ] Local REST API for MeshForge
- [ ] Expose Meshtastic + RNS endpoints
- [ ] Webhook support for events
- [ ] Integration with external tools

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      MeshForge NOC                          │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Meshtastic │  │  Reticulum  │  │  Gateway Bridge     │  │
│  │  Management │  │  Management │  │  (RNS↔Meshtastic)  │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                     │             │
│  ┌──────▼──────┐  ┌──────▼──────┐  ┌──────────▼──────────┐  │
│  │ meshtasticd │  │    rnsd     │  │   Gateway Service   │  │
│  │  (TCP 4403) │  │             │  │                     │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                     │             │
│  ┌──────▼──────┐  ┌──────▼──────┐  ┌──────────▼──────────┐  │
│  │ LoRa Radio  │  │ RNODE/Radio │  │   Shared Hardware   │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Dependencies to Add

```bash
# RNS ecosystem
pip install rns lxmf nomadnet

# Optional
pip install rnodeconf  # RNODE configuration
```

## References

- RNS Docs: https://reticulum.network/
- LXMF: https://github.com/markqvist/lxmf
- NomadNet: https://github.com/markqvist/nomadnet
- Meshtastic HTTP API: https://meshtastic.org/docs/development/device/http-api/
- meshttpd: https://github.com/lupettohf/meshttpd

---
*Created: 2026-01-03*
*Status: Planning phase*

# MeshForge Feature Parity Audit
## Rich CLI vs GTK vs TUI

Generated: 2026-01-10

---

## Executive Summary

| Interface | Features | Status | Recommendation |
|-----------|----------|--------|----------------|
| **Rich CLI** | 13 submenus, 80+ features | Complete | Gold Standard |
| **GTK** | 18 panels, 70+ features | ~85% parity | Needs minor additions |
| **TUI** | 5 screens, 30+ features | ~40% parity | Needs major work |

---

## Feature Matrix

### Core Features

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **Quick Status Dashboard** | Y | Y | Y | All have basic dashboard |
| **Service Management** | Y | Y | Y | Start/Stop/Restart/Logs |
| **Install meshtasticd** | Y | Y | - | TUI missing install |
| **Update meshtasticd** | Y | Y | - | TUI missing update |
| **Config File Manager** | Y | Y | Y | All support YAML editing |
| **Channel Presets** | Y | - | - | GTK/TUI missing |
| **Configuration Templates** | Y | - | - | GTK/TUI missing |
| **Full Radio Config** | Y | Y | - | TUI missing radio config |

### Meshtastic CLI Commands

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **Node Info (--info)** | Y | Y | Y | |
| **List Nodes (--nodes)** | Y | Y | Y | |
| **Get All Settings** | Y | Y | Y | |
| **Set Position** | Y | - | - | GTK/TUI missing |
| **Configure WiFi** | Y | - | - | GTK/TUI missing |
| **Channel Configuration** | Y | Y | - | TUI missing |
| **Send Message** | Y | - | - | GTK/TUI missing |
| **Request Position** | Y | - | - | GTK/TUI missing |
| **Request Telemetry** | Y | - | - | GTK/TUI missing |
| **Traceroute** | Y | - | - | GTK/TUI missing |
| **Set Node Name** | Y | - | - | GTK/TUI missing |
| **Reboot Node** | Y | - | - | GTK/TUI missing |
| **Factory Reset** | Y | - | - | GTK/TUI missing |

### Tools - RF

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **Link Budget Calculator** | Y | Y | - | TUI missing |
| **Free Space Path Loss** | Y | Y | - | TUI missing |
| **Fresnel Zone Calculator** | Y | Y | - | TUI missing |
| **LoRa Preset Comparison** | Y | Y | Y | TUI has basic version |
| **Range Estimator** | Y | Y | - | TUI missing |
| **Time-on-Air Calculator** | Y | - | - | GTK/TUI missing |
| **Detect LoRa Radio** | Y | Y | Y | |
| **SPI/GPIO Status** | Y | Y | Y | |
| **Frequency Band Reference** | Y | - | - | GTK/TUI missing |

### Tools - Network

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **Ping Test** | Y | Y | Y | |
| **TCP Port Test** | Y | Y | Y | |
| **Network Interfaces** | Y | Y | Y | |
| **Routing Table** | Y | - | - | GTK/TUI missing |
| **DNS Lookup** | Y | - | - | GTK/TUI missing |
| **Active Connections** | Y | - | Y | GTK missing |
| **Scan Local Network** | Y | Y | Y | |
| **Find Meshtastic Devices** | Y | Y | Y | |

### Tools - MUDP

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **Monitor UDP Traffic** | Y | - | - | GTK/TUI missing |
| **Listen to Multicast** | Y | - | Y | GTK missing |
| **View UDP Sockets** | Y | - | Y | GTK missing |
| **Send Test Packet** | Y | - | - | GTK/TUI missing |
| **UDP Echo Test** | Y | - | - | GTK/TUI missing |
| **Install/Update MUDP** | Y | - | Y | GTK missing |

### Tools - Site Planner

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **Open Site Planner** | Y | - | - | GTK/TUI missing |
| **RF Coverage Tools Links** | Y | - | - | GTK/TUI missing |
| **Antenna Guidelines** | Y | - | - | GTK/TUI missing |
| **Frequency/Power Reference** | Y | - | - | GTK/TUI missing |

### Tools - Tool Manager

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **Check Tool Status** | Y | - | - | GTK/TUI missing |
| **Install/Upgrade Tools** | Y | - | - | GTK/TUI missing |
| **Manage 7 Tools** | Y | - | - | GTK/TUI missing |

### Hardware Configuration

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **Enable/Disable SPI** | Y | - | - | GTK/TUI missing |
| **Enable/Disable I2C** | Y | - | - | GTK/TUI missing |
| **Configure Serial** | Y | - | - | GTK/TUI missing |
| **Hardware Detection** | Y | Y | Y | |
| **Select & Configure Device** | Y | Y | - | TUI missing |
| **View boot/config.txt** | Y | - | - | GTK/TUI missing |
| **Safe Reboot** | Y | - | - | GTK/TUI missing |

### System / Debug

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **View Logs** | Y | Y | Y | |
| **Check Dependencies** | Y | Y | - | TUI missing |
| **Check for Updates** | Y | Y | - | TUI missing |
| **Version History** | Y | Y | - | TUI missing |
| **Emoji Support Status** | Y | - | - | GTK/TUI N/A |
| **Uninstall** | Y | - | - | GTK/TUI missing |

### Reticulum (RNS)

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **RNS Service Status** | - | Y | Y | CLI missing |
| **Gateway Bridge** | Y | Y | - | TUI missing |
| **Install RNS Components** | - | Y | - | CLI/TUI missing |
| **NomadNet Control** | - | Y | - | CLI/TUI missing |
| **MeshChat Web Interface** | - | Y | - | CLI/TUI missing |

### Ham Radio Tools

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **HamClock Integration** | - | Y | - | CLI/TUI missing |
| **Solar/HF Propagation** | - | Y | - | CLI/TUI missing |
| **Callsign Lookup** | - | Y | - | CLI/TUI missing |

### AREDN

| Feature | Rich CLI | GTK | TUI | Notes |
|---------|----------|-----|-----|-------|
| **Node Discovery** | - | Y | - | CLI/TUI missing |
| **Topology View** | - | Y | - | CLI/TUI missing |
| **Router Setup** | - | Y | - | CLI/TUI missing |

---

## Gap Analysis

### TUI Needs (Critical - ~40% parity)

**Must Add:**
1. Install/Update meshtasticd
2. Channel Presets menu
3. Configuration Templates
4. Full Radio Config screen
5. Meshtastic CLI commands (WiFi, Position, Message, etc.)
6. RF Tools (Link Budget, FSPL, Fresnel, ToA)
7. Site Planner tools
8. Tool Manager
9. Hardware Configuration (SPI/I2C/Serial)
10. Dependency checker
11. Uninstall option

**Architecture Issue:**
- TUI is a single 1,379-line file - needs refactoring into separate screen modules

### GTK Needs (Minor - ~85% parity)

**Should Add:**
1. Channel Presets (quick setup menu)
2. Configuration Templates (hardware/network presets)
3. Set Position via CLI
4. Configure WiFi via CLI
5. Send Message functionality
6. Request Position/Telemetry
7. Traceroute
8. Node control (Reboot, Factory Reset)
9. Time-on-Air Calculator
10. Frequency Band Reference
11. Routing Table viewer
12. DNS Lookup
13. UDP monitoring tools
14. Site Planner links
15. Tool Manager
16. Hardware config (SPI/I2C enable/disable)
17. Safe Reboot option
18. Uninstall option

### Rich CLI Needs (Minor additions)

**Could Add:**
1. RNS component installation (has gateway, not install)
2. HamClock integration (web link?)
3. Propagation data display
4. AREDN scanning

---

## Priority Action Items

### Phase 1: TUI Critical (Required for Parity)
1. [ ] Refactor TUI into modular screens
2. [ ] Add Install/Update screen
3. [ ] Add Radio Configuration screen
4. [ ] Add Channel Presets
5. [ ] Add RF Tools screen
6. [ ] Add Hardware Config screen

### Phase 2: GTK Quick Wins
1. [ ] Add Channel Presets panel/dialog
2. [ ] Add Configuration Templates menu
3. [ ] Add Meshtastic CLI actions (message, position, etc.)
4. [ ] Add Site Planner links
5. [ ] Add Tool Manager panel

### Phase 3: Full Parity
1. [ ] TUI: Complete all missing tools
2. [ ] GTK: Complete all missing tools
3. [ ] Rich CLI: Add RNS install, HamClock link

---

## Recommendations

### Gold Standard Definition

**Rich CLI is the gold standard because:**
- Most complete feature set (80+ features)
- Works in any terminal (SSH, local, headless)
- Consistent navigation (Back/Main Menu everywhere)
- Proper emoji fallback
- Organized into logical submenus

**GTK should match Rich CLI + add:**
- GUI advantages (graphs, embedded web, async updates)
- Keep consolidated panels (Mesh Tools, Ham Tools)
- Maintain keyboard shortcuts

**TUI should match Rich CLI structure:**
- Same menu hierarchy
- Same keyboard shortcuts where applicable
- Same feature completeness
- Refactor from 1 file to modular architecture

### Implementation Order

1. **TUI Refactor** - Break into modules first
2. **TUI Features** - Add missing screens
3. **GTK Channel Presets** - Quick win
4. **GTK CLI Actions** - Message, Position, etc.
5. **Full Parity** - Complete remaining gaps

---

*This audit provides a roadmap for achieving "gold standard" feature parity across all MeshForge interfaces.*

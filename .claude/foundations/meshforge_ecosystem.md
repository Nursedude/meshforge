# MeshForge Ecosystem Architecture

> **Document Purpose**: Map the full MeshForge domain across all repositories
> **Created**: 2026-02-17
> **Status**: Active reference
> **Owner**: WH6GXZ (Nursedude)

---

## 1. Ecosystem Overview

MeshForge is not a single repository — it's a **domain** spanning five repos that collectively provide mesh network operations, monitoring, alerting, visualization, and tooling.

```
┌─────────────────────────────────────────────────────────────────┐
│                    MeshForge NOC (Core Hub)                      │
│            Nursedude/meshforge · v0.5.7-beta                    │
│       Gateway bridge · TUI · RF tools · Diagnostics             │
└────────┬──────────┬───────────────┬─────────────────────────────┘
         │          │               │
    ┌────▼────┐ ┌───▼──────────┐ ┌─▼──────────────────┐
    │ Maps    │ │ Meshing      │ │ RNS Management     │
    │ Plugin  │ │ Around       │ │ Tool               │
    │ v0.7.0  │ │ v0.5.0       │ │ v0.3.2             │
    └─────────┘ └──────────────┘ └────────────────────┘
    Visualization  Bot Alerting    RNS Installer
    Leaflet/D3     12 alert types  Cross-platform

                    ┌──────────────────────┐
                    │ RNS-Meshtastic       │
                    │ Gateway-Tool (alpha)  │
                    │ → Merging into NOC    │
                    └──────────────────────┘
                    Original bridge driver
```

---

## 2. Repository Ownership Map

### Nursedude/meshforge (Core NOC)
- **Role**: Central hub — the NOC itself
- **Status**: Beta (v0.5.7). MeshCore is an optional gateway handler on `main`; sister repo MeshAnchor is the MeshCore-primary NOC (split 2026-04-01).
- **Stack**: Python 3.9+, TUI (whiptail/dialog), MQTT, systemd
- **Owns**: Gateway bridge, node tracker, RF tools, diagnostics, TUI, service management
- **Branch model**: solo-dev direct-to-`main`. Alpha branch archived as tag `alpha-archived` (2026-04-19).

### Nursedude/meshforge-maps (Maps Plugin)
- **Role**: Multi-source mesh network visualization
- **Status**: Beta (v0.7.0)
- **Stack**: Python 3.9+, Leaflet.js, D3.js, WebSocket
- **Owns**: Node mapping, topology visualization, health scoring, alerting dashboard
- **Runs**: Standalone (ports 8808/8809) OR as MeshForge plugin via `manifest.json`
- **Data sources**: Meshtastic MQTT, Reticulum/RMAP, AREDN, NOAA/OpenHamClock

### Nursedude/meshing_around_meshforge (Bot Alerting)
- **Role**: Monitoring and alerting layer for the meshing-around Meshtastic bot
- **Status**: Beta (v0.5.0)
- **Stack**: Python 3.8+, Rich TUI, FastAPI, paho-mqtt
- **Owns**: 12 alert types (emergency, proximity, altitude, weather, iPAWS/EAS, volcano, battery, noisy node, new node, SNR, disconnect, custom)
- **Dependency**: Complements meshing-around v1.9.9.x (NOT standalone)
- **Notification channels**: Mesh DM, channel message, email (SMTP), SMS, sound, script exec

### Nursedude/RNS-Management-Tool (RNS Installer)
- **Role**: Cross-platform installer/manager for the entire RNS ecosystem
- **Status**: Beta (v0.3.2)
- **Stack**: Bash (Linux/RPi), PowerShell (Windows 11), Python 3.7+
- **Owns**: RNS/LXMF/NomadNet/MeshChat/Sideband installation, RNODE firmware flashing (21+ boards), backup/restore
- **Unique**: Only MeshForge ecosystem tool with native Windows support

### Nursedude/RNS-Meshtastic-Gateway-Tool (Bridge Driver)
- **Role**: Original RNS-to-Meshtastic bridge implementation
- **Status**: Alpha — **migration to MeshForge NOC in progress**
- **Stack**: Python, custom `Meshtastic_Interface` extending `RNS.Interfaces.Interface`
- **Contains**: `MESHFORGE_ANALYSIS.md`, `TO_MESHFORGE.md` (migration plan docs)
- **Future**: Core driver logic absorbing into `src/gateway/` in MeshForge NOC

### Security posture — declared, not remembered (audited 2026-08-28)

Quadrant rule: an app that is *deployed but unmaintained* either gets stopped
or earns a probe — no third option. Dependabot vulnerability alerts enabled on
all 8 Nursedude repos (incl. the reticulum/lxmf forks) 2026-08-28. First scan:
7 repos 0 open; RNS-Meshtastic-Gateway-Tool 5 high + 2 medium (stale lock
pins) — **FIXED same day** (`f850762`): lock regenerated with pip-compile
--generate-hashes on py3.11 (cryptography 50.0.1, urllib3 2.7.0, idna 3.19),
suite 534 passed on the new versions. Regen recipe: needs a py3.11 host
(moc4 is the fleet's only one), never hand-edit hash pins.

| App | Lifecycle | Deployed (fleet sweep 2026-08-28) |
|-----|-----------|-----------------------------------|
| meshforge, meshanchor, meshforge-maps | **active** | fleet-wide / meshanchor-server / moc:8808 + cloud |
| meshing_around_meshforge (+ meshing-around fork) | **dormant** (last upstream merge 2026-08-06) | NOT deployed — checkouts on VolcanoAI only, no units/procs anywhere |
| RNS-Management-Tool | **dormant** (2026-06-20) | NOT deployed — checkout only |
| RNS-Meshtastic-Gateway-Tool | **dormant/absorbing** (2026-06-01) | NOT deployed — checkout only; logic migrating into `src/gateway/` |
| reticulum-meshchat (3rd-party, liamcottle via Nursedude fork) | external | **RUNNING on moc5** — updated 2026-08-28 to `4d57d6a`, RNS env on the fork pin (1.3.8+mf.0). **Auth added 08-28**: nginx-light basic-auth owns LAN `:8000` (`sites-available/meshchat-auth`, WS-aware, 101 verified), meshchat itself loopback-only `127.0.0.1:8001` via systemd drop-in `localhost-behind-nginx.conf`; credential in `/etc/nginx/.htpasswd-meshchat`. MeshChatX (Quad4 fork) evaluated 08-28: not a drop-in — data-incompatible, untested vs the fork pin; canary in the lab first if wanted |

---

## 3. Boundary Rules (What Lives Where)

### Gateway & Bridge Logic → meshforge (NOC)
All protocol bridging, message routing, and 3-way MeshCore routing belongs in the core NOC. The RNS-Meshtastic-Gateway-Tool's driver is migrating here.

### Visualization & Mapping → meshforge-maps
Interactive maps, topology graphs, health dashboards, and map-based alerting. The NOC's `coverage_map.py` generates static Folium maps; meshforge-maps provides the live interactive layer.

### Bot-Adjacent Alerting → meshing_around_meshforge
Alert rules that operate on meshing-around bot data (proximity, EAS/iPAWS, volcano, etc.). This is NOT generic NOC alerting — it's specific to the meshing-around bot ecosystem.

### RNS Ecosystem Install/Manage → RNS-Management-Tool
Installing, updating, configuring, and backing up RNS components. The NOC **connects to** running RNS services; this tool **installs and manages** them.

### NOC Alerting vs Bot Alerting
- **NOC (meshforge)**: Service health, gateway status, link quality, node tracker events
- **Bot (meshing_around_meshforge)**: Emergency keywords, proximity geofencing, weather/iPAWS, noisy node detection

---

## 4. Shared Configuration Patterns

### Config Home Directories
| Repo | Config Location | Notes |
|------|----------------|-------|
| meshforge (NOC) | `~/.config/meshforge/` | Settings, propagation, message queue |
| meshforge-maps | `~/.config/meshforge/plugins/org.meshforge.extension.maps/` | Plugin manifest pattern |
| meshing_around | `~/.config/meshforge/meshing_around/` | INI-based alert config |
| RNS-Mgmt-Tool | `~/.reticulum/`, `~/.nomadnetwork/`, `~/.lxmf/` | Standard RNS paths |
| Gateway-Tool | `.reticulum/` config | Standard RNS interface config |

### Path.home() Bug (MF001) — ECOSYSTEM-WIDE
**NEVER use `Path.home()` in any repo.** Returns `/root` with sudo.
```python
# ALL repos must use this pattern when resolving user home
import os, pathlib
def get_real_user_home():
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return pathlib.Path(f"/home/{sudo_user}")
    return pathlib.Path.home()
```

### Config Formats
| Repo | Format | Why |
|------|--------|-----|
| meshforge (NOC) | JSON/YAML | SettingsManager auto-serialization |
| meshforge-maps | JSON | Plugin manifest + settings.json |
| meshing_around | INI | Human-editable alert config (12 sections) |
| RNS-Mgmt-Tool | .tar.gz/.zip | Backup archives; RNS uses its own config format |
| Gateway-Tool | RNS config | Standard `.reticulum` interface definitions |

---

## 5. Integration Points & Data Flow

```
                    External Networks
                    ─────────────────
    Meshtastic LoRa ──┐     ┌── AREDN Mesh
    MeshCore Heltec ──┤     │
    RNS/Reticulum ────┤     │
                      ▼     ▼
              ┌───────────────────┐
              │   MeshForge NOC   │ ← Service checks (systemd)
              │   Gateway Bridge  │ ← MQTT bridge (meshtasticd)
              │   3-way routing   │ ← LXMF (RNS)
              │   Node tracker    │ ← MeshCore handler (alpha)
              └──────┬──────┬─────┘
                     │      │
          GeoJSON API│      │Node events
                     ▼      ▼
              ┌──────────────────┐     ┌───────────────────┐
              │  meshforge-maps  │     │ meshing_around_mf  │
              │  :8808 (HTTP)    │     │ Bot alert layer    │
              │  :8809 (WS)     │     │ FastAPI + WS       │
              └──────────────────┘     └───────────────────┘
                     │                          │
              Interactive map           12 alert types
              Topology graphs           Email/SMS/Sound
              Health scores             iPAWS/EAS/Volcano

              ┌──────────────────┐
              │ RNS-Mgmt-Tool   │  (Independent installer)
              │ Bash/PowerShell  │  Maintains: rnsd, NomadNet,
              │ Cross-platform   │  MeshChat, Sideband, RNODE
              └──────────────────┘
```

### API Contracts

**meshforge-maps plugin discovery** (auto-detected by NOC):
```json
{
  "manifest.json": {
    "id": "org.meshforge.extension.maps",
    "ports": {"http": 8808, "ws": 8809}
  }
}
```

**meshforge-maps REST API** (consumed by NOC or standalone):
```
GET  /api/nodes/geojson          → Merged node FeatureCollection
GET  /api/node-health            → Per-node scores (0-100)
GET  /api/topology/geojson       → SNR-colored mesh links
GET  /api/alerts/active          → Unacknowledged alerts
GET  /api/analytics/growth       → Unique nodes per time bucket
GET  /api/health                 → System health + data freshness
```

**meshing_around_meshforge REST API**:
```
GET  /api/status                 → Connection info
GET  /api/nodes                  → Node list with status
GET  /api/messages               → Message history
POST /api/messages/send          → Send message to mesh
WS   /ws                        → Real-time alerts, nodes, messages
```

**MQTT topics** (shared across ecosystem):
```
meshforge/alerts                 → Full alert feed (maps plugin)
meshforge/alerts/{severity}      → critical, warning, info
msh/#                           → Meshtastic MQTT (protobuf)
```

---

## 6. Dependency Direction

```
meshforge-maps ──depends on──▶ MeshForge NOC (optional, can run standalone)
meshing_around ──depends on──▶ meshing-around bot (required external)
RNS-Mgmt-Tool ──independent──  (receives patterns from MeshForge)
Gateway-Tool ───merging into──▶ MeshForge NOC gateway/
```

**Rule**: Satellite repos may depend on the NOC. The NOC never depends on satellites. The NOC *discovers* plugins but runs fine without them.

---

## 7. Shared Dependencies

### Common Across Multiple Repos
```
paho-mqtt          → NOC, maps, meshing_around (MQTT connectivity)
rich               → NOC, meshing_around (terminal formatting)
requests           → NOC, maps (HTTP)
pyyaml             → NOC (config)
```

### RNS Ecosystem (NOC + Gateway-Tool + RNS-Mgmt-Tool)
```
rns>=0.7.0         → Reticulum Network Stack
lxmf>=0.4.0        → LXMF message protocol
pyopenssl>=25.3.0  → SSL/cryptography
```

### Meshtastic (NOC + maps)
```
meshtastic>=2.3.0  → Meshtastic Python API (optional in NOC, used in maps for protobuf)
```

### MeshCore (optional gateway handler on `main`)
```
meshcore_py        → MeshCore companion radio protocol
```

---

## 8. Branch Strategy Across Repos

### meshforge (NOC)
- `main` — Stable beta (currently v0.5.7-beta). Solo-dev direct-to-main; PR/feature-branch flow retired 2026-04-19.
- `alpha-archived` — Tag preserving the historical `alpha/meshcore-bridge` work.
- Sister repo: `Nursedude/meshanchor` (MeshCore-primary NOC, split 2026-04-01).

### Other Repos
Each repo manages its own versioning independently. Cross-repo releases are NOT synchronized — each ships when ready.

---

## 9. MeshCore on `main` (optional gateway handler)

`main` carries MeshCore as an **optional gateway handler** alongside Meshtastic and RNS. The MeshCore-primary NOC (`MeshAnchor`) was extracted to its own repo on 2026-04-01.

### Components
| File | Purpose |
|------|---------|
| `gateway/meshcore_handler.py` | MeshCore bridge logic via meshcore_py |
| `gateway/canonical_message.py` | Normalized message format (shared with MeshAnchor — keep compatible) |
| `gateway/meshcore_bridge_mixin.py` | TUI wiring for MeshCore stats |
| `plugins/meshcore.py` | Plugin → handler delegation |
| `gateway/message_routing.py` | 3-way routing tables |

### 3-Way Routing Architecture
```
Meshtastic LoRa ◄──►┐
                     │
RNS/Reticulum   ◄──►├──► MeshForge Gateway (canonical_message.py)
                     │        │
MeshCore Heltec ◄──►┘        ▼
                         RoutingClassifier
                         (src/utils/classifier.py)
```

### Test Coverage (alpha)
- `test_canonical_message.py` — 553 lines
- `test_meshcore_handler.py` — 602 lines
- `test_tribridge_integration.py` — 684 lines

---

## 10. Hardware Reference (Nursedude's Setup)

### Current Test Rig
- **Compute**: Raspberry Pi 5 (16GB), Debian Trixie
- **Meshtastic**: Two USB nodes via meshtoad (meshtasticd)
- **MeshCore**: Heltec V3 (companion radio)
- **RNS**: rnsd service running locally

### Supported Hardware Across Ecosystem
| Component | Supported Devices |
|-----------|------------------|
| Meshtastic | Heltec V3, TLORA, RAK, LilyGo T-Beam, T-Deck, etc. |
| MeshCore | Heltec V3 (companion mode), other ESP32-based |
| RNS/RNODE | 21+ board types (via RNS-Management-Tool) |
| Compute | RPi 3+, RPi 4, RPi 5, x86_64 Linux, Windows 11 |

---

## 11. Cross-Repo Design Decisions

### Security Rules Apply Everywhere
- **MF001**: No `Path.home()` — use `get_real_user_home()`
- **MF002**: No `shell=True` in subprocess
- **MF003**: No bare `except:` — always specify exception type
- **MF004**: Always include `timeout=` on subprocess calls

### Service Independence
MeshForge NOC **connects to** services; it doesn't embed them. Each external service (meshtasticd, rnsd, HamClock, MeshCore) runs independently under systemd. Satellite repos follow the same principle.

### Plugin Discovery Pattern
Satellites that integrate with the NOC use `manifest.json` auto-discovery. The NOC scans for plugins at startup but never requires them.

---

## 12. When to Add Code to Which Repo

| If you're adding... | Put it in... |
|---------------------|-------------|
| Protocol bridging / routing | meshforge (NOC) `gateway/` |
| Map visualization / topology | meshforge-maps |
| Bot alert types / notifications | meshing_around_meshforge |
| RNS/NomadNet install/update scripts | RNS-Management-Tool |
| RF calculations / link budgets | meshforge (NOC) `utils/rf.py` |
| Service management (systemd) | meshforge (NOC) `utils/service_check.py` |
| Node tracking / discovery | meshforge (NOC) `gateway/node_tracker.py` |
| TUI menus / NOC interface | meshforge (NOC) `launcher_tui/` |
| Static coverage maps | meshforge (NOC) `utils/coverage_map.py` |
| Live interactive maps | meshforge-maps |
| RNODE firmware flashing | RNS-Management-Tool |
| MeshCore protocol handler | meshforge (NOC) `gateway/meshcore_handler.py` |

---

*Made with aloha for the mesh community — WH6GXZ*

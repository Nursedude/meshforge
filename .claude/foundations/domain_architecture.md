# MeshForge Domain Architecture

> **Document Purpose**: Define the architectural vision and resolve systemic tensions
> **Created**: 2026-01-06
> **Status**: Active Planning

---

## 1. Core Mission

**MeshForge is a Network Operations Center (NOC) for heterogeneous mesh networks.**

It bridges two incompatible mesh ecosystems:
- **Meshtastic** (LoRa, consumer-grade, 915/868 MHz)
- **Reticulum/RNS** (cryptographic, infrastructure-grade, multi-transport)

Target audience: **HAM radio operators** who need reliable off-grid communications.

---

## 2. Privilege Model (Resolving the sudo Tension)

### The Problem
MeshForge has been running with `sudo` because some operations need root:
- Service control (systemctl start/stop)
- Config file editing (/etc/meshtasticd/)
- Hardware access (GPIO, SPI, I2C)

But `Path.home()` returns `/root` with sudo, breaking user config persistence.

### The Solution: Two Modes

```
┌─────────────────────────────────────────────────────────────────┐
│                    MeshForge Privilege Model                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  VIEWER MODE (No sudo)                ADMIN MODE (sudo)         │
│  ──────────────────────              ─────────────────          │
│  - Read node status                  - Start/stop services      │
│  - View RF/propagation data          - Edit /etc/ configs       │
│  - RF calculations                   - Hardware initialization  │
│  - Space weather (HamClock API)      - GPIO/SPI/I2C access      │
│  - Connect to running services       - Install packages         │
│  - View logs                         - Manage systemd units     │
│  - Educational content               - Write to /var/log        │
│                                                                 │
│  User's ~/.config/meshforge/         System /etc/meshtasticd/   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Implementation Strategy

1. **Default to Viewer Mode** - Launch without sudo
2. **Elevate for specific actions** - Use `pkexec` or `sudo` only when needed
3. **External services run independently** - meshtasticd, rnsd, hamclock as systemd
4. **MeshForge connects to services** - Not embedded, just API clients

---

## 3. Core vs Plugin Architecture

### CORE (Built-in, essential)

| Component | Purpose | Privilege |
|-----------|---------|-----------|
| **Gateway Bridge** | RNS↔Meshtastic routing | Viewer (connects to daemons) |
| **Node Tracker** | Unified node inventory | Viewer |
| **RF Calculator** | Link budget, Fresnel, FSPL | Viewer |
| **Config Editor** | YAML/JSON editing | Admin (for /etc/) |
| **Diagnostics** | Health checks, connectivity tests | Viewer |
| **University** | Educational content | Viewer |

### PLUGINS (Optional, external integrations)

| Plugin | Type | External Service | Notes |
|--------|------|------------------|-------|
| **HamClock** | Integration | hamclock daemon | HTTP API on :8080 |
| **AREDN** | Integration | AREDN mesh nodes | HTTP API on :8080 |
| **MQTT Bridge** | Integration | MQTT broker | Paho client |
| **Meshing Around** | Extension | None | Community bot |
| **MeshCore** | Protocol | None | Alternative mesh |

### Plugin Benefits
- **Isolation**: HamClock failure doesn't crash MeshForge
- **Optional**: Users install only what they need
- **Testable**: Each plugin has clear boundaries
- **Maintainable**: Updates don't affect core

---

## 4. RF & Propagation Tools (HAM Focus)

HAMs care about propagation. These tools are CORE to MeshForge:

### RF Calculator (`utils/rf.py`)
```python
# Pure functions, no dependencies
haversine_distance(lat1, lon1, lat2, lon2)  # Point-to-point distance
fresnel_radius(distance_km, freq_ghz)        # Clearance requirements
free_space_path_loss(distance_m, freq_mhz)   # FSPL in dB
earth_bulge(distance_m)                       # Terrain obstruction
link_budget(tx_power, gains, distance, freq)  # End-to-end analysis
snr_estimate(...)                             # Signal quality
```

**Optimization**: Cython `rf_fast.pyx` provides 5-10x speedup for batch operations.

### Space Weather (HamClock Integration)
```
Solar Flux Index (SFI) → HF band openings
Kp Index → Geomagnetic activity
A Index → Short-term conditions
X-Ray Flux → Solar flares
Aurora Activity → VHF propagation
```

### Band Conditions
```
80m-40m (Low bands)  → Night propagation
30m-20m (Mid bands)  → All-day workhorses
17m-15m (High bands) → Daylight, solar-dependent
12m-10m (VHF prep)   → Solar maximum bands
```

### Data Sources
1. **HamClock API** - Local daemon, rich data
2. **NOAA SWPC** - Authoritative solar indices
3. **N0NBH** - Band condition summaries
4. **DX Cluster** - Real-time propagation reports

---

## 5. Service Integration Model

MeshForge connects to external services; it doesn't embed them.

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   ┌─────────────┐       ┌─────────────┐       ┌─────────────┐ │
│   │ meshtasticd │       │    rnsd     │       │  hamclock   │ │
│   │  (systemd)  │       │  (systemd)  │       │  (systemd)  │ │
│   │ TCP :4403   │       │ Socket/LXMF │       │ HTTP :8080  │ │
│   └──────┬──────┘       └──────┬──────┘       └──────┬──────┘ │
│          │                     │                     │         │
│          └─────────────────────┼─────────────────────┘         │
│                                │                               │
│                    ┌───────────▼───────────┐                  │
│                    │      MESHFORGE        │                  │
│                    │   (NOC Dashboard)     │                  │
│                    │                       │                  │
│                    │  - Connects to APIs   │                  │
│                    │  - Displays status    │                  │
│                    │  - Routes messages    │                  │
│                    │  - RF calculations    │                  │
│                    └───────────────────────┘                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Service Status Pattern
Before using any service, check if it's running:

```python
def check_service_available(name, port):
    """Check if service is reachable before using it."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('localhost', port))
        sock.close()
        return result == 0
    except:
        return False

# In panel code
if not check_service_available('meshtasticd', 4403):
    show_actionable_error("meshtasticd not running. Start with: sudo systemctl start meshtasticd")
```

---

## 6. HamClock as Plugin Pattern

HamClock is the model for how external tools integrate:

### Current State (Panel)
- 1,103 lines in `gtk_ui/panels/hamclock.py`
- Connects to HamClock HTTP API
- Fetches space weather data
- Controls systemd service
- WebKit embedding disabled when root (workaround in place)

### Target State (Plugin)
```
plugins/
├── hamclock/
│   ├── __init__.py
│   ├── plugin.py        # IntegrationPlugin subclass
│   ├── api.py           # HTTP client for HamClock API
│   ├── widgets.py       # GTK widgets for panel
│   └── config.py        # Plugin settings
```

### Plugin Interface
```python
class HamClockPlugin(IntegrationPlugin):
    """HamClock integration for propagation and space weather."""

    @staticmethod
    def get_metadata() -> PluginMetadata:
        return PluginMetadata(
            name="hamclock",
            version="1.0.0",
            description="Space weather and propagation from HamClock",
            plugin_type=PluginType.INTEGRATION,
            dependencies=[],  # No pip dependencies, uses HTTP
            service_port=8080,  # Declares external service requirement
        )

    def activate(self) -> None:
        """Called when plugin is enabled."""
        if not self._check_service_available():
            raise ServiceNotAvailable("HamClock not running")

    def get_panel(self) -> Gtk.Widget:
        """Return GTK widget for UI integration."""
        return HamClockPanel(...)

    def get_data(self) -> dict:
        """Fetch current space weather data."""
        return self.api.fetch_space_weather()
```

---

## 7. File Structure (Target)

```
src/
├── core/                    # Essential MeshForge functionality
│   ├── gateway/             # RNS↔Meshtastic bridge
│   │   ├── bridge.py        # Message routing
│   │   ├── config.py        # Gateway configuration
│   │   └── tracker.py       # Node tracking
│   ├── rf/                  # RF calculations (extracted from utils/)
│   │   ├── calculator.py    # Link budget, FSPL, Fresnel
│   │   ├── propagation.py   # Band conditions, solar data
│   │   └── fast.pyx         # Cython optimizations
│   └── diagnostics/         # Health checks
│
├── plugins/                 # Optional integrations
│   ├── hamclock/            # Space weather
│   ├── aredn/               # WiFi mesh
│   ├── mqtt/                # MQTT bridge
│   └── meshing_around/      # Community features
│
├── ui/                      # User interfaces
│   ├── gtk/                 # GTK4 desktop
│   │   ├── app.py
│   │   └── panels/
│   ├── tui/                 # Textual terminal
│   └── web/                 # Flask browser
│
├── services/                # System service management
│   └── manager.py           # Privilege-elevated operations
│
├── university/              # Educational content
│
└── cli/                     # Command-line tools
```

---

## 8. Migration Path

### Phase 1: Stabilize Current (NOW)
- [x] Fix Path.home() across codebase
- [ ] Verify all services check availability before use
- [ ] Add timeout to all subprocess calls

### Phase 2: Privilege Separation
- [ ] Make viewer mode the default
- [ ] Use pkexec for admin operations
- [ ] Document which features need elevation

### Phase 3: Plugin Extraction
- [ ] Extract HamClock to plugin
- [ ] Extract AREDN to plugin
- [ ] Document plugin API

### Phase 4: Code Consolidation
- [ ] Split large files (rns.py, tools.py)
- [ ] Consolidate RF tools
- [ ] Increase test coverage to 50%+

---

## 9. Design Principles

1. **Services run independently** - MeshForge doesn't start them; it connects to them
2. **Fail gracefully** - Missing service = actionable error, not crash
3. **Privilege minimization** - Only elevate when absolutely necessary
4. **HAM focus** - Propagation and RF tools are first-class features
5. **Plugin isolation** - Third-party integrations don't affect core stability

---

## 10. Questions for User Verification

1. **Is viewer-mode-by-default acceptable?** Users would run `meshforge` normally, and only use `sudo meshforge` or be prompted for elevation when changing system configs.

2. **Should HamClock be extracted to plugin now, or after stability testing?**

3. **Are there other external tools (besides HamClock, AREDN) that HAMs commonly use that should have plugin integrations?**

4. **What's the deployment priority?**
   - Raspberry Pi (primary)
   - Desktop Linux
   - Other SBCs (Orange Pi, etc.)

---

*Document maintained by: Dude AI (development partner)*
*Last updated: 2026-01-06*

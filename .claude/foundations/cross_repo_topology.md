# Cross-Repo Topology — MeshForge Ecosystem

> **Purpose**: Define task delegation, shared rules, and communication patterns across the three active repos.
> **Scope**: meshforge (NOC), meshing_around_meshforge (Bot/Alerting), meshforge-maps (Visualization)
> **Owner**: WH6GXZ (Nursedude)

---

## Three-Repo Architecture

```
┌─────────────────────────────────────────────────────────┐
│               MeshForge NOC (Core Hub)                  │
│           /opt/meshforge · v0.5.7-beta                  │
│    Gateway · TUI · RF Tools · Service Management        │
│    63 .claude/ files · 3,460 tests · Custom linter      │
└───────────┬─────────────────────────┬───────────────────┘
            │                         │
   Plugin discovery          safe_import bridge
   (manifest.json)           (/opt/ co-location)
            │                         │
  ┌─────────▼──────────┐    ┌────────▼──────────────────┐
  │  meshforge-maps    │    │  meshing_around_meshforge  │
  │  /opt/meshforge-   │    │  /opt/meshing_around_      │
  │  maps · v0.7.0     │    │  meshforge · v0.5.0        │
  │  982 tests         │    │  743 tests                 │
  │                    │    │                             │
  │  Visualization:    │    │  Bot Alerting:              │
  │  • Leaflet.js map  │    │  • 12 alert types           │
  │  • REST API        │    │  • AES-256-CTR crypto       │
  │  • Node health     │    │  • MockMeshtasticAPI        │
  │  • Topology graphs │    │  • MQTT client              │
  │  • NOAA/EAS alerts │    │  • Rich TUI                 │
  │  • WebSocket live  │    │  • INI config               │
  └────────────────────┘    └─────────────────────────────┘
```

---

## Task Delegation Matrix

| Task Domain | Primary Repo | Why |
|-------------|-------------|-----|
| Protocol bridging (Meshtastic ↔ RNS) | **meshforge** | Gateway architecture owns routing |
| TUI menus / NOC interface | **meshforge** | Handler registry pattern |
| Service management (systemd) | **meshforge** | `service_check.py` is SSOT |
| RF calculations / link budgets | **meshforge** | `utils/rf.py` |
| Node tracking / discovery | **meshforge** | `gateway/node_tracker.py` |
| Static coverage maps (Folium) | **meshforge** | `utils/coverage_map.py` |
| Interactive web maps (Leaflet) | **meshforge-maps** | Map server + collectors |
| Multi-source data aggregation | **meshforge-maps** | BaseCollector pattern |
| Node health scoring (map layer) | **meshforge-maps** | `utils/health_scoring.py` |
| Topology visualization | **meshforge-maps** | SNR-colored mesh links |
| NOAA/EAS weather alert polygons | **meshforge-maps** | `noaa_alert_collector.py` |
| REST API for node/health data | **meshforge-maps** | `map_server.py` |
| Bot alert types (12 categories) | **meshing_around** | `core/models.py` AlertType enum |
| AES-256-CTR packet decryption | **meshing_around** | `core/mesh_crypto.py` |
| MockMeshtasticAPI (demo traffic) | **meshing_around** | `core/meshtastic_api.py` |
| MQTT client (standalone) | **meshing_around** | `core/mqtt_client.py` |
| Alert cooldown/callback logic | **meshing_around** | `core/callbacks.py` |
| INI-based alert configuration | **meshing_around** | `core/config.py` |

---

## Dependency Direction

```
meshforge-maps ──optional──▶ MeshForge NOC (plugin discovery, can run standalone)
meshforge NOC  ──safe_import──▶ meshing_around (alert types, crypto, MockAPI)
meshing_around ──independent── (no dependency on NOC or maps)
```

**Rule**: The NOC may import from meshing_around via `safe_import` (graceful degradation).
Satellite repos never depend on the NOC for core functionality.

---

## Shared Security Rules (Apply to ALL repos)

| Rule | Pattern | Fix |
|------|---------|-----|
| MF001 | `Path.home()` | Use `get_real_user_home()` / `get_real_home()` |
| MF002 | `shell=True` | Use list args: `subprocess.run(["cmd", arg])` |
| MF003 | bare `except:` | Catch specific: `except Exception as e:` |
| MF004 | missing timeout | Always: `subprocess.run(..., timeout=30)` |

---

## Integration Points

### NOC ← meshing_around (safe_import bridge)
```python
# In meshforge NOC (src/utils/mesh_alert_engine.py):
sys.path.insert(0, "/opt/meshing_around_meshforge")
AlertType, Alert, _HAS = safe_import('meshing_around_clients.core.models', ...)
```

### NOC ← meshforge-maps (plugin discovery)
```json
// meshforge-maps/manifest.json
{"id": "org.meshforge.extension.maps", "ports": {"http": 8808, "ws": 8809}}
```

### Shared MQTT Topics
```
msh/#                    Meshtastic protobuf (all repos)
meshforge/alerts         Alert feed (maps → NOC)
meshforge/alerts/{sev}   Severity-filtered alerts
```

---

## Config Locations

| Repo | Config Path | Format |
|------|------------|--------|
| meshforge | `~/.config/meshforge/` | JSON (SettingsManager) |
| meshforge-maps | `~/.config/meshforge/plugins/org.meshforge.extension.maps/` | JSON |
| meshing_around | `./mesh_client.ini` (local) | INI |

---

## Testing Standards

| Repo | Tests | Framework | CI |
|------|-------|-----------|-----|
| meshforge | 3,460 | pytest + regression guards + custom linter | Pre-commit hooks |
| meshforge-maps | 982 | pytest + ruff | GitHub Actions |
| meshing_around | 768 | pytest + flake8/black/isort | GitHub Actions |

---

*Made with aloha for the mesh community — WH6GXZ*

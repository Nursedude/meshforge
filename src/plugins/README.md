# MeshForge plugins

The three modules here are the **live** plugin set. They subclass
`IntegrationPlugin` from `src/utils/plugins.py` — that module is the plugin API
that actually runs.

| Module | What it does |
|---|---|
| `eas_alerts.py` | NOAA/NWS, USGS volcano and FEMA iPAWS alert feeds |
| `mqtt_bridge.py` | MQTT bridge with TLS + auto-reconnect |
| `meshing_around.py` | wrapper for the meshing-around mesh bot |

## History worth knowing (2026-09-15)

There used to be a **second** plugin system: a GTK4 SDK
(`src/core/plugin_base.py`, `manifest.json` + `type: "panel"`, discovery from
`~/.config/meshforge/plugins/`) with example panels in a top-level `plugins/`
directory. It was written for the GTK desktop app that was removed in v0.5.x.

Nothing loaded it. `src/launcher_tui/handlers/extensions.py` contains **zero**
references to `plugins/`, and no code anywhere called its `PluginManager`. The
examples and the NanoVNA GTK panel were deleted; the top-level `plugins/`
directory is gone.

⚠️ **One file in there was not cruft.** `plugins/nanovna_analyzer/nanovna_device.py`
was a complete, GTK-free NanoVNA antenna-analyzer driver — SWR, return loss,
complex impedance, best-match frequency — that no operator could reach. It now
lives at **`src/utils/nanovna.py`** with a TUI surface at
**RF & SDR > Antenna Analyzer**.

The lesson, for whoever next finds a "dead" tree: *verify the work-holder
before retiring it.* A directory nothing imports can still contain the only
copy of something you want.

## Writing a new plugin

Subclass `IntegrationPlugin` from `utils.plugins` and follow `eas_alerts.py`.
Config belongs under `~/.config/meshforge/plugins/`, resolved with
`utils.paths.get_real_user_home()` — never `Path.home()` (MF001).

# Meshtastic Site Planner Research

## Overview
The Meshtastic Site Planner is an open-source web utility for predicting node range and coverage using terrain-based RF simulations.

**Live Tool:** https://site.meshtastic.org/
**Source Code:** https://github.com/meshtastic/meshtastic-site-planner

## How It Works

### RF Propagation Model
- Uses **ITM/Longley-Rice** propagation model
- Powered by **SPLAT!** RF analysis software by John A. Magliacane, KD2BD
- Creates color-coded coverage maps with predicted RSSI values

### Terrain Data
- Uses **NASA SRTM** (Shuttle Radar Topography Mission) elevation data
- Terrain tiles streamed from **AWS Open Data**
- Accuracy: 90 meters resolution

## Key Features

### Coverage Prediction
- Color-coded signal strength maps
- RSSI predictions (regions with > -110 dBm have strong signal likelihood)
- Multi-node network simulation with overlapping coverage areas

### Customizable Parameters
- **Receiver Sensitivity** - How well radio decodes weak signals
- **Antenna Gain** - Adjust for different antenna types
- **Cable Loss** - Account for signal loss in cables/connectors
- **Clutter/Obstacles** - Average height of obstructions (buildings, trees)
- **Reliability Threshold** - e.g., 90% probability of coverage

## Technical Architecture

```
Browser (Web UI)
    ↓
Containerized API
    ↓
SPLAT! RF Analysis Software
    ↓
ITM/Longley-Rice Model + SRTM Terrain Data
    ↓
Coverage Map Output
```

## Integration Options for MeshForge

### Option A: Link/Embed (Implemented)
- ✅ **Implemented** - Button in Tools → RF Tools opens site.meshtastic.org
- Quick solution, no backend needed
- Uses official hosted tool

### Option A2: Built-in RF Line of Sight Calculator (Implemented)
- ✅ **Implemented** - Built-in LOS calculator in RF Tools section
- Input: Point A/B coordinates, antenna heights, frequency
- Uses Open-Elevation API for terrain data
- Calculates: distance, earth bulge, Fresnel zone, FSPL
- Web visualization with Chart.js elevation profile
- Shows: terrain, LOS line, 60% Fresnel zone, earth curvature
- Status: Clear (green) / Marginal (orange) / Obstructed (red)

### Option B: WebKitGTK Embed
- Embed the web tool directly in MeshForge GTK UI
- Requires: `gir1.2-webkit2-4.1` package
- Could pre-populate with current node positions

### Option C: Local Docker Deployment
- Run site-planner container locally
- Full offline capability
- Requires: Docker, significant disk space for terrain tiles

### Option D: API Integration
- If backend exposes an API, call directly from MeshForge
- Could auto-populate node locations from connected mesh
- Generate coverage reports programmatically

## Future Enhancements (per Meshtastic roadmap)
- Point-to-point link quality estimates
- Terrain visualization (3D?)
- Device-specific presets (different radio models)

## Related Tools
- **meshtastic-linkplanner** - https://github.com/meshtastic/meshtastic_linkplanner
- **HeyWhat'sThat** - https://www.heywhatsthat.com/ (general RF coverage)
- **Radio Mobile** - Classic RF planning software

## References
- Blog Post: https://meshtastic.org/blog/meshtastic-site-planner-introduction/
- Documentation: https://meshtastic.org/docs/software/site-planner/
- SPLAT! Software: https://www.qsl.net/kd2bd/splat.html
- ITM/Longley-Rice: https://www.its.bldrdoc.gov/resources/radio-propagation-software/itm/itm.aspx

---
*Last Updated: 2026-01-03*
*Status: Link integration implemented, API integration pending*

# Maps "Double Tap" — The Eye Candy Experience

> **Vision**: Make the map panel the graphical centerpiece of MeshForge. When someone opens our maps, they should immediately understand they're looking at a serious network operations tool — not a pin-on-a-map afterthought.

## Think Differently

Traditional mesh map tools show you where nodes **are**. We want to show what the network **does**:
- How data flows between nodes in real time
- Where coverage gaps exist before you deploy
- How link quality changes with weather, time of day, terrain
- What happens when a node goes offline (cascade effects)

This is the difference between Google Maps and an air traffic control display.

---

## State as of v0.4.7-beta (Phase 1 shipped — current map runtime is `src/utils/map_data_service.py` in 0.5.7-beta)

What we have today in `src/utils/coverage_map.py`:

- [x] Folium-based static HTML maps
- [x] Node markers with popups (battery, RSSI, hardware)
- [x] SNR-based link coloring (green → red)
- [x] Coverage radius circles (estimated from preset)
- [x] Offline tile caching (field-ready)
- [x] Multiple tile providers (OSM, Terrain, Satellite)
- [x] Heatmap layer (node density)
- [x] GeoJSON import/export
- [x] AREDN node overlay (when available)
- [x] RNS node discovery (UnifiedNodeTracker)

**Architecture Note (2026-01):** GTK4 UI removed. MeshForge is now TUI-only.
Live maps will open in system browser with local data server.

**Limitations:**
- Maps are regenerated from scratch each refresh (not incremental)
- No animation or real-time streaming
- Coverage radius is a guess (not RF-modeled)
- No node path history or movement tracking
- No interactive "what-if" planning tools

---

## Phase 1: Foundation — Live Map Engine

**Goal**: Replace static Folium regeneration with a live-updating map that doesn't lose state on refresh.

**Architecture (TUI-only):** Live map runs in system browser, fed by local HTTP server.

### Tasks

1. **Leaflet.js live map template**
   - Self-contained HTML/JS/CSS that polls for data updates
   - Node positions update without full page reload
   - Smooth pan/zoom preserved across data refreshes
   - File: Create `src/maps/live_map.html`

2. **Local map data server**
   - Python HTTP server serves map HTML + JSON data endpoint
   - `/api/nodes` returns current node GeoJSON
   - Polling interval: 5 seconds (configurable)
   - Auto-opens browser on launch
   - File: Create `src/maps/server.py`

3. **Node state machine visualization**
   - Online: solid marker with glow
   - Offline: grey, slightly transparent
   - New (< 5 min): pulsing ring animation
   - Alert (low battery, bad SNR): warning indicator
   - File: CSS/JS in `src/maps/live_map.html`

4. **TUI integration**
   - Add "Live Map" option to AI Tools menu
   - Launches map server, opens browser
   - Shows server URL for remote access
   - File: Modify `src/launcher_tui/ai_tools_mixin.py`

5. **Preserve Folium for export**
   - Keep CoverageMapGenerator for static HTML export
   - Live map is browser-based, export is Folium
   - Both use same data source (node_tracker)

---

## Phase 2: RF-Aware Visualization

**Goal**: Show the RF environment, not just node locations.

### Tasks

5. **Terrain-aware coverage modeling**
   - Use SRTM elevation data (free, downloadable)
   - Line-of-sight calculation between nodes
   - Fresnel zone obstruction visualization
   - Show where coverage actually reaches vs. theoretical circles
   - Dependencies: `src/utils/rf.py` (path loss models already exist)

6. **Link quality animation**
   - Lines between nodes pulse with data flow
   - Thickness = throughput estimate
   - Color = SNR quality (existing gradient)
   - Opacity = recent activity (dim = idle, bright = active)
   - Dashed line = intermittent/unreliable link

7. **Coverage prediction overlay**
   - Given antenna height + preset, model predicted coverage area
   - Terrain masking (hills block LoRa)
   - Color gradient: strong → marginal → no coverage
   - Toggle on/off per node

8. **Signal strength heatmap (from real data)**
   - Collect SNR/RSSI from node reports over time
   - Build actual coverage map from measurements (not predictions)
   - Show as heat overlay that builds up over time
   - Export as report: "Here's where your network actually reaches"

---

## Phase 3: Interactive Planning Tools

**Goal**: Let operators plan deployments before climbing towers.

### Tasks

9. **Click-to-place node simulator**
   - Click anywhere on map → "What if I put a node here?"
   - Shows predicted coverage circle
   - Shows which existing nodes would have line-of-sight
   - Estimates link budget to nearest neighbors
   - Antenna height slider (2m → 30m)

10. **Network resilience visualization**
    - Select a node → "What breaks if this goes offline?"
    - Highlight orphaned nodes (lose all paths)
    - Show alternate routing paths
    - Single-point-of-failure detection

11. **Deployment optimizer**
    - Given N nodes to deploy, suggest optimal placement
    - Maximize coverage area
    - Minimize single-points-of-failure
    - Consider terrain elevation data
    - Output: lat/lon list with predicted coverage %

12. **Time-series playback**
    - Record node states over time (SQLite)
    - Playback: watch network come online, nodes join/leave
    - Identify patterns: "This node drops every evening at 6pm"
    - Slider control: scrub through 24h/7d/30d

---

## Phase 4: Multi-Network Topology

**Goal**: Unified view of all three mesh ecosystems with logical relationships.

### Tasks

13. **Network layer toggle**
    - Meshtastic layer (LoRa nodes, channels)
    - Reticulum layer (RNS transports, paths)
    - AREDN layer (IP mesh, tunnel links)
    - Gateway layer (bridge connections between networks)
    - Each toggleable independently

14. **Logical topology view**
    - Switch from geographic → graph layout
    - Shows logical connections, not physical distance
    - Useful for understanding routing
    - Force-directed graph (D3.js or similar)
    - Highlight: gateway nodes that bridge networks

15. **Message flow visualization**
    - When a message traverses the bridge, animate the path
    - Source node → gateway → destination network → target node
    - Show queued messages (SQLite message_queue)
    - Real-time if possible, or replay from logs

16. **Multi-site view**
    - Zoom out: see multiple mesh clusters
    - Each cluster as a bubble showing health summary
    - Zoom in: explode into individual nodes
    - Useful for regional emergency comms networks

---

## Phase 5: Field Operations UX

**Goal**: Make maps usable on mobile/tablet in the field.

### Tasks

17. **Responsive/touch layout**
    - Map fills available space (no scrolling)
    - Large touch targets for markers
    - Swipe gestures: layers, zoom
    - Works on phone browser (7" screen minimum)
    - Double-tap marker → full node detail card

18. **GPS integration (operator position)**
    - Show "you are here" on map
    - Direction/distance to nearest node
    - Signal strength estimation at current position
    - "Walk towards better signal" compass indicator

19. **Offline-first architecture**
    - All map data cached locally
    - Works with no internet (field deployment)
    - Tile pre-caching by area (already exists in TileCacheManager)
    - Node data syncs when connectivity returns

20. **Quick status cards**
    - Tap node → slide-up card with key metrics
    - Battery, SNR, last heard, uptime, neighbors
    - "Actions" button: ping, trace, reboot (via meshtastic CLI)
    - Swipe between nodes

---

## Implementation Priority

### ✅ COMPLETE (Phase 1)
- Task 1: Leaflet.js live map — `web/node_map.html`
- Task 2: Data server + API — `src/utils/map_data_service.py`
- Task 3: Node state visualization — CSS animations in node_map.html
- Task 4: TUI integration — `src/launcher_tui/ai_tools_mixin.py`
- Task 5: Folium export preserved — `src/utils/coverage_map.py`

### Soon (Phase 2 — after stabilization)
- Task 6: Link quality animation
- Task 8: Real-data signal heatmap
- Task 5: Terrain-aware coverage (requires SRTM data)

### Later (Phase 3 — feature release)
- Task 9: Click-to-place simulator
- Task 10: Resilience visualization
- Task 12: Time-series playback

### Future (Phase 4-5 — major version)
- Tasks 13-16: Multi-network topology
- Tasks 17-20: Field operations UX

---

## Technical Decisions

### Why Leaflet.js over Folium for live maps?
- Folium generates static HTML (great for export, bad for live updates)
- Leaflet.js can receive incremental updates via JavaScript bridge
- WebKit's `run_javascript()` allows GTK → JS communication
- Leaflet plugins ecosystem: clustering, heatmaps, animated polylines

### WebKit Root Limitation
- WebKit refuses to run as root (sandbox restriction)
- **Solution**: Generate map HTML, open in system browser
- Or: run MeshForge in viewer mode (no sudo) for maps
- Long-term: privilege separation (map runs unprivileged)

### Data Storage for Playback
- Extend existing SQLite (message_queue.py pattern)
- Store: timestamp, node_id, lat, lon, snr, rssi, battery, online
- Retention: 30 days default, configurable
- Query: "give me all states for last 24h" for playback

### Performance Targets
- 500 nodes on map without lag
- Sub-second marker updates
- Smooth 60fps pan/zoom
- < 50MB memory for map component
- Tile cache: < 500MB disk for 50km radius

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/maps/live_map.html` | Create | Leaflet.js live map (HTML/JS/CSS all-in-one) |
| `src/maps/server.py` | Create | Local HTTP server + JSON API |
| `src/maps/data_provider.py` | Create | Node data aggregation for API |
| `src/utils/coverage_map.py` | Keep | Folium export (unchanged) |
| `src/utils/map_data_store.py` | Create | SQLite time-series for playback (Phase 3) |
| `src/utils/terrain.py` | Create | SRTM elevation, LOS calculation (Phase 2) |
| `src/launcher_tui/ai_tools_mixin.py` | Modify | Add "Live Map" menu option |

---

## Success Metrics

- **Demo-ready**: Someone sees the map and says "that's a real NOC"
- **Operational**: An operator can identify network issues from the map alone
- **Field-tested**: Works on a tablet with cached tiles, no internet
- **Informative**: Shows things you can't see from CLI tools alone
- **Beautiful**: Smooth animations, clear visual hierarchy, dark theme option

---

## References

- [Leaflet.js](https://leafletjs.com/) — lightweight, mobile-friendly maps
- [Leaflet.Realtime](https://github.com/perliedman/leaflet-realtime) — real-time GeoJSON updates
- [Leaflet.AnimatedMarker](https://github.com/openplans/Leaflet.AnimatedMarker) — smooth animations
- [SRTM Data](https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm-1) — free elevation data
- [D3.js Force Layout](https://d3js.org/) — for topology graph view
- Existing: `src/utils/rf.py` — path loss models, Fresnel calculations
- Existing: `src/utils/coverage_map.py` — Folium patterns, tile caching

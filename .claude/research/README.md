# MeshForge Research Documents

> Technical research, integration notes, and deep-dives for MeshForge development.

## Contents

### Core Integration Research

| Document | Description |
|----------|-------------|
| `dual_protocol_meshcore.md` | Dual-Protocol Mesh: Meshtastic <> MeshCore bridge research (Alpha) |
| `meshcore_proxy_analysis.md` | MeshCore-Meshtastic-Proxy firmware analysis for reliability patterns |
| `rns_comprehensive.md` | Complete Reticulum/RNS protocol documentation |
| `rns_complete.md` | RNS configuration and setup guide |
| `rns_integration.md` | RNS integration patterns for MeshForge |
| `rns_gateway_windows.md` | Windows-specific RNS gateway setup |

### Feature Integration

| Document | Description |
|----------|-------------|
| `xtoc_integration.md` | XTOC/XCOM tactical operations features, X1 protocol, integration roadmap |
| `hamclock_complete.md` | HamClock/NOAA API and integration (NOAA primary, HamClock optional) |
| `hamclock_decoupling.md` | HamClock decoupling session notes |
| `aredn_integration.md` | AREDN mesh network integration research |
| `meshforge_enhancement_todos.md` | Prioritized enhancement TODOs from proxy analysis |

### Meshtastic Technical Notes

| Document | Description |
|----------|-------------|
| `meshtastic_js_api.md` | Meshtastic JavaScript API reference |

### RF & Physical Layer Research

| Document | Description |
|----------|-------------|
| `lora_physical_layer.md` | LoRa PHY deep-dive: CSS modulation, SNR limits, link budget |
| `semtech_official_reference.md` | Official Semtech LoRa reference data |

### Tools & Planning

| Document | Description |
|----------|-------------|
| `maps_double_tap.md` | Maps vision & task breakdown |
| `gateway_setup_guide.md` | Gateway configuration guide (**SUPERSEDED 2026-06-04** — pre-mesh_bridge era; see `docs/GATEWAY_BRIDGE_CONFIG_GUIDE.md` + `docs/gateway_config_templates/`) |
| `firmware_viability.md` | Firmware compatibility analysis |

### Architecture & Design

| Document | Description |
|----------|-------------|
| `nginx_reliability_patterns.md` | NGINX patterns for MeshForge reliability |
| `local_mqtt_architecture.md` | Local MQTT bridging design |
| `uconsole_portable_noc.md` | uConsole portable NOC design |
| `fleet_architecture_2026_06_03.md` | Full fleet diagnosis: per-box roles, routing/messaging/telemetry/RF/transport map, gateway-cornerstone verdict, federation+cloud flow, 4-theme roadmap. +2026-06-04: Theme-A shipped, dual-radio/downlink data plane (§7.6), live config matrix + drift findings (§7.7) |
| `dudeclaw_second_brain_2026_06_17.md` | **Deep-research findings** (workflow `wf_0e34d5bd-51a`, 21/25 claims 3-vote-verified) for the dude-claw second-brain arc: autonomy ladder = Run-Time Assurance/Simplex (verified filter, not "trust the agent"); edge/brain split (guardrail migrates to the edge); cited power budget (ESP32-S3+WiFi dominant, GPS near-free, LoRa-TX = 1A peak); DTN/BPv7 solo mode (Pi-tier only); 3T cyber-physical naming. Answers the kickoff `.claude/plans/dudeclaw_second_brain_2026_06_18.md` |

### Session Notes

| Document | Description |
|----------|-------------|
| `session_rns_address_in_use.md` | RNS address-in-use troubleshooting session |
| `pytest_exit_status_flap_2026_07_28.md` | pytest exits 0 while reporting a failure (~50% of full-suite runs, byte-identical output). Its own `sessionfinish` hook says `TESTS_FAILED: 1` — the status is lost in CPython shutdown, where ~25 leaked non-daemon `ThreadPoolExecutor` workers are joined. Heisenbug: instrumentation suppresses it. 6 hypotheses refuted by measurement; cure is consumer-side (no gate may trust an exit code alone) |

---

*24 research documents. Updated 2026-07-28.*

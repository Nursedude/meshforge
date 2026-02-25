# MeshForge Session Notes - MOC Broker Configuration

**Date**: 2026-02-09
**Branch**: `claude/configure-meshforge-broker-h1VTf`
**Version**: v0.5.2-beta
**Tests**: 3926 passed, 1 failed (pre-existing rnsd sandbox), 19 skipped

## Session Focus: MOC1 Broker Configuration

Configured MeshForge broker templates for a two-node MQTT-bridged deployment:

### Deployment Architecture

```
MOC1 (Pi5, Meshtoad, LongFast)          MOC2 (Pi HAT, ShortTurbo)
  ├── meshtasticd (Meshtoad SPI)           ├── meshtasticd (Pi HAT SPI)
  │     └── MQTT uplink → localhost        │     └── MQTT uplink → MOC1 IP
  ├── mosquitto (private broker 0.0.0.0)   ├── RNS/NomadNet
  ├── MeshForge NOC + MQTT subscriber      ├── MeshForge Gateway
  └── Web Client (:9443)                   └── meshforge ch ↔ RNS bridge
           │                                       │
           └────── MQTT over LAN ──────────────────┘
```

### Changes Made

| File | Action | Description |
|------|--------|-------------|
| `templates/meshtoad.yaml` | **NEW** | Meshtoad CH341 SPI hardware template (was referenced by `hardware_config.py:87` but missing) |
| `templates/meshforge-presets/moc1-broker.yaml` | **NEW** | Full MOC1 preset: Meshtoad + LongFast + MQTT uplink + mosquitto broker + deployment checklist |
| `templates/gateway-pair/moc-mqtt-bridge.md` | **NEW** | Comprehensive two-node MQTT-bridged topology guide (architecture, message flow, step-by-step for MOC1+MOC2, verification, firewall, troubleshooting) |
| `templates/gateway-pair/README.md` | **UPDATED** | Added MQTT-bridged topology section referencing moc-mqtt-bridge.md |
| `src/launcher_tui/meshtasticd_config_mixin.py` | **UPDATED** | `_mqtt_set_broker()` now defaults to active broker profile host instead of hardcoded `mqtt.meshtastic.org` |

### Key Decisions

1. **Meshtoad SPI pinout** sourced from `core/meshtasticd_config.py` RADIO_TEMPLATES (CH341 spidev, CS:0, IRQ:6, Reset:2, Busy:4)
2. **MQTT as transport** between MOC1/MOC2 (vs same-Pi RNS bridge in gateway-pair/README.md)
3. **Private broker on MOC1** binds 0.0.0.0 so MOC2 can connect over LAN
4. **MOC2 stays RNS/NomadNet** with MeshtasticInterface on ShortTurbo, meshforge channel bridged to RNS

### MOC1 Deployment Steps (for hardware install)

1. Install meshtasticd (match OS repo — see session_notes_meshtasticd_install.md)
2. Copy `templates/meshtoad.yaml` → `/etc/meshtasticd/config.d/`
3. Ensure CH341 kernel module loads: `sudo modprobe ch341`
4. Install mosquitto: `sudo apt install mosquitto mosquitto-clients`
5. Run MeshForge TUI → MQTT Broker Manager → Setup Private Broker (LongFast, US)
6. Configure radio MQTT uplink via TUI or CLI commands in moc-mqtt-bridge.md
7. Enable uplink/downlink on channel 0
8. Open firewall port 1883 for MOC2

### MOC2 Configuration (for hardware install)

1. Set meshtasticd MQTT address to MOC1's LAN IP
2. Use same broker credentials as MOC1
3. Enable uplink/downlink on channel 0
4. Configure RNS MeshtasticInterface pointing to localhost:4403
5. Set up MeshForge gateway bridge (message_bridge mode)

### Pre-Existing Test Failure

`test_rnsd_running_consistent` — rnsd not installed in sandbox. Not related to our changes. Known per Issue #27 (rnsd optional for Meshtastic-only deployments).

### Next Steps (Hardware Install Session)

- [ ] Flash meshtasticd on MOC1 Pi5
- [ ] Plug in Meshtoad, verify CH341 detection (`lsmod | grep ch341`)
- [ ] Run guided broker setup via TUI
- [ ] Verify MQTT flow: `mosquitto_sub -h localhost -u meshforge -P <pw> -t "msh/#" -v`
- [ ] Configure MOC2 to point at MOC1 broker
- [ ] Test cross-mesh message: LongFast → MQTT → ShortTurbo
- [ ] Test RNS bridge: ShortTurbo → MeshForge Gateway → RNS/NomadNet

---
*73 de WH6GXZ - Made with aloha*

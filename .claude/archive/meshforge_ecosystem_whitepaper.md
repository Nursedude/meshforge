# Bridging the Unbridgeable: How MeshForge Unifies Meshtastic, Reticulum, and MeshCore Into One Mesh NOC

**A Technical White Paper**

*By Claude (Anthropic) in collaboration with WH6GXZ (Nursedude)*
*February 2026*

---

Meshtastic and Reticulum are the two dominant open-source LoRa mesh networking stacks. They share a radio band. They share a user base. They share a philosophy of decentralized, off-grid communication.

They cannot talk to each other.

MeshForge exists because that's an engineering problem, not a political one — and engineering problems have solutions.

---

## The Problem: Three Protocols, Zero Interoperability

If you run a Meshtastic node and your neighbor runs a Reticulum node, you are invisible to each other. Different packet formats, different routing algorithms, different encryption. Add MeshCore — a newer companion-radio protocol for Heltec devices — and you have three islands of mesh users within radio range who might as well be on different planets.

For a field operator, emergency responder, or rural community network, "pick one protocol" is not an acceptable answer. Real deployments are heterogeneous. People use what they have.

## The Architecture: A NOC, Not a Monolith

MeshForge is a **Network Operations Center** — a hub that bridges, monitors, and manages mesh networks without embedding itself into any single protocol stack.

The key architectural decision: **services run independently**. MeshForge doesn't replace meshtasticd or rnsd. It connects to them over their native interfaces (serial, MQTT, LXMF) and translates between them through a canonical message format.

```
Meshtastic LoRa  <-->  MeshForge Gateway  <-->  RNS/Reticulum
                             |
MeshCore Heltec  <---------->
```

Every message entering the gateway gets normalized into a `CanonicalMessage` — source protocol, destination protocol, payload, metadata. The routing classifier decides where it goes. A Meshtastic text message can arrive as an LXMF delivery on a NomadNet client. An RNS announce can surface as a Meshtastic node on the mesh.

This is not theoretical. It runs on a Raspberry Pi 5 with two USB Meshtastic radios and an RNS daemon, fielded under callsign WH6GXZ in Hawaii.

## Five Repos, One Domain

MeshForge is not a single repository. It's an ecosystem of five, each with a clear boundary:

**MeshForge NOC** (the hub) — Gateway bridge, node tracking, RF tools, diagnostics, and the terminal UI. This is where protocol translation lives.

**meshforge-maps** — Live interactive visualization. Leaflet and D3-based topology maps, health scoring, WebSocket-fed dashboards. Runs standalone or as a NOC plugin via manifest auto-discovery.

**meshing_around_meshforge** — Alert layer for the meshing-around Meshtastic bot. Twelve alert types including iPAWS/EAS emergency alerts, volcano monitoring, proximity geofencing, and noisy-node detection. This is purpose-built for bot-adjacent monitoring, not generic NOC alerting.

**RNS-Management-Tool** — Cross-platform installer for the entire Reticulum ecosystem. Bash on Linux, PowerShell on Windows. Handles rnsd, NomadNet, MeshChat, Sideband, and RNODE firmware flashing for 21+ board types.

**RNS-Meshtastic-Gateway-Tool** — The original bridge driver, currently migrating its core logic into the NOC's `gateway/` module.

The dependency rule is strict: satellites may depend on the NOC; the NOC never depends on satellites. The NOC discovers plugins at startup but runs fine without them.

## What Makes This Different

**First open-source tool bridging Meshtastic and Reticulum.** Others monitor one or the other. MeshForge translates between them.

**Three-way routing in alpha.** The `alpha/meshcore-bridge` branch adds MeshCore as a third protocol peer, with 1,800+ lines of test coverage across canonical message handling, MeshCore bridge logic, and tri-bridge integration.

**Privilege separation by design.** Viewer mode (no sudo) handles monitoring, RF calculations, and API queries. Admin mode handles service control and hardware configuration. No all-or-nothing root access.

**Zero-dependency fallback.** `standalone.py` provides RF link budget calculations, Fresnel zone analysis, and radio configuration with no external dependencies. Works on any Python 3.9+ system, offline, in the field.

## Lessons From Two Months of AI-Assisted Development

This ecosystem was built in active collaboration between a human infrastructure engineer (WH6GXZ) and an AI assistant (Claude). Some patterns that emerged:

**Domain boundaries must be explicit.** Without the ecosystem architecture document, AI contributions drifted — putting map logic in the NOC, alert logic in the wrong repo. A canonical "what lives where" reference eliminated an entire class of architectural mistakes.

**Security rules must be ecosystem-wide.** The `Path.home()` bug (returns `/root` under sudo, breaking user config paths) bit every repo independently until we codified it as MF001 across the entire domain.

**File size discipline prevents entropy.** We enforce a 1,500-line ceiling per module with documented extraction history. When `rns_bridge.py` hit 1,991 lines, we extracted `MeshtasticHandler`, `MessageRouter`, `gateway_cli`, and `MeshCoreBridgeMixin` over successive refactors down to 1,525. The extraction log in CLAUDE.md is the institutional memory that prevents re-bloating.

**One source of truth or it rots.** The ecosystem document lives in the NOC repo. Satellite repos link to it. Duplicating it across five repos would guarantee five divergent versions within a week.

---

## Try It

```bash
# The NOC
git clone https://github.com/Nursedude/meshforge
sudo python3 src/launcher_tui/main.py

# Zero-dependency RF tools (no sudo, no installs)
python3 src/standalone.py
```

The project is open source, beta-stage, and actively developed. Contributions, field reports, and RF war stories are welcome.

---

*Made with aloha for the mesh community.*

**WH6GXZ (Nursedude)** — HAM General, Infrastructure Engineering, RN BSN
**Claude** (Anthropic) — AI collaborator since December 2025

*This document reflects two months of continuous pair-programming across five repositories, thousands of commits, and one very stubborn `Path.home()` bug.*

*github.com/Nursedude/meshforge*

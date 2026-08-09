# MeshForge

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
</p>

<p align="center">
  <strong>Mesh Network Operations Center</strong><br>
  <em>Meshtastic + Reticulum + AREDN — Build. Test. Deploy. Monitor.</em>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshforge"><img src="https://img.shields.io/badge/version-0.6.2--beta-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.9+-yellow.svg" alt="Python"></a>
  <a href="https://github.com/Nursedude/meshforge/actions"><img src="https://img.shields.io/badge/tests-passing-brightgreen.svg" alt="Tests"></a>
</p>

<p align="center">
  <a href="https://nursedude.substack.com">Development Blog</a> |
  <a href="https://github.com/Nursedude/meshforge/issues">Report Issues</a> |
  <a href="docs/development.md">Contribute</a>
</p>

---

## What is MeshForge?

**MeshForge turns a Raspberry Pi into a mesh network operations center.**

Modern mesh networks are fragmented. Meshtastic nodes can't talk to Reticulum
nodes. AREDN operates on a different layer entirely. Each ecosystem has its own
tools, its own interfaces, its own learning curve. MeshForge is one interface
over all of them, plus the gateway that actually bridges messages between
incompatible meshes.

It runs on **one box**. No cloud dependencies, no subscriptions, no account.
A $35 Pi you can SSH into from anywhere.

```bash
sudo python3 src/launcher_tui/main.py
```

**Built for:** HAM operators, emergency comms teams, off-grid builders, and mesh
enthusiasts who want professional-grade network visibility without the complexity.

---

## Composable by design

MeshForge is **not** one monolithic thing you install whole. Each component
stands on its own, and you run the ones your site needs. A node that only does
coverage mapping never installs a gateway; a gateway box never needs the map
server.

| Component | What it does | Needs |
|-----------|--------------|-------|
| **Radio + RF tools** | meshtasticd, radio config, link budgets, site planning | LoRa radio |
| **Coverage maps** | SNR-based link quality, terrain-aware coverage | Radio + position data |
| **NOC map** | Live Meshtastic *and* RNS nodes on one map | Any source above |
| **Gateway bridge** | Meshtastic ⇄ Reticulum over MQTT, zero interference | Radio + RNS |
| **Reticulum (RNS)** | Encrypted transport, LXMF messaging, propagation | Network or radio |
| **Traffic inspection** | Wireshark-grade packet dissection, both networks | MQTT or radio |
| **AREDN** | Monitoring integration for the AREDN layer | AREDN node |
| **AI diagnostics** | Offline symptom → cause → fix, no API key required | — |
| **Watchdog + mini-dudeai** | One probe per failure class; briefs and pages | — |

You choose the combination at install with a **deployment profile** —
`radio_maps`, `monitor`, `meshcore`, `gateway`, or `full` — and change it later
without reinstalling:

```bash
python3 src/launcher.py --profile gateway   # or omit to auto-detect
```

### Sister project and extensions

Each of these is a separate, independently-runnable thing — install only what
you want.

| Project | Role |
|---------|------|
| **[MeshAnchor](https://github.com/Nursedude/meshanchor)** | Sister NOC, MeshCore-primary radio. Same reliability spine, different mesh. |
| **[MeshForge Maps](https://github.com/Nursedude/meshforge-maps)** | Multi-source map (Meshtastic, AREDN, MeshCore, MQTT, RNS), port 8808 |
| **NomadNet** | Terminal LXMF client for Reticulum users |
| **MeshChatX** ([setup](docs/MESHCHATX_IN_MESHFORGE.md)) | Browser LXMF chat, isolated RNS instance, port 8000 |
| **Dude-claw** | Pi brain driving WireClaw ESP32 edge nodes over NATS — sensors → rules → actuators |
| **Meshing Around** (client/monitor) | Alerting + maps-writer layer for the meshing-around bot — **not** the bot itself |

> **Two "meshing-around" repos — don't conflate them:** `Nursedude/meshing_around_meshforge`
> (above) is the **client/monitor + alerting** layer. The actual command **bot** is
> a separate fork, `Nursedude/meshing-around` (a T2 fork of `SpudGunMan/meshing-around`,
> governed by its `FORK.md`).

Extensions are managed as systemd services, and the TUI handles the whole
lifecycle: install (clone, venv, deps, service), auto-diagnose (wrong user,
missing venv, permission errors), one-click repair, and start/stop/logs/health.

---

## Quick start

```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
sudo bash scripts/install_noc.sh      # guided install
sudo python3 src/launcher_tui/main.py # the NOC
```

Runs on **Pi 3B, Pi 4, or Pi 5** (64-bit). You don't need a Pi 4 to start — a
**Pi Zero 2 W** (~$15) is the recommended low-cost board for a lightweight or
single-purpose node, because the software is tuned to fit the box.

Zero-dependency RF tools, no install required:

```bash
python3 src/standalone.py
```

**Full instructions — hardware, install, first run, upgrades: [docs/install.md](docs/install.md)**

---

## What Works (v0.6.2-beta)

Field-tested on a live multi-site fleet.

- **Gateway bridge** — Meshtastic ⇄ Reticulum via MQTT, zero radio interference
- **NOC + coverage maps** — both protocols on one map, SNR-coloured links
- **Traffic inspection** — packet dissection for Meshtastic, RNS, and MQTT
- **RF engineering** — link budgets, Fresnel zones, terrain, site planning
- **Radio config** — meshtastic CLI integration, transient and non-interfering
- **AI diagnostics** — offline symptom→cause→fix; optional Claude tier
- **Reliability spine** — per-failure-class probes, calibrated status reporting

**The full inventory, with what's proven vs. what needs field validation:
[docs/capabilities.md](docs/capabilities.md)**

---

## Documentation

| Doc | What's in it |
|-----|--------------|
| [Install](docs/install.md) | Hardware, install, first run, upgrade paths |
| [Capabilities](docs/capabilities.md) | Full feature inventory, AI tiers, coverage maps |
| [Architecture](docs/architecture.md) | How the pieces fit; where the code lives |
| [Configuration](docs/configuration.md) | Every knob and its file |
| [Development](docs/development.md) | Tests, gates, contributing |
| [The Lab](docs/the-lab.md) | The dev fleet, live demo, research foundation |

Deeper technical material lives in [`docs/`](docs/) — gateway deployment,
MeshChatX, environment variables, and the research notes.

---

## The Lab

MeshForge is developed on a live multi-box fleet across home-LAN and AREDN-linked
remote sites. **That fleet is the lab, not the product** — every box in it started
as a standalone install and was federated afterwards, which is the same path you'd
take. The fleet exists to find failures early, not because MeshForge needs one.

See [docs/the-lab.md](docs/the-lab.md) for how it's run, the live demo, and the
research foundation.

---

## Resources

| Resource | Link | Relation |
|----------|------|----------|
| Development Blog | [nursedude.substack.com](https://nursedude.substack.com) | Project updates |
| Meshtastic Docs | [meshtastic.org/docs](https://meshtastic.org/docs/) | Primary radio network |
| Reticulum Network | [reticulum.network](https://reticulum.network/) | Bridge target (encrypted transport) |
| AREDN Mesh | [arednmesh.org](https://www.arednmesh.org/) | Monitoring integration |
| RTL-SDR | [rtl-sdr.com](https://www.rtl-sdr.com/) | Spectrum analysis (planned) |
| uConsole AIO V2 | [hackergadgets.com](https://hackergadgets.com/products/uconsole-aio-v2) | Field hardware (Q2 2026) |
| MeshCore | [meshcore.co](https://meshcore.co/) | Optional gateway handler on MeshForge; primary radio on [MeshAnchor](https://github.com/Nursedude/meshanchor) |

---

## License

GPL-3.0 — See [LICENSE](LICENSE)

---

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="32" height="32"/><br>
  <strong>MeshForge</strong><br>
  <em>Made with aloha for the mesh community</em><br>
  WH6GXZ | Hawaii
</p>

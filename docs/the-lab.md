# The Lab

MeshForge is developed on a live multi-box fleet. That fleet is the LAB, not the product — the product runs on one box. This is where the fleet story, the live demo, and the research foundation live.

### How It's Operated — a Human + AI Fleet

The reference fleet (multiple Pis across home-LAN and AREDN-linked remote sites, plus a cloud peer) is run as a **human+AI collaboration**: a HAM operator pair-engineering with Claude — architecture, code, and live fleet operations over SSH — while two on-box organs keep watch between sessions:

- **The watchdog** — one probe per failure class learned in the field (wedged RPC, fd leaks, permission drift, role drift, channel silence…). Every real incident becomes a probe; nothing is diagnosed twice.
- **mini-dudeai** — a tiny on-box rule engine that turns watchdog signals into per-box briefs, fleet-wide rollups, and pages. The next session warm-starts from what mini saw.

The loop compounds: incidents → probes → signals → briefs → faster sessions. A recent data point — a brand-new Pi at a remote AREDN site went from bare SSH to fully-federated, lab-measured fleet member *in one evening*, surfacing (and fixing) two latent cross-repo bugs along the way. The collaboration record lives in [`docs/substack/`](substack/) and the development blog.

---

## Live Demo

See the Hawaii fleet's view of the LoRa mesh — node positions, SNR-coloured link quality, NOAA space weather, and active space-weather alerts — at **[meshforge-maps.ddns.net](https://meshforge-maps.ddns.net/)**.

The demo runs on a small VPS that mirrors a regional GeoJSON snapshot from one fleet box every two minutes via `scripts/cloud/push_snapshot.sh`. The mesh itself stays on-prem; only the map data is published. The page is mobile-friendly (tested on iOS, 13" MacBook, larger displays) and shows per-protocol counts (Meshtastic / AREDN / MeshCore) alongside SFI, X-ray, geomagnetic storm level, and per-band HF condition pills.

To stand up your own VPS demo, see `scripts/cloud/README.md` — one-shot setup via `setup_vps.sh <domain>` on a fresh Ubuntu 24.04 box.

---

## Research & Technical Foundation

MeshForge development is backed by 22 technical research documents covering
protocol analysis, integration architecture, and RF engineering. These inform
every major design decision in the codebase.

### Multi-Protocol Bridging

Deep analysis of bridging incompatible mesh ecosystems:
- MeshCore ↔ Meshtastic dual-protocol bridge architecture (3-way routing design)
- MeshCore reliability patterns: canonical packet format, MQTT origin filtering, lenient parsing
- Gateway scenario analysis: multi-protocol deployment topologies and trade-offs

### Tactical Operations & ATAK Interoperability

Research into tactical messaging standards and the ATAK ecosystem:
- [XTOC/XCOM](https://www.mkme.org/xtocapp/) integration analysis: X1 compact packet protocol,
  structured message templates, offline-first tactical operations
- ATAK ecosystem: [Meshtastic ATAK Plugin](https://github.com/meshtastic/ATAK-Plugin) (CoT XML,
  PLI, GeoChat, fountain code file transfer), [Akita MeshTAK](https://github.com/AkitaEngineering/Akita-MeshTAK)
  (SOS alerts, device health), real-world deployments (300+ personnel exercises, SAR operations)
- **Implemented on main (v0.5.4):** 8 tactical templates (SITREP, TASK, CHECKIN, ZONE, RESOURCE,
  MISSION, EVENT, ASSET), X1 codec for XTOC interop, transport-aware chunking, ham compliance
  (CLEAR/SECURE modes), QR code transport, tactical map with KML/CoT export for ATAK/WinTAK

### RF & Physical Layer

- LoRa PHY deep-dive: CSS modulation, spreading factors, SNR limits, link budget calculations
- Official Semtech LoRa reference data for engineering-grade RF planning

### Protocol Documentation

- Complete Reticulum/RNS protocol documentation, configuration guides, and integration patterns
- Meshtastic JavaScript API reference
- AREDN mesh network integration research

### Architecture & Infrastructure

- MQTT zero-interference bridging design (the foundation of v0.5.4's gateway)
- NGINX reliability patterns applied to mesh networking APIs
- uConsole AIO V2 portable NOC design for field operations

Full research library: [`.claude/research/`](../.claude/research/README.md)

---


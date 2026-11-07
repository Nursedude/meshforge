# Landscape read — 2026-11-04 (three new peers vs "what only we do")

> **Verdict:** three projects already relay Meshtastic↔MeshCore text (MeshMonitor,
> mesh-api, AMMB), and CoreScope owns MeshCore observer analytics. The defensible
> ground is narrower than the ROADMAP "Why we win" table first claimed: **RNS/LXMF
> as the hub, with per-leg delivery truth**. Lead with that alone.
>
> Source: each project's own README, read 2026-11-04 (the domain audit session).
> Star/fork counts are that day's; **re-check before quoting** — they move fast.
> These rows lived only on the shared roadmap PAGE until 2026-11-06, when the page
> became generated (`scripts/roadmap_html.py`) and the operator chose this doc +
> a one-line ROADMAP pointer (MF012 caps ROADMAP.md).

| Them | What they do well | What only we do |
|---|---|---|
| **CoreScope** (`Kpa-clawbot/CoreScope`, GPL-3.0; MeshCore-only analyzer — e.g. [meshrheinland](https://corescope.meshrheinland.de/#/home)) | 196★ / 73 forks, many regional instances. Go + SQLite, Docker amd64/arm64. MQTT ingest from many observers (`meshcoretomqtt`, multi-broker, per-IATA filter); packet tracing across observers, route patterns, hop-prefix candidate resolver scored by an observer neighbor graph, hash-collision inspector, clock-skew detection, advert-signature validation at ingest, channel decryption, VCR playback (v3.6.0) | Not a bridge: it observes, never transmits; no Meshtastic, no RNS. We do NOT beat it at MeshCore map/analytics (MAPV-1, CAP-1 are behind it). Opportunity, not threat. |
| **mesh-api** (`mr-tbot/mesh-api`, GPL-3.0) | 183★ / 20 forks. Meshtastic + MeshCore as core radios, `bridge_enabled` MT↔MC channel mapping, MCP server, 30+ plugins, web dashboard (self-described: MeshCore/multi-radio widely untested) | RNS/LXMF hub + per-leg delivery truth. MT↔MC text relay alone is NOT unique |
| **AMMB** (Akita Meshtastic-MeshCore Bridge) | 108★, updated 2026-09. Meshtastic ↔ MeshCore over serial or MQTT, sensor-data translation | Same as mesh-api: only the RNS hub + delivery truth are ours |

## CoreScope — proposed items (operator to place; NOT on the roadmap)

Researched in [Cascadia Mesh Study § CoreScope](https://claude.ai/artifact/UedXXTf9LoQXELqWPBdtdH#corescope) (10-31).

- **MC-OBS** [MA] — MeshAnchor reads companion push 0x88 and publishes
  `meshcore/{IATA}/{PUBKEY}/packets` as an observer.
- **HI-OBS** [MA] — feed the community's existing `corescope.hawaiimesh.net`
  (MQTT 1884, per their wiki) and propose per-observer credentials + TLS — not a
  rival self-hosted instance (corrected 11-04; see the Hawaii Meshnet analysis).
- **CS-READ** [MF] — the MOC reads `/api/scope-audit` beside Meshtastic/RNS/AREDN,
  never rebuilds it (replaces the region-check build idea).
- **CS-ISSUE** — operator files the two CoreScope doc defects (route-type labels in
  `decoded.proto`; `scope-stats` totals) in his own words.

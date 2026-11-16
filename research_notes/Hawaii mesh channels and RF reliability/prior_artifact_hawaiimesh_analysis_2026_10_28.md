# Prior artifact: "Hawaii Meshnet (hawaiimesh.net) — Critical Analysis & Nursedude Engagement Plan" (2026-10-28)

Source: operator's own Claude Doc, https://claude.ai/artifact/VygTjXDYPGpgW4PLPVDaqM (read via Claude Docs connector 2026-11-16, rev 8). Captured verbatim-in-substance by coordinator. Claims below are as of 2026-10-28 and were read from hawaiimesh.net pages that session.

## Bottom line (as written then)
- Small, real, dual-protocol community: ~130 Meshtastic + ~65 MeshCore nodes (self-reported Oct 2026; site footnote says accuracy not guaranteed).
- hawaiimesh.net is a Wiki.js coordination wiki ("Hawaii's off-grid network for everyone"), Discord + Telegram live community, CoreScope MeshCore analyzer at corescope.hawaiimesh.net, offline map tile pack, Google Earth coverage file. Listed as Hawaii's group on Meshtastic Local Groups page. Radios/bridges/bots are run by individuals — not infrastructure.
- WH6GXZ is already a contributor: the HawaiiNet channel's "wx" weather bot is listed as run by WH6GXZ.
- Operator context (2026-11-16, user message): the user has a hawaiimesh account and a MeshAnchor "rs1" connected to it (presumably CoreScope observer / MeshCore side).

## Meshtastic channels (self-reported, https://hawaiimesh.net/networks/meshtastic/meshtastic-channels)
| Channel | Key | Purpose/operator |
|---|---|---|
| LongFast | AQ== (default) | Bridged with neighbor islands |
| HawaiiNet | AQ== | Bot interaction, "wx" — run by WH6GXZ |
| hawaiimesh | AQ== | Bridge to MeshCore — run by pmow |
| alerts | AQ== | Bridge to MeshCore — run by WH6EPS |

## MeshCore channels (https://hawaiimesh.net/networks/meshcore/meshcore-channels)
Public (published key, general); #testing (name-derived); #hawaiimesh (name-derived, bridge testing, pmow); #alert (name-derived, emergency alerts, Delta-EDC).

## Radio settings prescribed
- Meshtastic: Region US, preset LONG_FAST (250 kHz). Wiki says enable "OK to MQTT", disable "Ignore MQTT" for neighbor-island delivery.
- MeshCore: 910.525 MHz, SF7, CR5, 62.5 kHz; some operators report better links with CR8.

## Observer network
Wiki asks MeshCore Wi-Fi board owners to flash observer firmware and publish to corescope.hawaiimesh.net MQTT port 1884 with a shared username/password printed on the page.

## Neighbors
- Oahu Mesh (hamradiohawaii.com/mesh): solar Meshtastic repeaters, south Oahu coverage fall 2025; relationship unstated.
- Hawaii AREDN MESH (HEARDn, Hawaii AllStar): 80+ Part 97 nodes and inter-island gateways by 2020 per old.hawaiiares.net.

## Critical analysis table (then)
1. Prescribed presets under FCC 500 kHz floor (15.247(a)(2) plain reading; no ruling/enforcement) — High. Sources Philly Mesh 2026-09-02, Hackaday 2026-09-17, NodakMesh.
2. Newcomers land on wrong preset: doc claimed "Meshtastic 2.8 defaults new US nodes to LongTurbo, which cannot hear LongFast" (Meshtastic blog "Let's talk about 2.8"). **CONFLICT: repo `.claude/research/landscape_2026_11_09.md:50` records this claim as REFUTED at the source. Needs re-check.**
3. Inter-island "off-grid" depends on internet MQTT (wiki recommends OK-to-MQTT on / Ignore-MQTT off) — High for EmComm claims.
4. Every Meshtastic channel uses AQ== — readable by anyone; 2.8 blocks precise position on known-key channels — Medium.
5. Alert and bridge paths single-operator (pmow, WH6EPS); no failover/rate limits/loop rules/uptime documented; names mismatch (alerts vs #alert) — Medium–High.
6. Observer MQTT credentials public → CoreScope spoofable — Medium.
7. Documentation skeleton (Getting Started ends "???" then "Win"; Hardware page empty) — Medium.
8. No governance/partner ties, no emergency-use disclaimer — Medium.
9. Small self-reported numbers, no coverage/delivery metrics — Low.
What they do well: early MeshCore adoption, invite observers rather than centralize, wiki open to Suggest Edits.

## Engagement plan (then)
Now: offer (not pitch) in Discord; suggest LongTurbo warning edit; document wx bot; run CoreScope observer; fill Recommended Hardware with fleet-proven boards.
Next: MeshAnchor hot-standby bridge for alerts↔#alert, hawaiimesh↔#hawaiimesh with published rules (dedupe, loop prevention, rate limit, attribution, health beacon); LongFast↔LongTurbo gateway pilot with delivery/airtime numbers; meshforge-maps on CoreScope+Meshtastic MQTT; per-observer creds + TLS.
Later: preset-migration proposal on measured data; AREDN inter-island backhaul with HEARDn/Hawaii ARES (Part 97 question on AQ== is theirs); "who runs what" page + emergency-use disclaimer.
Self-critique: don't pitch MeshForge as platform; don't introduce RNS; don't become new SPOF; don't decide preset migration; don't overstate lead (MeshMonitor); don't call any of it emergency-grade while inter-island depends on internet.

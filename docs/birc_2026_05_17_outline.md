# BIRC Talk 2026-05-17 — Outline (working draft)

> Big Island Radio Club, third Sunday of May. Audience: hams, mostly Meshtastic-curious, mostly haven't heard of Reticulum or MeshCore. Target ~35 min talk + ~10 min Q&A. Slides + 5-min live demo. Drafted 2026-05-10 from substack voice + current fleet state + the 7 SVGs in `docs/birc_2026_05_17_assets/`.

---

## Slide-by-slide

### 1. Title (1 min)

**Display**: MeshForge logo · "Five Pis, One AI, and the Mesh We're Building" · WH6GXZ · BIRC 2026-05-17

**Speaker**: "Aloha. I'm Shawn — WH6GXZ — General class, RN by day, built this thing called MeshForge with help from an AI partner I call Dude AI. Today's talk is what it is, what's running on the island right now, and how five Raspberry Pis and one AI ended up doing things I couldn't do alone."

**Note**: open at podium without slides, set the tone of personal-and-technical. The AI co-author angle is novel for BIRC and worth landing first.

---

### 2. Who I am (90 sec)

**Display**: BBN → GTE → nursing (BSN) → HAM (WH6GXZ) → MeshForge

**Speaker beat**: BBN built ARPANET. Y2K. RN BSN. HAM since [year]. Same pattern recognition every step — "what fails at 3 AM."

**Why**: gives credibility without bragging. Hams will recognize BBN as the ARPANET company.

---

### 3. The problem (2 min)

**Display**: 3 mesh-network logos side by side — Meshtastic / MeshCore / Reticulum — with a strikethrough between each pair.

**Speaker**: "Three open-source mesh networks. Each great in its niche. None of them talk to each other. If you're on LongFast you can't see ShortTurbo without a second radio. If you're on Reticulum you can't see Meshtastic at all. MeshCore, same story. Nobody was building the bridges. So I did."

**Audience anchor**: most hams in the room are on Meshtastic LongFast. The "you can't see ShortTurbo" point lands hard — they've experienced this.

---

### 4. What's actually on the air over Hawaii right now (3 min) — DEMO HOOK

**Display**: `hero_map.svg` — Hawaii silhouette with 641 LongFast nodes pinned (color-coding by SNR was anticipated but the captured `packets` table has no SNR column; renders as uniform pins).

**Numbers (verified on the capture box, 2026-05-07)**:
- 5.1-day capture window (Apr 28 → May 3)
- 16,235 packets observed
- 605 nodes in HI bbox
- 231 unique sources

**Speaker**: "This is what one Pi listening on LongFast saw last week. Every dot is a Meshtastic node — could be in your shack, on a ridge, on a backpack. Big Island, Oahu, Maui, all of them. This is YOUR network. You're already on it."

**Note**: the moment a ham sees their own callsign or a node they recognize on the map, they're sold. Pause for a beat after this slide.

---

### 5. The most active nodes (2 min)

**Display**: `leaderboard_most_active.svg` — top 10 by packet count, app mix per node.

**Speaker**: "Top of the leaderboard isn't always who you think. Some of these are router nodes — they're relaying traffic for everyone. Some are chatty handhelds. The mix tells you which nodes are infrastructure and which are people."

**MF015 check**: scan for any node `long_name` that leaks an operator's residential street name or callsign-with-personal-info before slide locks.

---

### 6. What the network is actually carrying (90 sec)

**Display**: `app_mix.svg` — squarified treemap: POSITION / NODEINFO / TELEMETRY / TRACEROUTE (the four portnums actually observed; TEXT_MESSAGE and ROUTING weren't captured in window).

**Speaker**: "Most traffic isn't text. It's position beacons and telemetry — temperature, battery, how every node says 'I'm still here.' That's why your channel utilization climbs without anyone typing. Numbers in the corner: this is what's chewing your airtime."

**Audience benefit**: this is genuinely useful operational knowledge. A ham will leave the room knowing why their Meshtastic battery drains.

---

### 7. Best signal-to-noise ratio (90 sec)

**Display**: `leaderboard_best_snr.svg` — top 10 by avg SNR with `n >= 30` floor footnote. **Note**: stub-empty in current render — the captured `packets` table doesn't carry an SNR column. Either drop this slide for v2 or instrument a fresh capture pipeline before the talk.

**Speaker**: "Whoever's at the top of this list either has a great antenna, a great location, or both. SNR floor on this slide is 30 packets — a node has to be heard 30 times before it qualifies, so a single lucky bounce doesn't game it."

**HAM bait**: site engineering, antenna placement — they'll want to know if their station is on the leaderboard.

---

### 8. The bridge in plain language (3 min)

**Display**: simple diagram — Meshtastic node → MeshForge gateway → Reticulum node, with frequency labels (915 MHz LoRa on the left, RNS over TCP/RNode on the right).

**Speaker**: "What MeshForge does in one sentence: a message from a Meshtastic node ends up as a message a Reticulum node can read, and vice versa. It runs on a Pi. It's free, GPL-3.0, on GitHub. RNS on its own gives you cryptographic identity, store-and-forward, and a network that doesn't care if the internet is up. That's why bridging matters — Meshtastic gets longer reach and async delivery; RNS gets the radios you already own."

**Reticulum primer**: most hams haven't heard of it. Three sentences max — don't get into the protocol weeds. Save for Q&A if asked.

---

### 9. Five Pis, deliberately heterogeneous (3 min)

**Display**: `timeline.svg` — 5-day packet timeline, color-banded by app + diagram of the 5-Pi fleet (each box's role/preset/HAT).

**Speaker**: "Five Pis. Different presets, different HATs, deliberately not the same. Every architectural mistake I make lands on at least one box that fights back. When I shipped a typo three weeks ago — `shared_instance_rpc_key` instead of `rpc_key` — RNS silently ignored it. Unit tests passed. The fleet caught it the first time inbound LXMF tried to talk and authentication failed. The fleet IS my QA environment."

**The line**: "I don't have a CI system. I have five Pis and a stubborn refusal to ship from a green local test."

---

### 10. The AI co-author (3 min)

**Display**: snippet of `CLAUDE.md` (the project root) + screenshot of a memory file.

**Speaker**: "I work with an AI partner. Claude. I named him Dude AI. He doesn't remember yesterday — he's a model running in a datacenter, every session starts cold. So I built him a memory: 95-some files in a `.claude/` directory, a project-level memory index, a `persistent_issues.md` with a hard 40 KB cap and a lint rule that fails the build if I exceed it. The files aren't for me. They're for him. That's what sustained human-AI collaboration actually requires."

**The 90-degree angle**: "I bring 30 years of infrastructure intuition. He brings the ability to hold 300 source files in context and write 140 unit tests for a gateway in 20 minutes. Neither of us could have built this alone."

---

### 11. What happened yesterday — Phase-1 relay (2 min) — FRESH

**Display**: log line screenshot — `Phase-1 relay: forwarded R→M origin to 1/1 peer gateway(s)` with timestamps.

**Speaker**: "Yesterday I ran a synthetic test. One LXMF message from one of my Pis. It reached BOTH a LongFast preset and a SHORT_TURBO preset — two RF channels that can't physically hear each other. The gateway relayed it across in under a second. That's seven days of code I and Dude AI iterated on, validated end-to-end. It's now the foundation for tri-bridge."

**Why this slide matters**: shows the project is alive THIS WEEK, not a year ago. Connects abstract "gateway" to concrete capability.

---

### 12. Where I broke things (2 min)

**Display**: bullet list of failure modes, each with a one-line fix and a commit hash.

**Failures worth telling**:
- **USB Meshtastic relay dead-end** — meshtasticd 2.7.x doesn't support it. Wasted an hour finding out. Documented so the next session won't.
- **rpc_key typo** — silent AuthenticationError for two days. Fleet caught it.
- **bridge_mode enum** — single-choice. Forced a 72-hour refactor to composable bridges.
- **Self-overwriting config files** — Claude wrote to `/etc/meshtasticd/config.yaml` directly. Audit found it. Lesson learned, encoded as MF014/MF015 lint rules.

**The line**: "Every scar becomes a file. Every mistake teaches the next session."

---

### 13. Live demo — :8808 (5 min) — IF CLOUD READY

**Plan A** (cloud :8808 launched): public URL on screen. Audience pulls it up on their phones. Watch HI mesh nodes in real-time.

**Plan B** (cloud not ready): SSH from laptop to one of the fleet boxes; show TUI launch, a node coming online, an LXMF receive, a bridge counter ticking. Same outcome, smaller blast radius.

**Plan C** (no internet at venue): pre-recorded screencap of plan B running on the Pi. Roll the recording.

**Note for slide-prep**: confirm by 5/15 which plan is live so the slide has the right URL/QR code.

---

### 14. Why this matters for hams in this room (2 min)

**Display**: 5-bullet "what you can do today":
1. Install MeshForge on a Pi you already have. ($35 hardware, free software.)
2. Run it as a passive listener — instant visibility into LongFast HI without changing your radio.
3. Stand up a gateway — let your shack bridge two presets that can't normally hear each other.
4. Try Reticulum — encrypted, identity-based, async messaging on the radios you own.
5. Contribute. Open source. PRs welcome.

**Speaker**: "If you have a Pi and a LoRa radio, you have the hardware. The software is free. I'll be at the back after the talk to help anyone who wants to start."

---

### 15. What's next (90 sec)

**Display**: roadmap — Phase 2 fluid bridge (just unblocked) · tri-bridge (Meshtastic + MeshCore + RNS in one process) · MeshAnchor (sister NOC, MeshCore-primary, github.com/Nursedude/meshanchor)

**Speaker beat**: tri-bridge by [year]. "If you're on MeshCore, MeshAnchor is for you. Same architecture, sister project, same gateway protocol — full interop with MeshForge."

---

### 16. Where to find everything (60 sec)

**Display**: three URLs, big text:
- `github.com/Nursedude/meshforge`
- `github.com/Nursedude/meshanchor`
- `wh6gxznursedude.substack.com` — ongoing field notes

**Speaker**: "Code is public. The substack is the field notes — what worked, what broke, what I'm still learning. Subscribe if you want to follow the build."

---

### 17. Close + 73 (60 sec)

**Display**: "73 de WH6GXZ — and aloha from the mesh." Logo. URL footer.

**Speaker**: "Mahalo. Questions?"

---

## Q&A prep — anticipated questions

| Q | Short answer |
|---|---|
| Why a Pi instead of the Meshtastic app on my phone? | The phone is your client. The Pi is your *site* — always-on, always listening, bridging. Different role. |
| Is Reticulum legal on ham bands? | RNS doesn't dictate band; you do. Run it on Part 97 frequencies and follow ID rules. RNode firmware on 903.625 MHz is what I'm using locally. |
| What hardware do I need? | Pi 4B or 5, a LoRa radio (RAK Wireless WisGate or RNode or Heltec V3), antenna. Total ~$100 if you don't have a Pi. |
| Why GPL-3.0? | Same reason as Linux — make sure derivatives stay open. The bridges between mesh networks shouldn't be proprietary. |
| Do I have to use Claude/AI to run this? | No. AI is how I built it. Operating it is just normal Linux + Python. |
| Will this work if the internet is down? | Yes. That's the whole point. RNS routes itself. Meshtastic mesh-routes itself. The Pi doesn't need cloud. |
| Where's MeshAnchor in this? | Sister project. MeshCore-primary instead of Meshtastic-primary. Same gateway protocol. Run both if you have radios for both. |

---

## Slide assets — current state (2026-05-10)

Tracked in-repo under `docs/birc_2026_05_17_assets/`:

| Slide # | SVG file | Status |
|---|---|---|
| 4 | `hero_map.svg` | ✓ |
| 5 | `leaderboard_most_active.svg` | ✓ |
| 6 | `app_mix.svg` | ✓ |
| 7 | `leaderboard_best_snr.svg` | ⚠️ stub-empty (no SNR column in capture) |
| 9 | `timeline.svg` | ✓ |
| — | `leaderboard_most_relayed.svg` | ✓ (held in reserve, may swap into slide 5 or 7 if needed) |
| — | `leaderboard_most_reliable.svg` | ⚠️ stub-empty (no reliability metric in capture) |

**Refresh consideration**: capture window is Apr 28 → May 3, currently 7 days stale. If we restart `diag_24h` on the capture box today (2026-05-10) we get fresh data through ~5/16 for re-render the night before. Decision point 5/14 — re-run analysis or ship as-is?

**MF015 audit pass before publishing**: scan rendered SVGs for any operator hostname / LAN IP / personally identifying node `long_name`. Run before slide deck locks 5/16.

---

## Outstanding decisions (for operator)

1. **Cloud :8808 by talk day?** — gates demo Plan A vs. Plan B. Decision point 5/14.
2. **Refresh capture data?** — restart `diag_24h` on the capture box today for fresh 5/10–5/15 data, or ship Apr 28–May 3 as-is. Trade-off: more recent vs. more days = more nodes captured.
3. **Slide deck venue** — Claude.ai design vs. Google Slides vs. reveal.js vs. pdf-export from markdown. The SVGs work in all four; just pick the runway.
4. **Live demo connectivity** — venue WiFi confirmed reliable? Cellular fallback (hotspot off operator's phone)? Pre-recorded fallback at minimum.
5. **Length** — 35 min vs. 45 min. Outline is paced for ~35; can stretch 8 (live demo) and 10 (AI co-author) if room is engaged.

---

## What's deliberately NOT in this outline

- **Long Reticulum protocol explanation**. 90% of audience has never heard of it; 5 sentences max. Q&A will go deep if anyone wants.
- **The Anthropic ask from the manifesto piece**. Wrong audience. BIRC isn't a recruiting venue.
- **Code walkthrough**. Hams want RF-and-results, not Python.
- **The drift / trust-gradient observations from the May 5 letter-to-AI piece**. Beautiful writing, wrong audience. That's a CHI talk, not a BIRC talk.
- **Detailed fleet hostnames / operator-identifying nodes**. MF015 — nothing in slides ends up indexed by Google with a residential network signature.

---

## Voice / tone reminders (drawn from substack pieces)

- **Specific over abstract**. "5.1 days, 16,235 packets, 605 nodes" beats "lots of data".
- **Personal stakes**. "the box in my kitchen" beats "one of my nodes".
- **Failures as proof of work**. The USB-Meshtastic dead-end and `rpc_key` typo are honest moments — they make the wins credible.
- **HAM-friendly RF specifics**. 903.625 MHz / SF7 / 17 dBm / RNode whenever it fits.
- **"Made with aloha for the mesh community"** — close.
- **"73 de WH6GXZ"** — sign-off.

---

*Outline drafted 2026-05-10 from substack pieces (`docs/substack/2026-03-29-500-hours-manifesto.md`, `2026-04-24-five-pis-one-ai-composable-gateway.md`, `2026-05-05-field-notes-letter-to-ai.md`), `project_birc_presentation_may_2026.md`, `project_moc_analysis_tool.md`, `project_cloud_8808_may17_demo.md`, fresh Phase-1 synthetic exercise findings (`project_phase1_relay_soak_findings.md`), and the 7 SVGs already produced. Reconcile against operator's existing Claude.ai slide draft on next pass.*

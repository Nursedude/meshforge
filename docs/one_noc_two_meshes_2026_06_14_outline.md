# MeshForge — "One NOC, Two Meshes" — Outline (working draft)

> **Audience**: HAMs & mesh operators — Meshtastic-curious, mostly haven't met Reticulum.
> **Format**: ~13 slides, ~15 min (stretch to ~20 if the room is hot). Slides + 2 short live demos.
> **Delivery model**: operator improvises the narrative and falls back on slides for the *facts*.
> So slides carry the load-bearing numbers (legible from the podium); speaker lines below are
> *beats to land*, not a script. Drafted 2026-06-14 from current fleet state + the chat draft.
> **Design runway**: Claude Design (claude.ai). Facts pulled from the companion file
> `one_noc_two_meshes_2026_06_14_live_numbers.md`.

---

## 🎨 Design system (give this block to Claude Design first)

**Palette — "Pacific / aloha"**

| Role | Hex | Use |
|------|-----|-----|
| Deep ocean (primary dark) | `#06222E` | Dark backgrounds, headers on light |
| Ocean blue | `#1B6CA8` | Primary brand · the "Meshtastic" cluster |
| Mesh teal (links) | `#2EC4B6` | The connecting lines in the node-graph motif |
| Sunrise coral (accent) | `#FF6B5C` | Accents, CTAs · the "Reticulum" cluster |
| Sunrise amber | `#FFB84D` | Highlights, the "aloha" warmth |
| Sand (light bg) | `#F7F3EC` | Light slide backgrounds |
| Cloud white | `#FFFFFF` | Text on dark, cards |

**Typography** — Headers: **Space Grotesk** (or Sora). Body: **Inter**. Mono: **JetBrains Mono**
for every callsign, frequency, command, version, and dB figure (monospace = "this is real").

**Recurring motif — the node-graph.** Dots + connecting lines that *evolve across the deck*:
two separate clusters at the open (blue + coral, no link) → one densely interconnected network
at the close. Links in mesh-teal `#2EC4B6`. Keep it consistent on slides 1 → 4 → 8 → 9 → 13 so
the network visibly "comes together."

**Tone & layout** — One idea per slide. Generous whitespace; let the sand/ocean breathe.
Dark slides for emotional beats (1, 10, 13), light for informational. Field-tested, not flashy —
no stock "digital network" clichés; favor real screenshots and real hardware photos.
**Facts must be legible from the slide** — this presenter reads numbers off the slide while talking.

---

## ⏱️ Pace card (on-stage safety net — glance here to stay on the clock)

| # | Slide | Time | Clock | The fact(s) on the slide |
|--:|-------|:----:|:-----:|--------------------------|
| 1 | Title | 0:30 | 0:30 | title · callsign · "made with aloha" |
| 2 | Two meshes | 1:30 | 2:00 | Meshtastic=LoRa · RNS=crypto/LXMF |
| 3 | Why a HAM cares | 1:15 | 3:15 | one map: Meshtastic+RNS+AREDN |
| 4 | A NOC, not just a bridge | 1:00 | 4:15 | runs on a Pi · TUI · no cloud |
| 5 | How the bridge works | 1:30 | 5:45 | `CanonicalMessage` · Mesh↔RNS+MQTT+MeshCore+CoT |
| 6 | What you see *(demo)* | 2:00 | 7:45 | live map · SNR · coverage |
| 7 | RF tools / uConsole *(demo)* | 2:00 | 9:45 | `standalone.py` · zero deps · runs on a handheld |
| 8 | Runs as a fleet | 1:00 | 10:45 | profiles: gateway·monitor·radio_maps·meshcore·full |
| 9 | Dude-claw | 1:15 | 12:00 | ESP32 · WiFi + LoRa RX/TX + BLE |
| 10 | Silence is the failure mode | 1:30 | 13:30 | watchdog + mini-dudeai pages your phone |
| 11 | Open source | 1:00 | 14:30 | `0.6.2-beta` · ~6,000 tests · MeshAnchor sister |
| 12 | Try it | 0:45 | 15:15 | the two launch commands · QR |
| 13 | Close | 0:30 | 15:45 | "Two meshes. One network. Aloha." |

> **Pace discipline**: if you're past the *Clock* value when a slide goes up, trim the next beat.
> The two demo slides (6, 7) are the stretch/squeeze valves — everything else is tight.

---

## Slide-by-slide

### 1. Title (0:30) — full-bleed dark, centered
**Display**: "MeshForge: One NOC, Two Meshes" · subhead "Bridging Meshtastic and Reticulum into a
single off-grid network operations center" · corner: `WH6GXZ` · footer "open source · made with aloha".
**Visual**: two faint, *separate* node-clusters (left blue, right coral) — not yet connected.
**Speaker beat**: "Two of the best things in off-grid radio right now can't talk to each other.
This is the story of building the bridge — and the operations center around it." Open at the podium,
set the hook, don't explain yet.

### 2. Two meshes (1:30) — two-column split (blue left / coral right)
**Display / on-slide facts**:
- **Meshtastic** — LoRa, cheap radios, dead-simple, huge community. Channels, positions, telemetry.
- **Reticulum (RNS)** — cryptographic networking, transport-agnostic, real addressing & secure messaging (LXMF).
- Different wire formats · different crypto · a message on one is invisible to the other.
**Visual**: the two clusters, labeled, jagged broken line + "✕" between them.
**Speaker beat**: ~20s each. Meshtastic = "the friendly LoRa mesh." Reticulum = "the serious comms
stack — crypto and routing baked in." Land the gap: "Run both, and you run two islands."

### 3. Why a HAM should care (1:15) — map-silhouette bg, light
**Display / on-slide facts**: EmComm — the network you have beats the one you wish you had · HAMs already
bridge worlds (VHF↔HF, voice↔digital) · one pane of glass: Meshtastic + RNS + AREDN on one map.
**Visual**: island/coastline silhouette, mixed node types (blue + coral) on one surface.
**Speaker beat**: the "why now." Tie to EmComm and the HAM interoperability ethos.
"We bridge bands and modes every day. This just bridges meshes."

### 4. A NOC, not just a bridge (1:00) — centered statement + hub diagram, dark
**Display / on-slide facts**: monitor · map · diagnose · heal — not just translate · runs on a
Raspberry Pi · terminal-first (TUI) · no cloud required · first open-source tool to unify the two.
**Visual**: the two clusters now joined by one solid teal line through a central hub (the NOC).
**Speaker beat**: "A bridge alone isn't operations. You need to *see* the network and *trust* it. That's the NOC."

### 5. How the bridge works (1:30) — horizontal flow diagram, light
**Display / on-slide facts**: everything becomes a `CanonicalMessage`, then routes anywhere ·
Mesh ↔ RNS + MQTT + MeshCore + ATAK/CoT · directed replies (a Reticulum user can answer a Meshtastic
node) · honest delivery: queues & retries; the UI never says "delivered" when it means "sent."
**Visual**: block diagram — inputs left (Meshtastic, RNS, MQTT, MeshCore) → `CanonicalMessage` core → routing out.
**Speaker beat**: conceptual, no code. "The hard part isn't translation — it's being honest about what
got through." (Think: ACK vs. 'I keyed up.')

### 6. What you actually see (2:00) — two real screenshots side-by-side, light · **DEMO HOOK**
**Display / on-slide facts**: live node map (position · last-heard · source network · SNR) · RF coverage
maps (link budgets · terrain reach) · a terminal NOC (TUI) for operators who live in SSH.
**Visual**: the web/Folium node map + the TUI dashboard, side by side. *(Clean captures — see Assets.)*
**Speaker beat**: talk over it. "This is a real fleet, right now." Demos beat diagrams here.

### 7. Real RF tools, in your hand (2:00) — handheld hero shot, dark · **DEMO HOOK**
**Display / on-slide facts**: `python3 standalone.py` — **zero dependencies** · link-budget / Fresnel /
path-loss / antenna math · space-weather & propagation (live NOAA) · LoRa presets / regions / frequencies ·
**standalone build runs on portable hardware — e.g. a ClockworkPi uConsole**.
**Visual**: a uConsole-style handheld running the terminal RF tools — green-on-dark link-budget output.
**Speaker beat**: win the room. "Before any mesh stuff — these are tools you'd want anyway. Zero deps on
purpose, so it runs on a handheld. This is the thing in your go-bag." Run a live link budget if you can.

### 8. It runs as a fleet (1:00) — constellation diagram, light
**Display / on-slide facts**: a field-deployed fleet of Raspberry Pis · deployment profiles:
`gateway` · `monitor` · `radio_maps` · `meshcore` · `full` · pick a role per box; the fleet federates.
**Visual**: node-graph as a constellation of labeled Pi icons, each tagged with a role, all linked to a federator.
**Speaker beat**: "You don't run the whole thing on one Pi. Pick a profile per box — one's a gateway,
one's a map server — and they federate."

### 9. Dude-claw — the edge (1:15) — hardware close-up, dark
**Display / on-slide facts**: **ESP32** · **WiFi + LoRa (RX+TX) + BLE passive-scan** on one board ·
the sensing *tip* of the fleet — deploy where a Pi won't fit or survive · pairs with a Pi-brain
(even a handheld) → a fully portable, standalone NOC · built lean & fail-loud · **currently in field soak**.
**Visual**: close-up of the ESP32 board, three radio-wave arcs (LoRa / WiFi / BLE), wired to a Pi-brain icon.
**Speaker beat**: the "where we're heading" beat — honest it's the newest frontier, not the shipped core.
"Sometimes you need eyes somewhere small, cheap, and disposable. Claw at the edge, brain in your pack."

### 10. Silence is the failure mode (1:30) — phone-notification mockup, dark, high-impact
**Display / on-slide facts**: an autonomous watchdog watches for the *absence* of signal, not just errors ·
**mini-dudeai** — a rule-loop that pages the operator's phone when something's actually wrong ·
doctrine: *every swallowed failure leaves a witness a probe can see.*
**Visual**: a realistic phone push: `[RED] node down · last reading attached`.
**Speaker beat**: the credibility slide. "A dead repeater that *looks* alive is worse than one obviously
down. We engineered against silent failure — the system tells on itself."

### 11. Open source & the ecosystem (1:00) — ecosystem map, light
**Display / on-slide facts**: **MeshForge `0.6.2-beta` · ~6,000 automated tests** · sister NOC
**MeshAnchor** (`0.1.0-alpha`, MeshCore-primary) · we maintain our own pinned forks of the RNS stack for
fleet reliability — **wire-compatible with the public network** · fully open source.
**Visual**: simple repo/ecosystem map — MeshForge center, MeshAnchor as sister, forks as feeders.
**Speaker beat**: "Open source isn't a feature here, it's the point — off-grid comms you can't inspect is
comms you can't trust." Note the test discipline briefly.

### 12. Try it (0:45) — big QR + commands, light
**Display / on-slide facts (mono)**:
- `sudo python3 src/launcher_tui/main.py` — the NOC.
- `python3 src/standalone.py` — RF tools, no dependencies.
- auto-detects your setup, or choose a deployment profile.
**Visual**: big scannable QR to the repo + the two commands in monospace.
**Speaker beat**: "Got a Pi and a LoRa radio? You're 10 minutes from this map. Bring your nodes."

### 13. Close + 73 (0:30) — full-bleed dark, centered — bookends Slide 1
**Display**: "Two meshes. One network. Made with aloha." · subhead "Come build the bridge." ·
"73 de WH6GXZ" · repo footer.
**Visual**: the Slide-1 node-graph, transformed — the two clusters now densely interconnected, traffic
animating along teal links.
**Speaker beat**: "We started with two islands. We end with one network. Mahalo — questions?"

---

## Q&A prep — anticipated questions

| Q | Short answer |
|---|---|
| Why a Pi instead of the phone app? | The phone is your client. The Pi is your *site* — always-on, listening, bridging. |
| Is Reticulum legal on ham bands? | RNS doesn't dictate band; you do. Run it on Part 97 frequencies, follow ID rules. |
| What hardware do I need? | A Pi 4B/5, a LoRa radio (RNode / RAK / Heltec), antenna. ~$100 if you start from nothing. |
| Do I need AI / Claude to run it? | No. AI is how it was built; operating it is normal Linux + Python. |
| Works with the internet down? | Yes — that's the point. RNS routes itself, Meshtastic mesh-routes itself, the Pi needs no cloud. |
| What's Dude-claw, really? | An ESP32 edge sensor — three radios on one board. In field soak now, not shipped core. |
| Where does MeshAnchor fit? | Sister NOC, MeshCore-primary, same gateway protocol. Run both if you have radios for both. |

---

## Voice / tone reminders

- **Specific over abstract** — "200 test files, 6,079 tests" beats "lots of tests."
- **Personal stakes** — "the box in my pack" beats "one of my nodes."
- **Failures as proof of work** — honest "in soak" / "silent-failure doctrine" makes the wins credible.
- **HAM-friendly RF specifics** — frequency / SF / dBm / RNode whenever it fits.
- **Close**: "Made with aloha for the mesh community." · **Sign-off**: "73 de WH6GXZ."

---

## Assets needed (placeholders until captured)

| Slide | Asset | Status |
|------|-------|--------|
| 6 | live Hawaii NOC map → `assets/live_map_hawaii.png` | ✅ captured 2026-06-14 (MF015-clean: clustered, no node labels) |
| 6 | topology core *(motif hero)* → `assets/topology_core_clean.png` | ✅ committed — **HD 2040×1920**, labels hidden at source (zero PII text), MF015-safe |
| 3/6 | **terrain LOS coverage** *(Civil Defense)* → `assets/terrain_coverage_kona.png` | ✅ committed — real `/api/coverage` terrain analysis, synthetic Kona station (no real node), MF015-safe |
| 6 | island coverage / network → `assets/coverage_islands.png` | ✅ committed (declustered nodes + inter-island links, MF015-clean) |
| 6 | topology full/wide *(richer, more grandeur)* → `assets/*_UNCOMMITTED_*.png` | ⚠️ local only — rim node-labels need an MF015 scan before publishing (gitignored) |
| 6 | TUI dashboard | ⬜ capture, or Design-styled TUI panel |
| 7 | RF link-budget panel → `assets/link_budget_sample.txt` | ✅ captured (real `rf.py` output); Design typesets, or render to PNG |
| 7 | uConsole handheld photo | 📷 operator camera (or Design mockup w/ the panel composited on the screen) |
| 9 | Dude-claw ESP32 board close-up | 📷 operator camera |
| 1,4,8,13 | node-graph motif (evolving) | ⬜ Claude Design generates |

> **MF015 audit before publishing**: scan every rendered slide/screenshot for LAN IPs, fleet box
> names, AREDN node IPs, or personally identifying node `long_name`s. Nothing in slides should end up
> indexed by Google with a residential network signature.

---

*Companion facts file: `one_noc_two_meshes_2026_06_14_live_numbers.md` (pull before lock).*

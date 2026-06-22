# dude-claw — Role, Firmware Ownership & Use-Case Roadmap

> **Deep-research synthesis · 2026-06-21**
> Net-new external knowledge (5 web-search angles, 3-vote adversarially verified) blended
> with codebase grounding, feeding a fork-aware role/use-case roadmap.
>
> **Provenance:** deep-research workflow `wf_1ede8af2-b8d` — 108 agents, ~6.0M tokens,
> 26 sources fetched → 123 claims → 25 verified → **24 confirmed / 1 killed** → 9 synthesized
> findings. Regulatory CFR text independently re-fetched this turn (§97.113, §97.119, §97.221).
> **Companion docs (referenced, NOT rehashed):** `dudeclaw_second_brain_2026_06_17.md`
> (autonomy ladder, power/solar, solo-mode, 3T naming) and `dudeclaw_reset_safe_set_2026_06_19.md`
> (GPIO-reset safe set G1–G7).
>
> **Calibrated-claims key:** **[V]** = externally verified (3-0 adversarial vote or quoted
> primary source, cited inline); **[B]** = believed / design synthesis (reasoned, not field-proven);
> **[U]** = unknown / open question. "Worked once" ≠ reliable; capability ≠ field-reliability.

---

## 0. Executive summary

The strongest net-new findings cluster on three fronts:

1. **Backbone is the most actionable role.** dude-claw's *exact* hardware (Heltec WiFi LoRa 32 V4,
   ESP32-S3 + SX1262) already runs a **standalone Reticulum Transport Node** firmware
   (**RTNode-HeltecV4**) that bridges local LoRa to a remote TCP/IP RNS backbone over WiFi with
   **no host computer** — proving a host-independent RNS-backbone role is feasible *on this board
   today* **[V]**. This complements (does not replace) the brain-paired mini-dudeai design.

2. **APRS-style messaging is a genuine net-new niche.** Working APRS↔mesh bridges exist
   (`aprstastic`, bidirectional; `meshtastic-bridge`, uplink-only) but **every one gates through
   APRS-IS — the *internet* backbone — not RF-to-RF** **[V]**. There is no off-the-shelf off-grid
   RF LoRa-APRS ↔ Meshtastic digipeater. That gap is exactly where an off-grid NOC like MeshForge
   has a differentiated story.

3. **Airborne relaying: balloon yes, drone mostly no (near-term).** Altitude gives huge, real range
   (a 702 km LoRa link from a 38.8 km balloon **[V]**), but US rules make a *drone* relay
   impractical (Part 107: 400 ft AGL, visual-line-of-sight, Remote ID) **[V]** while a **sub-1-lb
   balloon payload escapes FAA Part 101 Subpart D entirely** **[V]**. Recommend mast/tethered-balloon
   altitude, defer free-flying drones pending FAA Part 108 BVLOS (~March 2026) **[V, time-sensitive]**.

The **cross-cutting gate** is FCC Part 97 §97.113(a)(4): **no encryption on amateur bands** **[V,
quoted]**. Any role that TXes on 33cm/70cm amateur must run **clear-text + callsign-ID** — which
forecloses Meshtastic AES on those bands but *unlocks* higher power, more range, and (per §97.221(b))
**legal unattended/automatic operation** **[V, quoted]**. The strategic fork in the road is
**915 ISM (Part 15, AES-OK, low-power, no-ID) vs. 33cm/70cm amateur (Part 97, clear-text, callsign,
automatic-control-permitted)** — same hardware, operator's choice of regime.

---

## 1. Ground truth (brief — full detail in the firmware repo + companion docs)

dude-claw = `dudeclaw-01`, a **Heltec WiFi LoRa 32 V4 (ESP32-S3 + SX1262)** running a MeshForge-owned
**fork of WireClaw** (`Nursedude/WireClaw`, MIT; *not* Meshtastic firmware). Deployed `0.4.0+dudeclaw.14`;
`.15` (host-probe banner 800→2500 ms) staged on `pr/host-probe`. Source `/home/wh6gxz/src/wireclaw-dudeclaw`;
governance `FORK.md` (THE INVARIANT: `dudeclaw` is rebuilt, never hand-edited; features live on `pr/*`).

**Live today:** WiFi+NATS brain link to mini-dudeai (moc2); **LoRa RX** ("mesh ears", Meshtastic
LongFast/US 915 ISM, headers-only — *never decrypts*); **LoRa TX** ("mesh voice", `mesh_send`, ≥30 s
airtime guard, modest 2 dBm, on the fleet `meshforge` channel); passive **BLE scan**; **OLED** status
screen (`display_print`); `battery_read`; `host_probe`. **Not present:** GPS, APRS, repeater/relay role,
GPIO/host-reset actuation.

**Claw ↔ mini-dudeai path is live & file-mediated** (one-directional today): claw `host_probe` →
`scripts/host_probe_check.py` → `host_frozen` watchdog signal → mini `WatchdogJsonSource`;
`scripts/claw_metrics_push.py` paints fleet metrics onto the OLED; `src/mini_dudeai/claw_telemetry.py`
stitches claw telemetry into `/api/status.claw` + `/fleet`. A *standalone* preset
(`src/mini_dudeai/presets/standalone.py`) lets mini **be** the claw's Pi-brain.

**Already researched — reference, do not repeat:** autonomy ladder (RTA/Simplex), solar/battery budget
(~120 mA → 10 W/MPPT/10 Ah), DTN solo-mode, 3T architecture (`dudeclaw_second_brain_2026_06_17.md`);
GPIO host-reset safe set G1–G7 (`dudeclaw_reset_safe_set_2026_06_19.md`).

---

## 2. Verified external findings (the 5 angles)

### Angle 1 — APRS-style messaging over LoRa

- **[V]** (3-0) **Prior art is APRS-IS-mediated, not RF-to-RF.** `aprstastic` is a **bidirectional**,
  FCC-compliant (callsign-attributed) Meshtastic↔APRS-IS gateway running on *stock* Meshtastic devices
  (LongFast, 915 MHz), identifying to APRS-IS L2 servers with software ID `APZMAG`.
  `jaredquinn/meshtastic-bridge` is a plugin Python bridge that exports Meshtastic positions to APRS-IS
  **uplink-only**. Neither keys 144.39 MHz APRS RF. → *Net-new niche: an off-grid, APRS-IS-independent
  RF-to-RF bridge.* (sources: github.com/afourney/aprstastic, github.com/jaredquinn/meshtastic-bridge)
- **[V]** (3-0) **On-air LoRa-APRS has two live framings**, both handled by open i-gate firmware: the
  **legacy OE5BPA** format (broadly deployed, e.g. 433.775 MHz EU) and a newer byte-compressed
  **"APRS 434"** frame (ON4AA) for extended range. A bridge would interoperate with these.
  (source: github.com/aprs434/lora.igate). *Caveat: APRS-434 dominance is aspirational; OE5BPA is the
  deployed one.* **[B]** APRS RF frequencies are region-specific (144.39 MHz N.America VHF APRS;
  433.775 MHz EU LoRa-APRS) — a US 33cm/70cm LoRa-APRS deployment is a deliberate band choice, not a default.

### Angle 2 — Airborne / drone-borne repeating & beaconing

- **[V]** (3-0) **Altitude → large range.** A **702.676 km** LoRa link was achieved with the transmitter
  on a balloon at **38.772 km (127,205 ft)** altitude — validating the radio-horizon rationale.
  ⚠️ **The companion "25 mW / 14 dBm" power figure was REFUTED (1-2) — do NOT cite the power number.**
  (source: thethingsnetwork.org 702 km record)
- **[V]** (3-0) **Drone relay is impractical under standard US rules.** Part 107 caps small-UAS at
  **400 ft AGL** (limited structure exception) and requires **visual line of sight throughout the
  entire flight** — a loitering/out-of-sight relay needs a **BVLOS waiver**. Any drone required to be
  (or that is) registered must meet **Remote ID** (three paths: Standard RID / broadcast module / FRIA;
  module operation is itself VLOS-only). (sources: ecfr Part 107 §107.51/§107.31, faa.gov/remote_id)
- **[V]** (2-1) **A balloon payload is far more tractable.** A **sub-1-lb** payload (ESP32-S3 + small
  LiPo + antenna) falls below *all four* FAA Part 101 thresholds and is **exempt from Part 101 Subpart D**
  — *but the §101.7 general hazard prohibition still applies to any balloon.* (source: ecfr Part 101 Subpart D)
  *2-1 vote → treat the exemption boundary with per-deployment legal care.*
- **[V]** (3-0) **§97.215(c) caps telecommand of model craft at 1 W** (vs 1.5 kW PEP general) — bounds
  any amateur airborne *control* uplink (scope: the control link specifically, not all airborne ops).
  (source: law.cornell.edu §97.215)
- **[V, time-sensitive]** FAA is finalizing **Part 108 BVLOS** rules (target ~March 2026) which could
  materially change the drone-relay picture.

### Angle 3 — LoRa + Reticulum backbone & long-haul links

- **[V]** (3-0) **★ RTNode-HeltecV4 runs on dude-claw's EXACT board, host-free.** Standalone Reticulum
  Transport Node firmware for the Heltec V4 (ESP32-S3FH4R2 + SX1262), built on Mark Qvist's RNode
  Firmware + the **microReticulum** C++ port; runs **three simultaneous RNS interfaces** — LoRa
  (MODE_GATEWAY), a remote **TCP/IP backbone** (MODE_BOUNDARY, e.g. rmap.world), and an optional local
  TCP server — bridging local LoRa to an RNS backbone over WiFi autonomously. *The most directly
  actionable roadmap input.* (sources: github.com/jrl290/RTNode-HeltecV4, github.com/markqvist/Reticulum)
  ⚠️ **Hardware-variant note:** RTNode targets a **16 MB flash / 2 MB PSRAM** V4; dude-claw's PlatformIO
  uses **4 MB flash, stock partitions** — confirm the actual board variant before assuming coexistence **[U]**.
- **[V]** (3-0) **Reticulum is the heterogeneous backbone substrate** MeshForge already leads on: LoRa
  (via RNode), packet-radio TNCs (±AX.25), KISS modems, serial, TCP/UDP/IP, Ethernet, stdio/pipe
  externals as interfaces; **self-configuring multi-hop** routing over mixed carriers, coordination-less
  globally-unique addressing; envelope ~150 bps usable (250 bps floor) to gigabit, **500-byte MTU**,
  built for very-high-latency/very-low-bandwidth links. *Capability claims (README); field reliability
  at scale is separate.* (source: github.com/markqvist/Reticulum)
- **[V]** (3-0) **Meshtastic relay roles (describe the mesh dude-claw interoperates WITH):** ROUTER
  *always* rebroadcasts — only for **stationary, strategically-placed** nodes; REPEATER = ROUTER + silences
  its own telemetry (pure infrastructure relay, invisible in the node list); electing either imposes a
  **mesh-wide preference** — a mis-placed router "hop-gobbles" packets and creates one-way links.
  **Store & Forward server needs onboard PSRAM** (T-Beam, T3S3, "and maybe others" — Heltec V4 qualifies).
  (sources: meshtastic.org choosing-the-right-device-role, store-and-forward-module)
  ⚠️ **Firmware-identity caveat:** dude-claw runs WireClaw-fork firmware, *not* Meshtastic — these roles
  are NOT a drop-in config for dude-claw; they describe the mesh and what the *platform class* supports.

### Angle 4 — Beaconing cadence under airtime/duty-cycle limits

- **[B/U]** The verified claim set was **light on Angle-4 specifics** (SmartBeaconing parameters,
  exact dwell-time math) — flagged as an open question. What is solid:
  - **US 915 ISM** is governed by **FCC Part 15.247** (frequency-hopping / digital-modulation / dwell-time
    rules); it is the regime dude-claw TXes under *today*. The claw's existing **≥30 s inter-TX airtime
    guard** is a conservative, sensible self-limit for a beaconing node **[B]**.
  - **EU 868 MHz** imposes a hard **1% duty-cycle** — far stricter than US; **flag this divergence** for
    any EU-region build **[V, well-established background]**.
  - **APRS SmartBeaconing** is *speed/turn-adaptive* — designed for **mobile** trackers; for a **fixed**
    remote-site claw, a simple fixed interval (with jitter) is the right model, not SmartBeaconing **[B]**.
  - **Position compression** (APRS Base-91 compressed / MIC-E) reduces airtime per beacon **[B]**.
  (sources in set: law.cornell.edu §15.247, lora-aprs.org/settings, avbentem airtime-calculator, actility duty-cycle)

### Angle 5 — Part 97 regulatory gate (CFR text re-fetched & quoted this turn)

- **[V, quoted] §97.113(a)(4)** prohibits *"messages encoded for the purpose of obscuring their meaning,
  except as otherwise provided herein."* → **No Meshtastic AES (or any encryption) on amateur bands.**
  Also (a)(2)/(a)(3): no communications **for hire** or in which the licensee has a **pecuniary interest**
  → a ham-band claw must carry only non-commercial traffic. (47 CFR §97.113)
- **[V, quoted] §97.119(a):** *"Each amateur station… must transmit its assigned call sign on its
  transmitting channel at the end of each communication, and at least every 10 minutes during a
  communication…"* → a ham-band beacon/digipeater **must callsign-ID ≤ every 10 min**; §97.119(b)(3)
  allows ID **by a digital-code (RTTY/data) emission**, so the ID can be in-band data. (47 CFR §97.119)
- **[V, quoted] ★ §97.221(b)** permits **automatic control** of an RTTY/data station on **"6 m or shorter
  wavelength bands"** (plus listed HF segments). **6 m-or-shorter includes 70cm AND 33cm** → an
  **unattended/automatic LoRa digital station is broadly permitted on 70cm/33cm amateur** (the segment
  restrictions bind only HF). §97.221(c) governs automatic control on *other* (HF) frequencies: only when
  responding to a station under local/remote control **and** occupying **≤ 500 Hz** — *not* relevant to a
  wideband LoRa UHF node. (47 CFR §97.221) → **This is the legal green light for a fixed unattended
  amateur-band LoRa relay/digipeater.**
- **[B/background] Band-sharing — the strategic choice.** **902–928 MHz** is simultaneously **unlicensed
  ISM (Part 15)** and **amateur 33cm (Part 97, secondary)**. Same hardware, same band, two regimes:

  | Regime | Encryption | Callsign ID | Power | Unattended/automatic | Notes |
  |---|---|---|---|---|---|
  | **915 ISM (Part 15.247)** | ✅ AES OK | ❌ none | low (≈1 W / 4 W EIRP, FHSS) | ✅ no rule | Meshtastic default; what claw runs now |
  | **33cm / 70cm amateur (Part 97)** | ❌ clear-text only (§97.113) | ✅ ≤10 min (§97.119) | high (up to 1.5 kW PEP) | ✅ permitted (§97.221(b)) | more range; non-commercial; secondary on 33cm |

  *Operator is HAM General → both regimes available.* The amateur regime trades AES for range +
  automatic-control legality; ISM trades range for encryption + zero ID burden.

---

## 3. dude-claw role + use-case roadmap

Each cluster tagged **EXISTS** (live) / **EXTEND** (build on existing primitives) / **NET-NEW** (no code or
design today), mapped to the fork's `pr/*` workflow. All firmware items obey THE INVARIANT (feature on a
`pr/*` branch, `dudeclaw` rebuilt — *never* hand-edited). Recommendations are **[B]** design synthesis
unless a cited **[V]** finding is named.

### Cluster A — Backbone & remote links *(highest near-term leverage)*

- **EXISTS:** claw is a decoded node on the fleet `meshforge` channel; mini-dudeai monitors it; AREDN
  remote-site backhaul is live (moc5). MeshForge **leads the RNS-reliability arc** and owns the
  reticulum fork (`1.2.5+mf.5`).
- **EXTEND (T1, low risk):** deploy the claw as a **fixed Meshtastic-interop relay/witness at a remote
  AREDN site** — high mast for radio-horizon gain (cheapest altitude, no FAA exposure), mini-dudeai
  watching its LoRa-RX counters as a "mesh ears health" signal. Reuse `multihop.py` (hop math),
  `network_topology.py`, `node_tracker.py`. *Mind the Meshtastic ROUTER/REPEATER "hop-gobble" warning*
  **[V]** — validate with real mesh data before electing any always-rebroadcast role.
- **NET-NEW (T2, strategic eval):** an **RNS Transport-Node role on a DEDICATED claw**, RTNode-HeltecV4-derived
  **[V]**, bridging local LoRa to the fleet's RNS backbone over WiFi/AREDN — *host-free*, complementing
  the brain-paired node. **Gating decisions:**
  1. **Fork reconciliation** — MeshForge owns the RNS substrate (`1.2.5+mf.5`); RTNode uses **microReticulum
     (C++)**, a *different* implementation. Any RNS-on-claw must honor the **wire-compat invariant** (never
     change crypto/packet/announce format) so it interoperates with the fleet's `rnsd` hosts. **[B]**
  2. **Coexistence vs dedication** **[U]** — can microReticulum + MODE_BOUNDARY share one ESP32-S3 with the
     claw's existing LoRa-interop RX/TX + BLE + OLED + NATS, given the **2 MB PSRAM** envelope? RTNode's own
     "memory-adapted transport mode" note suggests it may want a *dedicated* board. Confirm the board variant
     (4 MB vs 16 MB flash) first.
  - *Decision shape:* a **second, dedicated "claw-transport" node** is likely cleaner than overloading
    `dudeclaw-01`. **[B]**

### Cluster B — APRS-style messaging *(differentiated net-new niche)*

- **EXISTS:** `CanonicalMessage` already has `MessageType.POSITION` + portnum mapping; the gateway
  (`rns_bridge.py`, `message_routing.py`, `message_queue.py` store-and-forward) is the bridging spine;
  `send_text_direct` is the inject path. CanonicalMessage is **parity-pinned with MeshAnchor** — keep compatible.
- **EXTEND (T1):** **brain-side mesh → APRS-IS uplink** (an iGate-equivalent), implemented as an **APRS
  adapter on `CanonicalMessage`** + a gateway egress, mirroring `aprstastic`'s callsign-attribution model
  **[V]**. Internet-dependent; lowest risk; immediate value (fleet positions visible on aprs.fi).
  *Compliance:* clear-text only, callsign-mapped per device (§97.113/§97.119) **[V, quoted]**.
- **NET-NEW (T2):** **bidirectional** mesh ↔ APRS-IS (inbound APRS messages → mesh), like `aprstastic` bidi **[V]**.
- **NET-NEW (T3, the differentiator, hard):** an **off-grid RF-to-RF LoRa-APRS ↔ Meshtastic bridge** —
  *no prior art found* **[V]**, so this is genuine new ground for an off-grid NOC. Open feasibility **[U]**:
  dual-modem timing (one SX1262 can't be on two framings/freqs at once), duty-cycle, and Part 97 ID if the
  APRS leg is on a ham band. Interop targets: OE5BPA + APRS-434 framings **[V]**. *Scope this as a research
  spike before committing firmware.*

### Cluster C — Airborne / altitude *(constrained; pick the legal lane)*

- **NET-NEW, but mostly legally-gated.** Altitude range gain is real **[V]**.
- **EXTEND (T1):** **fixed high mast** at the remote site — the pragmatic "altitude" with zero FAA exposure.
- **NET-NEW (T2, experimental):** a **tethered or free balloon claw beacon/relay** for event/emergency
  coverage — **sub-1-lb payload is the legal sweet spot** (exempt from Part 101 Subpart D **[V]**, §101.7
  hazard prohibition still applies). The claw + small LiPo is already near that weight. Beacon clear-text +
  callsign if on a ham band.
- **DEFER (T3):** **free-flying drone digital repeating** — foreclosed near-term by Part 107 (400 ft, VLOS,
  Remote ID) **[V]**; revisit after **FAA Part 108 BVLOS (~Mar 2026)** **[V, time-sensitive]** or with a waiver.
  Amateur control uplink capped at 1 W (§97.215(c)) **[V]**.

### Cluster D — Always-on second brain (role-deepening with mini-dudeai) *(internal synthesis)*

- **EXISTS:** `host_probe`→`host_frozen`; `claw_metrics_push.py`→OLED; `claw_telemetry.py`→`/api/status.claw`;
  standalone preset; the (already-researched) autonomy ladder.
- **EXTEND (T1, all low-risk, observe-only — honor mini's MF021 boundary):**
  - **More claw → mini signals:** battery trend, BLE-presence, LoRa-RX "mesh ears" health, (future) position —
    each a new `Condition`/signal class so the claw becomes a **distributed fleet sensor/witness**, not just a
    monitored node. New signal classes go in the closed `SIGNAL_CLASSES` enum + `persistent_issues.md`
    (coverage-gated).
  - **Claw TUI handler** (NET-NEW surface): currently claw is controlled only via NATS CLI. Add a handler
    modeled on `handlers/aredn.py` / `handlers/rnode.py`, living beside `handlers/mini_dudeai.py`
    (status / telemetry / OLED-write / LoRa-stats / config) — In-Domain (MF018), no shell-out.
  - **OLED glanceable metrics:** extend `display_print` rows; apply the `status_bar.py` discipline
    (compact, honest, **staleness-marked** — the existing `(old)` suffix). *(External research thin here —
    this is design synthesis.)* **[B]**
- **EXTEND (T2):** **position beaconing** from a fixed node — **fixed interval + jitter**, *not* SmartBeaconing
  (that's mobile-only **[B]**); cadence within the claw's ≥30 s airtime guard; **callsign-ID if on a ham band**
  (§97.119) **[V]**. On 915 ISM, no ID burden but lower range; on 33cm amateur, more range + clear-text + ID.
- **REFERENCE (T3):** supervised actuation / the reset line — **already designed**, gated behind G1–G7;
  see `dudeclaw_reset_safe_set_2026_06_19.md`. Do not duplicate.

---

## 4. The band-regime decision (call it explicitly)

The single most consequential roadmap choice cuts across Clusters A–D: **does a given claw role TX on
915 ISM (Part 15) or 33cm/70cm amateur (Part 97)?** **[V]**

- **Stay 915 ISM** when: AES channel privacy matters, the node is part of the public Meshtastic mesh, range
  is adequate, and zero-ID/zero-callsign operation is wanted. (Status quo.)
- **Go amateur (33cm/70cm)** when: range/power is the constraint, traffic can be **clear-text**, the role is a
  **backbone/digipeater/beacon** (automatic control is *legal* per §97.221(b) **[V]**), and callsign-ID every
  10 min is acceptable. Trade: no AES, non-commercial only.

A claw that bridges *between* a private ISM mesh and a clear-text ham-band backbone must **strip encryption at
the boundary** — never re-transmit ISM-encrypted payloads on amateur frequencies. **[B, compliance-critical]**

---

## 5. Open questions / verify-next (carried from the research + my analysis)

1. **[U]** RF-to-RF off-grid APRS↔mesh bridge feasibility — dual-modem timing, duty-cycle, Part 97 ID. *No
   prior art; research spike before firmware.* (Cluster B / T3)
2. **[U]** Can microReticulum + MODE_BOUNDARY **coexist** on one ESP32-S3 with the claw's existing functions
   (2 MB PSRAM)? Confirm the actual **board variant (4 MB vs 16 MB flash)**. *Likely a dedicated transport
   node.* (Cluster A / T2)
3. **[U]** Realistic, repeatable LoRa **air-to-ground range from LEGAL altitudes** (400 ft drone / sub-1-lb
   balloon) — vs the 38.8 km record — and the matching beacon cadence under 915-ISM dwell / 33cm-amateur norms.
   *Angle-4 specifics were thin in the verified set.* (Clusters C, D)
4. **[U]** Exact **915 ISM Part 15.247 frequency-hopping / dwell-time** math for the claw's modulation, to set
   a defensible max beacon rate. (Cluster D / T2)
5. **[B→verify]** RNS **wire-compat** between microReticulum (RTNode) and the fleet's `rns 1.2.5+mf.5` `rnsd`
   hosts — prove interop on a bench before any claw-transport deployment. (Cluster A / T2)

---

## 6. Caveats, refuted claim & sources

**Refuted (do NOT cite):** the 702 km record was *not* shown to be at 25 mW / 14 dBm (vote 1-2). Cite the
**altitude (38.8 km) and distance (702 km)** only.

**Source-quality notes:** firmware/prior-art claims rest largely on the projects' **own primary READMEs**
(appropriate for "it exists and does X"; *not* for "reliable at scale"). Regulatory claims rest on
**gold-standard primary sources** (eCFR / Cornell LII / FAA.gov) and the §97.113/§97.119/§97.221 text was
**re-fetched & quoted this turn**. Two airborne claims were 2-1 (balloon Part 101 exemption; Remote-ID
"required-to-register" scoping) — per-deployment legal review advised. **Time-sensitive:** FAA Part 108
(~Mar 2026); Reticulum tracks 1.3.5; APRS-434 + RTNode firmware actively evolving.

**Primary sources (verified set):**
- APRS: github.com/afourney/aprstastic · github.com/jaredquinn/meshtastic-bridge · github.com/aprs434/lora.igate
- Backbone: github.com/jrl290/RTNode-HeltecV4 · github.com/markqvist/Reticulum · meshtastic.org (device-role, store-and-forward)
- Airborne: thethingsnetwork.org (702 km) · ecfr Part 107 / Part 101 Subpart D · faa.gov/remote_id · law.cornell.edu §97.215
- Regulatory: law.cornell.edu §97.113 · §97.119 · §97.221 · §15.247 · §97.109 (+ en.wikipedia 33-centimeter_band)
- Beaconing: lora-aprs.org/settings · avbentem airtime-calculator · actility duty-cycle

*(Full URL list + per-source claim counts in the workflow output `wf_1ede8af2-b8d`.)*

---

## 7. One-paragraph recommendation

**Lead with backbone.** The RTNode-HeltecV4 proof **[V]** makes a host-free RNS transport node the
lowest-uncertainty net-new role on this exact hardware — start there as a *dedicated* second claw, after a
bench wire-compat check against the fleet `+mf.5` fork. **In parallel, ship the brain-side mesh→APRS-IS
uplink** (Cluster B / T1) — small, internet-dependent, immediate value, and it seeds the differentiated
off-grid RF-to-RF bridge later. **Treat airborne as "mast now, balloon experiment next, drone deferred."**
**Deepen the second-brain quietly** by adding observe-only claw→mini signals + a claw TUI handler. And
**decide the band regime per role explicitly** — 915-ISM-with-AES vs clear-text-amateur-with-range — because
§97.113 makes that a hard, irreversible compliance fork, while §97.221(b) hands you legal unattended amateur
operation if you want the range.

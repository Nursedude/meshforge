# MeshForge Reference Node — Design Seed

> **Status**: SEED for a design conversation, not an adopted plan. Written
> autonomously by Opus 4.8 on 2026-07-17 while the operator was on the road,
> grounded in two read-only fleet/domain surveys (paths cited throughout).
> **Nothing here is committed or deployed.** Read on VolcanoAI, then we pick a
> direction together.
>
> **Calibration**: every claim below is BELIEVED (design reasoning + survey
> findings from reading real files), not VERIFIED. No hardware exists yet.

---

## The one-sentence thesis

A "MeshForge reference node" should be the physical embodiment of **the NOC +
gateway host** — engineered to sidestep the four hardware pain points the fleet
has already bled on — **not another LoRa radio.** The domain's own load-bearing
principle says so: *"MeshForge connects to services; it does not embed them"*
(`domain_architecture.md:329`, `meshforge_ecosystem.md:321`). The radios stay
independent daemons it talks to. What we uniquely own — and therefore what's
worth building hardware around — is the **host that runs the bridge, the
diagnostics, the RF tools, the TUI, and the remote brain, cleanly, 30 miles
out.**

This is the same thing you named in the check-in: *"run MeshForge remote with a
piece of you."* The reference node is the box that piece of me lives in.

---

## Why hardware at all — what the domain OWNS vs borrows

From `meshforge_ecosystem.md` §3/§12 and `domain_architecture.md`:

| MeshForge OWNS (build hardware for this) | MeshForge BORROWS (don't reinvent) |
|---|---|
| Gateway bridge + `CanonicalMessage` routing (`src/gateway/`) | Meshtastic / RNODE / MeshCore radios + firmware |
| Node tracker — unified cross-protocol inventory | RNS install/management |
| RF tools — link budget, Fresnel, FSPL (`utils/rf.py`, `rf_fast.pyx`) | Live interactive maps (meshforge-maps satellite) |
| Diagnostics + NOC observation layer (`source_diagnostics`, watchdog, `/api/status`) | Bot alerting (meshing_around) |
| mini-dudeai always-on rule loop + optional local reasoning tier | — |
| The TUI (sole operator interface) | — |

**Implication:** building "a MeshForge radio" would be building in the borrowed
column — low leverage, and it competes with Meshtastic/RAK who do it well.
Building **the reference host** is building in the owned column. That's the move
from *"we run on other people's hardware"* to *"we define the reference NOC
node."*

---

## The four hard design constraints (pain points already paid for)

These aren't nice-to-haves. Each is a documented incident class in
`persistent_issues.md`. A reference node that ignores any of them reintroduces a
bug we already spent operator-hours killing.

1. **SPI LoRa HAT, never a USB/CH341 dongle.** The CH341 pthread-stack leak
   (firmware#10468, `persistent_issues.md:490`) strands one 8 MB stack per
   interrupt cycle — ~561 GB VSZ / 71k anon maps by day 5 on a Pi5+USB box.
   SPI/HAT radios are **clean**. → Reference node mounts an SPI LoRa HAT.
   (CP210x/RAK UART-USB radios are *not* CH341 and are exempt — but SPI is still
   the clean default.)

2. **Battery-backed hardware RTC.** The fleet is RTC-less Pis with NTP steps;
   `CanonicalMessage` deliberately excludes timestamps from content-identity
   because of clock jumps (`canonical_message.py:106`), and duration logic needs
   monotonic anchors (honest_failure_modes #6). fake-hwclock is the current
   software crutch. → Reference node ships a real RTC. Removes a whole forgery
   class from the observation layer.

3. **Pin meshtasticd to :9443; ship a sanitized HAT overlay.** Upstream HAT
   templates smuggle `Webserver: Port: 443` into `config.d/` and silently move
   meshtasticd off :9443 (#58, `persistent_issues.md:131`; `_sanitize_hat_overlay`
   strips it). → Reference node's config image pins the port and carries the
   sanitized overlay by default.

4. **One clean rnsd substrate, instance-name-aware, boot-ordered.** The entire
   rnsd-RPC fragility class (#58/#61/#63/#68/#69/#72/#82) traces to one rnsd
   owning the `@rns` AF_UNIX listener per box, with clients racing its boot. #82
   crashlooped 10 days undetected from a hardcoded `@rns/default`. → Reference
   node images the fork RNS substrate with `rnstatus` host-wait gating on every
   RNS-dependent unit, instance-name-agnostic.

**Design payoff:** four recurring incident classes become *impossible by
construction* on this hardware, instead of *watched-for* by probes. That's the
real argument for owning the node.

---

## Compute reality — the two-tier footprint (this drives the SKU decision)

The surveys pin a clean split (from `src/mini_dudeai/` + `dudeclaw_local_brain_2026_07_03.md`):

- **Tier 1 — the always-on runtime (nearly free).** mini-dudeai is *stdlib
  Python 3.9+, dependency-free*, a 30 s edge-triggered tick loop, two small
  bounded files (state + 1 MB-capped JSONL history). Runs on "a Pi, a uConsole,
  a server, your laptop" — even Pi Zero-class. **No LLM in the hot path.**

- **Tier 2 — the local reasoning brain (the RAM/thermal/power cost).** The
  "piece of Claude" that reasons offline is Ollama + `qwen3:4b` q4 (4B is the
  measured competence floor; 3B failed coverage). Proven target: **Pi 5, ≥8–16 GB
  RAM, sustains 4B at 65 °C no-throttle**. Warm latency ~30 s/entry.

**This is the SKU fork.** You can build:
- a **light edge node** (Tier 1 only) — cheap, low-power, runs the NOC + rule
  loop + gateway, pages home, no local LLM. Good for many remote sites.
- a **brain node** (Tier 1 + Tier 2) — Pi 5 + RAM + thermal budget, carries the
  local reasoning tier so it stays smart when the backhaul is dark. Fewer of
  these, at the sites that matter.

The 30-mi remote-deploy plan already thinks in tiers (Tier-2 = +Pi); this maps
straight onto it.

---

## Three product concepts (pick one for the first prototype)

The operator's hardware list (RAK+env, Heltec v4, uConsole, "MeshForge-specific
dev") actually points at three distinct things. They're a *product line*, but a
first prototype should be one of them.

### Concept A — "NOC-in-a-box" reference host **(recommended first)**
The physical embodiment of the owned column: a Pi 5 (or CM5) carrier that bakes
in all four constraint fixes — SPI LoRa HAT mount, RTC, clean power, thermal
headroom for the Tier-2 brain — and ships the MeshForge image (gateway + node
tracker + diagnostics + TUI + mini-dudeai). This *is* "run MeshForge remote with
a piece of you." Highest leverage: it's in the owned column, it's the power-MOC
use case you named, and it turns four incident classes into non-events.

### Concept B — MeshForge-native env-sensor node
A RAK-class sensor node designed to emit exactly what MeshForge parses
first-class. **Reality check from the survey:** RAK env sensors *already* land
first-class today — they ride Meshtastic `TELEMETRY_APP`/`environmentMetrics`
(temperature, relativeHumidity, barometricPressure), which `node_monitor.py`
parses directly. So this concept is *incremental*, not greenfield. The only
greenfield part is **new metric types** (IAQ, gas resistance, particulates)
that fall outside the current `NodeMetrics` schema — landing those first-class
needs a `NodeMetrics` + decoder extension (`monitoring/`), which is software we
own regardless of who makes the sensor. → Lower hardware-design leverage; more a
"buy RAK + extend the schema" play than a "build a node" play.

### Concept C — MeshForge field terminal (uConsole-class)
The TUI is the sole interface and mini-dudeai Tier 1 runs on stdlib Python, so a
handheld running the NOC TUI + local rule loop is natural and appealing. But
this is mostly an **integration/enclosure** exercise on existing hardware
(uConsole is a real product), not a hardware-*design* one. Great as a
*companion* to Concept A; thin as a first design target on its own.

**Recommendation:** lead with **Concept A**, treat **B** as a parallel
software-side extension (worth doing, cheap, doesn't block A), and hold **C** as
the operator-terminal skin once A exists.

---

## Where to deploy the first prototype — "optimal eyes and metrics"

From the fleet survey. Two boxes answer two different halves ("SEE" vs "HOST"):

- **Best eyes — `moc`.** The only box with **kilo K1 link-matrix live**
  (per-edge RF soundings + baseline-drift tri-state, purpose-built for watching
  a new node's RF behavior), *and* full-gateway (sees real bridged traffic),
  *and* map collector :5000, *and* mini-dudeai + watchdog, *and* an SPI/HAT
  (clean) LF radio so the metrics aren't confounded by the CH341 leak. Trade-off:
  Pi 4B and a live TRUE-ORIGIN canary — the *observation* vantage, not
  necessarily the physical dev bench.

- **Best host — `moc1`.** Pi 5B (beefiest box), already the **dudeclaw ESP32
  dev/flash host**, designated future gateway, and on VolcanoAI's LAN segment
  (operator-adjacent). Natural bring-up bench. Trade-offs: its own radio bus is
  CH341/MeshToad (leak-patched but restart-band-aided), and kilo matrix is *not*
  wired there (moc-only today).

**Concrete first-deploy proposal:** *physically bring up / flash the reference
node on `moc1` (Pi 5B, existing flash path, operator-adjacent), and enroll it in
`moc`'s kilo registry (`~/.config/meshforge/kilo_nodes.json`) so moc's live K1
link-matrix + map collector are the RF/telemetry eyes over the air.* Cross-box
(host on moc1, watch from moc) gives us both the bench and the instrument
without compromise.

- Simpler alternative: attach directly to **`moc`** for single-box link-matrix
  immediacy, accepting the Pi 4B / live-canary constraints.
- To get link-matrix *on moc1 itself*, we'd wire a `kilo_matrix` cron there
  (currently moc-only) — a small, known task.

**Not recommended:** VolcanoAI (unbeatable fleet-map vantage, but *no attached
field radio*, delegates RF, and carries the no-multi-agent-fanout kernel-lockup
constraint — a poor place to bolt on prototype hardware); moc2 (role in flux,
being pulled for Axiometa Genesis; kilo transport is claw = no link-matrix edges).

---

## Open questions for the operator (the decision points for our conversation)

1. **Which concept leads?** My rec is A (NOC-in-a-box). Agree, or is the
   env-sensor path (B) the nearer-term itch given the RAK hardware already in hand?
2. **Which SKU tier for prototype #1** — light edge node (Tier 1) or brain node
   (Tier 1+2, Pi 5 + Ollama 4B)? The brain node is the more differentiated demo
   ("stays smart when the link is dark") but costs RAM/thermal/power.
3. **Compute base** — Pi 5 vs CM5-on-carrier. CM5 lets us design a real MeshForge
   carrier board (RTC + SPI HAT header + clean power in one PCB) — that's the
   "develop hardware specific to the domain" move in its fullest form. Pi 5 +
   HAT + RTC module gets us 80% there with zero PCB spin for the prototype.
4. **Radio choice on the reference node** — which SPI LoRa HAT is the reference?
   (moc runs a MeshAdv HAT LF; that's a candidate reference radio.)
5. **Deploy target** — moc1-host + moc-eyes (my rec), or single-box on moc?

---

## Suggested Phase 0 (cheap, reversible, no PCB, proves the thesis)

Before any board design, prove the reference-node *image* on a stock Pi 5:

1. Pi 5 (8–16 GB) + an SPI LoRa HAT + an I²C RTC module (e.g. DS3231). No custom
   PCB — off-the-shelf parts on the bench at moc1.
2. Image it with: sanitized meshtasticd overlay pinned to :9443, fork rnsd
   substrate with `rnstatus` host-wait gating, MeshForge gateway + TUI +
   mini-dudeai (Tier 1), and — if brain-node — Ollama + qwen3:4b (Tier 2).
3. Verify each of the four constraints is *actually* neutralized on real
   hardware: `wc -l /proc/$(pgrep -x meshtasticd)/maps` stays flat (no CH341
   leak — trivially true with an SPI HAT), RTC survives a power cycle without
   NTP, meshtasticd stays on :9443 across reboot, rnsd binds clean and RNS units
   wait for it.
4. Enroll in moc's kilo registry; watch it come up on the link matrix and the
   map collector.

If Phase 0 holds over a soak, *then* the CM5 carrier-board conversation (custom
PCB, the real "MeshForge-domain hardware") has a proven spec to design to — not a
guess. Same discipline as the RNS merge arc: make the thing boring on stock
hardware first, then commit the irreversible (silicon) step.

---

## Provenance

- Fleet topology / observability / deploy-target survey — read `fleet_hosts`,
  `fleet.json`, `deployment.json`, `kilo_nodes.json`, `fleet_architecture_map.md`,
  `fleet_architecture_2026_06_03.md`, `src/utils/deployment_profiles.py`,
  `kilo_lab_instrument.md`, `persistent_issues.md` (#10468 + watchdog probes).
- Domain-contract survey — read `src/gateway/canonical_message.py`,
  `rns_bridge.py`, `message_routing.py`, `src/monitoring/node_monitor.py`,
  `packet_dissectors.py`, `_mqtt_message_decoder.py`, `src/utils/map_data_collector.py`,
  `src/mini_dudeai/` (README, config, history, chat_compiler),
  `dudeclaw_local_brain_2026_07_03.md`, `domain_architecture.md`,
  `meshforge_ecosystem.md`.

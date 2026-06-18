# Dude-Claw as a Second Brain — Deep-Research Findings (2026-06-17)

> **Provenance.** Output of the `deep-research` workflow run `wf_0e34d5bd-51a`
> (105 agents, ~6.1M tokens, 23 sources fetched → 106 claims → 25 adversarially
> verified by 3-vote → **21 confirmed / 4 killed**), plus two follow-up fetches
> that closed the workflow's own #1 open question (ESP32-S3 / Heltec V4
> board-level power). Companion to the kickoff plan
> `.claude/plans/dudeclaw_second_brain_2026_06_18.md` — that doc states the
> operator's invocation and the 5 research questions; **this doc is the answer.**
>
> **Calibration.** Every external fact below carries its source URL and the
> verification vote (e.g. `3-0`). Where I do my own engineering (the power
> budget arithmetic), it is labelled **DERIVED** — reasoned from cited component
> numbers, **not field-measured**. The architecture-naming answer (RQ5) is the
> weakest-evidenced and is flagged as such. Four claims were **refuted 0-3** and
> are listed at the end so we do not build on them.

---

## TL;DR — the five answers, then what to build

1. **Autonomy ladder.** The safe path from *report → act* is **not** "trust the
   agent more." It is to wrap the unverified agent in a **Run-Time Assurance
   (RTA) / Simplex filter**: a *verified, deterministic* monitor + backup
   controller, **agnostic to the agent's internals**, that keeps the system
   inside a forward-invariant *safe set*. The agent proposes; the verified
   filter decides what actually fires. Autonomy is **per-function**
   (Parasuraman/Sheridan/Wickens 2000): a node can be fully autonomous at
   *observe/report* while held at *propose-and-confirm* on *actuation*.
2. **Edge/brain split.** Keep the **fast, deterministic, survives-brain-loss
   critical loop on the Heltec**; keep **reasoning/memory/cross-correlation on
   the Pi**. As autonomy grows, the *safety guardrail migrates DOWN to the
   edge* so the critical loop stays closed when NATS/WiFi drops.
3. **Power.** Component numbers are now known and cited. The **ESP32-S3 +
   always-on WiFi is the dominant draw** (~95–180 mA board-level); LoRa RX
   (~5 mA) and a duty-cycled GPS (~0.07 mA with backup-state retention) are
   nearly free; LoRa TX is a **1 A peak-current / PSU** problem, not an energy
   one. **DERIVED budget:** ~120 mA central → a **10 W panel + MPPT solar
   charger + 10 Ah LiPo** is net-positive multi-day in Hawaii sun.
4. **Solo mode.** **DTN store-carry-forward (RFC 4838 / Bundle Protocol v7,
   RFC 9171)** is the standard — **but BP7-on-an-MCU was refuted**; DTN lives at
   the **Pi-brain tier**. The Heltec degrades by running its local rules,
   buffering witnesses to flash, and store-and-forwarding over LoRa (Meshtastic
   store-and-forward module). On reconnect: replay + reconcile, and **never emit
   "recovered" from the gap itself** (honest-failure-modes).
5. **Architecture name.** This is a **three-layer (3T) cyber-physical agent with
   runtime-assured actuation**: reactive skill layer (Heltec firmware) →
   executive/sequencing layer (mini-dudeai rule-loop) → deliberative layer
   (Claude-on-cadence), over a persistent memory/ledger (long-term + episodic
   continuity). "Second brain" is the operator's framing for the
   deliberative+memory tier — apt, but editorial, not a literature term.

---

## RQ1 — The autonomy ladder (how to grow observe → report → ACT safely)

**The load-bearing finding (`3-0`, ×3 merged claims).** Run-Time Assurance is an
*online verification mechanism* that **filters an unverified primary controller's
output to ensure safety** — "a monitor that watches the state of the system and
output of a primary controller, and a backup controller that replaces or
modifies control input when necessary to assure safety." Crucially, the filter
is *"constructed in a way that is entirely agnostic to the underlying structure
of the primary controller"* — which is **exactly** what lets an unverified,
learning, or LLM-driven controller (our Claude-on-cadence PROPOSE layer) drive a
real actuator safely. [arXiv:2110.03506, Hobbs et al., IEEE CSM]

- **Simplex / Black-Box Simplex** is the canonical named switching pattern
  (`3-0`): control authority switches from the *unverified advanced controller*
  to a *verified baseline/backup controller*, decided by a runtime **decision
  module (monitor)**. In the **Black-Box** variant both controllers are opaque
  (only I/O checked at runtime) while the *architecture itself is proven safe*.
  "The Simplex architecture is an instance of Runtime Assurance where a trusted
  component takes control when an untrusted component violates a safety
  property." [arXiv:2102.12981, Mehmood et al., NASA Formal Methods 2022]
- **Safety = forward invariance of a predetermined safe set** (`3-0`), enforced
  either by explicit set-membership **switching** (Simplex) or an implicit
  **optimization filter** (Active Set Invariance Filter / control barrier
  functions solving a QP) that is **minimally invasive** — *passing safe commands
  through, optimally modifying only unsafe ones*. The skeptical-check
  qualification matters: **switch at the RECOVERABLE set, not the unsafe
  boundary**, or the guarantee is silently void. [arXiv:2110.03506]
- **Autonomy is a per-function spectrum, not a binary** (`3-0`). Automation
  applies independently to four function classes — information **acquisition**,
  information **analysis**, **decision/action selection**, **action
  implementation** — each on a low→high continuum (Sheridan-Verplank 10-level
  scale). The model "provides an objective basis for deciding which functions to
  automate and to what extent." [Parasuraman, Sheridan & Wickens 2000, IEEE
  Trans. SMC-A 30(3):286-297, DOI 10.1109/3468.844354]

**The honest caveat (workflow-flagged, important).** The entire RTA/Simplex
corpus is grounded in **continuous control** (aircraft/spacecraft reachability
over plant models). The mapping onto a **discrete GPIO host-reset line** is a
*reasonable conceptual transfer the sources do not themselves validate*. The
guarantee also rests on (a) the backup/filter itself being verified and (b) the
safe set being correctly, conservatively defined. **A wrong safe-set definition
silently breaks the guarantee** — which is precisely our own honest-failure-modes
discipline (a degraded/incorrect internal bound must not present as "safe").

**→ The ladder, named, for dude-claw:**

| Rung | Function automated | Gate | Where we are |
|------|--------------------|------|--------------|
| 0 Observe | acquisition (high) | none | ✅ live (host_probe, sensors) |
| 1 Report | analysis (high) | none | ✅ live (host_frozen → mini → /fleet) |
| 2 **Propose-and-confirm** | decision (mid) | human ratify (the existing `propose→ratify→apply` trust model) | ⏳ **next rung** |
| 3 Supervised actuation | implementation (low) | **verified edge filter** (Simplex) + human-on-the-loop | designed, gated (Phase 5) |
| 4 Bounded autonomy | implementation (mid) | filter + rate-limit + kill-switch + boot-loop abort | future, post-soak |

The first physical-actuation step (Phase 5 RUN→GND host reset) must **never sit
directly under the agent**. It sits behind a verified, deterministic *edge*
filter whose safe set is defined first-principles (the literature won't hand us a
discrete-action safe set — see Open Questions).

---

## RQ2 — Intelligence split (what runs on the Heltec vs the Pi)

**Split computing** is the established named pattern (`3-0`, ×3 merged): a "head"
runs on the constrained device, a "tail" on the more capable tier, to cut edge
bandwidth/energy — motivated because *"continuously executing the entire DNN on
mobile devices can quickly deplete their battery."* IoT-Edge-AI partitioning is
taxonomized into Data / Computation-task / DNN-model families. [arXiv:2103.04505,
ACM Computing Surveys 55(5); arXiv:2406.00301, 2024 survey]

**Relevance gap (workflow-flagged).** This literature is about *splitting a neural
network* across device/server. The Heltec runs **no on-device model by design**,
so the specific mechanism (where to cut the DNN) describes an architecture we
*foreclose*. Only the **general principle transfers**: heavy compute drains the
constrained node → keep the light loop local, offload reasoning. The over-strong
form — "the constrained device *cannot* run the model, partly because of a
non-rechargeable/short-life battery" — was **refuted 0-3**: our no-LLM constraint
is **RAM, not battery**, and the design already states this correctly.

**→ The split for dude-claw, as autonomy grows:**

- **On the Heltec (fast, local, survives brain-loss):** sensor acquisition, the
  deterministic threshold loop, and — as we add actuation — *the verified safety
  filter itself* (rate-limit, safe-set check, kill-switch, post-reset boot-loop
  abort). The edge keeps the critical loop closed when the brain is unreachable.
- **On the Pi (mini-dudeai + Claude-on-cadence):** reasoning, memory,
  cross-correlation across the fleet, rule synthesis (PROPOSE), and the DTN
  bundle agent for inter-fleet reconciliation.
- **The migration rule:** *every time we add an actuation capability, its
  deterministic guardrail moves DOWN to the edge.* Reasoning stays up; the
  reflex that protects against a wedged-brain or wedged-NATS stays down.

---

## RQ3 — Power budget for true always-on + GPS

### Component numbers (all cited, all verified)

| Component | State | Current | Source |
|-----------|-------|---------|--------|
| ESP32-S3 (chip) | deep-sleep RTC-on | ~7 µA | ESP32-S3 datasheet §4.7 |
| ESP32-S3 (chip) | light-sleep | ~240 µA | ESP32-S3 datasheet |
| ESP32-S3 (chip) | modem-sleep (CPU on, WiFi assoc/DTIM) | ~15 mA | ESP32-S3 datasheet |
| ESP32-S3 (chip) | WiFi TX peak | ~310 mA | ESP32-S3 datasheet |
| **Heltec V4 (board)** | **deep standby floor** | **~12 mA** | Heltec V4 wiki test |
| **Heltec V4 (board)** | **BLE + LoRa, 46 h avg** | **~95 mA** | Heltec V4 wiki test |
| **Heltec V4 (board)** | **LoRa TX @27 dBm peak** | **~1 A for ~1 s** | Heltec V4 wiki test |
| SX1262 LoRa | RX continuous (DC-DC) | 4.6 mA (5.3 mA boosted) | Semtech SX1262 DS Rev.1.2 `3-0` |
| SX1262 LoRa | TX @ +14 / +17 / +22 dBm (optimal PA) | 45 / 58 / 118 mA | Semtech SX1262 DS `2-1` |
| u-blox MAX-M10S GNSS | acquisition / tracking | 11.5 / 9.5 mA | MAX-M10S DS UBX-20035208 R08 `3-0` |
| u-blox MAX-M10S GNSS | power-save tracking | 5 mA | MAX-M10S DS `3-0` |
| u-blox MAX-M10S GNSS | hardware-backup | 28 µA | MAX-M10S DS `2-1` |
| u-blox MAX-M10S GNSS | TTFF hot / cold | **1 s / 27 s** | MAX-M10S DS `3-0` |

Board-level figures **include** the ESP32-S3, regulator, and board losses;
chip-level figures (SX1262, MAX-M10S) **exclude** them and must be added on top
of the board baseline only where the radio is a *separate* module.

### The three power truths

1. **Always-on WiFi (NATS reachability) is the dominant consumer.** The Heltec's
   own ~95 mA average is *BLE + LoRa with no continuously-associated WiFi*.
   dude-claw adds an always-associated WiFi link for NATS, pushing the average
   up. Honest band: **~95 mA (idle, WiFi DTIM modem-sleep) to ~180 mA (busy NATS
   traffic, no power management)**, central estimate **~120 mA**.
2. **"Must stay reachable" forecloses deep-sleep.** Deep-sleep (~12 mA board) is
   off the table for the NATS-reachable role — the only levers are: **WiFi DTIM
   modem-sleep**, **drop LoRa TX power** (+27 dBm → lower), **duty-cycle GPS**,
   **blank the OLED** (~5–8 mA). Deep-sleep only returns in a *LoRa-wake solo
   mode* where reachability is via a LoRa beacon, not WiFi.
3. **GPS is nearly free if duty-cycled; LoRa TX is a peak-current problem, not an
   energy one.** A GPS hot-start fix (1 s @ 11.5 mA every 5 min, 28 µA backup
   between) averages **~0.07 mA** — negligible. LoRa TX at low duty (~30 msgs/day
   × ~1.5 s) adds **<0.01 Ah/day** of energy, but its **~1 A peak** dictates the
   battery/regulator's peak-source capability and a fat decoupling cap.

### DERIVED budget (reasoned from the cited numbers — NOT field-measured)

At a central **120 mA** average: **2.88 Ah/day ≈ 10.7 Wh/day** (3.7 V nominal).

**Battery-only autonomy** (to a 3.4 V brownout ≈ ~80–85% of rated LiPo capacity):
- 5,000 mAh LiPo → ~4.1 Ah usable → **~34 h (~1.4 days)**
- 10,000 mAh LiPo → ~8.3 Ah usable → **~69 h (~2.9 days)**
- Multi-day without solar → **10,000–20,000 mAh**.

**Solar to net-zero** at 10.7 Wh/day, Hawaii ≈ 5 peak-sun-hours/day, system
efficiency ≈ 0.55 (panel derate × MPPT × LiPo round-trip):
- Required panel ≈ 10.7 / (5 × 0.55) ≈ **3.9 W** to break even.
- With 2× margin for cloudy days → **6–10 W panel**.
- **Charge controller** rated ≥ panel short-circuit current (10 W/6 V ≈ 1.7 A) —
  a **CN3791-class MPPT** solar LiPo charger (~2 A) suits; a TP4056-with-solar is
  marginal at this current.
- **Battery for night + cloudy ride-through** ≥ 2× daily Wh ≈ ≥ 21 Wh
  (~5,700 mAh); for 2–3 dark days, **10,000–20,000 mAh**.

**Recommended always-on solar config:** **10 W panel + CN3791-class MPPT charger
+ 10,000 mAh LiPo**, net-positive in Hawaii sun with ~2–3 days of dark-weather
ride-through. **If WiFi DTIM modem-sleep is implemented** (pulling the average
toward ~80 mA), the panel can shrink to **~6 W**.

---

## RQ4 — Solo vs fleet (disconnected operation)

**DTN is the standard** (`3-0`). RFC 4838 (Delay-Tolerant Networking
Architecture): nodes "may choose to store bundles for some time… most DTN nodes
will use some form of persistent storage… stored bundles will survive system
restarts," forwarding when a *contact* (a positive-capacity interval) arises.
**Bundle Protocol v7, RFC 9171** (IETF Proposed Standard, **Jan 2022**, obsoletes
5050) realizes it as a *store-carry-forward overlay* for "highly stressed
environments, including those with intermittent connectivity," explicitly
handling the case where "the sender and receiver are not concurrently present."
[RFC 4838; RFC 9171]

**The hard constraint (refuted 0-3, do NOT build on the inverse).** uD3TN running
on microcontrollers / "BP7 store-and-forward viable on constrained ESP32-class
hardware," and uD3TN's custody-transfer + late-binding as *the* solo pattern,
were **all refuted**. **Treat DTN/BP7 as a Pi-brain-tier capability — do not plan
a bundle-protocol agent on the Heltec.**

**→ The solo-mode design for dude-claw:**

- **Edge degrade (Heltec):** keep the local deterministic rules running (the edge
  keeps the loop closed), buffer observations to a **bounded flash ring**, and
  **store-and-forward over LoRa** using the Meshtastic store-and-forward module
  (the proven edge mechanism). The OLED + LED still report locally.
- **Brain tier (Pi):** runs the DTN/BPv7 bundle agent for inter-fleet
  reconciliation across the fleet's two wired segments + AREDN.
- **Re-converge:** on reconnect, replay the buffered witnesses and reconcile with
  last-known fleet state (offline-first / eventual-consistency). **Honor
  honest-failure-modes: never emit a "recovered/cleared" transition from the
  link-gap itself** — hold edge state and surface the blindness as its own
  signal. This is exactly the existing mini-dudeai `source_hold` /
  "unobservable ≠ resolved" discipline, applied to the NATS link.

---

## RQ5 — The "second brain" architecture (naming)

**This is the weakest-evidenced question** (workflow's own assessment). The
verification corpus *strongly grounds the fast-reactive + slow-deliberative
LAYERED safety architecture* — Simplex/RTA **is** a fast verified reactive layer
beneath a slow unverified deliberative layer — but produced **no surviving
primary claim** for the embodied-agent / digital-twin / subsumption / 3T /
LIDA-SOAR cognitive-architecture framings. The fetched sources (subsumption,
the 3T "Intelligent Reactive Agents" paper, the CoALA-style cognitive-agent
survey) support the *shape* but were not run through the 3-vote gate, so I name
the pattern with appropriate humility:

**dude-claw is a three-layer (3T) cyber-physical agent with runtime-assured
actuation:**

1. **Reactive skill layer** — the Heltec firmware: deterministic, fast, no LLM,
   survives brain-loss. (Brooks-style reflexes + the verified safety filter.)
2. **Executive / sequencing layer** — mini-dudeai's rule-loop on the Pi:
   conditions → actions, edge-state, cooldowns, store-and-forward.
3. **Deliberative layer** — Claude-on-cadence PROPOSE: reasoning, rule synthesis,
   cross-correlation. Proposes; humans (and, later, the verified filter) ratify.

…over a **persistent memory/continuity substrate** (the memory directory +
calibration ledger = long-term + episodic memory, CoALA-style). The **"second
brain"** is the operator's framing for the *deliberative + memory* tier — a
persistent, embodied extension of the cloud Claude session. It is an apt name,
but **editorial, not a literature term**; the literature-anchored name is
"three-layer / 3T architecture with a runtime-assured (Simplex) actuation path."

---

## What we could NOT verify, and what was refuted

**Genuinely open (carry into the next arc):**

1. **End-to-end field power.** The component numbers are solid, but the *actual*
   dude-claw average under simultaneous WiFi+LoRa+BLE was not field-measured —
   the ~120 mA central is DERIVED. **Action:** instrument the VBAT divider
   (`battery_read` is already staged) over a 24 h soak to replace the estimate
   with a measured number before committing to a panel size.
2. **The discrete-action safe set.** RTA theory is continuous-control; the
   correctly-defined "safe set" + verified backup behavior for a *GPIO reset
   line* (rate-limit, reversibility window, confirm-token, kill-switch,
   boot-loop abort) is **unspecified in the literature** and needs first-
   principles design before Phase 5 actuation.
3. **Cognitive-architecture naming** beyond the grounded fast/slow split — the
   embodied-persistent-agent + continuity naming produced no surviving primary
   claim; the 3T mapping above is reasoned, not literature-gated.

**Refuted 0-3 (do not build on these):**

- The Heltec can't host the LLM *because of a non-rechargeable/short-life
  battery* — **RAM is the reason**, not battery.
- uD3TN / BP7 runs on microcontrollers → **no**; DTN is Pi-tier.
- uD3TN custody-transfer + late-binding as *the* named solo pattern → **not
  supported** by the source.

**Vote weaknesses:** the SX1262 TX-nonlinearity and MAX-M10S backup-current
claims carried `2-1` (nuance: optimal-vs-default PA settings; typical-vs-worst-
case) — every figure verified verbatim against the primary datasheet, so the
numbers stand, but real field draw will differ. All power numbers are datasheet
typicals at 25°C — temperature and board losses are extra.

---

## Recommendations for THIS system (the four deliverables)

**(a) Next safe autonomy rung after "report":** *propose-and-confirm on a single,
reversible, bounded action.* Keep Phase 5 host-reset human-ratified (the existing
`propose→ratify→apply` model is already the right pattern). **Before it can ever
go auto, build a verified deterministic safety filter ON THE HELTEC** enforcing
the safe set — e.g. *≤1 reset / N hours, only after K consecutive confirmed-FROZEN
observations, only if a recoverable precondition holds, hardware kill-switch, and
a post-reset observation window that aborts a boot-loop.* The agent proposes; the
edge filter is what closes the relay. This is RTA/Simplex mapped onto a discrete
action — and per Open Question #2, **the safe set is ours to design**, honestly.

**(b) Edge/brain split as autonomy grows:** the **deterministic guardrail
migrates DOWN to the edge with every new actuation capability**; reasoning,
memory, and cross-correlation stay UP on the Pi. The edge keeps the critical loop
closed when the brain/NATS is unreachable; the brain keeps proposing.

**(c) Battery + solar + GPS budget:** **10 W panel + CN3791-class MPPT charger +
10,000 mAh LiPo** → net-positive multi-day in Hawaii sun (shrink the panel to
~6 W if WiFi DTIM modem-sleep lands). **Duty-cycle the GPS** (hot-start fix +
hardware-backup retention = near-free). **Size the battery/regulator for the
~1 A LoRa-TX peak**, not the average. **First close Open Question #1** with a real
`battery_read` soak before buying a panel.

**(d) Solo-mode degrade/re-converge:** **DTN/BPv7 at the Pi tier only.** Heltec
degrades to local rules + a bounded flash ring + LoRa store-and-forward; on
reconnect, replay + reconcile offline-first, and **never claim "recovered" from
the link gap itself** — hold edge state, surface the blindness (mini-dudeai
`source_hold`).

---

## Immediate next steps (concrete, ordered)

1. **Carry-over firmware fix** (already queued for `+dudeclaw.15`): extend the
   `host_probe` banner window 800 ms → ~2500 ms (the loaded-`.32` false-FROZEN
   root cure). Ride it with whatever the next claw touch is.
2. **Power soak** (closes Open Question #1): enable a `battery_read`-based draw
   sample over 24 h to replace the DERIVED ~120 mA with a measured average — the
   prerequisite for committing to a panel size.
3. **Design the discrete-action safe set** (Open Question #2) for the Phase 5
   reset line, on paper, against the honest-failure-modes checklist, *before* any
   actuation firmware. This is the gate between rung 2 and rung 3.
4. **GPS hardware spec** — MAX-M10S (numbers above) as the candidate; duty-cycled
   with hardware-backup; position in reports + geofenced sensing.

> Sources (primary, verified): arXiv:2110.03506 · arXiv:2102.12981 ·
> Parasuraman/Sheridan/Wickens 2000 (DOI 10.1109/3468.844354) · arXiv:2103.04505
> · arXiv:2406.00301 · Semtech SX1262 DS Rev.1.2 · u-blox MAX-M10S DS
> UBX-20035208 R08 · RFC 4838 · RFC 9171 · ESP32-S3 datasheet §4.7 · Heltec V4
> wiki test result · Meshtastic store-and-forward module docs.

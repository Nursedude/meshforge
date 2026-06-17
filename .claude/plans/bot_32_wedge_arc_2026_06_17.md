# .32 Bot Wedge Arc — Research Synthesis + Session Plan (2026-06-17)

> Companion to memory `project_bot_32_hard_reset_2026_06_17`. Raw cited research in
> `./bot_32_research/{leg1_pi_freeze,leg2_alerting,leg3_esp32_watcher}.md` (3 remote
> agents, run off-box to respect the VolcanoAI no-research-workflow rule). Operator
> directives driving this: **"keep it in the TUI — if they have to fix manually the
> software isn't doing its job"** (MF018), **"3,2, item 1 durable"** (analyze → build
> in-app → durable), **"where was mini? the watchdog? all these safeguards and
> silence???"**, and **"you have dude-claw — this is you — why is that not better
> utilized?"**.

---

## 0. The reframe (VERIFIED ground truth from this session)

Three facts re-derived from our own logs/notes this session, not from the web:

1. **The silence was a surfacing failure, not a detection failure.** `~/fleet_alerts.log`
   recorded **every one of the 9 wedges** (ALERT + RECOVERED, incl. the 33h one), and
   `fleet_offline_check.sh:66` fired an ntfy push each time. But that cron is a
   side-channel with three defects: **fire-once** (no re-alert — a 33h outage got ≤1
   push; ntfy's ~12h TTL aged it off), **unwitnessed delivery** (`curl … >/dev/null
   2>&1` discards the result — we cannot prove any push reached the phone), and it
   **never reaches mini or the watchdog** — the two surfaces the operator actually
   watches. `.32` is not a watchdog box; its liveness lived only in a logfile read
   "on return."
2. **`.32` sits behind an AREDN node's WAN + a port-22 DNAT** (its only real iface is
   `10.120.250.195`; `192.168.86.32` is the node's WAN). Our remote SSH probe from
   VolcanoAI **structurally cannot tell "the box froze" from "the WAN/DNAT path
   died."** Some of the 9 "wedges" may have been path events, not box freezes.
3. **dude-claw (`10.120.250.199`) is on `.32`'s own subnet**, with independent power,
   independent comms (WiFi→NATS→moc2 brain + LoRa), and a mini-dudeai sensor loop —
   i.e. the textbook out-of-band witness, currently used for BLE/battery demos but
   **not** for watching the box dying next to it.

---

## 1. Research findings (BELIEVED — web-sourced & cross-checked; cite raw reports)

### 1A — Pi hard-freeze root cause + forensics  (`leg1_pi_freeze.md`)

- **PIVOTAL — and it corrects our existing plan:** ramoops/**pstore captures kernel
  *panics/oopses*, not silent hard hangs.** A true SoC/kernel wedge writes *nothing*
  to pstore. The existing `.32` plan assumed pstore would catch the hard reset — it
  won't, alone. Forensics must be **two-pronged**: (a) **convert silence into a
  panic** with sysctls (`softlockup_panic=1`, `hung_task_panic=1`, `panic_on_oops=1`,
  `panic=10`); (b) **arm the hardware watchdog** so the box self-recovers and leaves a
  reset fingerprint.
- **The hardware watchdog is the single biggest lever.** `bcm2835_wdt` +
  `RuntimeWatchdogSec=14` (⚠️ **≤15s hard limit on Pi** — larger is silently ignored)
  → a kernel/soft-lockup freeze becomes an **automatic ~15s reboot instead of a manual
  hard-reset.** This likely *ends the daily physical resets* on its own.
- **Ranked suspects for `.32`'s profile** (service `active`/0 restarts, whole box dead,
  ~daily, load ~2.2): **#1 undervoltage/brownout** (weak PSU / thin cable / peripheral
  inrush — *physically coupled to* **#3 SD corruption**), **#2 kernel soft-lockup /
  hung-task**, **#4 USB-peripheral-induced hang**. Thermal (#5) and OOM (#6) usually
  *self-recover* → **do not** match "manual-cycle-only" → low probability.
- **Cheapest highest-value witness:** a 1-min canary logging `vcgencmd get_throttled`
  (undervoltage bits 0/16 = brownout fingerprint) + PSI (`/proc/pressure/*`) + temp to
  **persistent** disk — catches #1 even when nothing else survives.
- **Verify `CONFIG_PSTORE` on-box FIRST** — RPi OS has historically shipped *without*
  it (`zcat /proc/config.gz | grep -i pstore`). If absent, rely on persistent journald
  + watchdog + UART, not ramoops.
- **Pi 5 only:** update EEPROM first (`rpi-eeprom-update -a`); set
  `POWER_OFF_ON_HALT=1`; use the true 5A/27W PD supply (don't force `usb_max_current`
  on a weak supply — that *creates* brownout).
- **Post-mortem discrimination tree** (run when a box returns): canary throttle bits →
  brownout; populated pstore backtrace → names the subsystem (mmc/ext4=SD, usb=#4,
  net/driver=that driver); **empty pstore + manual-cycle-needed = true hard hang** (#7
  firmware / #8 hard-lockup); journal ends mid-line = hard cut (power/freeze) vs clean
  shutdown target = something rebooted it cleanly (re-scope).

### 1B — Dead-man's-switch alerting  (`leg2_alerting.md`)

- **Invert the logic: make *silence* the alarm.** Push-on-failure-from-a-dying-node is
  the antipattern; the robust property is **who renders the verdict on silence — an
  independent timer, never the suspect node.** (The report cites our own
  `cron_verdict_stale`/`channel_feed_dark` philosophy back at us: "a verdict that
  nothing actively reads is no verdict.")
- **Heartbeat + period + grace** (healthchecks.io model): declare Down after ~3 missed
  beats. **Re-alert while down** (`repeat_interval` ≈ 1–2h, escalating priority) — a
  33h outage should produce ~16–33 nudges, not one.
- **Witnessed delivery is "one `if` statement":** check the ntfy POST returns HTTP 2xx
  + a message `id`; treat non-2xx/missing-id as a failed page → retry/escalate;
  optionally poll the topic back (`poll=1&since=<id>`) to confirm retrievable. Honest
  limit: ntfy has **no human-ack/read-receipt** — a phone-call tier (ntfy.sh `X-Call`,
  hosted) is the closest cheap proxy for "witnessed."
- **Age-off fix:** self-host ntfy with `cache-file`/`database-url` + longer
  `cache-duration`; the recurring re-alert independently defeats the TTL bug (always a
  fresh message present). Do both.
- **Watcher-of-the-watcher:** the central monitor is a SPOF; it must emit an
  always-firing heartbeat to an *independent* watcher (Alertmanager `Watchdog` =
  `vector(1)` pattern) **and** surface state on a habitually-viewed panel (`/fleet`).

### 1C — ESP32 out-of-band watcher  (`leg3_esp32_watcher.md`)

- **The claw already has the three independences** (power / compute+clock / comms) that
  *define* a real out-of-band witness — the same reason watchdog HATs (Witty Pi 5)
  moved their logic off the Pi.
- **Detection = fuse two channels:** Pattern 1 host-heartbeat (highest specificity,
  LAN-immune) + Pattern 2 active LAN probe (service liveness + corroboration).
- **The discrimination our remote probe can't do:** on `.32`'s own subnet, **ARP (L2)
  vs ICMP/TCP** tells *host-froze* (ARP replies, TCP/ICMP fail — NIC/kernel alive,
  userspace wedged) from *path-down* (no ARP). This is genuinely new diagnostic
  information that localizes a fault to the box vs the AREDN WAN.
- **Report HOST_FROZEN over NATS→moc2 (independent of dead `.32`) + LoRa.** Claw stays a
  dumb sensor/actuator; mini on moc2 is the judge (matches current architecture).
- **Optional auto power-cycle** (later, gated, needs hardware): relay/MOSFET on `.32`'s
  USB-C (power-cycle clears brownout latch; GLOBAL_EN→GND softer; RUN→GND hard reset).
  **Boot-loop-safe state machine:** debounce → confirm → MAX_RESETS/window → exponential
  backoff → boot-grace → confirm-recovery → LOCKOUT, + planned-reboot suppression +
  manual disable + **alert-only default**. Security: a reset-capable MCU needs
  authenticated/non-replayable commands, local-state agreement before acting, LoRa
  report-only, no WAN exposure.

---

## 2. The layered fix (defense in depth) — each leg has a TUI/MF018 expression

| Leg | What | Stops which failure | In-app (MF018) expression |
|----|------|---------------------|---------------------------|
| **A — Self-recovery** | Pi internal HW watchdog (`RuntimeWatchdogSec=14`) | Daily *manual* reset → auto ~15s reboot (kernel/soft-lockup class) | "Self-recovery" toggle in the forensics wizard; detect/arm/verify/revert |
| **B — Forensics** | persistent journald + `get_throttled`/PSI canary + panic sysctls + ramoops *if* pstore present + UART on worst box | Lost root-cause evidence each reset | The **"Arm crash forensics" wizard** (operator's directive) — fleet-wide (vendor volatile default is on every rpi); detect → arm → verify → revert |
| **C — Out-of-band witness** | dude-claw watches `.32` from its subnet; froze-vs-path-down; reports via NATS+LoRa; *optional* gated auto-reset | "Box dead but our only watcher is the dead box / a blind remote probe" | Claw watcher → mini signal on moc2 → `/fleet` panel + warm brief |
| **D — Never-silent alerting** | fleet-liveness as dead-man's-switch: witnessed delivery + re-alert cadence + **surface on mini + `/fleet`** | The silence (fire-once / unwitnessed / off-spine) | Box-reachability becomes a watchdog probe + mini signal class → warm brief & `/fleet`, not a logfile |

---

## 3. Proposed sequence (operator's "analyze → build in-app → durable", reliability-first)

- **Phase 0 — Stop the bleeding (operational, hours).** On `.32` now: re-arm persistent
  journald + the canary cron (capture the next wedge) **and arm the internal HW
  watchdog** (auto-reboot replaces manual reset). ⚠️ Tension with "keep it in the TUI":
  this is by-hand SSH, the thing we're trying to retire. *But* the watchdog likely ends
  the daily manual resets immediately, which **de-risks** doing the durable build at a
  considered pace. Decision for operator below.
- **Phase 1 — Build the in-app forensics+self-recovery wizard (MF018).** Leg A+B made
  durable & fleet-wide: detect vendor-volatile default → arm journald + canary +
  sysctls + ramoops(if `CONFIG_PSTORE`) + watchdog toggle → verify (`journalctl
  --header`, `/dev/watchdog`) → one-click revert. Mirrors RNS Repair / `offer_service_fix`.
- **Phase 2 — Build the never-silent alerting spine (Leg D).** Cheap, high-value:
  witnessed ntfy delivery (the "one `if`"), re-alert cadence, and box-reachability as a
  mini signal + watchdog probe surfaced on `/fleet`/warm brief. Could front-run Phase 1
  — it's the direct answer to "where was mini/the watchdog."
- **Phase 3 — Build the dude-claw out-of-band witness (Leg C).** Alert-only first
  (froze-vs-path-down → NATS→mini + LoRa). Gated auto-power-cycle is a later step that
  needs hardware + the boot-loop-safe state machine + operator go.
- **Phase 4 — Analyze → Fix root cause.** Once forensics are armed and the next wedge is
  captured (or the mf.5 soak ends 06-23 → verdict 06-24: do wedges stop?), run the
  Section-1A decision tree, identify the cause (brownout/SD/soft-lockup/USB), and fix
  the actual defect — not just paper over it.

---

## 4. Open decisions for the operator

1. **Phase 0 tension:** arm `.32` now by hand (watchdog likely ends the daily manual
   resets + journald/canary captures the next wedge) — accepting one more by-hand pass
   before the TUI version exists — **vs** build the in-app wizard first (purely durable,
   but `.32` stays exposed/unarmed meanwhile, ~daily). The HW watchdog is new info since
   we last discussed this: it can stop the pain *today*.
2. **Claw auto-reset ambition:** alert-only witness (safe, no hardware) — **vs**
   eventually wire a gated auto-power-cycle (needs a relay/MOSFET on `.32`'s supply +
   the safety state machine). Alert-only first regardless; this is about whether to plan
   toward actuation.
3. **Hardware reality check (cheapest test of the #1 suspect):** is `.32` on a known-good
   official PSU + short thick cable, with non-essential USB stripped? Brownout is the top
   suspect and the coupled cause of SD corruption — a PSU/cable swap is the single
   cheapest diagnostic and may be the whole fix.

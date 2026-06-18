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

---

## Leg C kickoff (NEXT CLEAN SESSION — dude-claw out-of-band witness)

Status at handoff (2026-06-17): Legs A/B-Phase-0 done (`.32` desktop fix + forensics
armed), **Leg D SHIPPED + live-drilled** (`ee759b16`+`93757ef6`). `.32` ROOT CAUSE was
mundane (Pi Zero W desktop overload) — so Leg C is now **defense-in-depth**, not the
headline. But it's still worth it: the internal HW watchdog can't catch the
systemd-still-pets class, and an out-of-band witness localizes faults the remote probe
can't (the AREDN-WAN/DNAT ambiguity).

**Precondition VERIFIED 2026-06-17 ~12:00 HST:** moc2 `nats-server` active; the claw is
answering NATS polls (`~/claw_ble_soak.log` fresh at 12:00); moc2 @ `93757ef6`. Claw +
brain are healthy → Leg C is unblocked.

**THE DESIGN CRUX (settle this FIRST):** the same-subnet *froze-vs-path-down*
discrimination — the whole reason the claw beats the remote probe — requires the **claw
itself** to probe `.32`'s real iface `10.120.250.195` (ARP/TCP) from inside the DudeNET
subnet. moc2/VolcanoAI can only reach `.32` via `192.168.86.32` (the AREDN WAN+DNAT), which
carries the SAME ambiguity. So the discriminating version needs a **lean claw firmware
probe tool** (e.g. `host_probe`: TCP-connect + ARP-presence to a target on the local
subnet) → a flash over moc1's USB. That firmware step is the heavy, FORK-disciplined work
that earns the clean session.
- **Lighter slice-1 (no firmware):** the claw is already a NATS sensor + an independent
  *witness by presence*. A first alert-only version can report "claw can/can't see `.32`"
  using whatever reach the claw already has, OR simply surface the claw's own liveness as
  the out-of-band heartbeat. Decide slice-1 (no-flash, alert-only) vs the full discriminating
  version (firmware probe tool) at session start.

**Integration shape (from `bot_32_research/leg3_esp32_watcher.md`):** mini-dudeai claw
**sensor** (`sources/nats_sensor.py` pattern) polls the probe via the claw's `tool_exec`
→ source-side threshold → Condition → mini rule on moc2 → report HOST_FROZEN over NATS
**+ LoRa** (independent of the dead `.32`) → surface on `/fleet`. **Alert-only first.**
Gated auto-power-cycle (relay/MOSFET on `.32`'s USB-C + the boot-loop-safe state machine:
debounce→confirm→MAX_RESETS→backoff→boot-grace→confirm-recovery→LOCKOUT) is a LATER,
hardware + operator-go step — do NOT build actuation in slice-1.

**Cautions (load-bearing):**
- ⚠️ **FORK.md INVARIANT — never `git commit` while checked out on the `dudeclaw` branch.**
  Deploy-branch = upstream main + merge `pr/*` + ONE residue commit, REBUILT not hand-edited
  (`.claude/plans/dudeclaw_upstream_prs.md`). Watch the open PRs when touching the claw.
- ⚠️ Memory budget: claw runs the V4 NATS-edge lean profile (~30 kB free heap); a new tool
  must be lean (the on-device tool-AGENT is disabled by design, but tools the claw *uses*
  are compiled in). Flash = app-only `esptool write-flash 0x10000` over moc1 USB (pipx
  esptool; apt's is dfsg-stripped); claw rejoins unaided; discover-verify the `+dudeclaw.N`.
- ⚠️ Topology: claw `10.120.250.199`; `.32` real iface `10.120.250.195` (`.32`'s
  `192.168.86.32` is the AREDN node WAN + :22 DNAT); claw portal reachable only from inside
  the subnet → use `.32` as the foothold; brain = moc2 (`meshforge-mini-dudeai-claw` user
  unit, env `~/.config/meshforge/mini_dudeai_claw.env`).
- New signal class (`host_frozen` / reuse `fleet_box_unreachable`?) → closed enum +
  BOTH seeds + the coverage/reachability/enum-doc gates (same drill as Leg D Piece 2).

**Cold-start reading:** memory `project_dudeclaw_phase_a_2026_06_11` (cold-start facts),
`.claude/plans/rf_claw_arc.md` §Phase 3, `.claude/plans/dudeclaw_heltec_v4_bringup.md`
(remote-flash recipe), `.claude/plans/dudeclaw_upstream_prs.md` (PR state machine).

**Verification gate:** alert-only first; drill it like Leg D Piece 2 (inject a synthetic
HOST_FROZEN via the claw sensor, confirm it surfaces in mini's brief + `/fleet`, NO
auto-reset, then revert). Field-prove the discrimination on a real `.32` reboot before
trusting it.

---

## Leg C — slice progress (2026-06-17, session 2)

**Decision (operator-driven "make it real / can it be more reliable"): FULL firmware
host_probe, actuation-ready but alert-only this session (2+2).** Slice-1 (claw-liveness-
by-presence) rejected as barely-informational. Auto-reset stays designed-for (RUN→GND
reset, NOT power-cut — `.32` is a Pi Zero W, micro-USB; hard power-cut risks the very SD
corruption that's a top suspect) but ships nothing actuating.

**VERIFIED preconditions (this session):**
- L2 premise: from `.32` (`wh6gxzTRDEV`), claw `10.120.250.199` is `dev wlan0 ... REACHABLE`,
  direct (no gateway hop) on the `10.120.250.192/28` WiFi segment. ARP/TCP discrimination is
  real and targets `.32`'s actual swap-thrash class (kernel/NIC alive, userspace wedged).
- No existing net-probe tool in WireClaw's 25-tool sheet (`_ion.discover --many`): HAL is
  pin-level (gpio), not socket — so the active probe genuinely requires a firmware tool.
- Toolchain: pio at `~/.local/bin/pio`; `esp32-s3-heltec-v4` env exists ONLY on `dudeclaw`
  (added by a PR), not `main`.

**host_probe (lwIP non-blocking connect + SO_ERROR + banner read):** honest-by-design — a
bare SYN-ACK is NOT read as healthy (kernel completes the handshake while a wedged sshd
never accept()s). Two-port: app port (banner liveness) + a normally-closed port (service-
independent kernel-alive RST check). Returns
`host_probe <ip>: ip_alive=N appP=open|refused|timeout banner=NB kstack=N rtt_ms=N`.
Verdict map for the brain: banner>0 → OK; app open + banner=0 + kstack=1 → HOST_FROZEN
(the money case); app refused + kstack=1 → host-up/ssh-down (NOT frozen); ip_alive=0 →
UNREACHABLE (path/wifi/SoC down).

**VERIFIED build state (local, unpushed):**
- `pr/host-probe` off `main` @ `3997578` — feature SSOT, +116 lines tools.cpp. Built green
  `esp32-s3` (BUILD_EXIT=0).
- `dc-hostprobe-candidate` off `dudeclaw` @ `82a265c` — merged pr/host-probe (only tools.cpp
  schema conflicted → resolved as union of all 6 fork tools + host_probe; handler+dispatch
  auto-merged), residue version.h → `0.4.0+dudeclaw.13`. Built green `esp32-s3-heltec-v4`
  (BUILD_EXIT=0): RAM 52.6%, Flash 61.0%. Flashable `firmware.bin` ready.

**GATED — remaining (operator):**
1. FLASH over moc1 USB (touches live field box). Recipe in `dudeclaw_heltec_v4_bringup.md`
   §Remote-flash. Post-flash VERIFY: `_ion.discover` shows `+dudeclaw.13`, free-heap-after
   ≈30 kB, smoke-test `host_probe` {host:10.120.250.195}.
2. Advance the `dudeclaw` ref per FORK.md (operator-gated force-update). Candidate firmware
   content is method-independent; cleanest = ff `dudeclaw`→`dc-hostprobe-candidate` (host_probe
   SSOT stays on `pr/host-probe`). Push pr/host-probe + new ref to moc1 bare backup.
3. THEN brain pipeline on moc2 (post-flash, against proven tool): `nats_sensor` polls
   host_probe(10.120.250.195) → condition → rule → NEW signal class (closed enum + BOTH seeds
   + coverage/enum-doc gates, Leg D Piece-2 drill) → `/fleet` + warm brief (NATS primary, LoRa
   backup). Drill synthetic HOST_FROZEN, NO auto-reset, revert. Field-prove on a real `.32` event.

⚠️ FORK PRs unchanged (6 open); watch them per the state machine when next touching the claw.

### ✅ FLASHED + dudeclaw advanced (2026-06-17, session 2 cont.)

host_probe is LIVE on the claw. Shipped `+dudeclaw.14` (not .13 — a clean ship:
post-.13 smoke-test found host_probe callable but ABSENT from `_ion.discover`,
because the tools list at `main.cpp:~1000` is a HARDCODED duplicate of TOOLS_JSON
[honest-failure-modes #5, pre-existing pattern]; .14 registers host_probe in BOTH
the dispatch and the discover list, amended into the one feature commit).

- `pr/host-probe` @ `0940e86` (feature SSOT, both files) · `dudeclaw` ff'd → `a73d574`
  (`+dudeclaw.14`). Built green esp32-s3 + esp32-s3-heltec-v4 (RAM 52.6%/Flash 61.0%).
- Flash: app-only `write-flash 0x10000` via moc1 pipx esptool 5.3.0, no erase (config
  preserved); FLASH_EXIT=0, hash verified; claw rejoined NATS in ~4s.
- VERIFIED live: version `+dudeclaw.14`, free_heap 30872 (no regression vs .12 30132),
  host_probe in discover (count=1), smoke `host_probe 10.120.250.195: ip_alive=1
  app22=open banner=43B kstack=0 rtt_ms=9` (reads .32 HEALTHY — sshd banner present).
- ⚠️ DESIGN NOTE for the brain rule: `.32` firewall-DROPS the closed port (:9) → no RST
  → `kstack=0` even when healthy. So the HOST_FROZEN verdict must key on
  `app_open=1 AND banner=0` (the app-port SYN-ACK is itself the kernel-alive proof);
  `kstack` is corroborating-only, not required. (Or set closed_port to one .32 RSTs.)

**STILL LOCAL/UNPUSHED** — pr/host-probe + dudeclaw.14 live only in VolcanoAI's repo
(claw runs .14, but the SOURCE is single-copy). Push to moc1 bare backup for durability.

**NEXT = brain pipeline on moc2** (alert-only): `nats_sensor` polls host_probe(10.120.250.195)
on a cadence → source threshold maps the line to OK/HOST_FROZEN/UNREACHABLE → Condition →
mini rule → NEW signal class (closed enum + BOTH role seeds + coverage/enum-doc gates,
Leg D Piece-2 drill) → `/fleet` + warm brief (NATS primary, LoRa backup). Drill a synthetic
HOST_FROZEN, confirm it surfaces, NO auto-reset, revert. Field-prove on a real .32 wedge.

---

## ✅ Leg C COMPLETE — brain pipeline SHIPPED + live-drilled (2026-06-17, session 2)

The dude-claw out-of-band witness is fully wired into the spine the operator
watches. MeshForge HEAD `97b9dfcd` (CI PASS run 27727766806, fleet 5/5 converged,
lint exit 0). The whole chain is live:

  collector cron (moc2) → claw host_probe(.32) over NATS → verdict file
  → probe_host_frozen → watchdog.json → mini host_frozen_any rule
  → mini_dudeai_state.json → /fleet + warm brief   (alert-only, propose_escalation)

**Shipped (MeshForge repo, `80760cad` + `97b9dfcd`):**
- signal class `host_frozen` (closed enum + BOTH role seeds + coverage/wiring gates).
- `probe_host_frozen` + `_read_host_probe_verdict` (watchdog_probes_drift.py): reads
  `~/host_probe_state.json`, INERT off the brain box, 2-tick debounce, never-raises.
  HOST_FROZEN/UNREACHABLE → wedge; sustained UNKNOWN (witness itself blind) →
  degraded (lost visibility != healthy, honest_failure_modes #2).
- `scripts/host_probe_check.py`: the OUT-OF-BAND collector (NATS call lives here, NOT
  in the sandboxed watchdog — mirrors fleet_offline_check.sh). Self-gates on a
  moc2-only config (operator IPs in `~/.config/meshforge/host_probe_targets.json`,
  not repo source — MF014). Fixed `97b9dfcd`: request() returns a dict, not str.
- Tests: `TestHostFrozen` (10) + the 3 signal-class gates. 376 green, lint 0.

**moc2 activation (the claw's brain box):** config written (target bot32=10.120.250.195,
app_port 22, closed_port 9), cron `*/3` wired with cron_verdict.sh (first verdict
seeded `host_probe_check OK`), watchdog restarted, seed promoted (`host_frozen_any`).
Baseline: bot32=OK (claw reads .32 healthy, banner=43B).

**LIVE DRILL PASSED (moc2):** injected synthetic HOST_FROZEN → probe fired `wedge`
in ~60s (2 ticks) → watchdog.json `class=host_frozen subject=bot32 severity=wedge` →
mini consumed via `host_frozen_any` in ~15s → healed (real collector → OK) → signal
CLEARED in ~15s (edge-down). NO auto-reset (alert-only confirmed). Cron restored.

**Fleet rolled:** all 6 boxes pulled + `host_frozen_any` promoted + watchdog restarted
(probe INERT off moc2). honest_status --quick: CI/fleet/lint/conf_rate PASS; watchdog
WARN = pre-existing dep_version_drift/dep_install_fragmented (deferred 06-24) +
claw_ble_soak_judge(never) — NO host_frozen, nothing new wedged.

**Remaining (NOT this session):**
- ⏳ backup/dudeclaw force-update to .14 + FORK.md recovery into the live lineage
  (operator-gated; pr/host-probe IS on the backup, so the source is safe).
- Leg C field-proof: the discrimination on a REAL .32 wedge (the next actual freeze).
- Phase 5 (later, gated): RUN→GND auto-reset hardware + boot-loop state machine.

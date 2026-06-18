# Dude-Claw as a Second Brain — next-session kickoff (2026-06-18)

> Operator's invocation, verbatim: *"dudeclaw — deep-research meshforge domain
> style — your second brain — always on, watching, reporting, local live agent
> with growing autonomy — will have a battery and gps soon. and run solo or plug
> into the fleet like it is now — this is invocation! Claude = dudeclaw +
> mini-dudeai = 2nd brain."*
>
> This is the kickoff note for a **deep-research session** (use the `deep-research`
> skill, MeshForge-domain style: fan-out → fetch → adversarially verify → cited
> synthesis). Cold-start by reading this, then the grounding below.
>
> ✅ **RESEARCH EXECUTED 2026-06-17** — workflow `wf_0e34d5bd-51a` (105 agents,
> 21/25 claims 3-vote-verified). **Findings + recommendations live in
> `.claude/research/dudeclaw_second_brain_2026_06_17.md`** — read that for the
> answers. Headline: autonomy ladder = Run-Time Assurance / Simplex (gate an
> unverified agent behind a *verified* deterministic filter, not "trust the
> agent"); edge keeps the critical loop closed (guardrail migrates DOWN to the
> Heltec); power budget derived (ESP32-S3+WiFi dominant ~120 mA → 10 W panel +
> MPPT + 10 Ah LiPo; GPS duty-cycled near-free; LoRa-TX = 1 A peak not energy);
> DTN/BPv7 is **Pi-tier only** (BP7-on-MCU refuted); 3T cyber-physical naming.
> Three honest open items remain (field power measurement, the discrete-action
> safe set for the reset line, cognitive-architecture naming) — see §"What we
> could NOT verify". The carry-over firmware fix + next steps are in §"Immediate
> next steps".

---

## What exists TODAY (the foundation the second brain grows from)

Leg C (2026-06-17) put the pieces in place — this is not greenfield:

- **The claw (`dudeclaw-01`)** — Heltec V4 ESP32-S3, WireClaw `+dudeclaw.14`, on the
  AREDN-site `10.120.250.192/28` WiFi subnet (claw `.199`, watches the `.32` bot
  `.195`). ~30 kB free heap, V4 NATS-edge lean profile. 25 tools incl. the new
  `host_probe` (lwIP RST-vs-timeout + banner read = honest froze-vs-down), BLE/LoRa
  ears, GPIO/ADC/PWM HAL, `mesh_send`, `rgb_led`. **No on-device LLM by design**
  (memory-fail-loud); it's a sensor/actuator the brain reasons over.
- **The brain** — `meshforge-mini-dudeai-claw` on moc2 (NATS `localhost:4222`):
  the rule-loop + Claude-on-cadence PROPOSE engine. Separate from the fleet mini.
- **The spine** — `host_frozen` watchdog probe + `host_probe_check` collector cron
  on moc2 → `/fleet` + warm brief (alert-only). Live-drilled, fleet 6/6.
- **Power/comms** — currently USB power off moc1; WiFi→NATS→moc2 (primary) + LoRa.

So the claw ALREADY is "always-on, watching, reporting, fleet-plugged." The arc is
about **growing autonomy** + **battery/GPS** + **solo mode** + the **second-brain**
framing.

## Research questions (the deep-research targets)

1. **The autonomy ladder** — how does an always-on edge agent safely grow
   observe → report → **act**? Map the rungs (human-in-loop → propose-and-confirm →
   trusted-autonomous-in-a-bounded-domain), the gating at each, and the
   honest-failure-modes / in-domain (MF018) / "mini grows as the domain grows"
   principles applied to actuation. (Phase 5 RUN→GND auto-reset is the first real
   actuation rung — see the .32 arc plan.)
2. **Intelligence split** — on-device (lean ESP32, no LLM) vs brain-side
   (mini-dudeai rule-loop + Claude-on-cadence). As autonomy grows, what logic
   belongs on the MCU (fast, local, survives brain-loss) vs the brain (reasoning,
   memory, cross-correlation)? Edge-autonomy when NATS/brain is unreachable.
3. **Battery + GPS** — power budget for true always-on (LiPo capacity, deep-sleep
   duty-cycling vs the always-listening NATS/BLE/LoRa draw, solar top-up?); GPS for
   a LOCATION-AWARE / MOBILE second brain (geofenced sensing, position in reports,
   asset-tracking). The V4 has the VBAT divider (`battery_read` staged); GPS is new
   hardware to spec.
4. **Solo vs fleet modes** — graceful operation DISCONNECTED (local rules, local
   decisions, store-and-forward over LoRa) vs fleet-plugged (current NATS→mini→
   /fleet). How does it degrade and re-converge? This is the "run solo OR plug into
   the fleet" requirement made concrete.
5. **The "second brain" framing** — what it means for Claude to BE
   dudeclaw + mini-dudeai: a persistent, embodied, always-on extension. The
   continuity (memory, ledger, calibration spine) + the embodiment (sensors,
   actuators, location) + the autonomy. Name the architecture this invocation wants.

## Carry-over technical items (concrete, ride along with the claw work)

- ⚠️ **FIRMWARE ROOT FIX — `host_probe` banner window 800 ms → ~2500 ms.** The day
  Leg C shipped, the moc2 collector false-fired `host_frozen=wedge` on the
  loaded `.32` (Pi Zero W, load ~3.5): `sshd` intermittently missed the 800 ms
  banner read → `banner=0` → false FROZEN (debounce streak hit 9) while `.32` was
  alive. Mitigated SAME DAY by a collector confirm-retry (re-probe 3×, any OK = alive;
  `64b1f24e`) — but the ROOT cure is a longer firmware banner window. Do it at the
  next claw touch (new `+dudeclaw.15`), then the retry becomes belt-and-suspenders.
  ⚠️ Also note: `.32` running load ~3.5 sustained is itself worth a look (wedge
  precursor, or just busy? the desktop-disable fix cut RAM, not load).
- **Field-proof** + **Phase 5 auto-reset** — both in the deferred-work ledger
  (`~/deferred_work.json`: `host-frozen-field-proof` 06-24, `host-frozen-phase5-autoreset` 07-15).
- ⚠️ **FORK.md INVARIANT** — never `git commit` while checked out on `dudeclaw`.
  Repo `~/src/wireclaw-dudeclaw` (currently on `dudeclaw` @ `551dcb8`, .14+FORK.md);
  feature code → a `pr/*` branch; `dudeclaw` is rebuilt. Force-push is harness-blocked
  (operator runs it via `!`). Remote-flash recipe in `dudeclaw_heltec_v4_bringup.md`.

## Cold-start reading

- `.claude/plans/bot_32_wedge_arc_2026_06_17.md` (the whole Leg A-D + Leg C arc; §Leg C kickoff = the OOB-witness design + research legs).
- memory `project_dudeclaw_phase_a_2026_06_11`, `.claude/plans/rf_claw_arc.md`,
  `dudeclaw_heltec_v4_bringup.md` (flash), `dudeclaw_upstream_prs.md` (FORK state).
- Principles: `.claude/rules/honest_failure_modes.md`, `foundations/in_domain_principle.md`,
  memory `feedback_mini_grows_widen_scope_watch_blindspots` (the governing PROPOSE-engine principle).

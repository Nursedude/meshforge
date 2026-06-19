# Dude-Claw Host-Reset SAFE SET — discrete-action design (2026-06-19)

> **What this is.** The first-principles answer to **Open Question #2** of the
> second-brain research (`dudeclaw_second_brain_2026_06_17.md`): *"the
> correctly-defined safe set + verified backup behavior for a GPIO reset line
> (rate-limit, reversibility window, confirm-token, kill-switch, boot-loop abort)
> is unspecified in the literature and needs first-principles design before Phase
> 5 actuation."* This is **paper only** — the gate between autonomy **rung 2
> (propose-and-confirm)** and **rung 3 (supervised actuation)**. No actuation
> firmware exists or should be written until the gates in §7 are met and the
> operator ratifies the numbers in §3.
>
> **Why now (§5 of the self-audit arc).** The 2026-06-19 `.32` episode is the
> canonical adversarial test for this design: a *healthy-but-busy* Pi Zero W
> (swap-thrash from a stray `meshforge-maps`) repeatedly read **HOST_FROZEN**
> because sshd missed the firmware's 800 ms banner window. A naive filter would
> have **hardware-reset a box that just needed `systemctl disable meshforge-maps`.**
> The safe set must survive exactly that. See §6.

---

## 1. The actuation primitive (what we are gating)

- **Physical action.** The claw (Heltec V4, ESP32-S3 — the *reactive/edge* layer)
  drives a spare GPIO → **opto-isolator** → the target Pi's **RUN** reset pad,
  pulled to **GND for a single ~150 ms momentary pulse** = a hardware reset
  (reboot). On a Pi Zero W (the `.32` board) RUN is a solder pad, not a header.
  **Opto-isolation is mandatory** — the claw and `.32` are separate boxes on
  separate power; never a direct GPIO-to-RUN wire. The drive is **open-drain /
  fail-released**: GPIO asserted = RUN pulled low; GPIO unasserted *or floating
  or claw unpowered* = RUN released (no reset). The hardware's resting state is
  "never reset."
- **Why edge-driven and out-of-band.** This exists to recover the *one* failure
  the box cannot self-recover from: the swap-thrash freeze where systemd PID1
  keeps petting the box's own 60 s HW watchdog (so no auto-reset) while sshd
  can't be scheduled. An out-of-band witness on a *separate* board is the only
  thing that can break that — the whole rationale of Leg C.
- **Reversibility.** A reboot is **not undoable**, but it **is bounded and
  recoverable** (the box returns). So "reversibility window" here means: the
  ability to *abort before the pulse fires* and to *observe the outcome and
  refuse to repeat* — not to undo. Treat every fire as irreversible and require
  the §3 guards to ALL hold first.

> **Hard design constraint (from reading the firmware).** The claw already has a
> generic rule→`actuator_set` engine (`ACT_ACTUATOR`, `DEV_ACTUATOR_PWM`,
> on/off rule actions). The host-reset line **MUST NOT** be exposed as an
> ordinary actuator that a rule or a `tool_exec`/`actuator.set` NATS call can
> drive. If it were, the deliberative agent (or a bad rule, or a spoofed NATS
> message) could fire a reboot directly — bypassing the whole filter. The reset
> is a **privileged, separate code path** with no rule-engine and no raw-set
> entry point; the only way to fire it is through the §2 filter with a valid §3
> confirm-token.

---

## 2. The Simplex / RTA structure (mapped to a discrete action)

The literature's load-bearing pattern (RTA / Simplex, `3-0` in the research):
an **unverified advanced controller** is filtered by a **verified backup
controller**, switched by a **deterministic monitor**, keeping the system inside
a **forward-invariant safe set**.

| RTA element | dude-claw instance |
|---|---|
| Unverified advanced controller | Claude-on-cadence + mini-dudeai **PROPOSE** (issues a reset *proposal*) |
| Verified baseline/backup controller | **The NULL action — do nothing.** |
| Decision module (monitor) | The **edge filter** on the claw: §3 guards G1–G7, all deterministic, all default-refuse |
| Safe set | The state region in §3 where a reset is *permitted* (AND of all guards) |

**The key simplification for a destructive discrete action:** the verified
backup controller is *trivially* the **NULL action**. For a continuous plant you
must prove the backup keeps you inside the safe set; here, **doing nothing is
always safe** — the box simply stays in its current (frozen) state, which is the
pre-existing condition, not a new harm. That makes the backup verified by
construction. **All the risk is in the monitor wrongly *permitting* a fire**, so
that is where 100% of the design effort goes (§3, §6). The monitor's default,
on *any* doubt, is **refuse → NULL action.**

**Switch at the RECOVERABLE set, not the unsafe boundary** (the research's
skeptical-check qualification): never fire on a single missed banner. Fire only
deep inside "confirmed sustained freeze" (G1) — see §6 for why the `.32` episode
makes this non-negotiable.

---

## 3. The safe set — guards (a reset is permitted ONLY if ALL hold)

Set membership = **logical AND** of G1…G7. Any guard `UNKNOWN`/`error`/`blind`
⇒ **refuse** (never "permit"). Every constant has a single authoritative
definition on the **edge** (§5-pt5).

- **G1 — Confirmed-frozen, not transient.** ≥ **K** consecutive `HOST_FROZEN`
  verdicts from `host_probe`, where *each* verdict is itself the existing
  3-attempt-agreed verdict (so K×3 individual probes all returned `banner=0`),
  spanning ≥ **T_min** wall-time. *Proposed: K ≥ 3, T_min ≥ 15 min.* This is the
  recoverable-set entry — sustained, not a blip.
- **G2 — Recoverable precondition (right failure class).** The verdict must be
  `HOST_FROZEN` specifically: `ip_alive=1` (kernel/NIC up) **and** app port
  `open` **and** `banner=0` (userspace wedged). **Never** fire on `UNREACHABLE`
  (box may be powered off / cabled out — a reset won't help and signals the
  model is wrong) and **never** on `UNKNOWN` (collector blind). Reset only the
  class a reset actually fixes.
- **G3 — Rate budget (forward-invariance).** ≤ **1** reset per **N** hours
  **AND** ≤ **M** resets per rolling 24 h. *Proposed: N ≥ 6, M ≤ 2.* A
  token-bucket on the edge, monotonic-anchored (§5-pt6).
- **G4 — Boot-loop abort.** After a fire, a mandatory **quiet window W** (≥ one
  full boot + settle; *proposed W ≥ 8 min*) during which **no** new reset is
  permitted regardless of verdict. If the box does not return to `OK` within
  **W_recover** (≥ 2 boots' worth; *proposed ≥ 20 min*), **latch to
  MANUAL-ONLY** (auto-reset disabled) and escalate — repeated resets that don't
  fix it mean the model is wrong; do not keep rebooting.
- **G5 — Hardware kill-switch.** A physical jumper on the claw that
  **open-circuits the reset drive**. The claw also reads a kill-switch sense
  GPIO each cycle and refuses if it reads disabled; **fail-safe default =
  DISABLED** if the pin reads floating/unknown. Two layers: the firmware can't
  fire if sense says off, and even a firmware bug can't fire if the jumper is
  physically open.
- **G6 — Fresh single-use confirm-token (the rung 2→3 boundary).** A reset
  requires a **fresh, single-use, expiring** token issued by the Pi-executive
  (which itself carries human ratification at rung 3). The token binds: exact
  **target id** + **nonce** + **issued-at** + **expiry ≤ a few min**. The claw
  verifies freshness and single-use; an **expired or replayed** token is
  refused. This keeps the agent at *propose*, the human/executive at *authorize*,
  and the edge at *decide-to-fire-now* — the per-function autonomy split.
- **G7 — Claw self-health.** The claw actuates only if its own state is sound:
  NATS reachable (or an explicitly sanctioned solo-autonomy mode), not mid-OTA,
  heap above floor, no active self-fault. A confused/degraded claw fails-safe to
  no-actuation.

---

## 4. The autonomy ladder — where each rung sits

| Rung | What fires | Gate | Status |
|---|---|---|---|
| 2 Propose-and-confirm | a *proposal* only; **no GPIO** | human ratify (existing propose→ratify→apply) | this design is its prerequisite artifact |
| 3 Supervised actuation | the GPIO pulse, via §3 filter | valid G6 token from a **human-ratified** proposal + G1–G5,G7, kill-switch armed | **design-gated; do not build until §7** |
| 4 Bounded autonomy | same filter | G6 token issued **autonomously** within a hard envelope | **only after** rung 3 soaks with **zero false-fires** over a long window |

The deterministic guardrail (G1–G7) lives **on the edge** and **migrates down
with the capability** (research RQ2): reasoning/memory/proposal stay on the Pi;
the reflex that refuses an unsafe reboot stays on the claw, so the critical
safety loop survives a wedged brain or dropped NATS.

---

## 5. Honest-failure-modes pass (the load-bearing review)

> *"A wrong safe-set definition silently breaks the guarantee"* (research). So
> the design is walked against all 9 points of `.claude/rules/honest_failure_modes.md`.

1. **Degraded ≠ valid value.** Every guard is **tri-state {permit / refuse /
   blind→refuse}**. No guard's error path may overlap "permit." Can't read the
   kill-switch → DISABLED. Rate-budget state file unreadable → refuse. The whole
   filter is **default-refuse**; "permit" requires positive evidence from every
   guard.
2. **Absence ≠ evidence.** A missing/`UNKNOWN` `host_probe` verdict (collector
   blind, NATS down, claw can't reach target) is **neither frozen nor ok** → no
   reset. Lost visibility **never** authorizes a destructive action (G2).
3. **Validators reject the impossible.** A token with null target / past expiry
   / malformed nonce / unknown target id → **reject loud**, witness recorded.
4. **Reader/writer wire together or refuse.** The token **issuer** (Pi) and
   **verifier** (claw) share ONE schema + ONE clock-skew tolerance. No token, or
   a half-wired issuer → claw **default-refuses** (no token = no fire). The
   detector cannot be "confidently wrong" because its permissive state requires a
   valid artifact from its pair.
5. **Two consumers, ONE constant.** K, N, M, W, W_recover, T_min, expiry, the
   target **allowlist** — defined **once** in a single config block. The **edge
   values are authoritative** (they enforce); the Pi's copy is advisory/display
   and is **test-pinned** to the edge values so they can't drift (the 24000-vs-
   24576 trap).
6. **Wall-clock is forgeable** (claw is RTC-less too). Rate budget (G3) and
   boot-loop windows (G4) use the claw's **monotonic `millis()`**, never
   wall-clock. A persisted last-reset marker must survive a **claw reboot
   fail-safe**: on claw boot, **assume a reset just happened** (start the rate
   window full) until proven otherwise — a claw reboot must NEVER reset the
   budget to "fire allowed." Clock-backward / absurd delta → refuse.
7. **Closed enum, closed consumers.** Permitted targets are a **closed allowlist**
   on the edge; an unknown/typo'd target → refuse. A **test fails** if a target
   is added without a safe-set entry (coverage gate, not memory).
8. **Single writer.** Exactly ONE actuation path; a single-owner lock on the
   reset state so two proposals can't interleave a double-pulse.
9. **Every swallow leaves a witness.** **Every refusal AND every fire** writes an
   audit record (claw flash ring + echoed to the brain): `refused: G<x>=<reason>`,
   `fired @ <mono_t> token=<nonce>`, `boot-loop-abort latched`. A **silent
   refusal is as dangerous as a silent fire** — the operator must always see why
   the filter did or did not act.

---

## 6. The `.32` episode IS the adversarial test case

On 2026-06-19 a **healthy-but-busy** `.32` (Pi Zero W, swap-thrash from a stray
`meshforge-maps`, RSS ~159 MB on a 426 MB board) repeatedly read **HOST_FROZEN**:
sshd completed the TCP handshake but could not be scheduled to emit its banner
within the firmware's **800 ms** window → `banner=0`. The fix was
`systemctl disable --now meshforge-maps` — **a reboot would have been the wrong
action** (it clears nothing; the stray service is enabled-on-boot and returns).

A naive reset filter — "FROZEN ⇒ reset" — would have **rebooted a box that was
merely overloaded.** This is the design's #1 failure mode, and the safe set is
built to refuse it:

- **G1 (K≥3 sustained + T_min≥15 min)** rides out a transient load spike; a busy
  box that recovers within the window never reaches the safe set.
- **The firmware banner-window fix is a HARD PREREQUISITE.** `host_probe`'s
  `HOST_FROZEN` verdict must be *trustworthy* before it can authorize a reboot.
  The carry-over **`+dudeclaw.15`** bump (banner window **800 ms → 2500 ms**,
  `src/tools.cpp:1331-1332`; see §8) is what stops a loaded-but-alive box from
  reading frozen. **No actuation rung may ship until that fix is flashed AND the
  false-positive rate is measured at zero over a soak.**
- **Even with both**, G4 (boot-loop abort → MANUAL-ONLY) ensures that if the
  model is *still* wrong, the filter reboots at most M times then latches off and
  escalates — it cannot reboot-storm a healthy box.

> **The lesson, stated for this design:** the `.32` overload was a *userspace
> resource* problem with a *userspace* fix. The reset line is the wrong tool for
> it. The safe set's entire job is to make sure the agent can only reach for the
> reset line when nothing softer will work AND the freeze is the genuine,
> sustained, kernel-up/userspace-wedged class — and even then, behind a human
> token and a kill-switch.

---

## 7. Gates before ANY actuation firmware is written

Do **not** write reset firmware until **all** of:

1. **Operator ratifies the §3 numbers** (K, N, M, W, W_recover, T_min, expiry).
2. **`+dudeclaw.15` banner fix is flashed** and `host_probe` false-FROZEN rate is
   **measured at zero** over a multi-day soak (§6 — the verdict must be trusted).
3. **Hardware built + bench-tested against a sacrificial Pi**: opto-isolated
   open-drain RUN drive + physical kill-switch + kill-switch sense, verified
   fail-released when the claw is unpowered/floating.
4. **Constants pinned in a shared config** with the §5-pt5 drift test (edge
   authoritative, Pi test-pinned).
5. **Audit-witness path live end-to-end** (claw flash ring → brain → `/fleet`),
   so every refuse/fire is visible *before* the line can ever fire.
6. Build stays on a **`pr/` branch** of the fork, flashed + tagged per `FORK.md`
   — **never commit on `dudeclaw`** (the deploy branch).

Rung 3 then ships **human-on-the-loop** (G6 token from a ratified proposal).
Rung 4 (autonomous token) is a *separate, later* decision after a long
zero-false-fire soak of rung 3.

---

## 8. Cross-references

- Open Question #2 source + autonomy ladder: `dudeclaw_second_brain_2026_06_17.md`
  (RQ1 RTA/Simplex `3-0`; recommendation (a)).
- The carry-over banner fix that gates §6/§7-2: `+dudeclaw.15`, firmware
  `src/tools.cpp:1331-1332` — split to `tv_sec=2; tv_usec=500*1000` (2500 ms);
  **not** `tv_usec=2500*1000` (violates POSIX `tv_usec < 1e6`). Bundle at next
  claw touch; do not flash mid-`.32`-watch (it blinds the witness).
- Collector that produces the trusted verdict: `scripts/host_probe_check.py`
  (3-attempt re-probe) + watchdog `probe_host_frozen`.
- Discipline applied in §5: `.claude/rules/honest_failure_modes.md`.
- FORK procedure / branch invariant: fork `FORK.md`; `[[project_dudeclaw_phase_a_2026_06_11]]`.

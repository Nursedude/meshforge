# Fleet Research-Lane Distribution — design + root-cause gate (opened 2026-06-11)

> **One sentence:** distributing parallel research "lanes" across the fleet is a
> sound design, but it is **gated on root-causing the 06-11 VolcanoAI lockups
> first** — building it now would bet the cause is load (the evidence doesn't
> support that) and route lanes onto moc1, the one box that shares VolcanoAI's
> exact kernel + SoC.
>
> Origin: a claude.ai cloud session proposed distributing lanes after the 06-11
> incident where six-lane research workflows hard-froze VolcanoAI twice. This doc
> captures the proposal, my assessment, the two factual findings that change it,
> and the sequencing decision — so the session that finally holds the ramoops
> verdict can pick this up with the reasoning intact. See
> [[project_volcanoai_hard_reset_2026_05_28]].

---

## The problem this is reacting to

06-11 events #4 and #5 were **kernel hard-lockups → BCM2835 HW watchdog resets**,
both triggered **during multi-agent research workflows** (WebFetch-heavy
`Workflow` fan-outs) on VolcanoAI. 2/2 froze the box. Standing policy until
root-caused: **NO multi-agent research workflows on VolcanoAI** (sequential/solo
research is fine). The cloud session's framing: "today's six-lane run could have
been six boxes × one lane instead of one box × six" — distribute the parallelism
so no single box carries the whole fan-out.

**Why that framing is incomplete:** the crashes had ~12 GB free, healthy power
(5.03–5.07 V, 42–47 °C), and **no OOM**. Resource exhaustion was *not* the
visible cause. The leading suspect is **kernel 6.18.33 × the research workload's
I/O pattern**, mechanism unknown (nothing flushed to the journal — a true hard
lockup). That distinction is the whole gate below.

---

## The proposal (cloud session, verbatim shape)

Reuse what the fleet already has — SSH identity everywhere, systemd, declared
roles, mini watching, `fleet_sync` for state. No orchestrator, no containers, no
new daemons. Three pieces:

1. **Declare lane capacity per role**, converge-enforced:
   ```yaml
   lane_capacity:        # max concurrent research lanes this role may host
     research_head: 2    # VolcanoAI — capped per the 06-11 incident
     federator: 1
     gateway: 0          # never trade RF bridging for cognition
     bot_node: 0         # Zero-class boards
     cloud_peer: 1       # VPS, if headroom after map serving
   ```
2. **A ~50-line `lane_dispatch.sh`** (no scheduler daemon): for each lane, pick
   the fleet box with most headroom (capacity > current lane count, skip boxes
   with PSI memory `some` ≥ 15, prefer free RAM), then
   `ssh "$host" systemd-run --user --unit lane-<name> -p MemoryHigh=3G
   -p MemoryMax=4G -p MemorySwapMax=512M -p Restart=no …`. Fail loud when no
   host has headroom (hard exit, not a silent queue). Enabling one-time bits:
   `loginctl enable-linger` per box; lanes checkpoint to journal at phase
   boundaries so a dead target means re-dispatch-resume, not rerun.
   Zero-code 80% alternative: `parallel --sshloginfile … --jobs 1` wrapping the
   same `systemd-run`.
3. **Results flow through the invariants already ratified**: each lane commits
   its journal/output to a scratch branch `lane/<topic>-<host>-<date>` (or scps
   the journal to the primary); **only the canonical writer merges + pushes** —
   lanes never push to main from remote boxes (the `one_canonical_writer` cure
   in miniature). Close the loop with `probe_lane_orphaned` (a `lane-*` unit
   failed/inactive with no journal verdict) routed to mini — a lane dying on
   moc3 at 2 a.m. pages instead of being found at merge time.

The proposal concedes its own punchline: *"the genuinely heavy multi-lane
synthesis still has its best home in the cloud."*

---

## Assessment

### What's genuinely good (extract regardless of the gate)
- **`probe_lane_orphaned` + the `one_canonical_writer` reuse** are correct and are
  a **prerequisite for any** distributed scheme. Worth specifying even before the
  dispatcher exists. "Silence is the failure mode" applied to a new surface.
- **cgroup bounds via `systemd-run -p MemoryMax/MemorySwapMax`** are cheap good
  hygiene we could wrap around *any* heavy local job today — even cloud-originated
  work that lands on a box.
- **`lane_capacity` as declared policy** is the right shape for the eventual
  handoff (encodes "VolcanoAI gets 2, not 6"). It just has to live where
  `provision_role.py` enforces, **not** in the docs YAML (see finding 1).

### The load-bearing risk it underweights
- It **mitigates resource exhaustion** (`MemoryMax`, `lane_capacity`) for a crash
  whose evidence shows **no** resource exhaustion. The likely cause is
  kernel/silicon, not load.
- Its **headroom heuristic routes at the risk**: "most headroom" preferentially
  picks the capable boxes, and the most capable is **moc1 — same kernel, same
  SoC** (finding 2). If the freeze is kernel-triggered, distribution doesn't
  contain it, it **spreads it onto the box whose loss hurts most** (moc1 is the
  cloud-map consumer *and* the brain-backup rsync target).
- It **sacrifices the experiment armed 06-11**: ramoops-pi5 + PSI + panic-sysctls
  now capture the next freeze on VolcanoAI. Moving the trigger off VolcanoAI
  removes it from the one instrumented box. And every dispatch target *except*
  moc1 currently **can't forensicate a freeze at all** (no persistent journald,
  no ramoops overlay) — a lane locking up moc3 is *un*-forensicable;
  `probe_lane_orphaned` says it died, never why. Strictly worse for root-causing.

### Two factual findings that change the proposal (verified 2026-06-11)
1. **`fleet_roles.yaml` exists only as `docs/fleet_roles.yaml` — a descriptive
   doc, not the converge-enforced governor.** Roles are actually governed by
   `scripts/provision_role.py` + `deployment.json` overrides. "Add it to the file
   that already governs the fleet" must mean: put capacity where
   `provision_role.py` reads it, or it's just a comment.
2. **moc1, the sister Pi 5 / the most-donatable box, runs the identical
   `6.18.33+rpt-rpi-2712` on the identical bcm2712 SoC as VolcanoAI.** It is no
   safer a research host than VolcanoAI if the cause is kernel/silicon.
3. **Linger is already `yes`** on both Pi 5s (enabled for the mini user units in
   #79) — the proposal's "one-time enable-linger" is already done.

---

## Decision: root-cause gates build

**Do NOT build the dispatcher yet.** For now, **run heavy multi-agent research in
the cloud** — research lanes are WebFetch + synthesis, they touch no RF or live
mesh state, so they have no reason to be on the fleet at all. Zero fleet risk,
and it's where the synthesis is best.

**The gate — the next freeze (or one careful instrumented repro on VolcanoAI)
tells us what we're containing, then placement policy follows:**

| Root cause | Placement policy |
|---|---|
| **Load-proportional** (pstore shows OOM/RCU-stall under memory churn) | Build ~as proposed: `lane_capacity` caps + `MemoryMax` bounds genuinely contain it. Distribution is safe. |
| **Kernel** (6.18.33 bug; reproduces under the I/O pattern regardless of free RAM) | **Pin/downgrade the kernel fleet-wide FIRST.** moc1 is no safer until then. Only distribute after the fix holds a soak. |
| **Silicon/firmware** (pstore EMPTY after watchdog reset = full SoC freeze) | **Never co-locate research with the other bcm2712 box.** Distribute only to non-Pi-5 hardware (or keep it in the cloud). |

**Build-now, independent of the gate** (safe, and prerequisites anyway):
- [ ] Sketch `probe_lane_orphaned` (degraded; `lane-*` unit failed/inactive with
      no journal verdict) — route in both role seeds, closed-enum gate bump.
- [ ] Decide the results-merge discipline (`lane/<topic>-<host>-<date>` scratch
      branches; canonical-writer-only merge) and write it down before any lane
      ever runs remotely.
- [ ] Optional hygiene: a `MemoryMax`/`MemorySwapMax` wrapper for heavy *local*
      jobs, usable today.

**Deferred until the gate clears:**
- [ ] `lane_capacity` in `provision_role.py` (NOT `docs/fleet_roles.yaml`).
- [ ] `lane_dispatch.sh` (or the `parallel --sshloginfile` 80% form).
- [ ] Forensic parity on dispatch targets (persistent journald + ramoops-pi5 on
      moc1, persistent journald on moc/moc2/moc3) — **a research host must be
      forensicable before it hosts a lane**, else we relive 06-11 blind.

---

## Pointers
- 06-11 incident + ramoops/PSI/panic instrumentation:
  [[project_volcanoai_hard_reset_2026_05_28]]
- Role governance: `scripts/provision_role.py`, `deployment.json`,
  `.claude/research/fleet_architecture_2026_06_03.md`
- Canonical-writer invariant + mini probe pattern: `persistent_issues.md`
  (#74/#78/#79/#80), `.claude/rules/honest_failure_modes.md`
- The cloud session that proposed this: claude.ai share
  `b94b90d4-9f37-4310-9f68-c5758f13131e` (JS-rendered — paste/transcribe if the
  full text is needed later).

# Standalone Enclave Usability Study — plan (drafted 2026-08-10)

> Studies the STANDALONE offering (domain doctrine 2026-07-31: two offerings,
> STANDALONE + FLEET; the end is "message arrives / truth told in-app") using
> the border-router enclave (m1 RouterOS border, alaula OpenWrt One radio
> node, kiai Pi brain) as the testbed. Born from the 2026-08-10 dead-call-audio
> incident: the enclave's config had drifted for weeks (open AP, placeholder
> SSID breaking DHCP, wifi-primary routing beside an idle wire, a leftover
> config cable) because nothing consumed its correctness. An environment
> without a check is furniture with an IP plan.

## The question

**Can a competent stranger stand this environment up cold and get a message
to arrive — how long, with how many interventions, and where do they bleed?**

Not "are these boxes usable." The boxes are a means; the offering is the
subject. Success is the domain's own end: message arrives, truth told in-app.

## Phase 0 — prerequisites (before any trial)

1. **Pin kiai's role.** It is currently half fleet-member (patched-build
   canary, RNS env class) and half enclave component. A study of a chimera
   measures nothing. Decide: enclave-component (preferred for the study;
   fleet duties migrate) or fleet-box-behind-m1 (then the enclave is m1 +
   alaula only). Decision is the operator's; record it here.
2. **Config baseline exists** — DONE 2026-08-10: private local snapshot repo
   (m1 export + alaula uci/packages/cron/tunnel-organ/drill; location and
   refresh procedure recorded in operator-side memory, not here — it holds
   secrets and stays out of this public repo).
3. **Runbook zero.** Write the deploy runbook AS IT IS BELIEVED TO BE, before
   any trial. The study measures the gap between this document and reality —
   so it must be committed first, warts and all.
4. **TX discipline.** Any trial that transmits goes through the tx_guard
   egress gate on a TEST channel — the 2026-08-09 lesson (a test suite keyed
   a live statewide channel) applies doubly to drills run by cold agents.

## Hypotheses & metrics (pre-registered — pick before, never retro-fit)

| # | Hypothesis | Metric | Threshold (initial guess) |
|---|-----------|--------|---------------------------|
| H1 | An operator following runbook-zero reaches message-arrives from powered-off hardware | time-to-first-message; intervention count (actions not in the runbook) | ≤ 60 min; ≤ 3 interventions |
| H2 | The enclave functions with the upstream WAN removed ("lives alone") | in-enclave message round-trip + all enclave services green during a WAN-cut window | 30 min cut, zero service losses |
| H3 | A restore-from-snapshot reproduces a working enclave on fresh/reset hardware | restore time; diff between restored and baseline config | ≤ 30 min; zero unexplained diff |
| H4 | Link failure degrades, never strands (wire↔wifi failover, tunnel re-dial) | failover time; packet loss window | ≤ 90 s; no manual action |
| H5 | Drift is detected within one check cadence, not discovered by incident | seeded-fault detection (see guard-drill rule) | every seeded fault caught |

Each trial logs: wall-clock per runbook step, every off-runbook action (these
are the product), every doc gap, and the raw terminal transcript.

## Subjects — two kinds, deliberately

1. **Human (the operator), post-decay.** After ≥1 month away from the enclave
   the operator IS a valid naive subject — 08-10 proved config knowledge
   decays past what anyone remembers. No rehearsal; the runbook is the only
   aid permitted.
2. **Cold agent session.** A fresh model session with NO project memory
   loaded, given only runbook-zero and ssh access. This is the repeatable,
   cheap proxy subject — and running it on a *smaller* model is a feature,
   not a compromise: if a Haiku-class session succeeds by the runbook alone,
   the runbook is genuinely complete (gates never scale down with the model;
   here the runbook IS the gate). Escalating model tier to pass a trial is a
   finding against the docs, recorded as such.

## Trials

- **T1 — restore drill** (H3): factory-reset alaula (m1 second, riskier),
  restore from snapshot, verify against baseline. Proves the backup is a
  backup (a guard that has never failed is not evidence it works).
- **T2 — cold deploy** (H1): from blank/reset hardware + runbook-zero to
  message-arrives. Human subject first (richest gap-finding), cold-agent
  repeats thereafter as the regression harness for the runbook.
- **T3 — lives-alone** (H2): cut m1's upstream WAN for a timed window;
  everything in-enclave must keep working; nothing may page falsely on the
  fleet side (the enclave being alone is DESIGN, not failure — probes that
  can see it must say inert/expected, honest-failure-modes #2).
- **T4 — failure drills** (H4): eth0→wifi failover (scripted, drilled once
  2026-08-10, 72 s pass), tunnel-kill re-dial, radio-node power-cycle.
- **T5 — seeded drift** (H5): deliberately corrupt one config item (re-enable
  the open AP, flip a route metric), confirm the drift check catches it
  within one cadence. Plant a violation; don't read (guard-drill doctrine).

Order: T1 → T5 → T4 → T3 → T2. (T2 is last: it consumes the runbook the
earlier trials harden, and it is the most expensive to repeat.)

## Results

### T1 (H3) — run 2026-08-10, residuals closed 2026-08-11

**H3 PASS by its own pre-registered metric — and the metric was found
incomplete.** Config diff vs baseline: zero unexplained (uci byte-faithful,
tunnel, failover, radio RX, API/web all verified; reboot-survival unaided
~90 s). Full drill narrative + corrected restore procedure:
`standalone_enclave_runbook_zero.md` §2.2 + §T1.

**Three residuals the metric could not see** (all surfaced by the fleet's
own detectors within hours, all closed 08-11, runbook commit `c1199370`):

1. **meshforge-scout absent** — not in extras/`sysupgrade.conf`; its cron
   fired into a missing command until `router_scout_degraded` +
   `cron_verdict_stale` flagged it. Re-enrolled; agent now registered.
2. **Stock meshtasticd restored** — `opkg install meshtasticd` pulled feed
   `2.7.26-r1`, silently replacing the patched `-r2` fork build; the #10468
   leak returned at ~15 maps/min on the USB meshtoad. Re-patched
   (hash-verified fork-CI ipk, 30-min soak flat at 112 maps); ipk now
   staged on-box in `/etc/meshforge/pkg/` so restore is self-contained.
3. **opkg hold flag wiped** with the package DB — nothing pinned the stock
   build out. Re-set; scout tick now witnesses `opkg_hold: true`.

**The study-level finding (feeds H3's metric forward, not retroactively):**
a config diff proves config, nothing else. The restored box was
*working-but-degraded* — right uci, wrong binary, missing instrument — a
state "zero config diff" reads as perfect. Future restore acceptance (T2
onward) adds two checks beside the config diff: `opkg status
meshtasticd-full` reports the patched release + `hold`, and a fresh scout
tick lands with `ok=true` + `opkg_hold: true`. **Restore acceptance =
PATCHED + INSTRUMENTED, not "working."** (H5's seeded-drift trial should
include residual #2's shape: a right-version-wrong-provenance binary is
drift that no config check catches — the scout tick's maps count is the
detector that actually caught it.)

## Model routing (the "what model should do this" answer)

| Work | Tier | Why |
|------|------|-----|
| Study design, protocol revisions, results analysis, adversarial review of "we passed" claims | **Frontier** (rationed) | novel-arc + the place miscalibration is most expensive |
| Trial execution as scribe/instrument: run drills, time steps, collect logs, draft runbook edits | **Opus-class** (day-to-day) | procedural, test-guarded, months proven on this cadence |
| Cold-agent T2 subject | **Smallest tier that passes** — start Haiku-class | subject competence must come from the runbook, not the model |
| Continuous drift watch (standalone_status, config diff cron) | **R (rules/probes)** + mini escalation | always-on tier; humans and frontier passes are for findings, not polling |
| Human-usability judgment (is the runbook *followable*, not just correct) | **Operator** | usability of a human artifact needs the human; models measure, humans experience |

## Lab-environment improvements for reliable testing

1. **`standalone_status.sh`** — the enclave's honest_status: re-derives from
   live boxes: default-route posture (wire primary, wifi backup standing),
   open-AP absent, tunnel established + recent re-dial capability, DHCP lease
   health, in-enclave message round-trip. Exit 0 / 1 / 2-UNKNOWN semantics
   identical to honest_status; UNKNOWN is never a pass. Wire into
   cron_verdict so silence pages (#78 for free).
2. **Config drift cron** — refresh the private snapshots on a cadence, `git
   diff` against baseline; any unexpected diff is a CONCERN verdict naming
   the moved lines (the fleet_hosts_selfheal pattern: heal/report, never
   silent-OK). This turns 08-10's "weeks of unnoticed drift" class into a
   ≤1-cadence detection.
3. **Reset-and-restore path, tested** (T1) — a lab that can't return to a
   known state can't run repeatable trials. The snapshot repo is necessary
   but unproven until a restore drill passes.
4. **Variable isolation** — one change per trial run; the enclave's parallel
   paths (wire + wifi + tunnel) made 08-10's diagnosis cost hours because
   three variables moved together. Trials pin all but the variable under
   test (and say so in the log).
5. **Physical hygiene** — label the two alaula ports (uplink vs LAN/config)
   and the config cable; the 08-10 root cause was ultimately an unlabeled
   cable nobody remembered. Cheap, embarrassing, real.
6. **Footprint discipline** — no new machinery to watch machinery: reuse
   cron_verdict, mini rules, and the existing tunnel watchdog. Benchmark any
   new check on the smallest box before wiring it (operator constraint
   2026-07-24). Prefer removing enclave variables over instrumenting them.
7. **TX-safe test channel** — a dedicated test channel/preset for enclave
   drills, enforced via tx_guard, so no trial can key a live channel even
   when run by a cold agent with no context.

## Analysis loop

After each trial: findings → (runbook fix | default-config fix | check in
standalone_status) — every finding compiles DOWNWARD into an artifact; a
finding that only becomes prose will be re-found (write-time rule application
beats harness-catch, measured 2026-07-31). Re-run the cold-agent T2 after
each runbook change: it is the regression suite for the docs. Study ends when
H1–H5 hold at their thresholds on a cold-agent run AND one post-decay human
run — then the enclave graduates from furniture to a tested offering.

# CLAW-1 — "make the DudeClaws better" (grilling, started 2026-11-11)

> **Progress 2026-11-11:** **GRILLING COMPLETE** — rounds 1–3 + Q17 + slice order SETTLED
> ("yes to all four / seven / five", "yes to Q17 and the slice order") · ROADMAP
> NEXT `CLAW-1` added · no code · build after BR-T, slice 1 first.
> Source: DudeAI Direction 11-11 page (https://claude.ai/artifact/B3nHXQUgTdAFxb3UbuhP83),
> where the operator decided **DudeClaw = the ESP32 radio witnesses** (`dudeclaw-01..03`).

## Settled decisions (operator's words: "yes to all four", 2026-11-11)

| # | Decision | Why (recommendation accepted) |
|---|---|---|
| Q1 | **Witness first.** A claw's END is telling the truth about the AIR — pipeline-dark vs air-dark. TX, BLE, battery, RNS transport are secondary. | The one thing nothing else in the fleet does; failures are measurable (the 11-10 moc2 re-key read green for 2 months). |
| Q2 | **Fleet first, then one ham.** Make the three claws trustworthy on our fleet; then the same thing installable by a ham with one Heltec V4 + one Pi, no fleet. | Standalone bar (operator 10-30); "small, portable" — a design that only works with moc2's NATS + our host list fails it. |
| Q3 | **Receive-first.** No new TX features this arc; the existing TX leg stays as-is. TX growth is a separate, ratified step after §97.115 is read verbatim. | A witness that transmits changes what it witnesses; zero regulatory surface for RX. |
| Q4 | **NEXT item `CLAW-1`, defined now, built after BR-T closes.** Cheap blind-spot fixes may go first as small items (reset_reason not persisted — deferred `claw-reset-reason-blind-spot-2026-11-10`; watch lists that rot when a node id re-keys). | NOW is the operator's (12-04 ship plan). |

## Operator framing (2026-11-11, with round 2)

> "what matters here is the tools we use that run and watch the NOC 24/7 like oracle"

The claws are one INSTRUMENT of the always-on watchers (mini = the night
watcher, the watchdog probes, the offline oracle). "Better" = what those
watchers can truthfully say because of the claws, around the clock, with no
session present.

## Settled — round 2 ("yes to all seven", 2026-11-11)

| # | Decision |
|---|---|
| Q5 | **Never silently wrong**: every tick, each watch list is checked against what the FLEET knows (meshtasticd node DBs, other witnesses, segment declarations); an id no witness heard in **7 days** reads **UNIDENTIFIABLE** — named, paged once, never HEARD/SILENT. claw-03's unrecorded watch ids are read back and recorded. |
| Q6 | **Health travels with testimony**: every tick persists reset_reason, uptime, firmware version, heap; an uptime drop no ordered reboot explains is a named event; each verdict carries "could see since HH:MM". Host-side; closes `claw-reset-reason-blind-spot-2026-11-10`. First slice. |
| Q7 | **WITNESSES DISAGREE** is a finding: two witnesses on one segment (claw↔claw or claw↔meshtasticd) disagreeing about one node past a window — both readings shown, never averaged, no winner picked. moc5 (two claw witnesses) is the first bed. |
| Q8 | **Host-side first**; firmware changes batched into ONE later flash (`.21`, the reboot tool, anything host-side work proves it needs). |
| Q9 | **That USB visit is the last**: the batched flash also repartitions with `ota_1` (LittleFS wiped → scripted re-provision of WiFi/NATS/channel/watch list, each claw's config backed up first). Physical, 3 sites: operator. |
| Q10 | **A read-only "Claws" TUI pane** per box: firmware, uptime + last reset reason, link, each watched id with HEARD / SILENT / UNOBSERVABLE / UNIDENTIFIABLE + age. |
| Q11 | **Standalone prerequisite = NATS token auth in the firmware** (upstream PR #16 or our fork) — replaces the pinhole; decided as direction, built after the fleet phase. |

## Inventory facts used (explorer 2026-11-11, read-only; dates per source)

Locations: claw-01 USB on **moc1** (other building), claw-02 at **moc5's site**
(next to the Airspy), claw-03 on **moc2**; all per-claw minis + NATS on moc2.
Firmware `Nursedude/WireClaw` branch `dudeclaw`; flashed `.20`/`.19`/`.20`
(01/02/03), `.21` built not flashed; no OTA (no `ota_1`); reboot tool unflashed.
No claw TUI handler (🦞 line in Fleet Watchers only). ~340 claw-specific tests.
No standalone docs / auth / binaries.

## Operator framing (2026-11-11, with round 3)

> "failure has a reason why - diagnostics is the cornerstone of our domain - a SOT - what makes a true NOC"

## Settled — round 3 ("yes to all five", 2026-11-11)

| # | Decision |
|---|---|
| Q12 | **Claw truth enters mini as first-class conditions** (UNIDENTIFIABLE, WITNESSES DISAGREE, unexplained reboot, "could see since") → warm brief + fleet rollup. mini observes and names; it never restarts, re-flashes or rewrites a watch list. |
| Q13 | **Observe before alarm**: counters + brief lines first, no pages; a page only after a soak baseline, on a RUN (≥2 ticks). Exception: UNIDENTIFIABLE pages once (Q5). |
| Q14 | **The offline oracle explains claw findings offline**: once `DAI-0` gives it a public corpus, the claw runbook is its first content (re-keyed id, lost AREDN DHCP reservation, WiFi/AP path, TASK-WDT, pinhole). |
| Q15 | **No new daemon**: ride the 5-min `claw_metrics_push` capture + the per-claw mini ticks; moc2 CPU measured before/after; measurable load = does not ship. |
| Q16 | **Done = planted faults named**, and they are `EVAL-PF`'s first cases: (a) dead id → UNIDENTIFIABLE (clock fixture); (b) USB power-cycle → unexplained reboot + reason, "could see since" moves; (c) one claw's RX blinded while its sibling hears → WITNESSES DISAGREE; (d) null drill → no page. (b)(c) physical: operator / standing go arms them. |

## Settled — Q17 + slice order ("yes to Q17 and the slice order", 2026-11-11)

**Q17 — every claw finding carries its reason, or says UNKNOWN.** Each named
claw state is a rule in the diagnostic engine (`diagnose()`, the one SOT the
TUI and mini already use), with ranked candidate causes and the ONE quick check
that separates them (the `persistent_issues` tell-row shape). No rule explains
it → "cause UNKNOWN — here is what was ruled out", never a bare state.

**Slice order:**
1. Q6 health-with-testimony (host-side; closes the reset_reason blind spot).
2. Q5 UNIDENTIFIABLE + record claw-03's watch ids.
3. Q10 Claws TUI pane, with Q17 reasons.
4. Q7 WITNESSES DISAGREE (moc5 first).
5. Q12/Q13 mini conditions → soak → paging decision.
6. Q16 drills → `EVAL-PF` cases.
7. Q8/Q9 the one batched flash (`.21`, reboot tool, `ota_1`) — physical, operator's go.
8. Q14 oracle claw runbook (after `DAI-0`).
9. Q11 standalone NATS token auth (after the fleet phase).

## Shared understanding (confirmed 2026-11-11)

The claws are independent witnesses of the air serving the 24/7 watchers, so
what the NOC says about the mesh is measured, self-describing and explained:
never silently wrong (UNIDENTIFIABLE), carrying their own health (reset reason,
"could see since"), disagreement surfaced as a finding, every finding naming
its reason or UNKNOWN through the one diagnostic SOT. Receive-first, host-side
first with one last USB visit, no new daemon, shown in a TUI pane, done when
planted faults are named — and those plants seed the benchmark. Fleet first,
then one ham.

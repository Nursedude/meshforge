# OpenWrt arc — use-case verdict (2026-07-12)

> Operator-triggered (07-12): "a lot of hardware for what? the arch of this
> gig is not usable." This page is the deliverable the research skipped: the
> operator problem first, the mechanism second. Companion to
> `babel_l3_fabric_spike_2026_07_12.md` (mechanics SSOT).

**Verdict, first line: as deployed today, this hardware solves no operator
problem the fleet doesn't already solve.** The router sits at the **AREDN node
site, <15 ft from moc5** (operator ground truth 07-12 — NOT co-located with
moc as first written); its toad duplicates ubex's (moc5's radio) RF vantage.
Everything below is either a cheap keep, a dated bet, or a kill.
⚠️ Caveat on "duplicates": the router↔ubex pair shows an RF asymmetry (ubex
hears the router; the router's recent-heard list lacked ubex despite 15 ft +
same LF/20 channel) — if ubex's coverage is unhealthy, the toad may be the
site's better radio. See the domain research addendum.

## What was spent

- Hardware: OpenWrt One + Meshtoad E22 (+ the MikroTik NAT segment it sits
  behind).
- Sessions: 07-10 (flash + fork-CI ipk + #10468 patch chase), 07-11 (use-case
  study: 103-agent deep research + 3 arcs shipped), 07-12 (babel wired A/B +
  soaks). Three frontier-session days on router-class hardware.

## What each piece actually earns — ranked, with kill criteria

### 1. meshforge-scout (Arc 1) — KEEP, already paying
The one shipped piece with a live consumer: owrt1 ticks into kilo +
`router_scout` cron_verdict (OK on 30-min cadence) + `probe_router_scout_degraded`.
Cost to keep ≈ one cron. **No kill criterion needed — it already earns its
line.** Pending absorb (mtd-soak/mapwatch) stays gated on the ≥48 h parity
soak per the arc note.

### 2. Babel L3 fabric (Arc 3) — NO-GO, TORN DOWN 2026-07-15 ("measured, not needed")
Mechanics are proven inside criteria (converge 30 s, withdraw ≤1 s, RSS
≤1.3 MB, zero leakage; wifi-leg sawtooth GONE on the wired A/B). But
mechanics were never the question. The bet only converts to a build if BOTH:
- **(a) the 48 h wired soak reads clean** (~07-14: flap count ≈0 on both soak
  logs, RSS flat), AND
- **(b) a named fleet problem exists that L3 dynamic routing solves and
  static routes + rtun do not.** Candidates: multi-path failover across the
  NAT/AREDN segments; automatic reroute when a tunnel dies. If no such
  problem can be named in one sentence with a box and a failure date on it,
  the honest answer is "measured, not needed."
**Kill criterion: 07-14 soak read fails (a) or nobody can state (b) → run the
teardown block in the spike doc, close the arc as "measured, not needed."**
That is a good outcome — the spike existed to make this call cheap.

**OUTCOME 2026-07-15: NO-GO.** (a) PASSED clean — the ~71 h wired soak read
zero `babel_routes=0` flaps on both arms, routes 1/1 solid, RSS flat (692 KB
router / 1348 KB moc2), moc2 30/30 OK verdicts. But (b) does NOT hold: the
spike's own conditional-GO also required (c) failover-with-a-second-path, which
was **never measured** (single-path testbed — the dynamic-routing value prop was
never demonstrated), and (d) AREDN parameters, which are **unmeasurable** (the
production hAP is a standing no-touch box; moc5's LAN leg saw 0 babel packets).
No current fleet problem needs L3 dynamic routing (the real recurring pains —
DHCP reshuffle, map wedges, RNS reliability, the meshtasticd VSZ leak — are not
routing problems). Per the kill criterion, the teardown block was run on both
arms (router: babeld + wg-spike + test-/32 + config + soak cron + the durable
fw `spike` zone; `fw4 reload` rc=0, rtun survived. moc2: same, sudo for the
root-owned artifacts). Both boxes clean-verified. The spike SUCCEEDED — it made
this call cheap and evidence-based. Learning banked in this doc + the babeld
feed; rebuildable if a genuine dual-path failover need is ever NAMED.

### 3. The toad radio on the router — RESOLVED EARLY 2026-07-12: PULLED for repurpose
The 07-19 question answered itself the day kiai came up: with a full-stack
site box carrying RF, a bare daemon's radio had no job left. Operator pulled
the toad 07-12 night; meshtasticd on the router stopped+disabled, its crons
(mapwatch, mtd-soak) removed, **scout verified honest post-retirement**
(tick ok=true, running=false as observation; pull eval OK exit 0) — no false
pages. The fork-ipk/opkg-hold maintenance retires with it. **Gap-site option
retained**: if a real site is ever named, router+toad redeploy together
(re-enable = `/etc/init.d/meshtasticd enable` + plug). Toad's new life:
go-kit / spare / canary bench — operator's call.
Decided 07-12: **no MQTT uplink** — it would duplicate moc's feed and add a
#77-class drift surface with zero new information.

### 4. meshtasticd-on-OpenWrt (the ipk + #10468 patch) — SUNK VALUE, BANKED
Real, but a means: upstream contribution (PRs #10/#11) + platform optionality
if #3 ever fires. No further investment; it is done and needs nothing.

### 5. Fleet naming (Arc 2 Phase 0) — KEEP, not router-specific
SSOT + audit shipped and live; phases 1–3 are operator-owned. It rode this
arc but stands on its own; not part of this verdict's kill scope.

## The meta-lesson (why this page exists)

The 07-11 deep research answered "can it work" exhaustively and never opened
with "what breaks for the operator without it." Corrective, effective now:
**any /deep-research or arc proposal leads with the operator problem in one
sentence — a box, a failure, a date — before any mechanism work; a research
verdict without a named consumer is trivia.** (Same shape as calibrated
claims: evidence before assertion — here, use case before mechanism.)

## Standing decisions summary

| Piece | Call | Date/gate |
|---|---|---|
| scout | keep | earning now |
| babel fabric | **NO-GO — TORN DOWN** (soak passed clean, but failover unmeasured + AREDN unmeasurable + no named current need → "measured, not needed"; both arms clean-verified) | 2026-07-15 |
| toad radio | **resolved early: pulled + repurposed; gap-site option retained** | 2026-07-12 |
| router meshtasticd | retired (stop+disable, crons removed, scout stays green) | 2026-07-12 |
| MQTT uplink | rejected | 2026-07-12 |
| ipk/#10468 | banked, no further work | done |

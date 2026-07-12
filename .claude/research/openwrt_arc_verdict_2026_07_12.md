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

### 2. Babel L3 fabric (Arc 3) — DATED BET, verdict due 2026-07-14
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

### 3. The toad radio on the router — PARKED unless it moves
As sited, redundant (moc hears everything it hears). Its only honest job is
**coverage-gap edge node**: a site with power + network but no Pi, where one
$100 router = mesh RF + management plane. **Kill criterion: if no gap site is
named by 2026-07-19, the toad comes off the roadmap** (stays plugged as a
spare — zero maintenance, but no further sessions, no features, no MQTT).
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
| babel fabric | GO only if soak clean AND problem named | 2026-07-14 |
| toad radio | park; deploy-to-gap or de-roadmap | 2026-07-19 |
| MQTT uplink | rejected | 2026-07-12 |
| ipk/#10468 | banked, no further work | done |

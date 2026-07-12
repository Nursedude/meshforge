# OpenWrt → MeshForge domain capability — deep research (2026-07-12)

> Operator question: "meshtastic dev has been hot on this set-up — what can it
> do for the MeshForge domain?" Use-case-first discipline enforced: every
> finding opens with a named operator problem; mechanism proofs and
> consumer-less best practices excluded by instruction.
> Companion to `openwrt_arc_verdict_2026_07_12.md` (the kill-criteria page).
>
> Provenance: deep-research workflow `wf_6ed78f0d-489` — 98 agents, 0 errors,
> 564 tool uses, 5 search angles, 15 sources fetched, every claim through
> 3-vote adversarial verification (≥2/3 refutations kill). 77 min wall clock.
> Zero vitals tripwires on VolcanoAI during the run.

## The one-sentence answer

**Exactly one domain capability is live, and it's gated: the ~$89–100
PoE-powered gap-site edge node.** Everything else "dev has been hot" about is
tailwind (upstream will carry our packaging burden away for free) or
already-refuted territory. The router platform's entire domain value hangs on
whether a real coverage gap exists — which is the standing 07-19 question, now
with independent evidence behind it.

## Ranked shortlist

### 1. Gap-site edge node — the only capability with a live consumer path (3-0)
- **Problem**: toad-as-sited is redundant (same room as moc); kill date 07-19
  unless a gap site is named. Hawaii Island emergency-mesh has candidate
  sites with power + network but no Pi.
- **Capability**: OpenWrt One = router + meshtasticd + USB SX1262 in one box,
  **single Ethernet cable delivers both backhaul and power** (IEEE 802.3af/at
  PoE PD on the 2.5G port — verified two ways: board README + OpenWrt wiki).
  Two-way RF already field-verified on this exact owned unit (07-10).
  Materially better siting than a Pi (separate PSU or ~$25 PoE HAT).
- **Build**: deploy the owned hardware to a named site + scout enrollment.
  ~1 session. **Cost**: hardware sunk.
- Qualifier: PoE module ships in the retail kit; board-only SKUs may omit it.

### 2. Fork-CI ipk retirement via upstream convergence — free tailwind (5×3-0, 2×2-1)
- **Problem**: 07-10 burned a frontier day on fork-CI ipk + patch chase;
  every router node carries an opkg-hold.
- **Capability**: OpenWrt is now a **first-party Meshtastic org target**:
  official feed at openwrt.meshtastic.org (APK 25.12 + OPKG 24.10/23.05/22.03
  — covers the toad's 24.10.7), dedicated `meshtastic/openwrt` repo at
  v2.7.26, meshtasticd merged into `openwrt/packages` master 2026-05-08
  (PR #29595), July 2026 commits actively upstreaming ("Added upstream, no
  longer needed here").
- **Build**: watch releases; swap fork ipk → stock when a release carries the
  #10468 fix. **Cost: minutes.** Hard qualifiers: upstream packages master
  carries 2.7.15 UNFIXED; **no upstream release contains the #10468 fix yet**
  — retirement is conditional, not available today. The 24.10 feed line goes
  EOL ~Sept 2026, so retirement may force a 25.12 (APK) migration.
- Correction vs the raw synthesis: the #10468 stale-bot comment was ALREADY
  POSTED 07-10 (issuecomment-4937991814, clock reset) — no action due 07-14.

### 3. Any-arch cheap-router scaling — an OPTION, not a build (2-1, verified verbatim)
- **Problem** (hypothetical until Rank 1 proves out): gap sites multiply and
  Pi-per-site becomes the expensive path.
- **Capability**: Meshtastic OpenWrt packages build for EVERY arch OpenWrt
  supports (official docs, verbatim) → cheap MIPS/ARM PoE outdoor APs become
  package-installable meshtasticd hosts.
- **Risk**: upstream tests exactly TWO device families (One, Pi 3/4/5);
  everything else is untested; 32–64 MB MIPS fit is installable-not-proven;
  the #10468 patch needs a per-arch rebuild. **Build: none now** — canary ONE
  cheap router only after a SECOND named gap site exists.

### 4. mikroBUS SPI radio-in-the-router — WATCH, do not build (3-0)
The upstream heat is real (LR1110/SX1262 mikroBUS pin configs published, Open
Collective funded modules, developer test image) **but the enabling PR #17399
was closed UNMERGED 2026-01-20** — stock OpenWrt exposes no spidev on the One,
so direct-SPI means carrying a DT patch in a custom image: exactly the
fork-maintenance posture the arc just banked and closed. Adds no capability
over the working USB toad. Revisit only if the overlay lands upstream.
(Evidence base is a single developer's work — markbirss — no independent
field confirmations.)

### 5. AREDN co-residency = TEXT BRIDGING ONLY — a scoping fact, not a build (3-0)
Bandwidth classes are physics-mismatched: AREDN is megabit 802.11 carrying
VoIP/video/Winlink; LoRa is ~37.5 kbps theoretical / ~5–10 kbps practical with
~237-byte payloads. Only chat + small structured data cross — which the
existing MQTT/gateway spine already handles. **Value: closes the question
permanently.** No future arc proposes AREDN-app-over-Meshtastic transport;
any router-on-AREDN deployment is scoped text/telemetry from day one.
(Confirms the 07-11 0/3 refutation from the outside.)

## Build-first recommendation (ONE)

**Name a gap site and deploy the owned toad+One there before 2026-07-19 — or
execute the de-roadmap per the standing verdict.** Nothing else on this list
is buildable, urgent, or blocked on anything but that one sentence.

## Not-worth-it list (explicit)

1. mikroBUS SPI LoRa on the One (closed-unmerged PR, custom-image tax, zero
   capability over USB).
2. AREDN application transport over the bridge (physics, not engineering).
3. MQTT uplink from the router radio (rejected 07-12: duplicates moc, #77
   drift surface, zero new information).
4. Fleet-wide arbitrary-router rollout ahead of a named site ("commodity
   routers meet the bar" was refuted 0-3; untested-by-upstream hardware).
5. RNS-on-router and MPLS (killed by the 07-11 study).
6. Router as self-contained operator web station (meshtasticd-web) — refuted
   1-2, no verified consumer.
7. A "flash-and-go" prebuilt image expectation — refuted 0-3; the prebuilt One
   image is developer test firmware; the supported path is package install
   onto stock OpenWrt.

## Open questions (the report's honest edges)

1. **Does a named gap site exist** — power + network, no Pi, a real RF hole an
   operator noticed — before 07-19? The entire Rank-1 value hangs on it.
2. When does an upstream release with the #10468 fix ship, and on which feed
   line (24.10 OPKG vs 25.12 APK — the latter forces an OS migration)?
3. Can the #10468 patch rebuild for non-aarch64 (MIPS AP), and does
   meshtasticd actually RUN (not just install) in 32–64 MB RAM?
4. **The two kill decisions may resolve jointly**: a remote gap site could be
   what finally names babel's missing use case (multi-path failover when a
   tunnel dies) — or static routes + rtun suffice for one edge node. The 07-14
   babel read and the 07-19 site question interact; neither this research nor
   the soak can resolve that coupling — it's an operator call.

## Addendum 2026-07-12 (operator ground truth, same day): the router is at the AREDN site

The premise "toad duplicates moc's RF vantage" was WRONG — the router sits at
the **AREDN node site, <15 ft from moc5** (whose radio is `wh6gxz-ube`/ubex,
LF slot 20, tx_enabled, fixed_position). Consequences:

1. **The babel coupling anchor is now concrete**: the router IS the remote
   AREDN-segment edge. "Multi-path failover when the tunnel to the remote
   site dies (inet path ↔ AREDN 10.143/8 path)" is no longer hypothetical —
   it is this box, this site. The 07-14 babel read should weigh this.
2. **Redundancy is vs ubex, not moc** — and it's not clean: live check found
   an RF asymmetry (ubex hears the router; the router's recent-heard table
   lacked ubex entirely despite 15 ft + same channel) plus a **stale-PKC-key**
   entry on ubex for the router (07-10 reflash kept the MAC-derived node ID,
   rotated the keypair → DMs would fail). Stale entry removed from ubex's DB
   2026-07-12 (verified 0 rows); re-learns on next nodeinfo. The asymmetry is
   unexplained — if ubex's RF is unhealthy, the toad may be the site's better
   radio, which reframes "redundant" toward "candidate replacement."
3. **Router's advertised position is wrong**: set to moc's coords 07-12
   (operator picked "same as moc" before the site fact surfaced); needs the
   AREDN site's coordinates (AREDN sysinfo unreachable from moc5; operator to
   supply).

## Time-sensitivity

This report is stale after 07-14 (babel read) / 07-19 (gap-site date) /
~Sept 2026 (24.10 feed EOL). Three merged claims carried 2-1 votes
(upstreaming trajectory, every-arch builds, five-line packaging) — verifier
evidence rated high on each, but one dissent weaker than the rest.

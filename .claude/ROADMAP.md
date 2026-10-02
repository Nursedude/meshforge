# MeshForge Roadmap — SSOT

> **Read this first if you are a new session.** This is the one roadmap. Per-box
> session notes (`~/.claude/plans/gateway-session-notes-*.md`) say what the last
> session was doing on that box. This file says where the domain is going and why.
> Owner: WH6GXZ (operator). Update it when a direction changes, not when a task ends.
> Last set: 2026-10-01 (operator words, Opus 5.5 session).

## What we are

A **MOC: Mesh Operations Center.** One interface over meshes that cannot hear
each other: Meshtastic, MeshCore, Reticulum (RNS), AREDN, MQTT, and IP backhaul
(the site has a Starlink uplink). We serve the **best available route** for a
message across whichever of those transports is up, and we **tell the truth when
we cannot see**.

- **RNS is the network stack and the hub.** Cross-mesh traffic goes RNS-first.
  Pairwise radio-to-radio bridges multiply into duplicates and false information.
  We own the RNS/LXMF forks (`requirements/rns.txt`, `MF-FORK-PIN`).
- **`CanonicalMessage` is the contract** (`src/gateway/canonical_message.py`),
  byte-identical with MeshAnchor.
- **Two offerings:** STANDALONE (one box, one ham) and FLEET (nine boxes on two
  sites, operated as one). The **ECOMM kit** (alaula router + kiai NOC) is the
  fleet folded into a box that runs with no WAN.
- **Built by a ham, for hams, engineers and scientists.** Field-tested on our own
  fleet, through storms and WAN outages.
- **The END** a change must serve: *a message arrives*, or *the truth is told
  in-app*. If the only honest END is "the harness", it is not roadmap work.

## Why we win (be unique, not a feature list)

| Them | What they do well | What only we do |
|---|---|---|
| **MeshMonitor** (web NOC, Meshtastic+MeshCore+MQTT) | Web UI, Docker/Helm install, analysis of the user's mesh, virtual nodes | **Bridge** LXMF across meshes (they only observe RNS); RNode over the air (their RNode mode has never touched hardware); a TUI that works with the WAN down |
| **SMCI / RNS_Over_Meshcore** (RNS-over-MeshCore plugins) | Ride community MeshCore repeaters; airtime budget; published per-hop numbers | Native RNode path; owned RNS fork + rnsd lifecycle cures (#68/#69/#72); fleet-measured reliability |

Our moat is **months of mesh operations**: real failures root-caused on real
radios and compiled into guards (`persistent_issues.md`). Source: research
artifact rev 4.4, <https://claude.ai/artifact/5wKgtARxZTss7Dsgwrh6Ep>.
Re-check competitor SHAs before quoting it; they move fast.

## The central architecture gap — best available route

Measured 2026-10-01:
- `MessageRouter` (`src/gateway/message_routing.py`) decides by **rules and
  direction**, not by path quality.
- `utils/link_quality.py` scores links (SNR, RSSI, hops, age, loss), but **only
  the TUI reads it**.
- RNS picks paths by **hop count, then the newest announce**. It has no notion of
  link quality (fork `Transport.py` ~1820-1870).
- R2c showed the cost: the moc3↔VolcanoAI RNode leg dies both ways during
  915 MHz interference bursts, and nothing upstream knows.

So the hub must **measure its legs** and choose among them. Link quality is one
input to best-available-route, not the goal by itself.

## Now / Next / Later

**NOW** (this sprint)
1. moc3 antenna baseline → operator swaps the antenna (it stays SMALL) → repeat R2
   with n≥5 per size, interference logged on every run.
2. **rnsd onto mf.4** (`rnsd-restart-onto-mf4`): the fork rolls were in place, so
   the substrate still RUNS pre-roll code. MEASURED by honest_status leg
   `running substrate` (`4c9cef06`), not by hand: 10-02 = 12 units — rnsd ×8,
   lxmd (moc, moc1), nomadnet + meshcore-chat (MA box). VolcanoAI canaried 10-01
   (clean); moc3 at its antenna-swap boot; the rest one box at a time, operator
   present, #69 order. Done = that leg PASS.
   (R1, R9 and R10/M3 closed 10-01; the lab-daemon half of the #69 race closed in
   `c8dca3bc` after a contextless audit found it open.)
3. **R3 decision due 10-07 (operator):** is RNS over MeshCore needed? A no-go
   closes R3, R4, R6 and the SMCI part of R5.

**NEXT** (architecture; product work, not frozen)
4. **Per-leg delivery measurement.** Each transport leg reports delivered and
   failed counts over time. Observe before alarming: counters first, then a soak
   baseline.
5. **Best-available-route selection.** The router uses measured leg health so a
   flaky RF leg cannot own paths. RNS interface modes do NOT do this (R9, read
   from the fork's Transport.py: modes only shorten path expiry; RNS picks by
   hops, then newest announce). Gate it with a breaker on the hand-off witness: an rnsd wedge
   takes down all cross-mesh traffic.
6. **One RF-leg pane** (R8): airtime against budget, per-hop success, dead-hop
   events. The operator never leaves the app to learn whether RF carries RNS
   (MF018).

6b. **Typed tri-state on the truth path** (ADR `plans/adr_truth_kernel_2026_10_02.md`,
    measured: 23% of 128 incidents, κ 0.91): `Seen | Unobservable | Failed`, no
    default collapse, mypy-exhaustive — first `hs_substrate_skew`, `watchdog_probe_core`,
    `rns_init`, `service_check`. Every truth-path check also ships one test that
    feeds an input where check and subject DISAGREE (F_SHARED = 27%). Compiled
    rewrite REJECTED (2%); compiled kernel not now (scope 10/23).

**LATER**
7. Mesh-issue rules for the *user's* mesh (MeshMonitor M1), with one thresholds
   file and a test that forbids harmful advice.
8. Lower the install barrier (M5): a `monitor`-profile container, and an OpenAPI
   spec over the map service.
9. Virtual-node / one-owner-many-clients for the radio (M2), if R3 is go.
10. **Contain rnsd plugins (R5)** — regardless of R3: a deploy-time manifest of
    what rnsd auto-loads from `interfaces/`, and an import failure must not kill
    rnsd (the moc3 zombie-plugin class). Ledger `rnsd-plugin-containment-r5`.
11. **RF-window capture header (R7)**: every RF measurement window starts with an
    effective-settings snapshot (rate, SF/BW/CR, noise, interference) — the
    09-22 stored-params reversion would have shown in one.
12. **Bots back off on a busy channel (M4)**: oracle/echo responders gate on the
    radio's reported channel utilisation.
13. **Pin wire formats to real bytes (M6)**: fixtures generated from the installed
    library, never hand-written keys (the 09-18 `channel_idx` class).
14. Upstream to SMCI (R6) — drafts only, and only if R3 is go; posting is the
    operator's call.

## Rules that shape the roadmap

- The harness freeze (until 2026-10-09) **never blocks a serious bug or a
  fundamental architecture change** (`.claude/rules/harness_restraint.md`).
- RNS-substrate changes land in MeshForge first, then port to MeshAnchor
  (`scripts/parity_check.py`).
- Batch commits: one docs commit per session, code fixes grouped by logical
  change, at most one fleet deploy per session.
- `.claude/plans/v1.0_roadmap.md` (2026-04) is **superseded** by this file; its
  v1.0 criteria (stable API, production-ready at a QTH) still stand.

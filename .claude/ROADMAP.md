# MeshForge Roadmap — SSOT

> **Read this first if you are a new session.** This is the one roadmap. Per-box
> session notes (`~/.claude/plans/gateway-session-notes-*.md`) say what the last
> session was doing on that box. This file says where the domain is going and why.
> Owner: WH6GXZ (operator). Update it when a direction changes, not when a task ends.
> Last set: 2026-10-04 — 1.0 exit criteria added (operator grilling, Opus 5.5).
> Previously: 2026-10-01 (operator words, Opus 5.5 session).

## What we are

A **MOC: Mesh Operations Center.** One interface over meshes that cannot hear
each other: Meshtastic, MeshCore, Reticulum (RNS), AREDN, MQTT, and IP backhaul
(the site has a Starlink uplink). We are building toward serving the **best available
route** for a message across whichever of those transports is up; until route
selection ships (1.1), we **measure every leg and tell you which is up**. Always:
we **tell the truth when we cannot see**.

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

## 1.0 = these checks pass (set 2026-10-04, operator grilling)

**What 1.0 promises:** a stable contract AND a product a stranger can run
without us. It covers the **STANDALONE** offering only. FLEET (and the ECOMM
kit) is the lab that produces the evidence; it gets its own milestone later.

**Why the gates look like this (measured 2026-10-04):** 22 stars, 0 forks, 21
unique human viewers in 14 days, **0 issues ever opened by anyone but the
operator**. Clone counts (5,071 / 985 unique in 14 d) are mostly our own CI and
cannot be separated. Strangers install and copy MeshForge and never speak. The
fleet and the lab are the only physical reality we have, and they are full of
our own config. So the gates make the lab stand in for the silent stranger.

**Path:** `1.0.0-rc1` when every gate below passes except the soak → the 30-day
soak runs ON rc1 → `1.0.0`. **No date.** Progress = N of 10 gates passing, each
by its check. A runnable gate script waits for the 2026-10-09 freeze review;
until then this table is the record. Status is measured, never carried: re-run
the check before changing a cell.

| # | Gate | Passes when | Check | Status 10-04 |
|---|---|---|---|---|
| 1 | **Contract frozen** | `CanonicalMessage` v1 (jointly with MeshAnchor), config formats, CLI entry points (`meshforge-launcher.sh`, `--profile`, `standalone.py`), and the REST endpoints our own consumers use are frozen. The REST doc is regenerated from code and test-pinned. NOT frozen: Python internals, TUI layout (the TUI is a surface, not an API). | `python3 scripts/parity_check.py` (exists) + a REST-doc drift test (owed) | PARTIAL: parity enforced; `docs/REST_API.md` header says 0.4.6-beta, no drift test |
| 2 | **Config upgrades** | Auto-migrate with a backup; refuse LOUDLY on a config written by a newer version. Never silently reinterpret a file it does not understand. | migration test incl. a newer-version fixture (owed) | PARTIAL: `gateway/config_migrations.py` fixes known keys; no schema version, no newer-version refusal |
| 3 | **Per-leg delivery truth** (NEXT #4) | No silent loss: every accepted message ends delivered, queued, or shown failed in-app. No false green: a leg-kill drill shows the leg down within N min (N from the detector cadence). 30-day soak on rc1. Rates are SHOWN, not gated (a rate measures RF weather, not software). | leg-kill drill + soak ledger (owed) | NOT MET: NEXT #4 not built |
| 4 | **No AI required** | Every gate passes with no API key and no Claude Code. AI features say plainly that they are not configured. | one stranger-drill run with no AI anywhere | NOT MEASURED |
| 5 | **Stranger install drill** | VIRTUAL, every release: fresh image, README commands only, no dotfiles / hosts block / registry / AI. PHYSICAL, at rc1: one existing fleet board per supported type, clean user account + separate checkout (no wipe). Pass = the domain END (a message crosses mesh↔RNS and arrives; truth panes honest, incl. UNKNOWN), never "installer exit 0". | VIRTUAL: `gh workflow run stranger_drill.yml [-f installer_args=--client-only]` (observe mode); PHYSICAL: bare Pi 4 procedure (owed) | NOT MET — virtual run 2 (10-04, `37232491579`/`37232494058`): literal README install COMPLETES on bookworm + trixie arm64 with no TTY (rnsd/map/mosquitto up, 0 failed units); `--client-only` false red FIXED `9c76a19f` (verify reads `/etc/meshforge/noc.yaml` `managed:`; drill `37233965857` → "Installation OK with warnings", 0 FAIL) |
| 6 | **Supported list = what gate 5 passed** | Pi 4 + Pi 5, Raspberry Pi OS bookworm / trixie 64-bit. Zero 2 W, Pi 3B, CM5 listed only if measured. Everything else "may work, untested", said so. | gate 5 results | NOT MET: README claims Pi 3B / Zero 2 W and install.md claims Ubuntu 22.04+, while `pyproject.toml` requires Python ≥3.11 (22.04 ships 3.10 — assertion, not measured here) |
| 7 | **No-radio `monitor` deployment** | Container or VM, `monitor` profile (MQTT + RNS over TCP, no LoRa). LATER #8 moved up. Gateway / radio profiles stay hardware-only. | gate 5 virtual leg | NOT MET: no container exists |
| 8 | **Resource budget** | RAM + SD writes per day, per profile, on the smallest supported box: baseline recorded now, "no worse than baseline" at rc1. The hardware we have is the hardware for ~a year. | baseline measurement (owed) | NOT MET: no baseline |
| 9 | **"Report a problem"** | TUI action writes a redacted diagnostic bundle (versions, service states, last errors; no keys, no IPs per MF015) + an issue template it points to. The only way a silent stranger's broken install becomes evidence. | redaction test that plants a key/IP and proves it absent (owed) | NOT MET |
| 10 | **Security** | Frontier pass: design-level NOW on the new surfaces (gate 9 redaction, gate 7 container exposure); full pass at rc1 on the shipping build. Zero open high findings at tag. | review_provenance row | NOT MET |

**Context, recorded not gated:** hardware supply is constrained and prices are
rising; the fleet's hardware is unlikely to change for ~a year, so virtual,
cloud and local deployment work matters more. The uConsole was lost in
shipping; **kiai** is the build-our-own alternative; a CM5 8 GB (bought for the
uConsole) awaits an enclosure. When prices improve the operator will invest —
new boards join gate 6 only by passing gate 5.
**Bare-metal stranger box (operator, 2026-10-04):** a bare-system Pi 4 + a
radio are on hand for gate 5's PHYSICAL leg. This closes the gap a
clean-account install on a fleet box leaves (the system config — hosts block,
rnsd, apt holds — would still be ours). Keep it OUT of the fleet so it stays a
stranger: README-only install, no fleet registry, re-imaged before each rc.

**`malihini`** (Hawaiian: newcomer, stranger — operator 2026-10-04):
- **Where:** the tent (power + LAN), wired to **m1**. m1 may give it a DHCP
  reservation and a plain name (`malihini.lan`); NEVER an `mf-fleet-naming`
  entry (the fleet naming system; the hourly identity check reads it). Access
  = one `~/.ssh/config` alias. Never `fleet_hosts`, `fleet_roles.yaml`, the
  registry, our `/etc/hosts` block, mini or `fleet_sync` — so the monthly
  `box_config_capture` never touches it (by design: re-imaged per rc).
- **Hardware:** RAK6421 WisMesh Pi HAT + (per moc4's identical set) RAK13302
  in slot 2 + WisBlock env sensors + ALFA 915 MHz 2 dBi elbow antenna.
- **Answer key (from moc4, read 10-04 — not used during the blind install):**
  `lora-RAK6421-13302-slot2.yaml`; pass = SX1262 `init result 0`, then a
  message crosses mesh↔RNS. Confirm malihini's module/slot on first boot.
- **Two stranger paths:** (A) stock Raspberry Pi OS Lite 64-bit trixie via
  Raspberry Pi Imager + our README — run FIRST (tests our own claim); (B) RAK's
  official pi-gen image (meshtasticd pre-configured) + our README — tests that
  our installer does not break a working vendor setup (the #58 class).
- **Hypothesis to measure (ASSERTION, 10-04):** the installer writes
  `Module: auto` and its friendly labels name only RAK6421 + 13300 slot 1/2.
  The HAT EEPROM (`/proc/device-tree/hat/product` = "6421 Pi Hat") is written
  at manufacture and cannot know which module sits in which slot, so `auto`
  cannot reach `13302-slot2`; a stranger with moc4's exact hardware gets no
  label for the template it needs. Likely fix after the drill confirms: on a
  RAK6421 EEPROM, ASK module + slot (all four combinations).
- **Channel step:** a stranger's radio boots on public default LongFast; fleet
  HATs run HawaiiNet primary with no public LongFast — the drill tests whether
  the README gets a stranger onto a channel the mesh can hear.

## Now / Next / Later

**NOW** (this sprint)
1. moc3 antenna baseline → operator swaps the antenna (it stays SMALL) → repeat R2
   with n≥5 per size, interference logged on every run.
2. ~~**rnsd onto mf.4**~~ **MET 10-05** — honest_status leg `running substrate`
   PASS twice on 10-05 (14:20, 15:06 runs): "60 units on 10 boxes run the rns lxmf
   copy that is installed". ⚠️ Two caveats stay on the record, not averaged away:
   24 units "could import it but were not judged (no entry/repo evidence)", and
   lehua has "no watched dist resolvable" — UNKNOWN, not pass, for those.
3. ~~**R3 decision due 10-07**~~ **DECIDED GO 10-05 (operator):** RNS over MeshCore
   IS needed — *"same reason meshforge has it — think standalone … meshanchor is the
   sister, why does meshforge get all the power."* MF already ships RNS over its
   radio as an OPTION (`Meshtastic_Interface` templates, `commands/rns_templates.py`;
   disabled on the fleet BY CHOICE, moc3 `.disabled`). MA has no MeshCore twin, so a
   standalone MeshCore ham cannot put RNS on the radio they own. GO opens R4, R6, the
   SMCI part of R5, LATER #9 and #14. Candidate transport: `afit21/Reticulum-Smart-
   MeshCore-Interface` 1.0.0 (research artifact rev 4, 10-01). Lands MA-first (its
   radio), contract shared; containment (R5/LATER #10) BEFORE it ships to anyone.

**NEXT** (architecture; product work, not frozen)
4. **Per-leg delivery measurement** (**1.0 gate 3**). Each transport leg reports delivered and
   failed counts over time. Observe before alarming: counters first, then a soak
   baseline.
5. **Best-available-route selection** (**1.1** — not a 1.0 gate). The router uses measured leg health so a
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

6c. **Fleet posture controls by REASON** (operator 10-05; `plans/fleet_posture_controls_2026_10_05.md`):
    move/power → dormant, travel/hardware → detached (no new state); fleet_sync +
    rollup honour posture; TUI declares AND powers off via `fleet_power.py` (09-11
    surface doctrine amended for this action). P1-P3 SHIPPED 10-05 (`59257aaf`,
    `1b0f672a`). P4 UPS auto-trigger waits for cabling — operator 10-05: cabling is a
    work in progress; nearest Pis to a UPS are VolcanoAI + kiai, or the yurt
    (Starlink + EcoFlow 1600). PHYSICS: the signal cable goes to a Pi POWERED BY THAT
    UPS (its signal means "MY power is on battery"), and the switch/AP must outlast
    the Pis or the central trigger cannot reach them. APC feeds VolcanoAI, alaula, moc5, moc,
    hAP, 3 switches, router (operator 10-05); no APC on USB anywhere yet (measured).

**LATER**
7. Mesh-issue rules for the *user's* mesh (MeshMonitor M1), with one thresholds
   file and a test that forbids harmful advice.
8. Lower the install barrier (M5): the `monitor`-profile container MOVED to
   1.0 gate 7 (2026-10-04); an OpenAPI spec over the map service stays here.
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
  two v1.0 criteria (stable API, production-ready at a QTH) are made measurable
  by the "1.0 = these checks pass" section above (2026-10-04).

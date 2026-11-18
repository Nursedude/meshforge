# Artifact audit — 2026-11-17 (46 domain pages vs repo HEAD `d1d784aa`)

> Operator 11-17: "the artifacts are reviews, research — at times an outside
> observation to local you … make sure we are being thorough". Four read-only
> subagent reviewers (Opus 5.5, contextless of each other) read every page and
> grepped the repo; the main session RE-MEASURED the claims of consequence
> (marked ✔ below). Everything else here is the reviewers' — BELIEVED until a
> session re-checks it before acting. Repo is the SoT; pages are dated views.

## Verdict

- **Pages carry no source.** 2 of 46 have `mf:source` (Axiometa v8, Why One Radio Missed It —
  the latter byte-identical to its repo file). `.claude/artifacts_registry.json` has no `source`
  field on any entry; 4 recent pages are not registered (3DAL, 5QYtK, VkbtX, FySnK). → SRC-1.
- **Findings get no disposition.** ~200 actionable items across the pages have no repo trace
  (no roadmap ID, no DONE, no refute). Many are ideas, not owed work — but nothing says which.
  → SRC-1 (disposition rule).
- **Substance lives only on pages** (~20 pages, wholly or partly). The worst: BASE-1's 7-stage plan (the roadmap row
  DELEGATES to the page), the Hawaii channels doc (11 actions), the 915 brief (its source file
  was never committed = A27), PIL-0..15, the 2 m HAT plan, the Femtofox procedure.
- **Pages the operator rereads mislead** (STALE-misleading, 11): Roadmap, Femtofox, Nursedude
  research, Cascadia, Meshtastic-only concept, RNS over MeshCore, Bridge Contract Matrix, Domain
  Audit, Hawaii Meshnet, Live Map / Map HTTPS packets (HOLD).
- **Personal exposure** (link-shared): Volcano Work Plan (phone, email, benefits) — operator
  making private 11-17; Bundled Page = résumé, contact details + unfilled `{{ startupName }}`.
  Public-bound draft "The Island Is the Test Bench" names fleet hosts (MF014 shape).

## Re-measured by the main session (✔ VERIFIED 11-17 23:4x–23:5x)

- ✔ ROADMAP gate 3 says "leg-kill drill … (owed)"; provenance 2026-10-31 21:23: "BIC-2 KILL RUN —
  PASS on all three measured criteria, with a protocol DEVIATION". Gate stays NOT MET (soak on rc1).
- ✔ meshforge-maps `scripts/meshforge-maps.service:28,31`: `User=pi`, `--host 0.0.0.0`. → SEC-2.
- ✔ meshing-around (branch `meshforge`, local checkout @ `3db5cef` 09-22) `update.sh`:
  `git pull origin main --rebase` on the fork branch; on conflict offers `git reset --hard
  origin/main`. lehua's live checkout NOT read. → PILINA (PIL-1).
- ✔ `192.168.50.x` in the map packets is not this fleet's LAN (fleet = `192.168.88.x`; 0 hits in
  `fleet_hosts`, `/etc/hosts`) → synthetic fixtures, not a leak.

## Per page (verdict · the one thing that matters)

| Page | Verdict | Note |
|---|---|---|
| MeshForge Roadmap `UL62HQ` | STALE-misleading | generated from `d8b3e4f6`; 8 roadmap commits since; MQTT-ARCH, SRC-1 absent; BIC-2 "no caller" → regenerate |
| Whiteboard `BvJfE3` | CURRENT | 5 config claims VERIFIED; SHORT_TURBO via MessageRouter UNKNOWN |
| DudeAI Direction `B3nHXQ` | STALE-minor | "not deployed" (deployed 11-15); 10 MISSING (MeshCore KISS seat, fw version per radio, root daemons, peer licences…) |
| Why Choose Us `7NMLUL` | STALE-minor | lab-check "not deployed" |
| One app `1Li1CG` | PAGE-ONLY | BASE-1 plan lives here only; 7 MISSING (`primary_radio`, Features screen, `is_licensed` rule…) |
| Outside Audit `QLgHJk` | STALE-minor | 11-14 verdicts not on page; A3 A20 A26 A27 A29 A32 still open in repo; 7 row-merge proposals undecided |
| Community vs MF `U21PnA` | STALE-minor / PAGE-ONLY | 16-row copy list vs PARITY-1's 7; TEMPLATE-PROOF etc. queued in provenance only |
| Cross-Audit `2hQCzN` | PAGE-ONLY (traceability) | ~20 proposed IDs (MT-28, GATE4-RUN, DIAG-1, MCX-EXIT, RNS-PANIC, LIC-1…) not on roadmap |
| Double Tap `FdbdWr` | STALE-minor | E1/E4/E6 + "archive review_provenance.md" (now 5,190 lines) MISSING |
| Bundled Page `SPnwP9` | personal | résumé, link-shared; AREDN overstated vs README:9 |
| 2 m LoRa HAT `3DALwe` | PAGE-ONLY | cites "math in the repo" — no such file; new edge vs STOP list / FW28-0 "no new edge" |
| Meshtastic on 2 m `S3q4BV` | STALE-minor / PAGE-ONLY | ~25-row plan, roadmap holds 4 defects; portnum already fixed `794fa373` |
| Axiometa `5oUqet` | STALE-minor | rnodeconf rule vs threat model (full by-id from rnsd config); cites hook `41839218` (real: `d278223f`); 3 FP counts disagree |
| Why One Radio `5QYtKF` | CURRENT | byte-identical; §8 predictions have no roadmap row |
| Hawaii channels `VkbtXi` | PAGE-ONLY | 11 actions, none tracked; `landscape_2026_11_09.md:50` correction owed |
| CardputerZero `FySnKB` | CURRENT / PAGE-ONLY | HW-PRE, HW-CPZ, POCKET-1 MISSING |
| 915 MHz Brief `8S65gk` | PAGE-ONLY | source file never committed (A27); "comments due Nov 9" past, replies Dec 7 |
| Femtofox `VJcBNa` | STALE-misleading | says `--client-only` skips services — it starts map + mosquitto (INST-1) |
| Nursedude Research `PStH58` | STALE-misleading | shows C16 as Med/High — REFUTED (provenance 10-18); C18 understated |
| Cascadia `UedXXT` | STALE-misleading | "self-host CoreScope" corrected 11-04 to feed hawaiimesh's; CoreScope issues already filed |
| Pilina Build Plan `C9aWkj` | STALE-minor | banner cites wrong sha (`d8b3e4f6`; real `b6a66229`); HIZ zone + #351 MISSING |
| Maps Audit `UieYc4` | STALE-minor | no H16 / CoreScope plan (in `plans/maps_audit_2026_11_06.md`) |
| Meshtastic-only Concept `Q4u8Xo` | STALE-misleading | no superseded banner; says PILINA unused (row since `9f50c3b1`) |
| Rust Roadmap `U5DF2A` | PAGE-ONLY / CURRENT direction | orjson Phase 0 + dissector crate: no row |
| Pilina standalone `13a9ro` | PAGE-ONLY (PIL-0..15) | 12 PIL rows MISSING; LongTurbo line refuted |
| RNS over MeshCore `5wKgtA` | STALE-misleading | fork pins old; "leg-kill not run" (ran 10-31); dummy load only "QUEUED" |
| Bridge Contract Matrix `URnK4p` | STALE-misleading | says repo is behind — reverse: F5 F6 F8 F9 F12 F15 F16 FIXED 10-26, F17/F18 absent from page |
| What Mesh Users Want `42fbS7` | STALE-minor | refuted footnote + "geo filter CHECK" still shown |
| Domain Audit `KgNxL2` | STALE-misleading | "health always empty" fixed (maps `811d1d2`); "no release tags" (4 exist) |
| Hawaii Meshnet `VygTjX` | PAGE-ONLY / STALE | LongTurbo premise CONTESTED; 13 MISSING |
| Oct packets/posts (16) | 5 LANDED, 8 PAGE-ONLY, 2 STALE, 1 empty | BIC-7/9/10/11/12 never tracked; MeshChatX EOL 2028-01-01 recorded nowhere; PipeInterface fork patch paused 10-12, no ID; `RNS.exit()` SIGTERM no-op untracked |

Not reviewed: 7 domain essays/posts dated ≤ 10-04 (Measuring the Air ×2, @rns squat issue draft,
Why Try MeshForge, Ecomm Field Card, Two Ways to Watch, audit posture 08-09, Unobservable Is Not
Healthy, BIARC ×2) and 8 personal pages (out of scope by choice). Doc comments on the Claude Docs
pages were not queried. Long pages were read head + grep (a late item can be missed).

## MISSING — the full list, for disposition (adopt → ID · refute → provenance · drop → say so)

Grouped by page; one line each. A 0-hit grep can miss an item worded differently.

**Strategy (group A)** — DudeAI: five "it just works" fleet tests · different-vendor reviewer +
one trusted ham (also Outside Audit) · MeshCore KISS-modem bench seat for MA · MeshCore fw version
per radio · stock MeshCore 1.18 as MA parity reference · repeater `flood.max.unscoped` dropping MA
injections · RNS 1.6.x stamp/older-node enforcement · meshtasticd + orchestrator as root · CoreScope
(GPL) + peer licence check · §97.115 verbatim before TX design. One-app: `primary_radio` key ·
Features screen (off/absent/broken) · HAM never sets `is_licensed` · ✔maps unit 0.0.0.0 as pi ·
six DudeAI principles atop ROADMAP · "does anyone run MA?" line · unit-name→program test. Outside
Audit: decide DET-WEDGE close-out, INBOX-T→BIC-2 merge, twin rows under BASE-1, BOT-* merge,
MAPS-AUD/MAP-H, R6+RNS-UP, SEC-URL3/SEC-KRN out of NEXT · fix A29 (ROADMAP:68 "rev 4.4"), A27,
A32 (`rns.txt:11`), A3 ("airtime respected" vs "no airtime budget yet"), A26 (research index).
Community: TEMPLATE-PROOF · 2.8 readiness drill · one TX predicate · airtime restraint defaults ·
pubkey identity across the 2.8 re-key · #10468 upstream comment (operator's) · copy-rows 3 7 9–11
13 14 16 · six small copies incl. AI.md · contribution ranks 2–4, 6–9. Cross-Audit: MT-28 GATE4-RUN
DIAG-1 RES-1 MCX-EXIT research-inbox MC-OBS HI-OBS CS-READ CS-ISSUE BCM-F16 BCM-OPEN LT-GW HI-STBY
HI-PROF RNS-PANIC PROP-FO LIC-1 · gravity eval before BIC-6 · SMCI airtime in R8 · traffic
attribution · per-failed-send diagnostics · incident→probe→fix series · "outside services are
edges" rule · F-number prefixes · D4 / "MeshForge Router" / O5 / O6 decisions. Double Tap: E1
30-day no-AI run · fleet-only probes → lab profile · archive `review_provenance.md` · AI-dataset
licence review.

**RF / hardware (group B)** — 2 m HAT: reference pair + bench · KiCad rev A · the "math in the
repo" · 145.010 with the coordinator (also S3q4) · §97.13(c) RF exposure · FCC equipment rules.
S3q4: §97.113 encryption ban in `compliance.py` · Part 97 warning in `rnode_profile.py` · fix
`docs/birc_2026_05_17_outline.md:196` · TCP-leg telemetry bugs · reading age · six region tables
→ one · frequency-aware cable loss · `is_licensed` badge · telemetry counters · telemetry-off
tri-state · sensor history DB · TUI telemetry config · 2.8 bump rows (NodeNum re-key, FromRadio
18/19/21/22, `--check`, bluetoothd/avahi) · Phase 3 amateur leg. Why One Radio: §8 predictions +
"baseline a week on 2.7.26 first". Hawaii channels: slot pinning on 5 boxes · wx-bot congestion
budget · broker-unreachable witness · both-ends census · noise-floor logging · per-hash airtime in
Channel Load · MA 0x88 → local broker · wiki edits · CoreScope per-observer TLS · correct
`landscape_2026_11_09.md:50` · hold for community. CardputerZero: HW-PRE · HW-CPZ · POCKET-1 ·
CardMesh upstream issues · hardware intake · 512 MB fit. 915 brief: commit the source (A27) ·
node-owner questions + ET 26-169. Femtofox: solar node + separate-machine confirm · step-14 memory.
Nursedude: O4 meshprobe ARM · O5 GPL vs Reticulum licence · O6 Quad4 report · O7 cloud broker ·
O9 no-RNS profile · O11 Substack quotes. Cascadia: MC-OBS/HI-OBS/CS-READ onto the roadmap or
refuted · adopt #3–#7 · checklist j1 j3–j7.

**Pinned plans (group C)** — Pilina plan: HIZ052 vs HIZ018/023 · Kilauea vnum 332010 ·
`fleet_admin_account.sh:22-23` KDE vs GNOME · `meshtastic_js_api.md` → `@meshtastic/sdk` ·
meshing-around #351 · 5 unaccepted doc suggestions. Concept + Domain Audit: per-leg airtime budget
for `mesh_bridge` (D1 says none yet). Rust: orjson Phase 0 · Pi 4 benchmark · dissector crate ·
ask upstream on a clean-room Rust RNS. Pilina standalone: PIL-0 · ✔PIL-1 update.sh · PIL-6 7 9 10
11 12/13 14 15 · repeat the 1,436-message run with an outside radio. RNS/MeshCore: dummy-load test
(on the R2 row) · antenna arrival · count October's real catches. Users: telemetry dashboard.
Domain Audit: ARES precedence · predicted-vs-measured coverage · MQTT standard topics · cloud
self-host kit · 30-day two-preset counter run. Hawaii Meshnet: Discord offer · wiki edits · wx bot
doc · hawaiimesh profile · Recommended Hardware · MA hot-standby bridge · LF↔LT gateway pilot ·
FCC 15.247 500 kHz proposal · AREDN backhaul · who-runs-what + disclaimer · 6 community questions ·
HI-OBS.

**October (group D)** — BIARC deck placeholders + LongTurbo slide re-check · GOVERNANCE.md,
funding.json, Sponsors · "≥30 % commits in gateway/routing" · MeshChatX EOL exit plan + installer
removal date + interop witness off MeshChatX + fetch source · carry-over backlog (delivery
diagnostics, propagation failover, integrity baselines, storage lock, `--require-hashes`, ratchet
persistence, config snapshots, in-app panic recovery) · BIC-7 9 10 11 12 + PROVENANCE.md (BIC-8
refuted by D2, unwritten) · fleet-CA decision · PipeInterface writer-thread patch · `RNS.exit()`
SIGTERM no-op.

## Gates seen during the audit

`bench_guard`'s letters-in-order scan refused plain read-only commands in 3 of 4 reviewers
(and 3× in the main session tonight) — the false-positive class BENCH-G removes.

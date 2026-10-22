# MeshForge Roadmap — SSOT

> **Read this first if you are a new session.** This is the one roadmap. Per-box
> session notes (`~/.claude/plans/gateway-session-notes-*.md`) say what the last
> session was doing on that box. This file says where the domain is going and why.
> Owner: WH6GXZ (operator). Update it when a direction changes, not when a task ends.
> Last set: 2026-10-18 — one-box router scope (D1), purpose test, competitor rows
> (operator: "go on your advice", after the Nursedude Mesh Research review).
> Previously: 2026-10-04 — 1.0 exit criteria added (operator grilling, Opus 5.5).
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
- **One box with N radios is its own hub** (D1, 2026-10-18). On a single box the
  bridge holds one dedup table and one airtime budget, so radio-to-radio on that
  box does not multiply. This is the STANDALONE Meshtastic router and needs no
  RNS (`requirements/core.txt` carries none; `mesh_bridge.py` imports none).
  Across boxes or protocols, traffic stays RNS-first. Build it on
  `src/gateway/mesh_bridge.py`; the research prototype `mt_router` re-solves the
  loop guard, dedup, DMs and identity worse (review_provenance 2026-10-18).
  We own the RNS/LXMF forks (`requirements/rns.txt`, `MF-FORK-PIN`).
- **The bridge contract is `.claude/foundations/bridge_contract_matrix.md`**
  (2026-10-16): per edge, what is kept / lost / translated, and the test that
  pins it. Upstream versions are variables; that matrix is the constant.
  `CanonicalMessage` (`src/gateway/canonical_message.py`, byte-identical with
  MeshAnchor) is its shared type, but today only the MeshCore edges build it
  (matrix F8).
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
| **Mesh Bridge** (IceNet-01; Node.js, Meshtastic-only) | Bridges encrypted channels across 2+ radios; ID dedup 2,000 / 1 h; MQTT both ways; systemd installer | NOT YET EARNED. Candidates: per-leg airtime budget (they limit only commands, 10/min), every message delivered-or-named-drop, true-origin identity + DMs, cross-box dedup. Claim nothing until a measured run |
| **MeshDash** (self-hosted dashboard) | "Up to 16 Meshtastic radios"; 100+ endpoint REST API; Docker runner | Nothing on radio count: `mesh_bridge` has 2 legs today. N legs is build work, not config |

**What best in class means here:** the most honest multi-radio router: every
message accounted for, airtime respected, the truth told when we cannot see.
Not the best dashboard — MeshMonitor and MeshDash are ahead there, and we do
not compete on UI, SSO or API size. (Rows above from their own READMEs,
2026-10-18; re-check before quoting.)

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
| 1 | **Contract frozen** | `CanonicalMessage` v1 (jointly with MeshAnchor), config formats, CLI entry points (`meshforge-launcher.sh`, `--profile`, `standalone.py`), and the REST endpoints our own consumers use are frozen. The REST doc is regenerated from code and test-pinned. NOT frozen: Python internals, TUI layout (the TUI is a surface, not an API). | `python3 scripts/parity_check.py` (exists) + `python3 scripts/rest_doc.py --check` / `tests/test_rest_doc.py` (exists, 10-22) | PARTIAL (10-22): parity enforced; REST index GENERATED + test-pinned (`6d6b4a0e`, 29 ghost endpoints removed). OWED: declaring the freeze itself (CanonicalMessage v1 with MA, config formats, CLI) |
| 2 | **Config upgrades** | Auto-migrate with a backup; refuse LOUDLY on a config written by a newer version. Never silently reinterpret a file it does not understand. | migration test incl. a newer-version fixture (owed) | PARTIAL (10-05): **`gateway.json` MET** — `schema_version` stamped on save, newer/non-integer refused via `load_error` (save refuses, file untouched), first save over a legacy file keeps `gateway.json.pre-v1`; `tests/test_gateway_config_schema_version.py`. MeshAnchor twin MET (`1ca766af`). OWED: other config files (`deployment.json`, `noc.yaml`, settings) |
| 3 | **Per-leg delivery truth** (NEXT #4) | No silent loss: every accepted message ends delivered, queued, or shown failed in-app. No false green: a leg-kill drill shows the leg down within N min (N from the detector cadence). 30-day soak on rc1. Rates are SHOWN, not gated (a rate measures RF weather, not software). | leg-kill drill + soak ledger (owed) | NOT MET: NEXT #4 not built |
| 4 | **No AI required** | Every gate passes with no API key and no Claude Code. AI features say plainly that they are not configured. | one stranger-drill run with no AI anywhere | NOT MEASURED |
| 5 | **Stranger install drill** | VIRTUAL, every release: fresh image, README commands only, no dotfiles / hosts block / registry / AI. PHYSICAL, at rc1: one existing fleet board per supported type, clean user account + separate checkout (no wipe). Pass = the domain END (a message crosses mesh↔RNS and arrives; truth panes honest, incl. UNKNOWN), never "installer exit 0". | VIRTUAL: `gh workflow run stranger_drill.yml [-f installer_args=--client-only]` (observe mode); PHYSICAL: bare Pi 4 procedure (owed) | NOT MET — virtual run 2 (10-04, `37232491579`/`37232494058`): literal README install COMPLETES on bookworm + trixie arm64 with no TTY (rnsd/map/mosquitto up, 0 failed units); `--client-only` false red FIXED `9c76a19f` (verify reads `/etc/meshforge/noc.yaml` `managed:`; drill `37233965857` → "Installation OK with warnings", 0 FAIL) |
| 6 | **Supported list = what gate 5 passed** | Pi 4 + Pi 5, Raspberry Pi OS bookworm / trixie 64-bit. Zero 2 W, Pi 3B, CM5 listed only if measured. Everything else "may work, untested", said so. | gate 5 results | NOT MET: README claims Pi 3B / Zero 2 W and install.md claims Ubuntu 22.04+, while `pyproject.toml` requires Python ≥3.11 (22.04 ships 3.10 — assertion, not measured here) |
| 7 | **No-radio `monitor` deployment** | A plain VM or non-Pi Debian host runs the `monitor` profile (MQTT + RNS over TCP, no LoRa) from the README alone. **No shipped container image** (D2, operator "your call — watch our overhead", 2026-10-18): an image is a second product (base-image CVEs, systemd + rnsd in a container, a second install path that drifts). Gateway / radio profiles stay hardware-only. | gate 5 virtual leg, run without a radio (the drill's throwaway systemd box stands in for the VM) | NOT MET: the radio-less install has not been drilled |
| 8 | **Resource budget** | RAM + SD writes per day, per profile, on the smallest supported box: baseline recorded now, "no worse than baseline" at rc1. The hardware we have is the hardware for ~a year. | baseline measurement (owed) | NOT MET: no baseline |
| 9 | **"Report a problem"** | TUI action writes a redacted diagnostic bundle (versions, service states, last errors; no keys, no IPs per MF015) + an issue template it points to. The only way a silent stranger's broken install becomes evidence. | redaction test that plants a key/IP and proves it absent (owed) | NOT MET |
| 10 | **Security** | Frontier pass: design-level NOW on the new surfaces (gate 9 redaction, gate 7 radio-less host exposure); full pass at rc1 on the shipping build. Zero open high findings at tag. | review_provenance row | NOT MET |

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

**BEST-IN-CLASS ORDER (operator 2026-10-13, after the cloud-session white paper
"Hub or Operations Center", artifact `JwQ2Fe5MuUJSbmtSvYUewo`).** Compete as a
cross-mesh BRIDGE, not a multi-inbox client: lead on delivery truth and route,
not on phone convenience. The cloud session's patch (`meshforge-moc-all-r3`,
3,669 lines, audit bundle `2ent1ZJfgo5Yi2wZunJ8v1`) is an ARTIFACT, never applied
whole; each item below lands on its own, re-tested here. In order:
1. **BIC-3 web auth** (token/session, viewer/operator roles) BEFORE anything on
   the web can transmit. Accept: an unauthenticated LAN POST is refused + logged.
   Grilled 10-13 (Q1–Q21): spec `.claude/plans/bic3_web_auth_2026_10_13.md`.
   **DONE + DEPLOYED 10-13** MF `feec8e31..f86274ac`, MA `09c3eb0c..34b75362`; review queued.
2. **BIC-1 `/inbox`** from the patch, after BIC-3. Sends read "handed to radio ·
   unconfirmed", never "delivered".
   **DONE + DEPLOYED 10-13** `834012d8` (+ RX every-channel fix + New message `95623429`);
   operator acceptance (a real send) still owed.
3. **BIC-15 operator line** (LXMF address, allowlisted read-only oracle + pages
   over RNS) — fix first: an unreadable identity is silently RECREATED (new
   address, corpse overwritten); quarantine + refuse + witness instead.
   **CODE LANDED 10-14, OFF everywhere** `8b476945` (verbatim) + `4f94f92e` (F1
   fixed in all THREE identity loaders; the real shape was `from_file` → `None`,
   never an exception). Spec `.claude/plans/bic15_operator_line_2026_10_14.md`.
   **Before any box enables it:** Fable security pass (gate 10, queued), a TUI /
   fleet surface for its status, then the 4-leg live acceptance on one box.
4. **BIC-2 = NEXT #4 per-leg delivery truth**, shown per message, UNKNOWN first-class.
5. **BIC-4 interop vs stock RNS 1.5.7**, both directions, real hardware → merge-forward
   or stay-pinned decision record.
6. **BIC-6 = NEXT #5 best-available-route.**
7. **BIC-5 credibility sweep**: generated version/test counts, delete the unbuilt
   `aprs_gateway` / `winlink_interface` / `plugin_marketplace` flags in `core/edition.py`.
   **DONE + DEPLOYED 10-22** MF `641ea0eb`/`87c1e90e`, MA `b5659edd`: the whole edition module
   was dead (no consumer) and is deleted; the cited version is a generated sentinel.
8. **HOLD BIC-13b/c** (per-box CA + `wss://`): a CA install per phone, an
   unexplained wss handshake stall, a large surface. Revisit only when outside
   users (BIC-14) ask for an installed app.

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

6d. **NOC read grant + `box_truth/v1` export** (operator 10-13, grilled Q1–Q18; spec
    `plans/noc_read_grant_2026_10_13.md`). Any center (MF or MA, standalone or fleet)
    reads any box, map-less included, once the box GRANTS that center a restricted ssh
    key (`restrict,command=<export>`), made as a TUI action (2nd acting carve-out). Why:
    MA cannot see a map-less box at all; MF sees moc3/kiai/moc/lehua only through
    UNRESTRICTED keys shared with the Claude session. 3 sessions: (1) spec — DONE 10-13;
    (2) MF: shared export + writer + grant CLI + migrate the 4 links, reviewers first;
    (3) MA port + TUI both apps, then minor bump BOTH (MF 0.7.0-beta, MA 0.3.0-beta).
    BEFORE session 2: a frontier design-level security pass (gate 10). Adds `box_truth/v1`
    to gate 1. Accept: MA `/fleet` 8/8 with moc3 `ssh_spool`; `ssh -i <noc key> moc3 bash`
    returns JSON, never a shell.

6e. **Mesh→RNS `@address` — a mesh user can START a private conversation** (operator
    2026-10-17; GRILL before build). Gap left by F14 (`7bbf6c82`, drilled live 10-17): a
    DM to the gateway with no open session is refused + the sender told, so a mesh user's
    only way into RNS is a PUBLIC channel post or waiting for an RNS user to write first.
    RNS→Mesh already has `@address`; Mesh→RNS has none. Shape: a DM to the gateway
    starting `@<name|hash-prefix> <text>` resolves to EXACTLY ONE RNS destination (identity
    SSOT / contacts / announce names) and goes as one private LXMF send that opens the
    session; no match or an ambiguous match → refuse + on-air notice naming why, never a
    fan-out. Open questions: name source + collision rule (two announces, one name);
    syntax clash with MeshCore's `@contact` leg and with the bridged-text `@` parse; does
    a peer-GATEWAY hash count as addressable (default no — infra, not people); 200-byte
    budget for the address + text; MA parity (MA tracks no own node id). Accept: handheld
    DM `@<known name> hi` → arrives at exactly that NomadNet/MeshChatX inbox, session
    opens, reply comes back as a DM; `@<unknown>` → notice, nothing sent.

6f. **M→R queue outage behaviour — "defer, don't count" + 24 h max age (F2+F5)**
    (operator 2026-10-17, grilled Q1–Q18; DEPLOYED both twins, MF lead). Measured
    (7 d journals): the RNS HUB never went down; the real outage is ONE destination
    offline for hours — MeshChatX `f0a6899b` 57 min–27.4 h, median ~4 h, circuit never
    closed on its own; moc's 14 dead letters were all `circuit open for f0a6899b`
    ⚠️ **CORRECTED 2026-10-17 (re-derived):** that span measured how long moc's
    IN-MEMORY circuit stayed OPEN until the next gateway restart reset it (9 opens,
    each after a restart; 0 closes; 0 deliveries) — NOT a peer's offline time.
    f0a6 is MeshChatX on VolcanoAI, `disabled`, last ran 2026-09-06; moc fans out to
    it via `rns.default_lxmf_destination`. 6f's behaviour stands on its own merits,
    but its f0a6 rows will EXPIRE at 24 h, never deliver. Finding (not fixed): the
    circuit is per-process memory, so every restart re-opens it after 5 failures
    (the bridge queue has no RetryPolicy, so #74's "temporarily unavailable" raise
    burns the 3-try budget). Decisions: (1) a queue primitive — a sender DEFERS a row
    until T, `retry_count` untouched; only `rns`/`rns_xform` use it. (2) defer ONLY on
    circuit-open / no-path / wedge; every other error still counts (3 tries →
    dead-letter). (3) circuit-open → defer to the circuit's recovery time; no-path +
    wedge → 15 s doubling, 5 min cap, deferral count on the row. (4) `_queue_send_rns`
    requests a path and defers — no in-worker 3 s busy-wait (live `send_to_rns` keeps
    its wait). (5) max age 24 h (`MESHFORGE_M2R_MAX_AGE_S`, one constant both twins) for
    deferred AND gated rows; expiry = dead-letter + DROPPED `expired` + counter +
    WARNING, enforced pre-dispatch AND in periodic cleanup (gated rows are skipped by
    `process_once`). (6) clock guard: age < 0 or > 7 d → re-stamp `created_at` once +
    `age_unknown` counter. (7) late delivery is delivery — `meshforge_queued_s` LXMF
    field, body untouched. (8) no on-air notice on expiry (v1). (9) counters only:
    `deferred_total`, `expired_total`, oldest pending age in `get_stats()`; alarm only
    after a soak baseline. (10) MF + MA together, reviewed before either deploys.
    Accept: red-first from the reviewer's probe (circuit open after reconnect → 8/8
    rows dead-lettered inside 60 s) now waits and delivers on recovery; a 24 h+ row
    expires witnessed; a wall-clock step while running is DETECTED (Δwall vs
    Δmonotonic) and queued rows shifted by it (operator Q15 — the first build's
    age-only guard let a +25 h step expire 2 h-old rows); requeued rows NORMAL so
    one dead peer cannot fill the queue (Q16); a replay hop keeps the first
    enqueue time (Q17); one knob both twins (Q18).
    **Next, grilled separately:** (a) **propagation handoff — SHELVED 2026-10-17 (no
    consumer).** Grilled Q1–Q14 the same day; the 27 h tail it answered was the
    measurement artifact above. **Reopen only when** a MEASURED intermittent peer
    exists that fetches from our node `3968a2ee` (moc1 lxmd, stamp cost 16). Design
    on file: shared code both twins, inert where `rns.propagation_node` is unset;
    trigger = 30 min since FIRST deferral (one knob); distinct terminal `propagated`
    state + counter + delivery disposition, never DELIVERED / `confirmation_rate`;
    exclusive of direct (two LXMessages = two hashes, recipient shows both); node
    unreachable (LXMF 5 attempts → FAILED) → back to deferred, uncounted, under the
    24 h age; node = `3968`; no on-air notice v1; stamp time MEASURED on moc/moc3
    before deciding, ≤1 stamp at a time; acceptance = a real client FETCHES the
    message from the node. **Precondition #1:** `on_delivered`
    (`bridge_ack_mixin.py`) must branch on `state` — LXMF fires the delivery
    callback on SENT for a propagated message, so today's code would record
    CONFIRMED and post "[delivered]" for a message only STORED (latent: no gateway
    path sends PROPAGATED). Side finding: moc runs a 2nd lxmd (`8271dd…`) nothing
    points at. (b) wake-on-announce (make a returning
    peer's deferred rows due at once) — only if the soak shows the ≤5 min cap matters.

**LATER**
7. Mesh-issue rules for the *user's* mesh (MeshMonitor M1), with one thresholds
   file and a test that forbids harmful advice.
8. Lower the install barrier (M5): the `monitor` profile MOVED to 1.0 gate 7
   (2026-10-04) and is a VM/host install, not a container (D2, 2026-10-18);
   an OpenAPI spec over the map service stays here.
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
15. **Uplink HEADROOM on `/fleet` (operator 10-06)**: `utils/starlink_dish.py`
    reads only the instantaneous `get_status` (one sample — read 0.5 Mbps down
    while YouTube streamed in the tent: a between-bursts sample, not headroom).
    Read the dish's own `get_history` ring buffer (per-second down/up bps,
    PoP latency, drop) and render peak/p95 over the window beside the current
    line. Read-only surface; the site has ONE dish, so this is the truth about
    the single path every non-mesh service shares (Starlink Mini backup WAN is
    the cure for that, separately).

## Rules that shape the roadmap

- **Purpose test (2026-10-18), the anti-bloat rule.** Every piece of work names
  (1) its END — a message arrives, or the truth is told in-app; (2) the 1.0 gate
  or NEXT item it moves; (3) the row it changes in
  `.claude/foundations/bridge_contract_matrix.md` or `docs/capabilities.md`.
  Work that cannot name all three is bloat. A test, not a freeze.
- **The biggest bloat is duplication between the twins**: MeshForge and
  MeshAnchor share 410 source files, 212 within 10 lines of each other, about
  75K duplicated lines (measured 2026-10-18). The cure is a shared base package
  (one base, a subclass per radio) — its own arc, not a side task.
- **No harness freeze** (the 09-09→10-09 freeze ended on its date; measured
  10-09: it never bound — volume doubled under it and every in-freeze
  instrument was exempt-and-right). Judge every change by its END; harness
  work is welcome when it fixes a detector that is loud, blind, or aimed at
  the wrong quantity. Measure the split with `scripts/harness_share.py`.
  Record: `.claude/plans/post_freeze_harness_budget_2026_10_09.md` §8.
- RNS-substrate changes land in MeshForge first, then port to MeshAnchor
  (`scripts/parity_check.py`).
- Batch commits: one docs commit per session, code fixes grouped by logical
  change, at most one fleet deploy per session.
- `.claude/plans/v1.0_roadmap.md` (2026-04) is **superseded** by this file; its
  two v1.0 criteria (stable API, production-ready at a QTH) are made measurable
  by the "1.0 = these checks pass" section above (2026-10-04).

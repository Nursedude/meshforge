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
without us. **What it does NOT mean** (carried from the superseded 2026-01
`v1.0_roadmap.md`, still true): feature-complete (features come in 1.x),
bug-free (bugs get 1.0.x patches), or final (2.0 may break the contract). It covers the **STANDALONE** offering only. FLEET (and the ECOMM
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
| 3 | **Per-leg delivery truth** (BIC-2) | No silent loss: every accepted message ends delivered, queued, or shown failed in-app. No false green: a leg-kill drill shows the leg down within N min (N from the detector cadence). 30-day soak on rc1. Rates are SHOWN, not gated (a rate measures RF weather, not software). | leg-kill drill + soak ledger (owed) | NOT MET: BIC-2 not built |
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

**How this list works** (roadmap cohesion, decided 2026-10-19 — Q-numbers in
`plans/roadmap_cohesion_proposal_2026_10_19.md`). This is the domain's ONLY list
of future work, for every repo. One line per item:
`- **ID** [repos] S|F|S+F [twin→MA|twin→MF] [maps:both] — title`, details
indented under it. IDs are stable and never reused. **Done leaves the list the
same day** as one provenance line `<ID> DONE <sha> — verdict`. **NOW is the
operator's**: only the operator moves items in or out; sessions may ADD to
NEXT / LATER with a reason and say so in the handoff. A "won't do" is proposed
with a reason and stays until the operator confirms. An item with `public:` +
`status:` appears in `docs/roadmap.md`, generated by `scripts/roadmap.py`
(`--check` is CI-pinned by `tests/test_roadmap.py`). Repos: MF meshforge ·
MA meshanchor · maps meshforge-maps · bot meshing_around_meshforge (the
companion client; no fleet box runs it) · meshbot the `Nursedude/meshing-around`
fork, branch `meshforge` (the LIVE routing bot on lehua; added 10-22) · fork
the RNS/LXMF forks. Offering: S standalone · F fleet.

### NOW

Operator's order. The best-in-class block (operator 2026-10-13, after the
cloud-session white paper "Hub or Operations Center", artifact
`JwQ2Fe5MuUJSbmtSvYUewo`): compete as a cross-mesh BRIDGE, not a multi-inbox
client; lead on delivery truth and route. The cloud patch
(`meshforge-moc-all-r3`, audit bundle `2ent1ZJfgo5Yi2wZunJ8v1`) is an ARTIFACT,
never applied whole — each item lands on its own, re-tested here.

- **R2** [MF][MA] F — moc3 antenna baseline → swap (it stays SMALL) → repeat R2 with n≥5 per size, interference logged on every run
  NEW antenna due Sat 2026-10-24 (operator 10-22). R2 = the RNode pair moc3↔VolcanoAI measured the way SMCI measures (MA `lab/lxmf_tracer` over the RNode-only path: delivered LXMF bps, RTT p50/p90, success rate); operator present, IP/TCP RNS legs held down so TCP cannot mask RF; baseline first (`rnstatus` rate = SF7/250), a control that can fail. Reminder `rnode-pair-measure-r2`. Feeds R8.
- **BIC-1** [MF] S+F — `/inbox` browser chat; sends read "handed to radio · unconfirmed", never "delivered"
  DEPLOYED 10-13 `834012d8` (+ RX every-channel `95623429`, dup/misfiling `adda3ff9`, BLIND-channel `0c3b08e8`; iPhone-verified 10-21). OWED: operator acceptance — one real send. Still unwitnessed: a NEW LongFast text from another node stays off the primary channel.
  public: A chat page in the browser for the mesh, which says a message was handed to the radio, never that it was delivered, unless it was.
  status: testing
  help: a Pi running meshtasticd and a phone browser on the same LAN — send and receive a few messages, tell us what the page claimed vs what arrived
- **BIC-15** [MF][MA] S+F twin→MA — operator line: an LXMF address with an allowlisted read-only oracle + pages over RNS
  CODE LANDED 10-14, OFF everywhere (`8b476945` + `4f94f92e`: an unreadable identity is quarantined + refused, never silently recreated). Gate-10 pass `578bcbdf` PASS for one box once B1 landed (`945be488`); status pane `a0e7cc0c`; pane non-author read PASS-with-notes `bd5141d3`. OWED before any box enables it: those notes, then the 4-leg live acceptance on one box. Spec `.claude/plans/bic15_operator_line_2026_10_14.md`.
- **BIC-2** [MF][MA] S+F twin→MA — per-leg delivery truth, shown per message, UNKNOWN first-class (= 1.0 gate 3)
  Each transport leg reports delivered and failed counts over time. Observe before alarming: counters first, then a soak baseline. No silent loss, no false green (a leg-kill drill shows the leg down within N min).
  COUNTERS LIVE (10-23, slices 1–3; history: review_provenance § BIC-2): `legs` on `/api/gateway/delivery` = `<src>><dst>` transport, 5-min buckets, 15m/1h/24h, with `legs_since_ts` / `legs_partial_windows` / `legs_write_errors`; direct sends, the persistent queue and ACK/NAK confirms all name their leg (guards over `src/`). TUI: Dashboard → "Delivery legs" (COUNTS ONLY; `c808b2df` + 2 review rounds). MA: slice 1 `79065a43`, 2a `ae80b992`, 2b `4cc35ea6` DONE+DEPLOYED (provenance § BIC-2 MA). MeshCore queue-row ordering DEFERRED (operator 10-24). ⚠️ OWED (deferred `ma-meshcore-slot-enqueue-validation`): MA `0ba66869` = live-latent Issue #37 leak fix, pushed NOT deployed — redo as ONE check at `PersistentMessageQueue.enqueue`, review, deploy. Queued: MA supervisor-path slice (inert). NEXT: MA TUI pane; MF-first twin fix (provenance § MA 2a + MA 2026-10-24 rows): preset-bridge `?` legs + `_leg_source` into byte-locked `delivery_legs`, MF copies of F2/F6, MF copies of 2b-review F4 (queued R→M hard-codes `source="rns"`), F8 (one SENT helper), F6 (per-CHUNK units — decide before any rate); (3) normalise the leg origin once at ingress; then soak baseline (start it AFTER 10-24 ~08:00: rows queued before slice 2 carry no origin and expire on `?>rns` for up to their max age) → leg-kill drill (N ≤ window + 5 min). ⚠️ Before any rate: broadcast SENTs can never confirm — split confirmable vs unconfirmable (= UNKNOWN first-class).
  public: For every message, MeshForge will say which network legs it crossed and whether each one delivered, failed, or cannot be seen.
  status: planned
- **BIC-4** [MF][fork] S+F — interop vs stock RNS 1.5.7, both directions, real hardware → merge-forward or stay-pinned decision record
  public: Prove MeshForge's pinned Reticulum talks to stock Reticulum in both directions on real radios.
  status: planned
- **BIC-6** [MF] S+F — best-available-route: the router uses measured leg health so a flaky RF leg cannot own paths (1.1, not a 1.0 gate)
  RNS interface modes do NOT do this (R9, read from the fork's Transport.py: modes only shorten path expiry; RNS picks by hops, then newest announce). Gate it with a breaker on the hand-off witness: an rnsd wedge takes down all cross-mesh traffic. Needs BIC-2's measurements first.
  public: Pick the best working route between meshes from measured link health, not just hop count.
  status: planned
- **BIC-13** [MF] S+F — HOLD per-box CA + `wss://` (13b/c)
  A CA install per phone, an unexplained wss handshake stall, a large surface. Revisit only when outside users (BIC-14) ask for an installed app.

### NEXT

- **R8** [MF] S+F — one RF-leg pane: airtime against budget, per-hop success (never aggregate), dead-hop events — the operator never leaves the app to learn whether RF carries RNS (MF018)
  With R7 (capture header). Reminder `rf-leg-view-r8`.
  public: One screen that shows whether the radio link is actually carrying traffic: airtime used, per-hop success, dead hops.
  status: planned
- **TK-1** [MF][MA] S+F twin→MA — typed tri-state on the truth path: `Seen | Unobservable | Failed`, no default collapse, mypy-exhaustive
  ADR `plans/adr_truth_kernel_2026_10_02.md` (measured: 23% of 128 incidents, κ 0.91). First `hs_substrate_skew`, `watchdog_probe_core`, `rns_init`, `service_check`. Every truth-path check also ships one test that feeds an input where check and subject DISAGREE (F_SHARED = 27%). Compiled rewrite REJECTED (2%); compiled kernel not now (scope 10/23).
- **POS-4** [MF] F — fleet posture P4: UPS auto-trigger (posture by REASON, `plans/fleet_posture_controls_2026_10_05.md`; P1–P3 shipped 10-05 `59257aaf`/`1b0f672a`)
  Waits for cabling (operator 10-05: work in progress; nearest Pis to a UPS are VolcanoAI + kiai, or the yurt — Starlink + EcoFlow 1600). PHYSICS: the signal cable goes to a Pi POWERED BY THAT UPS, and the switch/AP must outlast the Pis or the central trigger cannot reach them. APC feeds VolcanoAI, alaula, moc5, moc, hAP, 3 switches, router; no APC on USB anywhere yet (measured 10-05). moc2 + moc4 share one NON-UPS power strip (operator 10-22): a short cut there rebooted both 10-19 ~07:00 (journals end abruptly 06:59:25 / 07:00:06, both back 07:00:26 / 07:00:31; no other box rebooted).
- **POS-5** [MF] F — dormant posture: the remaining consumers (batches 1–3 shipped 09-01 `fe220772`/`a6cbfaf1`/`4b0e8b4d`). Reminder `dormant-posture-consumers-remaining`.
- **NRG** [MF][MA] S+F twin→MA — NOC read grant + `box_truth/v1` export: any center reads any box once the box GRANTS it a restricted ssh key (`restrict,command=<export>`)
  Spec `plans/noc_read_grant_2026_10_13.md` (grilled Q1–Q18). Steps 0–2 DEPLOYED 10-14; NEXT = non-author pass on step 2, then 3–8; then MA port + TUI both apps, minor bump BOTH (MF 0.7.0-beta, MA 0.3.0-beta — re-check, MF is already 0.7.0-beta). Adds `box_truth/v1` to gate 1. Accept: MA `/fleet` 8/8 with moc3 `ssh_spool`; `ssh -i <noc key> moc3 bash` returns JSON, never a shell.
  public: A central MeshForge or MeshAnchor can read the status of any box you grant, through a key that can only export status, never open a shell.
  status: planned
- **ADDR-1** [MF][MA] S+F twin→MA — Mesh→RNS `@address`: a mesh user can START a private conversation (GRILL before build)
  A DM to the gateway starting `@<name|hash-prefix> <text>` resolves to EXACTLY ONE RNS destination and goes as one private LXMF send that opens the session; no match / ambiguous → refuse + on-air notice, never a fan-out. Open: name source + collision rule; syntax clash with MeshCore's `@contact` and the bridged-text `@` parse; peer-GATEWAY hashes addressable (default no); 200-byte budget; MA parity. Accept: handheld DM `@<known name> hi` → arrives at exactly that inbox, reply comes back as a DM; `@<unknown>` → notice, nothing sent.
  public: Let a mesh radio user start a private conversation with someone on Reticulum by name.
  status: planned
- **R4** [MA][MF] S+F twin→MF — RNS over MeshCore (R3 decided GO 10-05): radio ownership solved in the MA supervisor, then the SMCI transport
  Candidate `afit21/Reticulum-Smart-MeshCore-Interface` 1.0.0; lands MA-first (its radio), contract shared; R5 containment BEFORE it ships to anyone. Reminder `smci-gates-r4-r5` (paused to 12-14).
- **DEDUP-SUB** [MF][MA] F twin→MA — cross-gateway coordination-substrate suppression (the arc that actually SUPPRESSES the live cross-gateway dup that dedup STEP 6 proved). Reminder `dedup-coordination-substrate-arc` (blocked, 10-30).
- **ORC-1** [MF] S+F — oracle-leg residuals: (1) id-less fallback dedup — decide from the GATEWAY journal on moc + moc3: `journalctl -u meshforge-gateway --since "2026-10-22 14:54:07" | grep "fallback key"` on moc (moc3: `--since "2026-10-22 14:54:36"`; both HST, the gateway restarts onto `82e7f30c`; the unit is `meshforge-gateway`, NOT `meshforge`; ⚠️ the window now spans a SECOND gateway restart, the DET-WEDGE deploy: moc 2026-10-23 00:04:09, moc3 00:04:39 — read across it, the counters reset there); zero counts only if the same window HAS INFO lines (`-p info..info | wc -l` > 0) and the gateway stayed up receiving MQTT → close as moot; the first drop is always written, totals are a lower bound; (3) reply channel by name (low); a real multi-hop query answered by moc3's tap after a reconnect is still unwitnessed. Reminder `oracle-leg-hardening-arc-2026-07-05` (11-05).
- **RPCK** [MF] F — rpc_key wizard live break-and-recover on moc4 (operator present, no roll in flight, space rnsd restarts #69). Reminder `rpc-key-break-test`.
- **TUI-OWES** [MF][MA] S+F twin→MA — the TUI must not fall behind what we can do (hfm #11; operator 10-03). Open rows:
  #4 SDR slot occupancy; #4b delivery above vs below the 25% knee; Channel Load follow-ups (per-module knee, duration estimate, sample-set bias); TX Power Truth admin-shutdown note; audit follow-ups; #6 RNS ingress pane (highest value); #7 gateway posture sentence in-app; #8 field-kit uplink row; #9 SDR receiver-blind reason; #10 paging-health row. Read-only, local-box-only, two contextless reviewers before first deploy.
- **PULSE** [MF][MA] S+F twin→MA — traffic_pulse residual defects (reviewer B, tri-state adopter #3): #4 `_mqtt_node_facts` freshness gate; #6 negative clock deltas clamp to "fresh"; #7 lifetime `circuit_open` drives a present-tense ALERT; #8 `_diag_seen` ignores watchdog coverage; #9 `installed:false`+`read_error` reads "not installed"; #10 "0 duplicates" on zero ingress should read QUIET; #11 unobservable queue vanishes; handler cosmetics.
- **UNIT-1** [MF][MA] S+F twin→MA — unit-file follow-ups from the 10-21 trap fix and four review rounds
  [first] `stayed_up()` FAIL→UNKNOWN on an empty post-restart `show`; `_waived_pipx` keys on the full scoped label; `exec_program` reads through `bash -c`/`env` wrappers (MA's daemon/map); report UNKNOWN and FAIL both, never `max()`; launcher-variant boxes need a repair route; `_fixes` for shadowed; a symlink at a templated name is its own state; render after the LAST empty `ExecStart=`; reset-idiom compare. Not blocking: orphan links; install_noc/update.sh copies outside the plan; copy2 vs cp mode/TOCTOU; `svc_cls.unit`; raw RENDERED unit silent in update.sh; install_noc `echo -e`; regex/placeholder edge cases (#7, #9).
- **INST-1** [MF] S — installer is not role-aware (gate 7): `install_noc.sh --client-only` starts meshforge-map + installs mosquitto (absent for `lab`), installs no watchdog; map's stop hook needs `sqlite3`. Read the role, or document `provision_role --apply` as the step.
  Also (lab mini delta review 10-22): only `update.sh` installs `95-meshforge-journal-cap.conf` (`Storage=persistent`), so meshforge-lab — never update.sh'd — runs Pi OS's `Storage=volatile`: `/var/log/journal` empty, every reboot wipes history, and `user_timer_unit_failing` sits `indeterminate` on the once-a-day dream timer. The installer must lay the same drop-in.
- **ENR-1** [MF] F — `fleet_sync` mini enrollment: a box whose mini never ran gets no `mini_dudeai.env` and no live rules file (seeded by hand on the lab). One enrollment script, test-first.
- **FREQ-1** [MF] S+F — `utils/frequency.py` `US_SLOT_MAP` is wrong (slot 20 → 920.625 MHz; the firmware formula `meshtastic_modem.channel_centre_mhz` gives the fleet's 906.875 / 905.75). Find consumers, test-pin the fleet literals, replace with the formula.
- **C21-W** [MF] S+F — mesh_bridge does not detect a leg whose serial link WEDGES without closing (only `connection.lost`); C21 reconcile would read it as RF loss.
- **MC-Q** [MA] S+F — MeshCore ingress `queue.Full` paths (meshcore_handler ~708/~879) WARN per message, uncounted — MA sibling of MF `ff94052a`.
- **GIT-OWN** [MA] F — find the writer that leaves root-owned objects in meshanchor-server's `/opt/meshanchor/.git` (fleet_pull `HEALED(46)` after 10-19 — re-poisoned every deploy is not clean).
- **CRONF** [MF] F — `cron_verdict_freshness.sh` reader passes: 5 fail-opens fixed (`7173d488`…`67adc7cc`), still open — the operator's call on #5 (an unanswered remote box under OK). Reminder `cron-freshness-fourth-pass` (11-03).
- **MAPS-AUD** [MF] S+F — deferred findings from the 07-05 maps-domain QA audit (44 found, ~30 fixed). Reminder `maps-audit-hardening-2026-07-05` (10-28).
- **ECOMM** [MF] F — ECOMM kit (OpenWrt One + USB MeshToad + Starlink Mini) hot-swap made real (operator 09-15: in and out of the fleet). Reminder `ecomm-kit-hotswap-posture`.
- **REV-SEC** [MF][MA] S+F — non-author reads owed (security-relevant): BIC-3 N1–N3 (MF `security(web): BIC-3 non-author N1-N3` + MA); the 10-07 identity arc fixes (MF `fix(identity)` + MA); the :4403 LISTEN fix (MF `fix(radio)`; MA port decision open)
  Provenance § QUEUED 2026-10-15 ×3. `code-review medium <range>` from the main session.
- **FLEET-S1** [MF][MA] S+F twin→MA — `/fleet` slice 1 + witness-test residuals, "not fixed, by choice" across review rounds 1–6 (10-20/21)
  [first]: MA witness test vacuous + `if named_here: return` skip; absence regex column-0 anchored (indented METHOD passes); presence half measures DEFINITION not REGISTRATION; absence half has no shape check; honest_status FAIL/UNKNOWN headlines count audit-only boxes AT HEAD; pre-push "exit 0 must say in sync" is a string match (refuse/exit 2 on zero compared, in `parity_check.py`). Full lists: provenance § QUEUED 2026-10-20 (rounds, round 5, round 6, round-6 fixes) + 2026-10-21 (witness-test fix).
- **INBOX-2** [MF] S+F — inbox follow-ups from the operator's 10-21 iPhone report: a channel post the fleet demonstrably heard shows "handed to radio · unconfirmed" FOREVER; the phone lost its web login with no pairing code on screen (server session intact)
  Provenance § QUEUED 2026-10-21 evening.
- **CONNECT-1** [MF] S+F — "How to connect" card (TUI + /fleet): our RNS TCP transport endpoint, network name, propagation-node address, allowlist posture — what a new Sideband / MeshChatX / NomadNet user needs (operator 10-21 "queue 1")
  public: A screen that tells a new Reticulum user exactly how to connect to your node.
  status: planned
- **MCX-PIN** [MF][MA] S+F — pin MeshChatX's version: `install_meshchatx.sh` takes `releases/latest` from a third-party repo; pin a known-good tag, canary new releases (operator 10-21 "queue 3")
- **DET-CFD** [MF][MA] S+F twin→MA — `channel_feed_dark` reads "dark" for a channel that was never uplinked — fix test-first (detector finding 10-14)
- **DET-DCS** [MF] F — `delivery_confirmation_stall` goes blind for DAYS after every gateway restart on a low-traffic box: the `recent_terminal` ring (`delivery_counters.py`) is in-memory and needs 20 confirmable terminals — fix test-first (detector finding 10-22, moc3 mini delta review)
  Measured on moc3: blind 09-22→09-28 (~6 d), 10-12→10-16 (~3.6 d), and again now (6 of 20) after the 10-21/22 deploy restarts — a total confirmation collapse cannot be seen for days after any deploy. Honest (`indeterminate`, not clean), but a probe indeterminate for days is a finding. Cure shape: persist the terminal ring across restarts (write the snapshot, reload on start), and/or a traffic-scaled minimum. Accept: restart the gateway on a box with a full ring → the probe judges on the next tick, never `indeterminate` from the restart alone.
- **DET-WEDGE** [MF] F — `probe_lxmf_process_wedge` (`watchdog_probes_rns.py`) calls a lab load-run's own transient `unix_wait_for_peer` an rnsd wedge, and its cure text says "restart rnsd" (the #69 race trigger) — fix test-first (detector finding 10-22, moc mini delta review)
  Measured on moc: 58 lifetime `main_thread_wedge_any` fires on `lab.lxmf_multi_user_synth`, ALL at :08 (synth-soak timer `*:07`), mean active ~30 s, self-clearing, 1–2/day 10-06..10-19, none since (unexplained). moc3 (same review): `lab.lxmf_tracer` + `lab.lxmf_echo` fires cluster at minute :x0 — the 10-min tracer timer, so the long-running echo blocks while the tracer drives it; not only short load runs. One-snapshot signal, no confirm ticks. Cure shape = the RNS-RPC sibling (`eb8a6529`): N consecutive ticks on the SAME pid/tid before signalling, and cure text that says "check `rpc[rnsd.` round trips first", never restart-first. Accept: a planted 30 s block does not fire; a held block on one tid fires within N ticks.
  CODE LANDED 10-22 (lxmf leg; non-author pass, provenance row 2026-10-22): per-(pattern, pid) streak, knob `MESHFORGE_LXMF_WEDGE_CONFIRM_TICKS` (default 5 — measured: the longest transient was moc3 echo at 105.8 s, about 4 ticks), confirm-first cure text shared by BOTH legs (leads with the gateway rpc journal; rnstatus 124 may be CPU starvation). Live A/B on real `/proc`: old 4/4 one-tick runs fire, new 0/4; a held process fires on tick N. Two non-author rounds (provenance row 2026-10-22). DEPLOYED 10-23 00:04 HST (`213c6952`, after a second non-author round; full honest_status 9/9 rc=0). Live on moc3's RUNNING watchdog: a planted one-tick `lab.lxmf_echo` block read `indeterminate` (named, 1/5), no signal, then clean. OWED: watch moc's :08 and moc3's :x0 for a week (no `main_thread_wedge` fire); the unit leg `probe_main_thread_wedge` still judges one snapshot (no false fire measured there).
- **CAP-1** [MF][MA] S+F twin→MA — capture / topology truth residuals (09-26): TUI Export refuses on every box (never runs the tracker) — redirect to the map service's data or retire; `5c61ba30` redelivery dedupe disproven — keep or remove; flaky wall-clock test `test_spawner_returns_fast`; MA twin gaps (`graph_observable`, shared-DB header, coverage `source_origin`, tile-cache wording)
  Provenance § QUEUED 2026-09-26 ×4.
- **MAPV-1** [MF][MA] S+F — operator call (09-26): meshanchor-server `/api/nodes/directory` serves ~50k world MeshCore features and the dev box federates them into a 29.8 MB `/api/nodes/geojson` (46 in Hawaii) — region-filter at the source or at federation
- **REV-0925** [MF][MA][maps] S+F maps:both — finish the non-author read of the 09-25 messaging/RNS/map range (`c98dd56b..151eaa00`, MA twins, maps `5477d56`+`f8e8d2a`); partly read 10-08, `151eaa00` read 09-28, CSP + rnsd key read 10-15. Provenance § QUEUED 2026-09-25.
- **M2R-MA** [MA] S+F — MA port of M→R accounting (no attempted/delivered/dropped triplet in MA) + the monotonic dedup (mesh_bridge :358/:772/:1077, mqtt_bridge_handler :1024; heartbeat/EAS twins unchecked); MF leads
- **Q-CLAIM** [MF][MA] S+F twin→MA — persistent-queue claim races (found by the BIC-2 slice-2 non-author re-review, 10-23; PRE-DATE BIC-2): (a) a slow worker's late `mark_failed` lands on a row `cleanup_stale` already reset to pending (and maybe re-claimed), bumping retry_count twice for one attempt; (b) a late `mark_delivered` flips a dead-lettered row to delivered after its DROPPED was counted (leg shows both; `_fire_terminal` fires twice); (c) `mark_failed` is read-modify-write with no compare on the retry_count it read. Cure class: a per-claim token (or compare-and-swap on status+retry_count) so only the current claimant can resolve a row — design first (grill), then test-first. Added by a session (reason: correctness of the queue the BIC-2 legs read from).
- **LOG-1** [MF][MA] S+F — the first `get_logger()` in a process clears root handlers a host configured another way (`setup_logging` `_initialized` guard). Provenance § QUEUED 2026-10-18. (The second half — late `mark_failed` writing stats/DROPPED before the guarded UPDATE, and the message_queue.py 1,500-cap mixin split — DONE 10-23 in BIC-2 slice 2: `09531b7d` split, `97219b8d` + this round's commit gate; the wider claim races moved to Q-CLAIM.)
- **AUDIT-1018** [MF][MA][maps] S+F maps:both — domain-audit residuals (10-18): OPERATOR decisions — Substack link (two live), moc5 + kiai declared `collector` but the maps unit is NOT INSTALLED (install, or fix the role), #151 F4 (MA bridge auto-enable only with a LOCAL meshtasticd — changes WHICH services run); design tasks — a serial-pinned, group-scoped MeshCore udev rule (today generic + 0666), a human surface for maps `/api/core-health`
- **SEC-URL3** [MF] F — urllib3 GHSA-vxq7-64xx-v4gw + GHSA-8988-9cw3-xx77 on the apt copies (moc2, lehua, moc4): wait for the Debian backport, never pip over apt. Reminder `urllib3-apt-backport-moc2-lehua-moc4` (11-12).

### LATER

- **M1** [MF] S — mesh-issue rules for the *user's* mesh (MeshMonitor M1), with one thresholds file and a test that forbids harmful advice
  public: Plain-language advice about problems on your own mesh, from one reviewed set of rules.
  status: planned
- **M5** [MF] S — the no-radio `monitor` profile on a plain VM / Debian host (= 1.0 gate 7; a VM install, NOT a container — D2, 2026-10-18); an OpenAPI spec over the map service
  public: Run MeshForge's monitoring on a plain Debian machine or VM with no radio attached.
  status: planned
- **M2** [MF][MA] S+F — virtual-node / one-owner-many-clients for the radio (R3 is GO)
- **R5** [MF][MA][fork] S+F twin→MA — contain rnsd plugins regardless of R3: a deploy-time manifest of what rnsd auto-loads from `interfaces/`, and an import failure must not kill rnsd (the moc3 zombie-plugin class). Reminder `smci-gates-r4-r5`.
- **R7** [MF][MA] S+F — RF-window capture header: every RF measurement window starts with an effective-settings snapshot (rate, SF/BW/CR, noise, interference) — the 09-22 stored-params reversion would have shown in one. Rides R8.
- **M4** [MF][MA] S+F — bots back off on a busy channel: oracle/echo responders gate on the radio's reported channel utilisation
  public: Automatic responders wait when the radio channel is busy.
  status: planned
- **M6** [MF][MA] S+F — pin wire formats to real bytes: fixtures generated from the installed library, never hand-written keys (the 09-18 `channel_idx` class)
- **R6** [fork] S offer-back — upstream to SMCI: drafts only; posting is the operator's call. Reminder `research-unledgered-recs`.
- **UPL-1** [MF] F — uplink HEADROOM on `/fleet`: read the dish's own `get_history` ring buffer (per-second down/up, PoP latency, drop) and render peak/p95 beside the current line — `get_status` is one sample (read 0.5 Mbps while video streamed). Read-only.
- **M2R-WAKE** [MF][MA] S+F twin→MA — wake-on-announce: make a returning peer's deferred M→R rows due at once — only if the 6f soak shows the ≤5 min cap matters. Propagation handoff SHELVED 10-17 (no consumer; design on file in the 6f provenance row; reopen only for a MEASURED intermittent peer that fetches from `3968a2ee`).
- **JSONL** [MF][MA] S+F twin→MA — ONE append-only JSONL posture, `utils/jsonl_io.py`; move every copy onto it (`mini_dudeai/history.py`, `scripts/sdr_interference.py`, …) (operator 09-24 "queue B").
- **HX-INERT** [MF][MA] S+F twin→MA — the inert-tier CUT (operator 10-22 "your call": keep, reframed): measure every detector class's disposition on every box; a class `inert` on ALL boxes is cut (with its tests + docs), a class `clean` anywhere is kept — the footprint rule (`clean` everywhere = KEEP, `inert` everywhere = CUT); removal only, never a new instrument
- **BOT-HW** [bot] S — verify the companion client on real radios: serial (USB disconnect/reconnect, T-Beam / T-Echo / Heltec, baud detection), TCP to meshtasticd (local + remote), MQTT (public-broker flow end to end, topic parsing, custom brokers), BLE (Linux + Pi discovery, pairing lifecycle, several devices)
  Code for all four transports exists; none of these runs is recorded. No fleet box runs this client (measured 10-22, 0 of 11 — lehua's bot is the `meshing-around` fork), so this is a tester's item.
  public: The meshing_around_meshforge companion client (terminal UI over USB, TCP, MQTT or Bluetooth) needs testing on real radios.
  status: testing
  help: any Meshtastic radio over USB, TCP, MQTT or Bluetooth — connect the client, then unplug and replug or drop the network, and report what it showed
  The client's troubleshooting guide + per-OS setup notes get written FROM these reports (all that survived BOT-DOCS, won't-do 10-22).
- **BOT-DATA** [bot] S — client data-model checks owed: message-history limits, encrypted messages, node online/offline accuracy + timeout thresholds, telemetry from real devices, a 50+ node mesh, proximity (haversine) zones, alert severity escalation
- **BOT-TUI** [bot] S — client TUI checks owed: terminal sizes + colours, a test for the `PlainTextTUI` fallback (it exists, untested), Unicode/emoji, keyboard + shortcuts, the send flow end to end, long paste, CPU and memory over 24 h (message-history leaks), screen-rendering + web-API endpoint tests
- **BOT-NOTIFY** [bot] S — notification checks owed (code exists): Gmail + custom SMTP, rate limiting, quiet hours, SMS via email gateways / Twilio / HTTP gateway, phone-number validation — needs accounts we do not run
  public: Email and SMS alerts from the companion client need testing with real mail and SMS providers.
  status: testing
  help: an SMTP account or an SMS gateway you already use — send a test alert, check rate limiting and quiet hours, and report what arrived
- **BOT-CFG** [bot] S — client config: a malformed-config test, migration from older config versions (MF gate 2's pattern: refuse a newer file loudly, keep a backup), the update workflow on a Pi Zero 2 W
- **BOT-UP** [bot] S — upstream parity: interface2…9 multi-radio in the TUI + a combined dashboard; highlight bot commands + response previews; parse upstream `trap_list`; `[sentry]` radius detection matching upstream (interface2 + sentry code exists; behaviour vs upstream unverified)
- **HX-UNIT** [MF][MA] F — the unit-file divergence layer (queued 10-06): option B (`fleet_sync` REPORTS effective `systemctl show` vs the template's key directives, changes nothing). Partly answered by the 10-21 per-unit-name audit + TUI pane; remaining = the effective-directive diff.

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
- The superseded `plans/v1.0_roadmap.md` copies (MF + MA) were DELETED 2026-10-22
  after a line-by-line diff into "1.0 = these checks pass" (cohesion Q13); what
  was not carried is listed in that day's provenance row. `git log --all --
  .claude/plans/v1.0_roadmap.md` recovers them.

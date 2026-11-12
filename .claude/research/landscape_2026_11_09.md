# Landscape + critique audit — 2026-11-09 ("Why would a user choose us over N?")

> **Verdict:** the critique's direction holds and matches the 30-day ship plan
> (per-message view, a release, the first-run path). Its strongest claim for us
> does NOT hold as a public sentence yet: a review by running found the two-leg
> bridge and the inbox both say more than they saw (§B, §C). Two of its upstream
> "facts" are refuted at source (§A). **Nothing here is fixed; this is the record
> and the queue.** ROADMAP: `BR-T`, `INBOX-T`, `SHOW-1`.
>
> Audited: <https://claude.ai/artifact/7NMLULS6Bf1ZURjRdcEYC9> (cloud session,
> read a clone, saw no live box) and the roadmap page
> <https://claude.ai/artifact/UL62HQMSLZrKnejKkoo4Ud>.
> Lab: VolcanoAI, Fable 5.2, 2026-11-09 23:20–23:55 HST (`date`), HEAD `cb4008ab`.
> Nothing on the live fleet was touched or measured (no ssh, no service contact).
> Numbers below are that night's; re-measure before quoting.

## A. Claim audit

Tags: VERIFIED (checked tonight, source named) · CORRECTED · REFUTED · UNKNOWN.

### About us

| Page claim | Tag | Measured |
|---|---|---|
| Two-leg bridge accounted for 1,436 of 1,436 in one 24 h run on our radios | VERIFIED as a record, not re-run | `docs/capabilities.md:44`, provenance C21 `c21r1` 10-19→20, reconcile rc 0. The page drops the caveats: serial+serial lab radios at ~1 dBm, hop_limit 1, broadcasts; the HAT/meshtasticd leg is unproven. And see §B #9: the reconcile labels up to 5 % bridge loss per leg as RF loss |
| "dedup by packet ID instead of a text tag, DMs refused instead of fanned out" (the page says it of the MT↔MC edge) | REFUTED for `mesh_bridge` (§B #2, #3); MT↔MC edge not reviewed tonight | `mesh_bridge.py:71-74` key = sender + md5(text); DMs are forwarded at HIGH priority |
| Per-message view due 11-24, not shipped | VERIFIED | ROADMAP BIC-2; `web/inbox.html` renders no leg; §C: no per-message join exists |
| 1.0 excludes the fleet offering | VERIFIED | ROADMAP "1.0 = these checks pass" |
| "76 probes, about 15,000 tests" | CORRECTED | 75 top-level `def probe_*` in `src/` (77 with nested); 18,755 tests collected |
| One developer, one release tag, no outside issues, 22 stars | CORRECTED | 25 stars, 0 forks; 6 issues, all the operator's; 1 version tag (`v0.7.0-beta`, 10-15) and **0 GitHub Releases**; HEAD is 557 commits past the tag |
| Fork based on RNS 1.3.9; upstream 1.6.0 (11-07) | VERIFIED | `requirements/rns.txt` `MF-FORK-PIN rns 1.3.9+mf.0`; GitHub release 1.6.0 2026-11-07 |
| "11 releases behind upstream" | CORRECTED | 12 PyPI versions after 1.3.9 (1.4.0–1.4.2, 1.5.0–1.5.7, 1.6.0) |
| "current clients need 1.5.7 or later" | REFUTED for Sideband | `markqvist/Sideband` `setup.py`: `rns>=1.2.0`, `lxmf>=0.9.6` (default branch, 11-09). Other clients not checked. Interop with stock is still owed on real hardware (BIC-4) |
| No screenshot or demo of the unique thing | VERIFIED | README images = the logo + 4 badges |
| The 11-04 cross-audit was refuted on 6 of 16 claims | UNKNOWN | no such tally in `review_provenance.md` |
| Fleet view is wired to our own fleet's config | not measured | consistent with ROADMAP ("full of our own config") |

### About them

| Page claim | Tag | Measured (GitHub API, 11-09) |
|---|---|---|
| MeshMonitor v4.17.2, 11-03 | VERIFIED | published 2026-11-03; 726 stars |
| MeshMonitor: 6 stable releases in 60 days | CORRECTED | 4 non-prerelease GitHub Releases since 09-10 |
| MeshMonitor ships "unconfirmed, not failed" | VERIFIED | `docs/features/delivery-diagnostics.md`. The four per-field labels were not found by search: UNKNOWN |
| ~79 % of MeshMonitor commits carry a Claude co-author line | CORRECTED (window) | 70 of the last 100 commits |
| CoreScope v3.16.0, 10-29 | VERIFIED | 209 stars |
| meshing-around 538★ | VERIFIED (544 now) | one stable release since 09-10 (2.0.0.1) |
| MeshSat pre-release, "never deployed to a real user" | VERIFIED | its README lines 38-39; no stable release |
| The comparable preset bridge (IceNet) dormant since Dec 2025 | VERIFIED | last push 2025-12-13, 6 stars. Its README says it dedups by packet ID; ours does not (§B #3) |
| **Firmware 2.8's Long Turbo default will make the preset bridge a common need** | **REFUTED at source** | `meshtastic/firmware` `v2.8.2.d5f1619` `src/mesh/NodeDB.cpp:1192` still sets `ModemPreset_LONG_FAST`; every 2.8 tag is a pre-release (stable = 2.7.26). The need is real on OUR fleet (two presets by design); "common soon" has no basis |
| mesh-api, AMMB | not re-checked | see `landscape_2026_11_04.md` |

### The roadmap page

VERIFIED current: the live page differs from a fresh `scripts/roadmap_html.py`
run in ONE line (the generated timestamp). Built from ROADMAP `a243e8de`, 75
items (6 / 43 / 26), item rules clean. No hand edits.

## B. Review by running — `mesh_bridge` (the two-leg preset bridge)

> **Progress 2026-11-12:** #19–#21 + #22/#23 reverts HOLD (r4, r5) · R4-F1..F3 fixed
> test-first MF `c6f6cea9` `429a00b9`, MA `bf445460` (CI success) · **round 5 NOT CLEAN on
> tests** (R5-F1..F3) → leg-state tables `924cce9e`/`eee7722f` → **round 6 NOT CLEAN on tests
> (R6-F1..F5)**; src unchanged since `3d912a7f`, held r4–r6 · **loop STOPPED (operator 11-12)**:
> r6 scenarios adopted MF `5c5be03f` / MA `3c5ad2fc`; emulated drill at HEAD PASS 2/2, pre-fix control
> FAIL · NOT deployed (fleet `d185bfd2`) · next: the deploy = operator's call.

> **Progress 2026-11-11:** #4 #5 #6 #14 fixed — v1 `bf5a3b90` → review r1
> (code-review medium: ready_fn ran ahead of downlink + dual-path dedup) → v2
> `16f97fa7` → Fable r2 by running (MQTT leg not covered; episode log) → r2 fix
> `3e6f8156` → Fable r3 by running: the MQTT part REVERTED (#14b queued) ·
> CI success on `bf5a3b90` + `73334e8c` · NOT deployed (brake: no same-session
> self-deploy of a review fix) · MA twin not
> ported · **end-state Fable review CLEAN vs the 10-item spec (11-11)**; #18 found
> (pre-existing, thread-killing DM) · next: #18, #2 DM refusal / #3 dedup by packet id (design: ask operator),
> #7 #9 #15–#17 T-15, r2 residuals F-4/F-5.

Non-author Fable reviewer, scratch copy, fake radios, no live contact. Targeted
tests: 155 passed, rc 0. The main session re-ran `d1_false_green.py` and read the
cited lines for #1–#3. CONFIRMED = a driver printed it. Drivers were in the
session scratchpad (tmpfs; not kept) — each row names the input, so it can be re-made.

| # | Finding | Where | Input → what the bridge said |
|---|---|---|---|
| 1 | **A failed radio write is counted and logged as sent** — **FIXED 2026-11-10** `6fe286ef` (test-first; `tests/test_mesh_bridge_refused_write.py`); non-author Fable review by running 11-10 (10/12 mutants killed; the 2 survivors pinned since); **DEPLOYED 10/10 11-10 04:45**; MA twin `7906bc93`. Holds for the MQTT leg ONLY — TCP/serial: #10 | `mesh_bridge.py:1151-1162` discards `sendText()`'s return; `_mesh_bridge_mqtt.py` signals failure by `return False` | MQTT/HAT leg whose send fails → `messages_secondary_to_primary: 1`, a `tx` line, queue empty. A 230 B broadcast that the tag pushes to 249 B is refused, never sent, and still booked delivered (matrix F17b, worse than recorded) |
| 2 | No DM refusal exists | `:909-915`, `:1153-1158` | a DM is forwarded to the other leg at HIGH priority and counted; no refusal, no counter. (Matrix MF-6 says "DM vs broadcast KEPT" — the claim is wrong, the code matches the matrix) |
| 3 | Dedup is sender + text, never the packet id; the loop guard is the text tag | `:71-74`, `:850-867` | two different packets, same sender, "ok" → second dropped `duplicate`. Alice's "ok" then Bob's "ok" → Bob's dropped `dual_path` while the queue books 2 delivered. `add_prefix=False` removes the loop guard |
| 2-D | **DECIDED (operator 11-11): DMs are CARRIED — as an ADDRESSED RELAY, refuse-by-default.** A DM to the bridge node starting `@<name\|!id> <text>` is re-sent to EXACTLY ONE node the far mesh has heard (wire fact: heard-by/node DB), else a named refusal; DMs the bridge merely overhears are never fanned out. Labels stay per-leg honest: "handed to the far radio · unconfirmed" until BIC-2's cross-leg ack design exists — never "delivered". Same shape as the RNS `@`-relay item. **First step is a LAB EXPERIMENT, not code:** a PKI DM LongFast→SHORT_TURBO, watch what moc's bridge receives (BELIEVED: PKI DMs are unreadable to the bridge radio — measure it); control: a channel broadcast that must cross | operator: "a NOC carries DMs"; scope by physics + crypto (the bridge carries only what its radio can read) | — |
| 3-D | **DECIDED (operator 11-11, "your call"): bridge dedup keyed on `(from, packet id)`** — the wire fact for "the same packet heard twice"; text hash only as a fallback when no id exists. The cross-PATH layer (a re-originated copy via RNS/MQTT carries a NEW id) stays the content-based dual-path registry, named and windowed. Every drop names WHICH layer made it. Lab test: same node sends "ok" twice 5 s apart → both cross (today the 2nd is dropped); one packet via two relays → crosses once; control: dual-path suppression still fires | guess vs measure vs physics: packet id = measured, text equality = a guess | Order: #3 before #2 (smaller, pure correctness), both after deploy + MA port |
| 4 | One-way `direction`: the reverse message is accepted and sits pending — **FIXED 2026-11-11** `bf5a3b90`: one rule `_direction_allows()` for RX gate + drain; ingress drop `direction`, `direction_filtered` | `:776` (only `_bridge_loop:500-505` checks) | `rx` line, no stat, row pending until shed at 1000; DMs never shed |
| 5 | Broad `except` in `_process_receive`: no counter, no drop line — **FIXED 2026-11-11** `bf5a3b90`: `receive_errors` + `drop … receive_error` | `:924-925` | injected sqlite fault → `rx` line + ERROR, `stats: {}` |
| 6 | Queue-full is named `queue_dedup` — **FIXED 2026-11-11** `bf5a3b90`: `enqueue_with_reason()`; drop `queue_full`, `queue_full_rejected`, WARNING | `:917-919` | wrong reason, no bridge counter |
| 7 | Queue dedup is wall-clock | `message_queue.py:365-378` | a repeat 10 min later after a 1 h backward step → dropped |
| 8 | **Default (TCP) leg receives nothing after connect** — **FIXED 2026-11-10** with #10–#13 (one seam `_attach_stream_listeners`, shared with serial; `tests/test_mesh_bridge_tcp_leg.py`); 6 Fable review rounds by running, round 6 CLEAN; **DEPLOYED 10/10 @ `33743347` 11-10 09:57, honest_status EXIT 0 11:14**; MA twin of #8–#13 not yet ported | `:725-728`: listener closure weakly held, no keepalive (the serial path has one) | 0 packets reach the callback; control with a held listener gets 1. Also no interface filter |
| 9 | The C21 reconcile passes with real bridge loss ≤ 5 % per leg | `scripts/c21_bridge_run.py` `reconcile`/`_label` | 2 of 40 lost before the rx line → PASS, labelled `rf_ingress_loss`. Ingress/egress are witnessed only by the bridge's own log |
| 10 | **A dead TCP leg is still booked as sent** (11-10 review of #1) — **FIXED 2026-11-10** with #8: tcp leg hears `connection.lost`; send-time `_write_target_gone()` reads what the library's `_writeBytes` gates on (`socket`/`stream`) and REFUSES (queue retries) — it no longer tears the leg down (r1 review F1: that left a zombie socket in 19/20 drills; now 0/20); teardown is one atomic `_take_leg` (F2: 21 counts for 20 losses → 2 for 2). Residual UNKNOWN: a write racing a mid-call socket drop | `mesh_bridge.py` `_forward_message`; meshtastic 2.7.11 `TCPInterface._writeBytes` no-ops when `socket is None` (a failed `_reconnect()` leaves it so) and `_sendPacket` returns the packet anyway; `connection.lost` subscribed for serial only (`:625`) | real `TCPInterface` vs a loopback that accepts then dies, 6 forwards → 5 booked sent with `socket=None`, `_primary_connected` stays True. Serial: same no-op, only before `on_lost`. Pre-existing; fold with #8 (same leg) |
| 11 | **TCP leg never released the connection lock** — FIXED 11-10 with #8 | `_connect_tcp` holds `MESHTASTIC_CONNECTION_LOCK` (plain `Lock`) "until disconnect"; neither `_disconnect_*` released it, and the loop's `except` branch dropped the leg without a teardown | old code: lost → reconnect waits 10 s on its own hold, `lock busy`, forever |
| 12 | **TCP leg ignored its port** — FIXED 11-10 with #8 | `TCPInterface(hostname=…)` without `portNumber` | a `:4404` leg connected to `:4403` — the other leg's radio |
| 13 | **Two TCP legs can never both connect** — **FIXED 2026-11-10** (operator: "go with your advice" = option (a)): ONE rule `connection_lock_for(host, port)` in `utils.meshtastic_connection` — the local meshtasticd (`_LOCAL_HOSTS` × 4403) keeps `MESHTASTIC_CONNECTION_LOCK` (local behaviour unchanged), every other endpoint its own lock; bridge + `node_monitor` both use it; AST gate refuses a direct `MESHTASTIC_CONNECTION_LOCK.acquire(` outside the module. Same radio on both legs is still refused, and now SAYS so. r4 + r5 reviews: deciding the endpoint from a HOST split one daemon three ways (spelling; then resolver hiccup, zone ids, multi-address names) → the lock is now keyed by PORT only (a daemon listens on one port): every spelling of one daemon = one lock; two radios on the SAME port share (over-excludes, safe, = pre-#13); no resolver, no I/O, nothing raises | one process-global non-reentrant lock held for a connection's LIFE (Issue #17 contract) | scratch probe on the fixed code: primary ok, secondary waited 10.0 s → False; the loop retries forever. The DEFAULT config (both legs TCP) never bridges |
| 14 | **A message arriving while a leg is down > ~5 s is dead-lettered** (11-10 r6 review, pre-existing, both trees) — **FIXED 2026-11-11** `bf5a3b90`→`16f97fa7` (+ r2 episode/pins; test-first, `tests/test_mesh_bridge_leg_outage.py`): the TCP/serial link-down refusals raise `SendDeferred` (no attempt spent) — MQTT leg NOT covered, see #14b;  bounded `MESH_BRIDGE_MAX_AGE_S` 600 s ASSERTED (operator 11-11 "10 min, measure later"; deferred `mesh-bridge-leg-outage-measure-2026-11-11`), witnessed `dead_letter`; status `*_held_link_down`. Residual: a drop racing gate→write still spends one attempt. Not deployed; non-author review pending; MA twin not ported | queue retry budget (`RetryPolicy.for_meshtastic`) < the 10 s reconnect cadence | serial unplug: refused at +0.26 s and +5.4 s, then `dead_letter unknown_error_exhausted`; never delivered after replug. Also r6: one unnamed thread leaks per reconnect (origin UNKNOWN); the connection manager's TCPInterface has no portNumber (#12 shape). **11-10 MA-port review re-measured it on the TCP leg** (radio down → `not connected` → dead-letter `unknown_error_exhausted` at +5.1 s, leg back at +10.3 s, never delivered); the `_forward_message` comment "REFUSE — the queue retries" overstates it. Fix direction: a transport-down refusal is a RETRIABLE error, not `unknown` |
| 14b | **MQTT-type leg: a meshtasticd outage still dead-letters in ~5 s** (Fable r2 11-11; the #14 hold covers TCP/serial only) — r2 fix `3e6f8156` (infer "unreachable" from the send's failure shape) **REVERTED** after Fable r3: it held a reachable daemon's HTTP 500s for 600 s, missed a 2nd outage (stale session `is_connected`), and each re-ask blocked the one bridge thread 5–10 s for BOTH directions — QUEUED as a design item | `_mesh_bridge_mqtt.py` `sendText` (all failures = bare False); `meshtastic_protobuf_client` session singleton never clears `is_connected` | Design direction: a DIRECT reachability fact for the leg's HTTP port, measured OUTSIDE the send path and never blocking the bridge loop; tests against the REAL protobuf client + a fake daemon (r3's drivers `a1/a5`), not mocks. Pinned meanwhile: `test_known_limit_mqtt_leg_outage_spends_the_budget_named` |
| 15 | **A leg re-attaching during an in-flight publish drops that packet for the OTHER leg** (11-10 MA-port review, both trees — the leg methods are byte-identical) — QUEUED | `_attach_stream_listeners` overwrites `_stream_rx_keepalive[name]` → the old closure's last strong ref goes while the library's publish thread is mid-`sendMessage`; pypubsub raises `BUG: Dead Listener called`, later listeners never run; the library's `DeferredExecution._run` swallows it → no bridge witness | deterministic driver: secondary re-attaches during a primary dispatch → primary callback got `[]`; stress 8–16 per ~1,000 reconnects on 4 of 4 seeds. Production frequency UNMEASURED. Fix direction: one long-lived listener per leg that checks `interface is self._<leg>_interface` |
| 16 | **A leg waiting on its port lock when `stop()` lands still dials the radio after stop** (same review, both trees) — QUEUED | no `_stop_event` check after `conn_lock.acquire` in `_connect_tcp` | `Connected to primary via TCP` logged after `Mesh bridge stopped`; handle then refused + closed, lock freed — but a full config download on the single client slot. Fix: re-check the event after the acquire, release, return |
| 17 | **`start()` refusal says "retry in a few seconds"; the old leg thread can take ~29 s** (same review, both trees) — QUEUED, wording | the library's 30 s `_waitConnected` | radio accepts TCP, never completes config: stop 5.3 s, lock held, `start()` False, thread exits 28.9 s after stop. Settles clean |
| 18 | **A DM whose `toId` the library could not resolve KILLS the bridge-loop thread silently** (end-state review 11-11, PRE-EXISTING — base reproduces identically) — **FIXED 11-11** `208d2ab5` + r2 (Fable by running: wire `to` decides alone, toId only via `node_dest` ('!'+8 ASCII hex, fullmatch) — r3 (`node_dest` = ONE validator for ingress AND the send boundary; persisted older-build rows refused) — **Fable r4 CLEAN** (`b486aaef`: 250k-input oracle, old-build rows drained over real TCP, 15/15 mutants); O1 low: a pre-upgrade row with a valid-but-spoofed stored destination ages out ≤ 600 s — closes r2 F1 `'!abc'`→`our_exit`, F2 neighbour `user.id='!ffffffff'` rebroadcast a DM, F6 bool) · not deployed | `mesh_bridge.py` `_process_receive` `to_id = packet.get('toId','')` is `None` → `sendText(destinationId=None)` → meshtastic `our_exit` → `SystemExit`, which `except Exception` does not catch | real `bridge.start()` + two real TCPInterface vs fake daemons: `bridge thread alive: False · status running: True · errors: 0`; DM row `in_progress`, later rows `pending` both ways, nothing crosses; drained queues stop expiring. Field frequency UNKNOWN (a DM normally targets the local node). Fix direction: refuse a DM with no resolvable destination at ingress (named drop) AND a loop guard that witnesses a dead bridge thread |
| 18b | **A serial leg on auto-detect with >1 USB serial device: the library `our_exit`s and the LEG thread dies silently** (#18 r2 F3, PRE-EXISTING, same class) — QUEUED, needs the MF025 split FIRST (`mesh_bridge.py` at 1500) | `SerialInterface()` with no devPath + several ports → `our_exit` (`serial_interface.py:49`); `_connect_serial` / `_leg_loop` catch `Exception` only | real SerialInterface, `findPorts` → 2 paths: `MeshBridge-Secondary` dead, no ERROR, `running=True`, leg never retries. Aside: 0 ports → `SerialInterface()` returns un-constructed and the leg reads connected. Residuals (#18 r2 F4/F5, low): `dm_no_destination` counts ATTEMPTS and a pre-fix row's terminal drop reads `dead_letter`; a caught SystemExit aborts the batch, pauses both directions 1 s, leaves the row `in_progress` unwitnessed (re-sent only by the 300 s stale sweep); counters not pre-seeded |
| 18c | **MQTT leg: the "numeric `to` decides, bool is not a node" rule is not what runs** (#18 r3 F3, PRE-EXISTING, low) — QUEUED | `_mesh_bridge_mqtt.py:176-204` turns the JSON `to` into a `toId` only and drops the number | real `MQTTMeshInterface._on_message`: JSON `"to": true` → DM to `!00000001`; `0`/`null`/`false`/absent → broadcast; str/float/list → `Error processing MQTT message`, nothing enqueued. Fix direction: carry the numeric `to` through so `dm_destination` decides; one rule, both leg types |
| 14r | **#14 end-state residuals** (same review, LOW) | — | F2 a fresh NORMAL row can overtake a held HIGH DM after an outage (≤ `LINK_DOWN_RETRY_S`, 2 s); F3 a persisted row expires on a wall step of only ~+10 min before a new bridge's first tick (not "+3 d" as the r2 note said), named; F4 one ERROR per interleaved downlink send while flag-set/socket-gone (~1 s window); F5 `s2p_held_link_down` True for a row held by DIRECTION, not link; no TUI consumer of `*_held_link_down` yet (lands with BIC-2/CLAW-1 surfaces). Cost measured (tmpfs): ~1.5–2 ms CPU per re-ask, healthy direction never starved up to 990 held rows |
| T-15 | **Test gaps the MA-port review's mutants found** (both trees) — QUEUED | survived both new files: M01/M01b (absent-radio 10 s wait removed or 1 s), M17b (port lock released BEFORE `interface.close()` in `_close_taken`), N05 (MA only: node_monitor fallback returns a fresh lock per call); nit: MA's `_on_stream_link_lost` docstring cites a c21 script MA lacks | 43/49 killed by the reviewer; my replay of MF's 96-mutant corpus: 69/69 applicable killed |
| C21-W | worse than queued | — | TX into a wedged leg is logged + counted sent (#1's path) and reconciled as RF loss |

Holds: tagged content is never re-bridged; a re-heard packet after 61 s does not
cross twice; a serial write that RAISES is counted, retried, dead-lettered;
bridge-level dedup is monotonic. Not checked: real radios, the run's own data,
restart paths, the MeshAnchor twin.

**MeshAnchor twin (2026-11-10, from 15:55 HST `date`):** #1 ported `7906bc93`; #8 #10–#13
ported `90f1d3e7` after an MF025 split `43b5c07a` (MQTTMeshInterface →
`_mesh_bridge_mqtt.py`, as MF `6262fc61`). Non-author Fable review by running: no
critical defect; #15–#17 + T-15 are its findings, shared with MF. Not deployed as
running code: meshanchor-server runs `bridge_mode: meshcore_bridge` (pull only).

**2026-11-11 (15:03–17:59 HST `date`) — MF #4/#5/#6/#14/#18 DEPLOYED, MA port landed.**
MF fleet 10/10 @ `bc5effbf` (sync 15:03 → pull; moc's gateway MainPID
1639666→2156079 started 15:03:09 > t0, both legs connected 15:03:14; the 7
stopped gateways stayed stopped); honest_status of record exit 0 16:20 (7/9 +
WARN ntfy ack, meshforge-lab 1 apt security update). Live leg-down drill NOT
run — queued `mesh-bridge-leg-down-live-drill-2026-11-11` (lab waits on 2 Muzi
Duos; emulated tier proposed: meshtasticd `--sim` on meshforge-pro, never the
UX seat). MA `b2e0fb85`: port + MF's C21 witness layer MA never had; test gate
FIRST (`gateway.mesh_bridge` in both twins' `_OPERATOR_STORE_MODULES`, MF
`5def21be`); red vs old src 42 behavioural fails; MA suite 9866 passed.
**Port review r1** (code-review medium, contextless, 10 findings): fixed in MA
test-first — F2 (no-destination DM retried + re-counted → MA `SendRefused`), F3
(new counters not pre-seeded), F8 (MA-only rejections uncounted, WARNING
unthrottled), F9. **Queued twin-wide, MF FIRST** (the port copied MF, so they
hold here): **#19** F4 `SystemExit` caught only at the loop → row stays
`in_progress`, batch skipped; **#20** F5 every terminal drop named
`dead_letter` (hold expiry / direction / refusal indistinct); **#21** F6
`*_connected` status event reads `held_link_down=True` (notify before
install); **#22** F7 `not connected` defer runs ahead of the downlink branch
(a broker-path broadcast is held though it could cross); **#23** F10 per-row
re-defer = 2 SQLite writes/row/2 s on SD while a leg is down (short-circuit per
leg); F1 = #14b (known-limit pin ported). MF has no `SendRefused` — porting it
MA→MF is the shape of the #20/F2 fix there.

**Emulated tier-1 drill (11-11, meshforge-pro, `scripts/emu_bridge_drill.py`):** two
meshtasticd `--sim` nodes + the real bridge over TCP legs — fixed `bc5effbf` PASS 3/3
(held through a 20 s leg kill, crossed 3.3 s after return), pre-fix `33743347` FAIL
2/2 (`dead_letter`). First live-process evidence for #14 on the TCP leg; not RF, not
serial, not MQTT.

**moc live drill (11-11 18:36):** ST leg unbound 25 s, one LF broadcast sent — the bridge
never queued it (RNS-first hand-off, by design); it reached ShortTurbo ~1 s later via
moc3's RNS path (moc2 heard `[RNS:VAIL] …`). Hold path NOT exercised live; the hand-off
witness read UNOBSERVABLE (its own radio was the one cut) while moc2 had heard it →
**#24** the witness should consult a peer ST box's hearing before saying unknown.

**2026-11-11 #19–#21 FIXED; #22 and #23 REVERTED** (MF first, MA ported same session; not
deployed) — see provenance. **#23** (drain gate) reverted after Fable r2 by running: it ran
ahead of the sender's per-row decisions (the #14 v1 kind) → seam guard
`TestDrainOffersEveryQueue`. **#22** (downlink ahead of the not-connected defer) reverted after
Fable r3 by running: a leg flagged down usually means meshtasticd — the downlink topic's only
subscriber — is down, and a broker accepting a QoS-0 publish is not delivery; held rows read
`delivered` and were lost (CONFIRMED with the real injector + a fake broker, re-measured here).
**#25 (NEW, PLAUSIBLE — measure first):** in that probe the real `DownlinkInjector` reported
success against a listener that accepts TCP but never CONNACKs — on a CONNECTED primary leg
(moc runs `injection_mode: downlink`) a broker-side failure could also read `delivered`.
Measure on the emulated tier (sim meshtasticd + mosquitto) before designing; MF first.

**Round 4 (Fable subagent by running, 2026-11-12 00:01–00:21): NOT CLEAN on TESTS only — every
claimed fix holds in both twins; queued for the next session (test-first, MF first):**
- **R4-F1 (SHOULD-FIX, CONFIRMED + re-measured here):** the seam guard calls `_drain_once`
  directly; a leg gate inlined in `_bridge_loop` (mutant m23k) SURVIVES all 452 MF / 410 MA
  bridge+queue tests (my run: guard + m18 `12 passed` on the mutant = the control). Fix: drive the
  through-the-loop cases via the REAL `_bridge_loop` (one-pass harness) and drop m18's "legs up".
- **R4-F2:** `_mesh_bridge_drain.py` is not in `parity_check.py`'s byte tier — the "byte copy" has
  no check that can fail. Add it.
- **R4-F3:** B19 pins `_connect_tcp` only; a notify restored in `_connect_mqtt` / `_connect_serial`
  survives. **R4-F4:** `_install_or_close` can emit `<leg>_connected` AFTER a concurrent
  `_disconnected` (no consumer today). **R4-F5:** stale `dead_letter` comment in
  `_mesh_bridge_legs.py`; `_note_link_down` has one caller. **R4-F6:** stale-sweep drop name unpinned.

**R4-F1/F2/F3 fixed test-first 2026-11-12** (MF `c6f6cea9` loop harness drives the real
`_bridge_loop`, `429a00b9` drain file byte-locked + serial/MQTT B19 pins; MA `bf445460`; CI success
×3). Not deployed.

**Round 5 (Fable subagent by running, 2026-11-12 06:55–07:26): NOT CLEAN on TESTS only** — src
fixes all hold; R4-F2 closed; the guards are blind ONE STEP OVER (re-measured here: control and
each mutant `129 passed` on the 5 bridge test files):
- **R5-F1 (SHOULD-FIX):** a gate on the PRIMARY leg once a queue has rows survives (g03 in
  `_drain_once` via `_held_link_down`; g05 the same in `_bridge_loop`) — the every-queue case mocks
  empty queues and every behavioural loop case arms the SECONDARY leg. Effect under g03, leg down:
  dedup copy `pending` not `delivered`; no-dest DM never refused; a 720 s row never expires.
- **R5-F2 (SHOULD-FIX):** #21/B19 miss an early `<leg>_connected` in the dispatcher
  `_connect_interface` (n03) or atop `_install_or_close` (n02), and never assert the primary leg
  (n12/n14). n03 re-emits the original #21 event (leg still down) first.
- **R5-F3 (NIT):** #22-revert pins only `interface=None`; x05 (flag down, handle kept) re-opens r3.
- **Seam rule:** same kind twice (r4, r5) → the fix is a leg-SYMMETRIC state table (leg × leg
  state × row kind → outcome) through the real `_bridge_loop`, and an exact event-list assertion
  per leg through the real `_connect_<leg>`; not more single-mutant pins.

**Leg-state tables 2026-11-12** (MF `924cce9e`, MA `eee7722f`, CI success): every r5 leg-class
survivor killed (MF 61/68, MA 59/68 of r5's mutants). **Round 6 (Fable by running, own harness,
08:36–09:06): NOT CLEAN on tests only — the THIRD tests-only round.** src holds; the table spec
is right. Blind spots: row POSITION (one row per case — g01 "one ask per leg per pass" lets rows
behind a held row skip dedup/DM refusal; re-measured here: 437 passed = control) and outage
HISTORY (g02/d03, MA); NITs R6-F3 (connect one frame above), R6-F4 (`LINK_DOWN_RETRY_S`
use-site), R6-F5 (MA t07). The reviewer's drivers S1/S2/S6/S7/S9/S10 kill all of them.
**The mutant space here is open-ended (any gate shape); per-round test growth is not converging
on CLEAN — loop decision is the operator's.**

**Loop STOPPED after round 6 (operator 11-12: "adopt the tests, then the drill").** The r6
reviewer's 23 scenarios adopted (MF `5c5be03f`, MA `3c5ad2fc`) kill every R6 finding in both twins;
known-uncaught shapes recorded in `.claude/audits/review_provenance.md` (f13, t05=R4-F6, MA t07, the
r5 NITs). End-domain evidence instead of round 7: emulated drill at HEAD PASS 2/2 (held 20 s,
crossed ~2.4 s after restart), pre-fix control FAIL at (2). Deploy = operator's call.

**#24 design — DECIDED 2026-11-11 (operator: "agree with your recommendation - lets do this"); build test-first in a FRESH session, inside BIC-2.**
Rule: **chain each transport's OWN wire id, hop by hop — never a content hash**
(a hash is text in disguise and collapses two identical "ok" texts — the BR-T #3
defect; memory 09-18 "decided on a wire FACT, never text"). A deployment only lacks
the hops it does not run; a hop with no id-carrying record reads `unobservable`,
never guessed.

| Deployment | Hops | Join |
|---|---|---|
| Meshtastic-only preset bridge (no RNS) | LF → bridge → its ST radio → ST hearers | the bridge's `sendText` returns the packet id it sent → ST boxes' `rx_heard` `[from, id, rx_ts]` — ONE hop. **= the channel syn/ack; FIRST SLICE** |
| RNS hub (this fleet) | LF → moc → LXMF → moc3 → ST hearers | moc's LXMF message id → injector records `(LXMF id → injected packet id)` → `rx_heard` — two hops |
| All-RNS RF | LXMF end to end | LXMF's cryptographic delivery proof IS the ack; no hearing join |

Measured on the 18:36 drill (`MF drill held 183625`): moc3 logged `Sent text via
stateless HTTP protobuf (id=2886856464)` = **0xac11ef10**, the SAME id moc2's
`rx_heard` holds (`from=0xebfa1b11` = moc3) — the injector→hearer hop already joins
on a wire id. moc's hand-off note keys on `content_id` and reads only its OWN
secondary radio (UNOBSERVABLE when it is down) — the CENTER may upgrade that with
evidence another box wrote; the box's own view stays honest.
**Double tap DONE 11-11 19:49 (`date`):** moc's `lxmf-1794458192791-002c` ids are LOCAL
delivery-tracking ids (one per dest, 4 for 4/4); moc3 logs only `Stored message
23657 from 3dfbdb5d…` (local row + sender identity). Neither logs a shared id —
but LXMF has one: `LXMessage.message_id = hash = full_hash(hashed_part)`
(`LXMF/LXMessage.py:368-369`, the PACK side; that the receiver derives the same hash is BELIEVED — read its unpack path first). And row 2's
home EXISTS: `BridgeCorrelationStore` (`gateway/correlation_store.py`, Thread-2)
carries nullable `mesh_packet_id` / `lxmf_msg_hash` columns "up front so later
steps need no migration" — its producers do not fill them yet (flag-gated: check
per box). Row 2 = fill `lxmf_msg_hash` at moc's send and `(lxmf_msg_hash →
injected mesh_packet_id)` at moc3's inject, in that store; no new id scheme.
**Slice 1 acceptance (pre-register before code):** the preset bridge records the
packet id `sendText` returned for each forward; the center joins it to any
far-preset box's `rx_heard` → `heard_by [box]`; with no far-preset ring covering the
window → `unobservable`, never `not_heard`. Byte-locked: `box_truth_rx.py` is
parity-locked with MA — any new field lands in both.

**What the public sentence can be today:** "one 24 h serial-to-serial lab run:
1,436 of 1,436 broadcasts crossed, 48 of 48 planted controls dropped under their
names." Not "every message accounted for", not "DMs refused", not "dedup by packet ID".

## C. Review by running — `/inbox` + delivery legs (BIC-2's ground)

Same method. Targeted tests: 207 passed, rc 0. Main session re-ran `d_inbox.py`
(F1: 1 of 2 rows shown) and grepped F2 (no caller of `inbox_heard` in `src/`,
`web/`, `scripts/`). No path renders the word "delivered" on `/inbox`.

| # | Finding | Where |
|---|---|---|
| F1 | `_fold_duplicates` hides a stranger's message and upgrades my send to `radio_echo`: match is channel + text + ≤ 10 s, no sender, no order | `inbox_api.py:133-143` |
| F2 | The "heard by" fix (INBOX-2 steps 1-3) reaches no page: no caller; `/api/inbox` rows carry no `packet_id` or heard field; `inbox.html` has no heard state. A fleet-heard post still reads "unconfirmed" forever | `inbox_api.py:170-179`, `inbox.html:110` |
| F3 | `send()` treats `0` / `False` as handed off (latent: today's sender returns int or None) | `inbox_api.py:260` |
| F4 | A recorded send can appear nowhere: `ORDER BY timestamp DESC LIMIT 500` with 500 rows stamped ahead (the stale-clock Pi) | `commands/messaging.py:486` |
| F5 | One id lands in two terminal buckets; a confirm counts with no send (`rate = 1.0`). = Q-CLAIM (b) | `delivery_counters.py:602-704` |
| F6 | Broadcasts enter the confirmable population once one DM confirms on that protocol | `delivery_counters.py:249,279` |
| F7 | A drop with no / unknown reason lands in the benign `dropped_other` | `delivery_counters.py:197-205` |
| F8 | A bad state passed to `record()` is swallowed, log line only (hfm #9) | `delivery_counters.py:625-635` |
| F9 | An inbound row whose `from_id` is the literal `local` renders as my send. Whether a sender can inject it: UNKNOWN | `inbox_api.py:44,68` |
| F10 | `judge_box` ignores the entry's sender; `sent_ts=NaN` → `not_heard` (latent, module has no caller) | `inbox_heard.py:75,99-105` |
| F11 | By reading (no browser): the send-failure banner is wiped by the next `load()`; an unknown state renders no label; a failed channels fetch turns the BLIND notice off silently | `inbox.html:160,196,251,312-316` |
| F12 | PLAUSIBLE: multi-chunk TUI sends flag only the last chunk | `messaging.py:311,399` |
| F13 | A killed leg reads `ok` ("quiet" and "killed" identical) — the BIC-2 acceptance gap, by design so far | `delivery_view.py:655` |

Holds: no `innerHTML` anywhere (no injection from mesh text); an unreadable store
→ 503 + banner; `not_sent` is never upgraded; `judge_box` says `unobservable` for
pid 0 / missing section / clock-ahead.

## D. Plan — BIC-2 "each message's legs on /inbox" (due 11-24)

**There is no per-message join today.** Leg records are aggregate buckets; an
inbox send goes straight to the radio and never calls `delivery_counters.record`.
Smallest seam: the **packet id** (already resolved by `meshtastic_handler` for
ROUTING_APP ACKs; `rx_heard` carries it).

1. `inbox_api.py` — emit `packet_id` per row; fix F1 + F3 at that seam.
2. A pure `message_legs(packet_id)` with a CLOSED vocabulary; broadcast half =
   heard-by from `inbox_heard` (fix F10), UNKNOWN when the spool is absent; wire
   into `_map_inbox_endpoints.py`.
3. `inbox.html` — closed STATE map, unknown default says "state unknown", legs line, F11.
4. `inbox_api.send` records SENT by packet id. ⚠️ DMs need the ack tracker, which
   lives in the GATEWAY process, not the map process — design decision first.
5. `delivery_counters.py` — first terminal per id wins (F5 / Q-CLAIM), `unknown` outcome (F7).
6. Bridged messages: carry `content_id` into `store_incoming`, join on `events.content_id`.

Acceptance that can fail: a DM from `/inbox` to an injected sender with the leg
killed + a no-kill control. Killed: never delivered/confirmed/heard; UNKNOWN,
then "dropped (delivery_timeout)". Control: confirmed. Spool removed: a
broadcast reads "UNKNOWN — cannot see", never "not heard".

**Realism (reviewer's read, BELIEVED):** steps 1–3 (the broadcast "heard by"
slice) fit 11-24. Steps 4–6 are at risk: the cross-process ack path, the open
Q-CLAIM race, the owed counter drill + soak. **Operator decision:** narrow the
11-24 slice to broadcast heard-by + UNKNOWN, or move the date.

## E. What changes, and what is the operator's

Adopted into ROADMAP tonight (facts): gate 3 status; MeshMonitor row ("works
with the WAN down" is not ours alone); Mesh Bridge row; BIC-4 upstream note;
INBOX-2 wording; `BR-T`, `INBOX-T`, `SHOW-1` added to NEXT (session-added).

Operator decisions, NOT taken:
1. **BIC-2 scope for 11-24** (above).
2. **`BR-T` before or after REL-1?** The release is where a stranger first meets the bridge claim.
3. **"Stop leading with Reticulum."** ROADMAP (11-04) says lead with the RNS hub
   + per-leg truth; the page says lead with the two-preset bridge and name RNS
   second. D1 already makes the one-box bridge RNS-free. With §B open, the bridge
   cannot lead yet either.
4. **Pilina** is in no ROADMAP tier and `Nursedude/pilina` is not in its repo
   list, though the roadmap is "the domain's only list". Add a tier line or keep
   it in its own build plan.
5. **REL-1's own rule vs the date:** a tag needs "the fleet ran X 7 days green";
   the deploy is HELD (CI chromium hang), fleet at `c7571c04`. For 12-04 the
   latest X must be on the fleet by ~11-27.

## Appendix — moved out of ROADMAP `DET-WEDGE` to make room (verbatim, 2026-10-22)

Measured on moc: 58 lifetime `main_thread_wedge_any` fires on `lab.lxmf_multi_user_synth`, ALL at :08 (synth-soak timer `*:07`), mean active ~30 s, self-clearing, 1–2/day 10-06..10-19, none since (unexplained). moc3 (same review): `lab.lxmf_tracer` + `lab.lxmf_echo` fires cluster at minute :x0 — the 10-min tracer timer, so the long-running echo blocks while the tracer drives it; not only short load runs. One-snapshot signal, no confirm ticks. Cure shape = the RNS-RPC sibling (`eb8a6529`): N consecutive ticks on the SAME pid/tid before signalling, and cure text that says "check `rpc[rnsd.` round trips first", never restart-first. Accept: a planted 30 s block does not fire; a held block on one tid fires within N ticks.

## Appendix — moved out of ROADMAP `DET-DCS` to make room (verbatim, 2026-10-22)

Measured on moc3: blind 09-22→09-28 (~6 d), 10-12→10-16 (~3.6 d), and again now (6 of 20) after the 10-21/22 deploy restarts — a total confirmation collapse cannot be seen for days after any deploy. Honest (`indeterminate`, not clean), but a probe indeterminate for days is a finding. Cure shape: persist the terminal ring across restarts (write the snapshot, reload on start), and/or a traffic-scaled minimum. Accept: restart the gateway on a box with a full ring → the probe judges on the next tick, never `indeterminate` from the restart alone.

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

Non-author Fable reviewer, scratch copy, fake radios, no live contact. Targeted
tests: 155 passed, rc 0. The main session re-ran `d1_false_green.py` and read the
cited lines for #1–#3. CONFIRMED = a driver printed it. Drivers were in the
session scratchpad (tmpfs; not kept) — each row names the input, so it can be re-made.

| # | Finding | Where | Input → what the bridge said |
|---|---|---|---|
| 1 | **A failed radio write is counted and logged as sent** — **FIXED 2026-11-10** `6fe286ef` (test-first; `tests/test_mesh_bridge_refused_write.py`); non-author Fable review by running 11-10 (10/12 mutants killed; the 2 survivors pinned since); **DEPLOYED 10/10 11-10 04:45**; MA twin `7906bc93`. Holds for the MQTT leg ONLY — TCP/serial: #10 | `mesh_bridge.py:1151-1162` discards `sendText()`'s return; `_mesh_bridge_mqtt.py` signals failure by `return False` | MQTT/HAT leg whose send fails → `messages_secondary_to_primary: 1`, a `tx` line, queue empty. A 230 B broadcast that the tag pushes to 249 B is refused, never sent, and still booked delivered (matrix F17b, worse than recorded) |
| 2 | No DM refusal exists | `:909-915`, `:1153-1158` | a DM is forwarded to the other leg at HIGH priority and counted; no refusal, no counter. (Matrix MF-6 says "DM vs broadcast KEPT" — the claim is wrong, the code matches the matrix) |
| 3 | Dedup is sender + text, never the packet id; the loop guard is the text tag | `:71-74`, `:850-867` | two different packets, same sender, "ok" → second dropped `duplicate`. Alice's "ok" then Bob's "ok" → Bob's dropped `dual_path` while the queue books 2 delivered. `add_prefix=False` removes the loop guard |
| 4 | One-way `direction`: the reverse message is accepted and sits pending | `:776` (only `_bridge_loop:500-505` checks) | `rx` line, no stat, row pending until shed at 1000; DMs never shed |
| 5 | Broad `except` in `_process_receive`: no counter, no drop line | `:924-925` | injected sqlite fault → `rx` line + ERROR, `stats: {}` |
| 6 | Queue-full is named `queue_dedup` | `:917-919` | wrong reason, no bridge counter |
| 7 | Queue dedup is wall-clock | `message_queue.py:365-378` | a repeat 10 min later after a 1 h backward step → dropped |
| 8 | **Default (TCP) leg receives nothing after connect** — **FIXED 2026-11-10** with #10–#12 (one seam `_attach_stream_listeners`, shared with serial; `tests/test_mesh_bridge_tcp_leg.py`; author Opus 5.5 → non-author review owed; not deployed; MA twin not yet) | `:725-728`: listener closure weakly held, no keepalive (the serial path has one) | 0 packets reach the callback; control with a held listener gets 1. Also no interface filter |
| 9 | The C21 reconcile passes with real bridge loss ≤ 5 % per leg | `scripts/c21_bridge_run.py` `reconcile`/`_label` | 2 of 40 lost before the rx line → PASS, labelled `rf_ingress_loss`. Ingress/egress are witnessed only by the bridge's own log |
| 10 | **A dead TCP leg is still booked as sent** (11-10 review of #1) — **FIXED 2026-11-10** with #8: tcp leg hears `connection.lost`; send-time `_write_target_gone()` reads what the library's `_writeBytes` gates on (`socket`/`stream`) and refuses + reconnects. Residual UNKNOWN: a write racing a mid-call socket drop | `mesh_bridge.py` `_forward_message`; meshtastic 2.7.11 `TCPInterface._writeBytes` no-ops when `socket is None` (a failed `_reconnect()` leaves it so) and `_sendPacket` returns the packet anyway; `connection.lost` subscribed for serial only (`:625`) | real `TCPInterface` vs a loopback that accepts then dies, 6 forwards → 5 booked sent with `socket=None`, `_primary_connected` stays True. Serial: same no-op, only before `on_lost`. Pre-existing; fold with #8 (same leg) |
| 11 | **TCP leg never released the connection lock** — FIXED 11-10 with #8 | `_connect_tcp` holds `MESHTASTIC_CONNECTION_LOCK` (plain `Lock`) "until disconnect"; neither `_disconnect_*` released it, and the loop's `except` branch dropped the leg without a teardown | old code: lost → reconnect waits 10 s on its own hold, `lock busy`, forever |
| 12 | **TCP leg ignored its port** — FIXED 11-10 with #8 | `TCPInterface(hostname=…)` without `portNumber` | a `:4404` leg connected to `:4403` — the other leg's radio |
| 13 | **Two TCP legs can never both connect** — OPEN, operator decision | one process-global non-reentrant lock held for a connection's LIFE (Issue #17 contract) | scratch probe on the fixed code: primary ok, secondary waited 10.0 s → False; the loop retries forever. The DEFAULT config (both legs TCP) never bridges |
| C21-W | worse than queued | — | TX into a wedged leg is logged + counted sent (#1's path) and reconciled as RF loss |

Holds: tagged content is never re-bridged; a re-heard packet after 61 s does not
cross twice; a serial write that RAISES is counted, retried, dead-lettered;
bridge-level dedup is monotonic. Not checked: real radios, the run's own data,
restart paths, the MeshAnchor twin.

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

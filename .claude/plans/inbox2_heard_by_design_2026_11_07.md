# INBOX-2 — "heard by" from fleet RX evidence (design, 2026-11-07)

ROADMAP: INBOX-2 (NOW). Feeds BIC-2 (per-leg truth, UNKNOWN first-class).
Operator: "go with fleet RX evidence — your call on design" (2026-11-07).

## Progress
| date | commit | deployed? | next |
|---|---|---|---|
| 2026-11-07 | (design only) | — | step 1: inbox keeps `packet_id` |
| 2026-11-08 | step 1: `messages.packet_id` `54304040` | YES fleet 10/10, honest_status exit 0 01:34 | step 2: box_truth `rx_heard` section (twin, Fable by running before deploy) |
| 2026-11-08 | step 2: `utils/box_truth_rx.py` + section `rx_heard` MF `2da1bfe9` + MA `d608f1ca` | YES — canary lehua 2 ticks, then fleet 10/10 + MA 1/1; FULL honest_status exit 0 06:03 | step 3: center join + tri-state |
| 2026-11-08 | live drill (no code): operator posted `Wx` on ch 3 from `/inbox` 06:21:51 | row 4920 `local` packet_id 3128482533 (`0xba78dae5`); by-hand id join over the spool: lehua · kiai · moc HEARD +1.1 s, moc3 covered-not-heard (SHORT_TURBO), meshforge-lab unobservable | step 3 (fresh session) |

## Measured before design (2026-11-07, read-only)
- 61 inbox sends 10-13 → 11-07, **61 channel posts, 0 DMs** (operator: "dm's are rare -
  majority of chat is in channels"). Every one read "handed to radio · unconfirmed".
- **53 / 61 (87 %)** were decoded by ≥1 other fleet box: meshtasticd INFO line
  `Received text msg from=0x6201ce25, id=0x…, msg=…` within +2 min. Recent posts: 6 boxes
  (kiai, lehua, moc, moc1, moc4, moc5). moc2/moc3 = SHORT_TURBO, cannot hear LongFast.
- 8 not heard: 4 on 10-13 (only 3 boxes had journals), 1 on ch 2 (VolcanoAI channel,
  others likely don't carry it), `Leaderboard` 10-21 (bot replied, maybe to someone else),
  2 on 10-26 15:25 (all 8 readable boxes silent — UNEXPLAINED, look before acceptance).
- ⚠️ **CORRECTED 2026-11-08 — the INSTRUMENT was wrong: 55 / 61 heard, 6 not.** The 10-26
  15:25 pair (rows 2360/2362, ids `0xeb622577`/`0xeb622578`) was decoded by ALL 6 LongFast
  boxes within ~2 s (moc, lehua: `[Router] Received text msg from=0x6201ce25, id=0xeb622577`).
  My 11-07 `join.py` matched on a 30-char TEXT prefix; both texts carry `’` (U+2019, 3 UTF-8
  bytes) and meshtasticd logs each non-ASCII byte as `#` (`I###m`), so the prefix never
  matched. Re-run with byte-faithful normalisation over the same 61 sends + logs → 55 heard.
  Remaining 6: 4 on 10-13 (3 journals then), 1 ch 2, `Leaderboard` 10-21. The lesson IS D2:
  join on the packet id, never text. (Those two were sent by a PhoneAPI client — meshtasticd
  logged `Received text msg from=0x0` on VolcanoAI — and still landed as `local` rows.)
- Leg: the INFO line does not say RF vs MQTT. BELIEVED RF/relay (VolcanoAI broker is
  127.0.0.1, no bridge). Check: a DEBUG RX line with SNR. Until checked, the page says
  "heard by", never "over RF".
- `inbox_api.send()` receives `packet_id` and DISCARDS it — the one wire fact that joins a
  send to anyone's RX record.
- Implicit ACK (option a) REFUTED as a witness: firmware v2.7.26.54e0d8d
  `ReliableRouter.cpp:39-62` acks on any rebroadcast of our own packet, and
  `dm_delivery.py` (measured 10-03) records our own radio acking EVERY send ~20 ms after
  TX and again on an MQTT echo. It proves "left", not "heard".
- All 8 radio boxes: operator user in `adm`, reads the meshtasticd journal without sudo.
- lehua (bot box, a key hearer) runs NO watchdog and NO map — its only read path is the
  box_truth grant. So the writer cannot live in the watchdog or the map.

## Decisions
- **D1 writer = a box_truth section `rx_heard`** (`src/utils/box_truth_rx.py`, byte-locked twin
  MF+MA; precedent: `role` was added inside v1 as the 11th section — this is the 12th).
  ⚠️ CORRECTED 2026-11-08 by measurement before code: `journalctl` REFUSES `--since` with any
  cursor option ("Please specify only one of --since=, --cursor=, --cursor-file=, and
  --after-cursor="), so the module keeps the cursor itself: `--show-cursor` → next read
  `--after-cursor=<c>`; `--since=@<now-6h>` only on a first read or a reset (cursor rejected /
  older than 6 h / clock went backward). Measured 11-08 against the live journals: first 6 h
  read VolcanoAI 72 ms · moc4 (systemd 252) 344 ms · lehua 1.28 s (6.3 s COLD for 24 h — why
  the 6 h bound); resume 9–77 ms; ring ~1.8 KB, 35–39 entries per box.
  Parses `[Router] Received text msg from=0x<from>, id=0x<id>,` (anchored — text inside `msg=`
  cannot forge a header) into a RING `$XDG_STATE_HOME/box_truth/rx_heard.json` (24 h, cap
  2000) — never message text. Section = `{entries: [[from, id, rx_ts]], covered_since,
  covered_until, last_line_ts, read ∈ initial|incremental|reset|failed, lines_read,
  future_dropped, window_s, cap, note}`. `covered_since` = the first journal line ACTUALLY read
  (never the `--since` bound), moved forward by reset/trim/cap; None → unobservable. A failed
  read serves the old ring and does NOT advance `covered_until`. Written under the export's
  existing flock (S7) — the collector's ONLY caller is `run_export` (hfm #8).
  Non-author review (Fable, by running, 11-08) — fixed before commit, each pinned by a test
  that kills its mutant: F1 a RESUME that returns rc 0 with NO cursor line (journalctl sees no
  entries for the unit — renamed / user lost `adm`) is `failed`, never covered; F2 the state
  carries `/etc/machine-id` — a foreign/cloned/unknown-id state restarts the ring and drops
  its hearings (journalctl ACCEPTS another machine's cursor, seeking by realtime); F3
  `from=0x0` (`[ServerAPI]` = this box's own client sends, 4 in lehua's 6 h) is never a
  hearing; double failure says "nothing restarted".
  BLIND SPOT (named, for step 3): a meshtasticd that stops logging mid-window leaves the
  coverage interval intact; `last_line_ts` shows a silent journal only at its END.
- **D2 join on the center**: own send `packet_id` (step 1) ⨝ each box's spooled `rx_heard`
  on `id` AND `rx_ts ∈ [send-10 s, send+600 s]`. No own-node-id inference (memory: never
  inferred from traffic; a 32-bit random id inside a 10-min window is the key).
- **D3 per box, a closed tri-state** (hfm #7): `heard` · `not_heard` (ONLY when the box's
  section is fresh past send+600 s AND its journal starts before the send) · `unobservable`
  (no section / old export / stale spool / journal starts after the send / read error) ·
  `cannot_hear` (declared other preset — from the fleet's declared segment, never guessed).
- **D4 display** (TUI + `/inbox`, same words): "heard by moc, kiai · 2 of 6 that could hear
  · 1 unobservable". Never "delivered". Standalone box (no fleet) → unchanged text.

## Acceptance (pre-registered — before code)
1. Replay: the join over the 61 historic sends reproduces the measured 53 heard (± boxes
   [⚠️ 11-08: the measured number is **55** — the 53 came from a text-prefix instrument
   defect, see "CORRECTED" above; the target is the id join = 55, same 61 sends]
   whose ring could not cover them — each named).
2. Live: one new channel post reads "heard by ≥1" within 2 spool ticks (~4 min).
3. Control that can fail: revoke/stop ONE hearer's export → that box reads `unobservable`,
   never `not_heard`, never `heard`.
4. A post on a channel no other box carries reads `not_heard` across the observable boxes.

## Owed to step 3 (from the step-2 review, 11-08)
- `covered_since` is NOT "oldest entry": a reset keeps older hearings ("heard stays heard").
- D2's `rx_ts` window rides box wall clocks (hfm #6; boots before NTP are real here): join
  on `id`, make the window a TAG ("heard, clock-skewed"), not a gate.
- **Deployment gap for acceptance 1–3:** only lehua, kiai, moc, moc3, meshforge-lab carry a
  box_truth grant/state dir; measured hearers **moc1, moc4, moc5 have NO grant** → they read
  `unobservable` whatever the code does. Granting is a box write → operator's words.
- A center dark > 6 h drops a still-valid cursor (bounded walk) and loses hearings in that
  gap; `--after-cursor` + `--until=@` is accepted (lehua) if slicing is ever wanted.

- Every inbox send writes TWO rows: `local` (carries `packet_id`) + a `!6201ce25` echo with
  `packet_id` NULL (rows 4919/4920, 11-08). Join from the `local` row; never count the echo
  as a second send.

## Steps
1. inbox keeps `packet_id` (messages.db column, guarded ALTER). MF only.
2. box_truth `rx_heard` section — MF first, byte-copy to MA, parity green. Non-author
   (Fable, by running) before first deploy; deploy per-target (the export IS the live
   spool's input — 10-14 dark-window lesson).
3. Center join + tri-state, pure, test-first.
4. TUI + `/inbox` line. Acceptance 1–4.

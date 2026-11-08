# INBOX-2 — "heard by" from fleet RX evidence (design, 2026-11-07)

ROADMAP: INBOX-2 (NOW). Feeds BIC-2 (per-leg truth, UNKNOWN first-class).
Operator: "go with fleet RX evidence — your call on design" (2026-11-07).

## Progress
| date | commit | deployed? | next |
|---|---|---|---|
| 2026-11-07 | (design only) | — | step 1: inbox keeps `packet_id` |
| 2026-11-08 | step 1: `messages.packet_id` (this commit) | deploy follows CI | step 2: box_truth `rx_heard` section (twin, Fable by running before deploy) |

## Measured before design (2026-11-07, read-only)
- 61 inbox sends 10-13 → 11-07, **61 channel posts, 0 DMs** (operator: "dm's are rare -
  majority of chat is in channels"). Every one read "handed to radio · unconfirmed".
- **53 / 61 (87 %)** were decoded by ≥1 other fleet box: meshtasticd INFO line
  `Received text msg from=0x6201ce25, id=0x…, msg=…` within +2 min. Recent posts: 6 boxes
  (kiai, lehua, moc, moc1, moc4, moc5). moc2/moc3 = SHORT_TURBO, cannot hear LongFast.
- 8 not heard: 4 on 10-13 (only 3 boxes had journals), 1 on ch 2 (VolcanoAI channel,
  others likely don't carry it), `Leaderboard` 10-21 (bot replied, maybe to someone else),
  2 on 10-26 15:25 (all 8 readable boxes silent — UNEXPLAINED, look before acceptance).
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
- **D1 writer = a box_truth section `rx_heard`** (byte-locked twin MF+MA; precedent: `role`
  was added inside v1 as the 11th section). The export runs
  `journalctl -u meshtasticd --cursor-file=<state>/rx_heard.cursor --since -6h -o short-unix`
  (cursor = incremental, ~2 min of journal per spool tick; `--since` bounds the first read),
  parses `Received text msg from=0x<from>, id=0x<id>` and keeps a RING file (last 24 h,
  cap 2000) — never message text. Section = `{entries: [[from, id, rx_ts]], journal_first_ts,
  window_s, cursor_ok}`. A ring, not a delta: an export whose answer never reaches the
  center loses nothing. flock the ring + cursor (two centers may read at once — hfm #8).
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
   whose ring could not cover them — each named).
2. Live: one new channel post reads "heard by ≥1" within 2 spool ticks (~4 min).
3. Control that can fail: revoke/stop ONE hearer's export → that box reads `unobservable`,
   never `not_heard`, never `heard`.
4. A post on a channel no other box carries reads `not_heard` across the observable boxes.

## Steps
1. inbox keeps `packet_id` (messages.db column, guarded ALTER). MF only.
2. box_truth `rx_heard` section — MF first, byte-copy to MA, parity green. Non-author
   (Fable, by running) before first deploy; deploy per-target (the export IS the live
   spool's input — 10-14 dark-window lesson).
3. Center join + tri-state, pure, test-first.
4. TUI + `/inbox` line. Acceptance 1–4.

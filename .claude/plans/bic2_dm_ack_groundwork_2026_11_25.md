# BIC-2 next slice — DM delivery truth: groundwork (2026-11-25, Opus 5.5)

> **Status: groundwork for a FRONTIER design pass** (ROADMAP BIC-2: "DMs +
> bridged legs: design the cross-process ack path first, then a new date").
> Written on a non-frontier session (model_advisor upshift): facts measured
> here, the design is NOT decided here. Operator: "start BIC-2 when ready".

## Acceptance sentence (draft, for the design pass to sharpen)

An `/inbox` DM row says, per send, exactly one of: **delivered** (a positive
ROUTING_APP ack whose `request_id` is this row's packet id came FROM the
destination) · **failed: <reason>** (a NAK) · **left, not confirmed** (only
acks from someone else — our own radio, a relay, an MQTT echo) · **no witness
yet / unobservable: <why>** (nothing came back, or this box cannot see acks).
Never "delivered" from our own radio's ack. END = the operator sends a DM from
`/inbox` to a node that is up, and the row turns "delivered"; to a node that
is off, it never does.

## Measured 2026-11-25 (`date` 07:2x HST)

| Fact | How measured | Result |
|---|---|---|
| Map listener mode, every box with a map | `curl 127.0.0.1:5000/api/inbox` → `rx.subscription` on VolcanoAI, moc, moc1, moc2, moc4, moc5, kiai | **MQTT JSON on all 7** (`mqtt localhost:1883 — every channel this radio uplinks as JSON`). No map holds a TCP radio session. |
| Gateway ack consumption | `grep` of the key in `~/.config/meshforge/gateway.json` | moc `false`; moc3 key absent (= default `False`, `gateway/config.py:594`). **No DM verdict is live anywhere.** |
| Ack consumer sender rule | read `gateway/ack_tracker.py:236-290` | positive ack confirms ONLY from the registered destination (`not_from_destination` refused + counted). Matches `utils/dm_delivery.py` (measured 10-03: own radio acks every send ~20 ms after TX). |
| Where `/inbox` DMs leave | read `utils/inbox_api.send` | map process, `send_text_direct_with_id(want_ack=True for a DM)`; packet id stored on the row (INBOX-2). |
| Where the ack is decodable without a fromradio read | read `utils/meshtastic_se_crypto.py` header, `gateway/config.py:572-594` | the MQTT `/2/e/` ServiceEnvelope topic, channel-decrypted with the channel PSK; JSON uplink carries no `request_id`. |

**Correction recorded:** earlier this session I said the witness "could live in
the map process" because the listener subscribes to every `meshtastic.receive`
— true of the code, false of the fleet: that path is the TCP mode, and no map
runs it. In the live MQTT mode, the map sees no ack at all.

## MEASURED 2026-11-25 10:29–10:32 HST — unknowns 1 + 2 ANSWERED (n=2)

Operator sent two `/inbox` DMs from VolcanoAI to kiai (`!851a9fe7`) while a
180 s `mosquitto_sub -t 'msh/2/e/#'` ran on VolcanoAI's LOCAL broker
(scratch capture, 7 envelopes; decoder `utils.meshtastic_se_crypto`, counts
and ids only). Per DM, the same three envelopes, all `gateway=!6201ce25`
(VolcanoAI's own radio):

| t | topic | from → to | decoded |
|---|---|---|---|
| +0 s | `PKI` | us → kiai | the DM itself, `pki_encrypted`, `want_ack` — NOT decryptable (as expected) |
| +2–3 s | `HawaiiNet` | **kiai → us** | **ROUTING_APP, `request_id` = the DM's packet id, error NONE** |
| +2–3 s | `HawaiiNet` | us → kiai | ROUTING_APP, hop_start 0 (our radio's own routing packet) |

- **UNKNOWN 2 → VERIFIED yes:** the sender box's radio uplinks the ack it
  RECEIVES to its own local broker's `/e/`.
- **UNKNOWN 1 → VERIFIED: the destination's ack is CHANNEL-keyed, not PKI** —
  decryptable although the DM itself is PKI. Both DMs (`0xab3c8abc`,
  `0xab3c8abd`) acked by kiai, `request_id` exact, error NONE, ~2–3 s.
- **No secret needed on this fleet:** both acks decrypted with the DEFAULT
  key `AQ==` alone (the HawaiiNet channel here is default-keyed). This
  removes the "channel PSK at runtime in the map" collision with 11-24's
  no-stored-secret posture FOR THIS CHANNEL; a custom-keyed channel still
  needs the operator decision.
- **The gap, live:** both DMs read `handed_off` in `/inbox` while the
  destination's ack sat on the local broker — fork A (per-box `/e/` witness in
  the map's MQTT listener + read-time join with `dm_delivery.classify`) is now
  the evidenced candidate. Design pass still owed (frontier), n=2 on ONE pair
  (VolcanoAI → kiai, direct, 0 hops): a relayed DM and a NAK are unmeasured.

## UNKNOWN — the measurements that decided the design (kept for the record)

1. **PKC.** DMs between nodes that both hold keys (fw ≥ 2.5) are
   `pki_encrypted`; `meshtastic_se_crypto` cannot decrypt those (header). Is
   the destination's ROUTING_APP ack ALSO PKI-encrypted? If yes, an `/e/`
   witness is blind to exactly the modern-DM case. Measure: one DM to a known
   up node with a fresh key exchange, then count `/e/` packets whose decrypt
   yields `ROUTING_APP` with that `request_id` (a short `mosquitto_sub` on the
   SENDER box's local broker + offline decrypt; needs the channel PSK →
   operator present, MF014: never printed).
2. **Uplink.** Does each box's meshtasticd uplink the ack it RECEIVES (addressed
   to itself) to its local broker's `/e/`? Uplink is per channel; a DM's ack
   rides the DM's channel index. Same measurement as 1.
3. **Multiple API clients.** Only relevant if a TCP witness is chosen: whether
   meshtasticd fans a received packet to every PhoneAPI client. TCP sessions
   are the class that force-closed the map (11-11) — any TCP option starts
   with that cost.

## Design fork (for the frontier pass — not chosen here)

- **A. Per-box `/e/` witness inside the map's MQTT listener.** Same process as
  `/inbox`; subscribe to the local broker's `/e/`, decrypt with
  `meshtastic_se_crypto`, keep `(request_id, from, error, t)` in a bounded
  ring; `/inbox` joins by packet id at READ time with `dm_delivery.classify`
  (one classifier). Mirrors the heard-by design (spool + read-time join).
  ⚠️ Needs the channel PSK at runtime in the map — collides with 11-24's
  "MeshForge keeps no stored secret" posture: where the key lives is an
  OPERATOR decision. Blind to PKI acks if UNKNOWN-1 says so.
- **B. Gateway witness + cross-process spool.** Turn on the existing gateway
  path (moc/moc3) and have it write every decoded ack to a spool the center's
  `/inbox` reads (like `rx_heard`). Only the two gateway radios witness; an
  `/inbox` DM sent from moc5 is acked to moc5's radio, which the gateway may
  never hear → mostly "unobservable" off the gateways.
- **C. Sender-session witness (`scripts/dm_delivery_check.py` shape).** The
  sender's own API session sees `request_id` + sender for each ack — the
  measured-correct witness (10-03) — but it is a TCP PhoneAPI client: the
  contention class. Probably a drill tool, not a product path.

Whatever is chosen: the row's UNKNOWN is first-class — a box that cannot see
acks says "this box cannot see delivery acks (<why>)", never a blank, never
"sent" read as delivered (hfm #2, BIC-2 one-box precedent).

## Not in this slice

Bridged legs (mesh ↔ RNS per-leg truth) — the roadmap's other half of
"DMs + bridged legs"; needs its own groundwork.

## MEASURED 2026-11-25 14:11–14:49 HST — design §4 session (operator sent every DM)

Capture = `timeout mosquitto_sub -t 'msh/2/e/#'` on the SENDER box's local
broker + its meshtasticd journal; ids/counts only. Sender VolcanoAI
`!6201ce25` unless stated. Packet ids are the `/inbox` rows' `packet_id`.

| # | DM → dest | packet | `/e/` saw | journal saw | `/inbox` row |
|---|---|---|---|---|---|
| NAK-a | → `!5eed1e56` (never seen) ×2 | `0xab3c8abf`, `…c0` | **nothing** (not even the DM) | `Unknown public key … refusing to send legacy DM` · `Error=39` · `Alloc an err=39,…,idFrom=0xab3c8abf` | `handed_off` ✗ |
| relay-1 | → `!514eae28` (1 hop, relay `0xd8`) | `0xab3c8ac1` | dest NAK **`PKI_UNKNOWN_PUBKEY`**, `req` exact, +24 s, `LongFast` topic; our radio then sent NODEINFO | `Received routing from=0x514eae28` | `handed_off` ✗ |
| relay-2 | same, after key exchange | `0xab3c8ac2` | dest ack **NONE**, `req` exact, +4 s, 1 hop | same | `handed_off` ✗ |
| on | → `!b03da894` (p5, 0 hops) | `0xab3c8ac3` | dest ack NONE +1.9 s; the ack carried `want_ack`, our radio acked the ACK (`req` = the ack's id) | | `handed_off` ✗ |
| off-1 | p5 unplugged | `0xab3c8ac4` | the DM **3×** (+0, +7.3, +16.5 s; 3rd after `Resetting next hop`) · **no NAK, ever** | no give-up line at INFO, 6+ min | `handed_off` |
| off-2 | p5 unplugged | `0xab3c8ac5` | the DM **1×** (why not 3× = UNKNOWN, n=1) · no NAK | no give-up line | `handed_off` |
| replug | p5 back, ~50 s after boot | `0xab3c8ac6` | dest ack NONE +3.0 s | | `handed_off` ✗ |
| moc5-1/2 | sender **moc5** `!5f01371f` → p5 ×2 | `0x2a089819`, `…1a` | the DMs only (PKI) · **no ack** · topics in 6 min: HawaiiNet 2, meshforge 3, PKI 2, **no LongFast** | `Received routing from=0xb03da894` +2–3 s, both | `handed_off` |

(✗ = the row is wrong today: refused, failed, or delivered — all read `handed_off`.)

- **Radio give-up (MAX_RETRANSMIT) is invisible to both witnesses** (n=2): never on `/e/`, never at INFO. A powered-off destination can only end "no ack seen: window closed" (+ "sent N×").
- **Local refusal is journal-only, with the exact id** (`idFrom=`), never on `/e/` — and the DM is never transmitted.
- **Acks ride the channel topic the destination answers on** (`LongFast` here, `HawaiiNet` on the 10:29 run) → the subscription must be `+`. Every ack decrypted with the DEFAULT key; no custom-keyed channel met (§4 #4 = not applicable so far).
- **moc5 is half-blind**: its broker carries its own DMs but not the acks. **Cause — OPERATOR-STATED 2026-11-25 ~15:0x HST** (read in the app): moc5 channels = 0 HawaiiNet, LongFast, meshforge; **LongFast has MQTT uplink OFF, by design ("ok to mqtt off")**. Consistent with the capture: the p5's acks rode the `LongFast` topic on VolcanoAI, and moc5's broker showed no `LongFast` topic in 6 min. So a DM whose ack returns on LongFast is invisible to moc5's `/e/` witness BY DESIGN — slice 1's rows there read "possible ack, not matched — this box doesn't uplink LongFast" (Q17/Q19), never a config change (Q18). Load average 0.20–0.40 throughout; 2 of 7 envelopes non-broadcast.
- Ack latencies observed (destination acks, n=6): 1.9, 2–3, 2–3, 3.0, 4, 24 s (the 24 s = a NAK).
- **Control that can fail** (offline, the real `dm_delivery.classify` over these captures): true acks → FAILED `PKI_UNKNOWN_PUBKEY` + DELIVERED 4.0 s; empty replies → NO_WITNESS ×2; own-radio ack only → LEFT_ONLY. Never delivered without the destination.
- Side observations, NOT chased: an unrelated PhoneAPI client connected + dropped on VolcanoAI at 14:34:48 (not this session); the own-node thread `!6201ce25` shows sent DMs as incoming copies (echo-fold class → INBOX-T).
- ⚠️ Session hygiene: four reads (journal greps + an `/api/inbox` walker) printed message text / a node position into the transcript (nothing to the repo). Each was a BLOCKLIST; the fix that held was an ALLOWLIST. → design rule D-1 below.
- **moc5 MQTT VERIFIED read-only ~15:1x HST:** every envelope on its broker is from its own radio `!5f01371f` (HawaiiNet, meshforge, PKI — no LongFast); MQTT client connections loopback-only; broker listens 127.0.0.1/::1 only; no bridge (`conf.d` = README). Operator: "you can fix this if you like" → **planned as slice 1's acceptance experiment, NOT done now**: before = moc5 row "possible ack, not matched — this box doesn't uplink LongFast"; flip LongFast uplink on moc5 (backup channel config first, map up after, touch line); after = "delivered". A row that doesn't flip = slice 1 is wrong.
- **moc5 LongFast uplink FLIPPED 15:23:09 HST (operator "flip now") — END VERIFIED 15:29:00.** Before (14:47/14:49, n=2): p5 acks in moc5's journal, absent from its broker. After: DM `0x2a08981b` (row 705206299) → p5 ack `req=0x2a08981b err=NONE` +2.1 s ON moc5's broker, `LongFast` topic (n=1). Cause VERIFIED: LongFast uplink was off. ⚠️ This REMOVES the live "possible ack, not matched" case on moc5 — slice 1's acceptance needs another uplink-less channel/box for that state (or a test fixture). Other boxes' per-channel uplink NOT surveyed.

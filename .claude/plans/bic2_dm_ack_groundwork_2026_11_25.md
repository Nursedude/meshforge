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

## UNKNOWN — the measurements that decide the design

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

# Honest Meshtastic delivery confirmation in `mqtt_bridge` mode — feasibility

> Investigation 2026-06-08, triggered by the Thread-2 step-4 soak finding: the
> ROUTING_APP ACK-consumption feature is inert on the fleet because the fleet
> runs `bridge_mode=mqtt_bridge` (HTTP-toradio TX + MQTT-json RX), where the ACK
> is never ingested. Question: is *any* honest per-DM confirmation possible in
> this mode **without** a PhoneAPI `fromradio` read (which #17/#75 forbid)?

## ✅ UPDATE 2026-06-08 — BUILT (operator chose to implement)

The `/e/` path below was implemented the same day:
- **`src/utils/meshtastic_se_crypto.py`** — native AES-CTR ServiceEnvelope decrypt
  (no `meshing_around` dep), **proven live on moc** (NODEINFO/POSITION decoded +
  cross-checked vs `/json/`) + round-trip unit tests.
- **`send_text_direct_with_id`** returns the minted packet_id (bool wrapper kept).
- **`MQTTBridgeHandler`** decodes ROUTING_APP from `/e/` when a DM is pending
  (cost-guarded) and feeds the step-4 `AckTracker` → `delivery_counters`
  CONFIRMED / DROPPED. The old "inert" warning became an "ACTIVE" log (warns only
  if the crypto deps are absent). New `meshtastic.channel_keys` config supplies
  the PSKs to decrypt the `/e/` channel.
- Live on moc: the **ACTIVE** log fired, `/e/` subscribed, gateway healthy.

**Still pending a live positive CONFIRMED** — two environmental blockers, neither a
code defect: (a) the quiet mesh has no reachable node to ACK a DM; (b) moc's
downlink channel uses a custom PSK not in the gateway config, so the operator must
set `channel_keys` to decrypt its `/e/` ACKs (live PSKs must not be read/printed).
The default LongFast channel works zero-config.

Original feasibility analysis (retained):

## Verdict

**Theoretically yes — via the `/e/` ServiceEnvelope MQTT topic — but it is
high-effort, partial-coverage, and not currently a correctness gap.** The system
is *already honest* in mqtt_bridge mode (Meshtastic delivery stays "Sent (not
guaranteed)", #16; the #74 probe correctly excludes Meshtastic since it records
no `confirmed` events → no false signal). So `/e/` confirmation is a
**nice-to-have enhancement, not a fix**. The honest-signal startup warning
(`81ac4ac`) already makes the limitation visible.

## The candidate path: subscribe to `/e/`, decrypt, match `request_id`

meshtasticd publishes **two** MQTT topic families:
- `msh/<region>/2/json/<ch>/<node>` — decoded app payloads only (text, position,
  nodeinfo, telemetry). **No ROUTING_APP.** This is what the gateway subscribes
  to today.
- `msh/<region>/2/e/<ch>/<node>` — the **encrypted ServiceEnvelope** topic:
  *"when MQTT is enabled the device uplinks every raw protobuf MeshPacket it sees,
  encapsulated in a ServiceEnvelope"* [meshtastic.org/docs/software/integrations/mqtt].
  This carries **every** MeshPacket the radio hears, **including ROUTING_APP
  (portnum 5) ACK/NAK** packets.

So a gateway in mqtt_bridge mode could, **staying entirely within MQTT (no
fromradio read → zero-interference invariant preserved)**:
1. Subscribe to `msh/+/2/e/#` + `msh/2/e/#`.
2. Parse each ServiceEnvelope → MeshPacket.
3. Decrypt the channel-keyed payload (the gateway holds the channel PSK).
4. For `portnum == ROUTING_APP`, read `request_id` + `routing.error_reason`.
5. Match `request_id` to a sent DM's `packet_id` → honest CONFIRMED / DROPPED.

`send_text_direct` **already sends `want_ack=True` by default** (the ACKs are
already being requested + uplinked; the gateway just never looks at `/e/`).

## Live evidence gathered (moc, 2026-06-08)

- ✅ The `/e/` topic EXISTS and has traffic: `msh/N/e/LongFast/<node>` observed.
- ✅ MeshForge HAS decrypt machinery (`utils.mqtt_decryptor`, `get_decryptor()`).
- ❌ …but it is **non-functional on the fleet**: it wraps an external
  `meshing_around` package (`/opt/meshing_around_meshforge`,
  `meshing_around_clients.core.mesh_crypto.MeshPacketProcessor`) that is **not
  installed** on moc (`_HAS_CRYPTO=False`, `_MeshPacketProcessor=False`).
- ⚠️ A 60 s `/e/` decode probe caught only 1 packet (quiet mesh — no node text in
  24 h) and could not decrypt it (decryptor down). 3 wantAck DMs to an
  unreachable synthetic node (`!5e700000`) produced **no** `/e/` ROUTING_APP —
  consistent with a locally-generated MAX_RETRANSMIT NAK **not** being uplinked
  (only OTA-heard packets are).

## Blockers / caveats (the honest part)

1. **Decryption not available** — needs either installing `meshing_around` fleet-
   wide, or a **native AES-256-CTR decrypt** in MeshForge (the `cryptography` dep
   is present; the meshtastic default-key + `packet_id‖from` nonce scheme is
   well-documented). The latter is the cleaner fix (no external dep).
2. **Partial coverage** — confirms only DMs whose recipient ACK is **heard** by
   the gateway's own radio (1-hop, or relayed back) and then uplinked. A failed
   delivery's local NAK is not uplinked, so NAK-side honesty is weaker than the
   TCP path's.
3. **Channel-keyed only** — PKC (curve25519) DMs can't be channel-decrypted; only
   channel-keyed DMs are observable.
4. **`packet_id` plumbing** — `send_text_direct` mints the id internally and
   returns only a bool; it would need to return the id for request_id matching.
5. **Empirically unconfirmed** — quiet mesh + broken decryptor blocked a live
   ROUTING_APP capture; the path rests on firmware docs + architecture, not a
   captured packet.

## Recommendation

**Shelve implementation; document as a known future enhancement.** Rationale: the
mqtt_bridge mode is already honest (SENT, not CONFIRMED — exactly #16), so this is
not a correctness gap; the `/e/` path is real but high-effort (native decrypt +
packet_id plumbing + `/e/` subscribe + decode pipeline) for *partial* (heard-only,
channel-only) coverage. Revisit if Meshtastic delivery confirmation becomes a
priority — at which point the first step is a **native ServiceEnvelope decrypt**
(replacing the absent `meshing_around` dependency), which is independently useful
(it would also let the map/monitor decode `/e/` traffic the fleet currently sees
only as opaque node-existence pings).

If pursued, build it as a new ingestion in `MQTTBridgeHandler` (subscribe `/e/`,
decode ROUTING_APP, feed the existing `AckTracker` + `delivery_counters` from
step 4 — that machinery is correct and reusable; only the *ingestion source*
differs from the TCP path).

## Sources

- MQTT integration (uplinks every MeshPacket as ServiceEnvelope; `/2/e/` topic):
  https://meshtastic.org/docs/software/integrations/mqtt/
- MQTT module config: https://meshtastic.org/docs/configuration/module/mqtt/
- ROUTING_APP = portnum 5, payload is a `Routing` protobuf:
  https://meshtastic.org/docs/development/firmware/portnum/
- Ack-when-uplink-only nuance (FR #6596):
  https://github.com/meshtastic/firmware/issues/6596

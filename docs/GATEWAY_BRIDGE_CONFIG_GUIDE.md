# Gateway & Bridge Configuration Guide

How to pick and configure a MeshForge gateway/bridge **variant** — standalone,
fleet roles, dual-radio, MeshCore, and the experimental LAB flags.

- **Installing a box from scratch?** Start with
  [`GATEWAY_DEPLOYMENT.md`](GATEWAY_DEPLOYMENT.md) (field-validated recipe),
  then come back here to choose your config shape.
- **Ready-made starting points:**
  [`gateway_config_templates/`](gateway_config_templates/README.md) — one
  annotated `gateway.json` per variant.
- **Why the fleet is shaped this way:** the fleet architecture research doc
  (`.claude/research/fleet_architecture_2026_06_03.md`).

> ## ⚠️ BETA — built to change
>
> The gateway is under active weekly development. Two mechanisms keep this
> guide honest:
>
> 1. Every template is loaded through the real `GatewayConfig` parser by
>    `tests/test_gateway_config_templates.py` — schema drift fails CI loudly.
> 2. Anything in the **LAB** section below is canary-grade: enabled on at most
>    one box, default OFF, may change shape without notice.
>
> If this guide and `src/gateway/config.py` ever disagree, the code wins —
> and please fix the guide in the same commit.

---

## 1. Pick your variant

| You have / want | Variant | Template |
|-----------------|---------|----------|
| One box, one radio, simplest Meshtastic↔RNS bridge | **Standalone** | `standalone-basic.json` |
| A production fleet gateway (MQTT RX, LXMF fan-out, peer relay) | **Full gateway** | `full-gateway.json` |
| A light board that bridges but runs no map services | **Gateway-only / hot spare** | `gateway-only-hotspare.json` |
| An RF-sparse site that should observe, not bridge | **Collector** | `collector.json` |
| The box that pushes cloud snapshots | **Cloud publisher** | `cloud-publisher.json` |
| Two radios bridging different LoRa presets | **Dual-radio mesh_bridge** | `mesh-bridge-dualradio.json` |

Config file: `~/.config/meshforge/gateway.json` (operator user, never root —
see the born-correct permissions foundation). The gateway runs as
`meshforge-gateway.service`.

**Composable bridges**: `bridge_mode` is an advisory label; what actually
gates each leg is its own `enabled` flag (`rns_bridge_enabled`,
`mesh_bridge.enabled`, `meshcore.enabled`, `meshtastic_broadcast.enabled`,
`rns_transport.enabled`). Legs compose — a full gateway can run the RNS
bridge AND a dual-radio mesh_bridge simultaneously.

## 2. Standalone (no fleet)

One radio, one box, no MQTT broker required: the gateway holds a TCP
connection to meshtasticd (`:4403`) and bridges to RNS/LXMF. Fine for a
single-operator install; the trade-off is that TCP RX shares the connection
budget with other meshtasticd clients. If you run anything else against
`:4403`, move to the MQTT RX shape (next section).

Minimum moving parts: `meshtasticd`, `rnsd`, `meshforge-gateway`.

## 3. Fleet variants

All fleet shapes use **MQTT RX** — the zero-interference path. meshtasticd
publishes mesh packets to a local mosquitto; the gateway subscribes instead
of holding TCP. The web client (`:9443`), TCP (`:4403`), and the gateway
coexist without contention. TX goes out via the HTTP protobuf API
(`http_port: 9443` — never 443, that's a forbidden shape).

Per-role deltas (services are converged by `scripts/provision_role.py` from
`docs/fleet_roles.yaml`; gateway.json carries the bridge shape):

- **full-gateway** — RNS bridge ON, multi-recipient `default_lxmf_destination`
  (list form), `peer_gateway_destinations` for cluster relay so a NomadNet
  send into one gateway reaches every RF preset the cluster covers.
- **gateway-only** — same bridge, no map services (light boards). Keep
  mesh_bridge/meshcore sections **wired but disabled** so the box can be
  promoted to active dual-radio duty by flipping one flag — but first copy
  the active gateway's destination/peer lists (see the harmonization
  runbook in `gateway_config_templates/`).
- **collector** — gateway unit disabled by role (RF-sparse site). Config
  kept on disk for painless promotion.
- **cloud-publisher** — minimal bridge; its special duty
  (`meshforge-cloud-push.timer`) is a systemd unit, not a gateway.json key.

**Invariant — `independent_bridges`**: fleet gateways bridge complementary
RF coverage active-active. There is no cross-gateway failover pairing; the
`gateway_heartbeat_*` keys are deliberately unwired and templates omit them.

## 4. Dual-radio mesh_bridge (cross-preset)

Bridges two Meshtastic networks on different LoRa presets (e.g. a LONG_FAST
wide-area mesh ⇄ a SHORT_TURBO local mesh). Primary is usually the
meshtasticd HAT (MQTT RX + HTTP TX); secondary can be a plain USB radio via
`connection_type: "serial"` — **no second meshtasticd needed**.

Three settings carry the safety load:

1. **`channels` allow-list** — serial RX hears *every* channel of its radio.
   Without the allow-list, a secondary's ch0 text would re-TX on the
   primary's ch0 (often a public channel). List only the channel indexes
   that carry the same channel name + PSK on both radios.
2. **`prefix_format: "[Mesh:{source_preset}] "`** — the echo-loop invariant.
   The `[Mesh:` tag is how *every* gateway (this one, and any LXMF peer
   sharing an RF segment) recognizes already-bridged content and refuses to
   re-bridge it. An untagged prefix re-opens the cross-gateway
   echo-amplification loop. Don't change it.
3. **`dedup_window_sec`** (default 60) — content-hash duplicate suppression,
   the backstop behind the tag.

`direction` can pin the bridge one-way (`primary_to_secondary` /
`secondary_to_primary`) for asymmetric setups.

## 5. MeshCore leg

`meshcore.enabled` attaches a MeshCore companion radio (serial USB most
common; TCP for WiFi-firmware radios or ser2net). Needs `pip install
meshcore`. `bridge_channels` / `bridge_dms` gate what crosses.
`simulation_mode` runs the leg without hardware for testing.

## 6. Meshtastic broadcast fan-out

`meshtastic_broadcast.enabled` runs a *separate* LXMF identity that fans
allow-listed Meshtastic channels out as LXMF DMs to subscribed RNS peers
(opt-in via a "subscribe" DM; `autosubscribe` defaults off). RX-only —
no reverse path into Meshtastic.

## 7. 🧪 LAB — experimental flags (canary-grade, default OFF)

These are under single-box canary soak. **Do not enable fleet-wide.** They
may change shape or disappear.

### True-origin downlink injection (`injection_mode` / `downlink_psk`)

Per-radio (`meshtastic` section, and per mesh_bridge leg):

- `"toradio"` (default) — cross-bridge traffic enters via HTTP
  `/api/v1/toradio`; the radio rewrites `from` to itself, so its own web UI
  never shows bridged traffic as incoming.
- `"downlink"` — the gateway publishes a true-origin encrypted protobuf MQTT
  downlink; meshtasticd attributes it to the real source node, the web UI
  renders it as incoming, and a NodeInfo downlink teaches the radio each
  origin's name. Requires `downlink_psk` (the channel PSK, base64 — an
  **operator secret**: source it at runtime, never commit it, and don't let
  it sit in world-readable backups). Any failure falls back to toradio —
  the message is never dropped.

Scope today: broadcast messages on the mesh_bridge primary leg. DMs, the
serial secondary, and RNS-origin messages still use toradio (RNS origins
have no native node id — synthetic-id mapping is an open design).

### Theme-A addressability trio (`rns` section)

Observe-first rollout, one canary box, defaults OFF:

- `reply_routing_enabled` — reply-context memory: R→M honors explicit
  `@addr` > echoed reply-to field > remembered thread > broadcast.
- `cross_protocol_identity_enabled` — IdentityBinder auto-populates
  cross-protocol contacts from observed traffic (unverified; the operator
  CLI `scripts/gateway_contacts.py` always works regardless).
- `sessions_enabled` — durable session layer: a DM to the gateway's own
  node routes privately to the sender's most-recent session peer
  (24h idle TTL, survives restart).

### `meshtastic_broadcast.ack_required`

Issue #66 first-caller delivery opt-in — synthetic `[delivered:<id>]` back
to the originating channel on first confirmed LXMF receipt. Incomplete.

## 8. Placeholders & secrets

Templates use `<PLACEHOLDER>` tokens (legend in
[`gateway_config_templates/README.md`](gateway_config_templates/README.md)).
Rules:

- Never commit real LXMF hashes, node ids, channel names, or PSKs
  (security rules MF014/MF015).
- `downlink_psk` is the most sensitive value in this file: treat
  `gateway.json` and **its backups** as secret-bearing once set.
- MQTT credentials (`mqtt_bridge.username`/`password`) are empty on
  LAN-internal brokers; if you set them, same rules apply.

## 9. Keeping this guide honest

- Add a template → drop it in `docs/gateway_config_templates/`; the anti-rot
  test picks it up by glob. Annotation keys (`_name`, `_description`,
  `_usage`, `_validated_by`) are required, top-level only.
- Change `src/gateway/config.py` → run
  `python3 -m pytest tests/test_gateway_config_templates.py` — if a template
  breaks, fix it in the same commit.
- New keys that `load()` reads must be added to
  `TOP_LEVEL_KEYS_READ_BY_LOAD` in the test (deliberate friction: it
  documents the read-set).

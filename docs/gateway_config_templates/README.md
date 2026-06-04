# Gateway Config Templates

Annotated, **validated** `gateway.json` starting points — one per deployment
variant. Narrative + decision help: [`../GATEWAY_BRIDGE_CONFIG_GUIDE.md`](../GATEWAY_BRIDGE_CONFIG_GUIDE.md).
Per-box install recipe: [`../GATEWAY_DEPLOYMENT.md`](../GATEWAY_DEPLOYMENT.md).

> **BETA**: the gateway config schema changes frequently. Every template here is
> loaded through the real `GatewayConfig` parser by
> `tests/test_gateway_config_templates.py` on every test run — a template that
> drifts from `src/gateway/config.py` **fails CI loudly** instead of rotting.
> If you add a template, the test picks it up automatically (glob).

## Selection table

| Template | Fleet role | Bridges | Pick when |
|----------|-----------|---------|-----------|
| `standalone-basic.json` | none (single box) | Meshtastic↔RNS via TCP | One radio, one box, no broker, simplest start |
| `full-gateway.json` | `full-gateway` | MQTT RX + RNS fan-out + peer relay; mesh_bridge wired-off | The standard production gateway |
| `gateway-only-hotspare.json` | `gateway-only` | RNS active; mesh_bridge + MeshCore wired-off; broadcast on | Light board, no map services, standby for dual-radio duty |
| `collector.json` | `collector` | none (unit disabled by role) | RF-sparse site — observe, don't bridge |
| `cloud-publisher.json` | `cloud-publisher` | MQTT RX + RNS single dest | Box whose special duty is the cloud snapshot timer |
| `mesh-bridge-dualradio.json` | testbed / `full-gateway` | LONG_FAST ⇄ SHORT_TURBO cross-preset | Two radios, bridging presets; RNS optional |

## Placeholder legend

Every `<PLACEHOLDER>` must be replaced before use. None of these values may
ever be committed (security rules MF014/MF015).

| Placeholder | What it is | Where to get it |
|-------------|-----------|-----------------|
| `<GATEWAY_NODE_ID>` | This gateway's own Meshtastic node id (e.g. `!a1b2c3d4`) | `meshtastic --host localhost --info` |
| `<LXMF_DEST_HASH>` / `<LXMF_DEST_HASH_N>` | Operator NomadNet inbox `lxmf.delivery` hash (32 hex) | NomadNet → Conversations → your identity |
| `<PEER_GATEWAY_HASH_N>` | Another gateway's `lxmf.delivery` hash (cluster relay) | That gateway's startup log / announce |
| `<CHANNEL_NAME>` | The Meshtastic channel name the gateway rides (e.g. your private channel) | `meshtastic --ch-index N --info` |
| `<SERIAL_DEVICE>` | USB radio device path (e.g. `/dev/ttyUSB0`) | `ls /dev/ttyUSB* /dev/ttyACM*` |
| `<MESHCORE_SERIAL_DEVICE>` | MeshCore companion radio device path | `ls /dev/ttyUSB*` |
| `<BASE64_CHANNEL_PSK>` | Channel PSK, base64 (LAB downlink injection only) | Radio admin config — **secret, never commit** |

Notes:
- `channel: 2` in the templates is the fleet convention for the dedicated
  gateway channel index — set it to YOURS.
- `http_port` is always `9443` (443 is a forbidden shape, Issue #58; the
  loader migrates a stale 443 but don't write one).
- Annotation keys (`_name`, `_description`, …) are **top-level only** — the
  loader ignores unknown top-level keys but nested sections reject them.

## Relationship to other config locations

- `templates/gateway/gateway.json.template` — the installer's render source
  (`scripts/configure_gateway.sh` sed-substitutes `@TOKEN@`s). Untouched;
  these docs templates are the human-readable catalog.
- `examples/configs/gateway-*.json` — **superseded by this directory** (and
  `gateway-mqtt.json` is schema-broken against the current loader).
- `src/gateway/profiles/*.yaml` — radio **hardware** presets (different
  concern, still current).

## LAB flags (experimental — do not enable fleet-wide)

`injection_mode: "downlink"` + `downlink_psk` (true-origin MQTT downlink
injection) and the Theme-A trio (`reply_routing_enabled`,
`cross_protocol_identity_enabled`, `sessions_enabled`) are under single-box
canary soak. Templates ship them **OFF**. See
[`../GATEWAY_BRIDGE_CONFIG_GUIDE.md`](../GATEWAY_BRIDGE_CONFIG_GUIDE.md) §LAB.

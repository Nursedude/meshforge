# MeshForge Gateway Deployment

Deployment recipes for a MeshForge gateway box — a Pi that bridges a local
Meshtastic mesh to Reticulum/LXMF (and, optionally, to an RNode for LoRa-RNS
egress). Field-validated across the moc/fleet-host-1/fleet-host-2/fleet-host-3 fleet 2026-04-24.

> **This is the canonical, end-to-end SF ↔ MeshForge ↔ RNS runbook** — bare box to
> a verified bridged message. The gateway doc set:
>
> | Doc | Use it for |
> |-----|-----------|
> | **`GATEWAY_DEPLOYMENT.md`** (this file) | the runbook + the "[where every knob lives](#where-every-knob-lives-the-sf--meshforge--rns-map)" map + end-to-end verify |
> | [`GATEWAY_BRIDGE_CONFIG_GUIDE.md`](GATEWAY_BRIDGE_CONFIG_GUIDE.md) | **variant reference** — choose a config shape (standalone / fleet roles / dual-radio / MeshCore / LAB flags) |
> | [`gateway_config_templates/`](gateway_config_templates/README.md) | validated per-variant `gateway.json` starting points |
>
> (The older `.claude/research/gateway_setup_guide.md` is **superseded** — its
> body was removed 2026-07-07; it documented a config shape the code no longer uses.)

## What the gateway does

```
    ┌──────────────────┐       ┌──────────────────────┐
    │  Meshtastic HAT  │──SPI──│ meshtasticd :4403    │
    │  (LongFast etc.) │       │ + :9443 web + MQTT   │
    └──────────────────┘       └──────────┬───────────┘
                                          │ mosquitto :1883 pub/sub
                                          ▼
                               ┌──────────────────────┐
                               │ meshforge-gateway    │
                               │ bridge_mode=mqtt_    │
                               │     bridge           │
                               └──────────┬───────────┘
                                          │ LXMF via RNS
                                          ▼
                               ┌──────────────────────┐
                               │ rnsd (Reticulum)     │
                               │   shared instance    │
                               └──┬─────────┬─────────┘
                                  │         │
                       ┌──────────▼──┐  ┌───▼──────────────┐
                       │ AutoInterface│  │ RNodeInterface   │
                       │ (LAN/WiFi)   │  │ (optional LoRa   │
                       │              │  │  USB radio)      │
                       └──────────────┘  └──────────────────┘
                                          ▲
                                          │
                               ┌──────────┴───────────┐
                               │ NomadNet (user svc)  │
                               │ LXMF inbox, browser  │
                               └──────────────────────┘
```

Messages on the Meshtastic `meshforge` channel get picked up via MQTT,
forwarded as LXMF to a local or remote NomadNet, and vice versa. The RNode
(when attached) carries RNS packets over LoRa to other RNode-equipped
peers out of WiFi range.

## Where every knob lives (the SF ↔ MeshForge ↔ RNS map)

Standing up one gateway touches **eight config planes that don't reference each
other**. This is the single biggest source of "green logs, zero throughput"
confusion. Here is every knob, where it lives, and what drives it — so you never
have to guess which of eight files owns a setting.

| Plane | Lives in | Key knobs | How to set it |
|-------|----------|-----------|---------------|
| **Radio LoRa** | the Meshtastic device | `lora.region`, `lora.modem_preset` **(← SF lives here, implicitly)**, `lora.channel_num`, `lora.tx_power`, `lora.hop_limit` | `sudo scripts/configure_lora.sh <profile>` (baked profiles: `us_default`, `us_longrange`, `us_fast`, `eu_default`, `au_default`) |
| **Channel** | the Meshtastic device | the `meshforge` channel PSK, per-channel `uplink_enabled` + `downlink_enabled` | `configure_gateway.sh` sets the uplink/downlink flags (both required — see gotcha #2) |
| **MQTT uplink** | the Meshtastic device | `mqtt.json_enabled = true`, `mqtt.address = localhost` | `configure_gateway.sh` (both required — a remote `mqtt.address` silently starves the bridge) |
| **HAT pins** | `/etc/meshtasticd/config.d/` | SPI / CS / IRQ / Busy / Reset / gpiochip | TUI → Meshtasticd LoRa handler (`handlers/meshtasticd_lora.py`) — hardware wiring, not modem params |
| **Bridge** | `~/.config/meshforge/gateway.json` | `rns_bridge_enabled`, `meshtastic.{channel, http_port=9443, mqtt_channel}`, `mqtt_bridge.{root_topic, region, channel}` | `configure_gateway.sh` renders it from `templates/gateway/gateway.json.template`; TUI Gateway menu edits it |
| **RNS / rnsd** | `/etc/reticulum/config` (search order `/etc` → `~/.config/reticulum` → `~/.reticulum` → `/root/.reticulum`) | **`rpc_key`** (the most error-prone knob), optional `[[RNode LoRa]]` block with an **explicit `spreadingfactor =`** | pin `rpc_key` once per box (see "rpc_key pinning"); RNode SF is a literal integer here |
| **Role** | `~/.config/meshforge/deployment.json` (`role`) | whether this box runs a gateway unit *at all* | `sudo scripts/provision_role.py --set-role <role> --apply` (catalog: `docs/fleet_roles.yaml`) |
| **Service** | systemd | `meshforge-gateway.service` | `sudo scripts/install_gateway_service.sh` |

### "Spreading factor" is two different knobs — say which radio

- **Meshtastic leg**: SF is **implicit** in `lora.modem_preset`. There is no
  `spreadingfactor` field on the Meshtastic side — the preset picks it
  (`SHORT_TURBO`≈SF7, `LONG_FAST`≈SF11, `VERY_LONG_SLOW`≈SF12). Set it with
  `configure_lora.sh`, *not* in `gateway.json` (the `preset` field in
  `gateway.json` is a documentation string only; it does not touch the radio).
- **RNS / RNode leg**: SF is an **explicit integer** — `spreadingfactor = 7` in
  the `[[RNode LoRa]]` interface block of `/etc/reticulum/config`.

So "set the spreading factor" means `configure_lora.sh` for the mesh radio, or
the rnsd RNode block for the LoRa-RNS egress radio — never `gateway.json`.

## Configs supported

| Template | Bridges | Field-proven on |
|----------|---------|-----------------|
| Single-radio HAT + MQTT + RNS | Mesh ↔ NomadNet on one box | moc, fleet-host-1, fleet-host-2, fleet-host-3 |
| Single-radio HAT + RNode | Same, plus LoRa-RNS egress via RNode | fleet-host-3 |
| Dual-radio (serial secondary) | LF mesh ↔ ST mesh via mesh_bridge mode | code landed, not yet field-live — see "Known Limits" |

## Deployment

The recipe splits into two idempotent scripts. Re-running either is safe —
existing state is detected and preserved.

```bash
# 1. Configure the gateway: deps, channel flags, gateway.json, rpc_key check
sudo scripts/configure_gateway.sh                 # uses $SUDO_USER
sudo scripts/configure_gateway.sh wh6gxz          # specific user
sudo DRY_RUN=1 scripts/configure_gateway.sh       # preview only

# 2. Install + enable the systemd unit
sudo scripts/install_gateway_service.sh
```

After both, watch the first startup:

```bash
sudo journalctl -u meshforge-gateway -f
```

You're healthy when the 30-second status line shows:

```
Meshtastic: connected
RNS: via rnsd (transport handled by rnsd)
```

## Prerequisites

The configure script checks these and tells you what's missing.

| Thing | Why | How |
|-------|-----|-----|
| `meshtasticd` running on `:4403` + `:9443` | Source of mesh packets | `systemctl status meshtasticd`; HAT config in `/etc/meshtasticd/config.d/` |
| `rnsd` running | RNS transport + shared instance | `systemctl status rnsd` |
| `mosquitto` running on `:1883` | MQTT bridge carrier | `systemctl status mosquitto` |
| A `meshforge` channel on the radio | Bridged channel (fleet-shared PSK) | See "meshforge channel" below |
| Channel `uplinkEnabled=true` + `downlinkEnabled=true` | MQTT traffic in both directions | `configure_gateway.sh` sets these |
| NomadNet identity file present | LXMF destination derives from it | Run NomadNet once to generate `~/.nomadnetwork/storage/identity` |
| `rpc_key` pinned in rnsd's config | Prevents Issue #37/#41 auth failures | Below |
| LXMF + RNS + paho-mqtt + meshtastic in system Python | Gateway unit runs as `User=wh6gxz` with `/usr/bin/python3` | `configure_gateway.sh` pip-installs them |

### The `meshforge` channel

All fleet boxes share a `meshforge` PSK so messages bridge cleanly across
gateways. The existing fleet PSK (base64) is
`JVq8FDehjzw0GCB9iVtbdI5Yuf9iB0nV` — check any fleet box with `meshtastic
--host localhost --info | grep meshforge`. Use the same PSK on new boxes.

Then enable MQTT flags (`configure_gateway.sh` does this automatically, but
by hand it is):

```bash
meshtastic --host localhost --ch-index <N> \
    --ch-set uplink_enabled true \
    --ch-set downlink_enabled true
```

### rpc_key pinning

rnsd derives its RPC key from its transport identity. Any client running in a
different configdir (the gateway uses `/tmp/meshforge_rns_client/`) generates
a different identity → different key → **every RPC to rnsd fails with
AuthenticationError** on inbound traffic. See Issue #37 / #41.

Fix once per box:

```bash
# Generate a key
openssl rand -hex 32

# Add to /etc/reticulum/config (or wherever rnsd's config lives) under [reticulum]:
#   rpc_key = <that-64-hex-string>

sudo systemctl restart rnsd
```

MeshForge's `utils.paths.ReticulumPaths.get_shared_rpc_key()` reads this key
and propagates it to every client config (gateway, TUI RNS commands, map
collector). The option name is literally `rpc_key` — earlier `shared_instance_rpc_key`
was a bug; RNS silently ignored it.

## Attaching an RNode (optional)

For LoRa-RNS egress (fleet-host-3's pattern), add an `RNodeInterface` block to rnsd's
config. fleet-host-3's block for reference (903 MHz ISM, 22 dBm hardware cap):

```ini
  [[RNode LoRa]]
    type = RNodeInterface
    interface_enabled = True
    port = /dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0
    frequency = 903625000
    bandwidth = 250000
    txpower = 17
    spreadingfactor = 7
    codingrate = 5
    id_callsign = <YOUR_CALL>
    id_interval = 600
    network_name = regional rns
```

Use a `by-id` port path, not `/dev/ttyUSB0` — USB re-enumeration scrambles the
integer suffix. Verify after restart:

```bash
sudo systemctl restart rnsd
rnstatus
```

The interface should show `Status: Up`, `Mode: Full`, and a noise-floor
reading in dBm (anything quieter than `-90` is reasonable).

**Hardware**: the RNode is a Heltec LoRa32 v3 (or similar SX126x board)
flashed with RNode firmware (not Meshtastic). Do not confuse with a
Meshtastic-firmware Heltec on the same family — wrong firmware on the USB
port just fails to respond to rnsd.

## Known gotchas (field-validated)

Findings from the 2026-04-24 deployment session. Each is either already
handled by `configure_gateway.sh` or documented below.

1. **`gateway.json` absent → wrong bridge_mode default**. With no config
   file, the gateway defaults to `message_bridge` and hard-codes
   `meshtastic.channel=0` — if your `meshforge` channel is at index 2,
   every TX goes to the wrong channel. Fix: always deploy a
   `gateway.json` via the template.

2. **Channel `uplinkEnabled/downlinkEnabled=false`** is the installer
   default on fresh Meshtastic channels. Without both, MQTT carries
   nothing in either direction. Fix: `configure_gateway.sh` sets them.

3. **`lxmf` missing from system Python** even though pipx has it. The
   gateway systemd unit runs with `/usr/bin/python3`, which is a
   different Python than the pipx `nomadnet` venv. Result:
   `Python package(s) missing: lxmf`. Fix: `configure_gateway.sh` runs
   `pip3 install --user --break-system-packages` for the service user.

4. **`paho-mqtt` also missing from system Python** on some boxes, same
   reason. `configure_gateway.sh` installs it.

5. **Backwards-read rnsd config path**. rnsd running as `User=root` reads
   `/root/.reticulum/config`, not `/etc/reticulum/config`, on fleet boxes
   without the system-wide config. `get_shared_rpc_key()` follows the
   same search order (`/etc` → `~/.config/reticulum` → `~/.reticulum`)
   so it Just Works as long as the key is pinned *somewhere* readable
   by the gateway user. Check with the preflight script output.

6. **`meshtastic` CLI in pipx isn't on root's PATH**. `sudo meshtastic
   ...` fails with "command not found." `configure_gateway.sh` uses
   the absolute path via `sudo -u <user>` to dodge this.

7. **Gateway's own LXMF hash ≠ NomadNet's LXMF hash**. The gateway
   generates its own identity on first start (see the startup log line
   "Gateway LXMF destination: ..."). That's the hash under which
   bridged mesh messages appear in a remote NomadNet inbox — not the
   local user's NomadNet hash. See Issue #35 for the UX gap and Issue
   #39 for the envelope-identity fix.

## Enabling multiple bridges on one box (composable-bridges model)

`bridge_mode` in `gateway.json` is now an **advisory display label only**.
Each bridge's startup is gated by its own `enabled` flag:

| Config setting | What it enables |
|----------------|-----------------|
| `rns_bridge_enabled: true` (default) | `RNSMeshtasticBridge` — the RNS ↔ Meshtastic message bridge (mqtt_bridge / message_bridge behavior) |
| `mesh_bridge.enabled: true` | `MeshtasticPresetBridge` — cross-preset Meshtastic ↔ Meshtastic (e.g. LF HAT + ST USB) |
| `rns_transport.enabled: true` | `RNSMeshtasticTransport` — RNS over Meshtastic as a transport layer |

Any combination is valid. The common deployment (`rns_bridge_enabled=true`,
everything else `false`) runs exactly what the fleet runs today. A
dual-radio gateway also bridging to NomadNet enables both the RNS bridge
and `mesh_bridge`, and both run in the same process with independent
queues, threads, and connections.

**Refusal-on-inconsistency, not silent fallback** (per operator request
— "don't let the user config their way into a broken app"): the gateway
runs `validate_bridge_conflicts()` at startup and exits with a clear
error message if the config is inconsistent. Current refusal conditions:

- No bridges enabled (need at least one)
- `mesh_bridge.primary.serial_device == mesh_bridge.secondary.serial_device` (both radios can't share one serial port)
- `mesh_bridge.enabled` and `rns_transport.enabled` both true (both claim the Meshtastic radio's data path)
- `mesh_bridge.secondary.connection_type="serial"` with a `serial_device` path that doesn't exist

On refusal the service exits with code 2 and prints what to fix. There
is no auto-correction that would leave the gateway running in a different
mode than the operator asked for.

**Legacy-config migration**: deployments with `bridge_mode="mesh_bridge"`
but `mesh_bridge.enabled=false` (the pattern the old single-enum code
used) are auto-migrated in-place at startup with a `MIGRATION:` warning —
set `mesh_bridge.enabled: true` explicitly in `gateway.json` to silence.

## Known limits

- The **RNSSniffer** throws `TypeError: received_announce() got an
  unexpected keyword argument 'destination_hash'` in a background thread on
  startup. Cosmetic — does not break bridging — but shows up as noise in
  `journalctl`. RNS 1.1.x signature change; fix is small and orthogonal.

- `monitoring.traffic_storage` warns "Traffic log not writable
  ([Errno 13])" at `/home/<user>/.cache/meshforge/logs/traffic.log`.
  Harmless; the log just disables. Fix:
  `sudo chown -R $(id -un):$(id -gn) ~/.cache/meshforge`.

## Fleet truth table (2026-05-02 — dual-gateway, one per RF preset)

| Box | Role | Gateway LXMF hash | RNode | Bridging live |
|-----|------|-------------------|-------|---------------|
| fleet-host | manager / NomadNet | — (no gateway) | no | n/a |
| **moc** | **active gateway — LongFast bridge** | `3dfbdb5d24c6de195ae4f3c0f56b5ea5` | no | **yes (LongFast HAT)** |
| fleet-host-1 | NomadNet client (LongFast HAT) | (was `f5bb192d…`, disabled) | no | no |
| fleet-host-2 | NomadNet client (SHORT_TURBO HAT) | (was `b185b0de…`, disabled) | no | no |
| **fleet-host-3** | **active gateway — SHORT_TURBO bridge** | `0123456789abcdef0123456789abcdef` | yes (903.625 MHz, SF7) | **yes** |

Two active gateways by design — each handles one RF preset. moc bridges
LongFast Meshtastic ↔ RNS/LXMF; fleet-host-3 bridges SHORT_TURBO ↔ RNS/LXMF.
The presets can't cross-RX over the air, so a single gateway only ever
covers one segment. This dual-gateway shape gives full bridge coverage:
LongFast handheld traffic + SHORT_TURBO handheld traffic both reach
NomadNet operators.

Each gateway has a distinct `rns.gateway_name` set in its `gateway.json`
(`MeshForge Gateway (moc)` vs `MeshForge Gateway (fleet-host-3)`) so the
two threads index separately in NomadNet's per-source view (Issue #35).
The `meshtastic.gateway_node_id` self-echo filter is set per-box to its
HAT's own node ID — `!32962f10` on moc, `!ebfa1b11` on fleet-host-3 —
so neither gateway re-bridges its own outbound TX as a duplicate.

If a third gateway is ever needed (e.g. hot standby), the recipe is the
same `scripts/configure_gateway.sh <user>` + `scripts/install_gateway_service.sh <user>`,
followed by post-edit of the rendered `gateway.json` to set
`meshtastic.gateway_node_id`, `rns.gateway_name`, and the
`rns.default_lxmf_destination` broadcast list. Older disabled-hash
references in `node_cache.json` (e.g. `f5bb192d…` from fleet-host-1's
prior gateway role) are inert — `scripts/fleet_sync.sh` after `8899ae8`
uses `systemctl try-restart` so stopped+disabled gateways stay down
through syncs.

## Topology & data flow — where each message lands

The **single-gateway** topology has a deliberate asymmetry that confuses
first-time operators. Documenting it here so the design intent is
explicit rather than feeling like a workaround.

```
  ┌──────────────────────────────┐         ┌──────────────────────────────┐
  │  fleet-host-3 (gateway #1)   │         │      moc (gateway #2)        │
  │  ─────────────────────────── │         │  ─────────────────────────── │
  │  meshforge-gateway           │         │  meshforge-gateway           │
  │  meshtasticd ─── HAT         │         │  meshtasticd ─── HAT         │
  │     RF: SHORT_TURBO meshforge│         │     RF: LongFast meshforge   │
  │  rnsd  ── hub :4242          │         │  rnsd  ── TCPServer :4242    │
  │  NomadNet  6b1a0120…         │         │  NomadNet  7cda0fab…         │
  │  Gateway   f68c2f56…         │         │  Gateway   3dfbdb5d…         │
  └──────────┬───────────────────┘         └────────────┬─────────────────┘
             │   RNS Transport (TCP / AutoInterface mesh)│
             └──────────────────┬─────────────────────────┘
                                │
                  ┌─────────────┼─────────────┐
                  │             │             │
              ┌───┴────┐    ┌───┴────┐    ┌───┴────────┐
              │fleet-host-1│ │fleet-host-2│ │ fleet-host │
              │ NomadNet   │ │ NomadNet   │ │ (manager)  │
              │ 522c…      │ │ d1df…      │ │ no NomNet  │
              │ HAT:LF     │ │ HAT:ST     │ │            │
              └────────────┘ └────────────┘ └────────────┘
```

Each gateway's `default_lxmf_destination` broadcast list contains all
four NomadNet inboxes (`522c…`, `d1df…`, `6b1a…`, `7cda…`) so any
inbound RF message fans out to every operator. The two gateway hashes
(`f68c2f56…` and `3dfbdb5d…`) appear in NomadNet as separate threads
named `MeshForge Gateway (fleet-host-3)` and `MeshForge Gateway (moc)`
respectively — the operator can tell at a glance which RF preset a
bridged message originated on.

**Where does a message appear?** Roles in **boldface** are the canonical
display surface for that direction:

| Direction | Source | Lands at fleet-host-3 :9443 (Mesh UI) | Lands at fleet-host-3 NomadNet | Lands at fleet-host-1/fleet-host-2 NomadNet | Lands at fleet-host-2 HAT (RF) |
|-----------|--------|-------------------------------|------------------------|------------------------------|------------------------|
| **Mesh→RNS** (someone TXes on `meshforge` channel) | A SHORT_TURBO Meshtastic node OR fleet-host-3 :9443 web UI | as **outgoing** if you typed it; as **incoming** if it came from a peer node | **as incoming** under "MeshForge Gateway (fleet-host-3)" thread, `[Mesh:xxxx]` prefixed | **as incoming** under same thread (multi-recipient) | depends on RF: only if it came from another node and fleet-host-2 was in range |
| **RNS→Mesh** (NomadNet types into the gateway thread) | fleet-host-1 / fleet-host-2 / fleet-host-3 NomadNet → gateway hash `f68c2f56…` | as **outgoing** in :9443 message log (the gateway just told fleet-host-3's HAT to TX) | **does NOT auto-appear** in fleet-host-3 NomadNet's gateway thread (it's the transmit side, not receive — see "echo filter" below) | depends on what's in the gateway thread already; usually not, since the bridge is one-shot, not echoed back | **as incoming** over RF (this is the whole point — fleet-host-2 hears it on SHORT_TURBO `meshforge`) |

**The "fleet-host-3 :9443 doesn't show incoming RNS messages" question.** This is
the asymmetry. fleet-host-3's HAT is the **transmitter** for RNS→Mesh. From
its own RF perspective it has nothing to receive — its own outbound TX
isn't returned over the air. meshtasticd's web UI may or may not list
that outbound TX as a message-log entry depending on which page you're
on (recent firmware shows it under "Messages" as your own send; some
builds only show it in the packet log). For the **operator-canonical
view** of bridged content from fleet-host-3's seat, use **fleet-host-3's NomadNet** and
look at the "MeshForge Gateway (fleet-host-3)" conversation — that's where
Mesh→RNS-bridged content lands.

**Why the gateway box doesn't loop its own RNS→Mesh sends back into
NomadNet (the "echo filter").** When the gateway TXes a `[RNS:xxxx]
…` message, meshtasticd republishes it on MQTT. Without filtering,
the gateway's MQTT subscriber would see its own outbound TX and
re-bridge it back to RNS as a fresh "incoming Mesh message." Every
RNS-originated send would land twice in every NomadNet inbox. The
filter (`meshtastic.gateway_node_id` in `gateway.json`) drops only
messages where `sender == own_id` AND text starts with `[RNS:` —
the unambiguous loopback signature. Plain web-UI / CLI sends from
the gateway box (no `[RNS:]` prefix) are NOT filtered and DO bridge
to RNS, so the operator's own sends still reach the fleet's NomadNets.

**Why this is topology, not a workaround.** A single-gateway design
deliberately collapses the role: the gateway box is *both* a Meshtastic
TX/RX endpoint AND an RNS LXMF endpoint. The price is that traffic
appears in different places depending on direction and where you're
looking from. It's the same asymmetry that exists in any IRC↔Slack
bridge or email gateway. Multiple gateways would split the load but
also split conversations (Issue #35) — operators expect a single
canonical thread, not N parallel threads. Single-gateway is the
trade-off we picked; this section names it explicitly.

**Operator viewing recipe.** "Where do I look for bridged messages?":

| You are at | You want to see | Open this |
|-----------|-----------------|-----------|
| fleet-host-3 (gateway box) | Mesh content bridged to RNS | `fleet-host-3` NomadNet → "MeshForge Gateway (fleet-host-3)" conversation |
| fleet-host-3 (gateway box) | RNS content the gateway just sent over RF | `fleet-host-3` :9443 → Messages tab (your own outgoing) |
| fleet-host-3 (gateway box) | Your own NomadNet outbound | `fleet-host-3` NomadNet → recipient's conversation |
| fleet-host-1 / fleet-host-2 (NomadNet client) | Mesh content from anywhere on `meshforge` | NomadNet → "MeshForge Gateway (fleet-host-3)" conversation |
| fleet-host-1 / fleet-host-2 (NomadNet client) | Direct chat with another NomadNet operator | NomadNet → that peer's conversation (Issue #47 seeding flow) |
| fleet-host-2 (SHORT_TURBO HAT) | Anything bridged from RNS into Mesh | fleet-host-2 :9443 → meshforge channel (incoming RF) |
| fleet-host-1 (LongFast HAT) | Anything bridged from RNS into Mesh | **n/a** — fleet-host-1's HAT is preset-incompatible with fleet-host-3's TX. RNS-bridged content reaches fleet-host-1 only via NomadNet, not RF |

## Verifying end-to-end

From a NomadNet client:

```
@HAT-moc <your message>     # DM to a specific mesh node by short name
@!ebfa1b11 <your message>   # DM by mesh node hex id
<your message>              # broadcast to the meshforge channel
```

Verify via MQTT at the gateway box:

```bash
mosquitto_sub -v -t 'msh/#'
```

A valid downlink publishes JSON with `"channel": <N>` and optionally
`"to": <numeric>`. Issue #40 documents the HTTP TX path this uses
(`send_text_direct()` via meshtasticd's `/api/v1/toradio`) and why MQTT
publish was never the TX contract.

### Green-but-dead: the two silent zero-throughput traps

The gateway can log a clean startup and bridge **nothing** in either direction.
Two field incidents (Issues #34 and #40) both presented as healthy logs with
0 RX/0 TX. Test the data path directly instead of trusting the logs.

**Trap #34 — MQTT topic-shape mismatch.** meshtasticd 2.7.x publishes on
`{root}/2/json/{channel}/{node}` (no region segment); a region-ful subscription
sits at 0 RX with green logs. The validated default is now `region=""` (both the
render template and the RX subscriber accept the region-less and region-ful
shapes). Prove RX by injecting a synthetic publish the bridge must pick up:

```bash
# Synthetic RX probe — publish a fake mesh text on the meshforge channel and
# confirm the gateway ingests it (watch `journalctl -u meshforge-gateway -f`).
mosquitto_pub -h 127.0.0.1 -t 'msh/US/2/json/meshforge/!deadbeef' \
  -m '{"payload":{"text":"rx-probe"},"sender":"!deadbeef","type":"text","channel":2,"to":4294967295,"from":3735928559,"id":9999001}'
```

If the gateway logs the received text, the M→R (mesh→RNS) leg is live. If it
sees nothing, the channel index / topic shape / uplink flags are wrong — walk
the knob map above.

**Trap #40 — R→M bytes + wrong downlink topic.** The RNS→Mesh leg was silently
dead because LXMF `message.content` arrives as **bytes** (crashed the str path)
and the downlink was published to a channel-named topic instead of the literal
`toradio` HTTP contract. Both are fixed; the R→M acceptance test that does not
need the NomadNet TUI is:

```bash
python3 scripts/validate_rns_to_mesh.py    # shell-runnable LXMF sender for R→M
```

Downlink lands on the radio via `send_text_direct()` → meshtasticd
`/api/v1/toradio` (never an MQTT publish). If `validate_rns_to_mesh.py` reports
delivery but nothing hits the radio, check `rpc_key` pinning (Issue #41) — a key
mismatch fails every rnsd RPC and aborts inbound LXMF with `AuthenticationError`.

> **Rule of thumb:** a gateway is only "verified" when a real or synthetic packet
> is observed crossing — not when the service is `active` and the logs are quiet.
> Silence is a failure mode, not a pass.

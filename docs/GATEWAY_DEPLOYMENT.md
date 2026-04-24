# MeshForge Gateway Deployment

Deployment recipes for a MeshForge gateway box — a Pi that bridges a local
Meshtastic mesh to Reticulum/LXMF (and, optionally, to an RNode for LoRa-RNS
egress). Field-validated across the moc/moc1/moc2/moc3 fleet 2026-04-24.

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

## Configs supported

| Template | Bridges | Field-proven on |
|----------|---------|-----------------|
| Single-radio HAT + MQTT + RNS | Mesh ↔ NomadNet on one box | moc, moc1, moc2, moc3 |
| Single-radio HAT + RNode | Same, plus LoRa-RNS egress via RNode | moc3 |
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

For LoRa-RNS egress (moc3's pattern), add an `RNodeInterface` block to rnsd's
config. moc3's block for reference (903 MHz ISM, 22 dBm hardware cap):

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
    network_name = hawaiinet rns
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

## Known limits

- **`bridge_mode` is a single-choice enum**. `mesh_bridge` (dual-Meshtastic
  cross-preset) and `mqtt_bridge` (Meshtastic ↔ RNS) cannot run on the same
  gateway instance today. Option B support (`connection_type=serial` in
  `MeshtasticConfig`) is merged and unit-tested, but field-deploying
  dual-radio on a box that also bridges to NomadNet needs either (a) a
  second gateway service instance running its own config, or (b) a
  concurrent-modes refactor in `bridge_cli.py`. Tracked as future work.

- The **RNSSniffer** throws `TypeError: received_announce() got an
  unexpected keyword argument 'destination_hash'` in a background thread on
  startup. Cosmetic — does not break bridging — but shows up as noise in
  `journalctl`. RNS 1.1.x signature change; fix is small and orthogonal.

- `monitoring.traffic_storage` warns "Traffic log not writable
  ([Errno 13])" at `/home/wh6gxz/.cache/meshforge/logs/traffic.log`.
  Harmless; the log just disables. Fix:
  `sudo chown -R $(id -un):$(id -gn) ~/.cache/meshforge`.

## Fleet truth table (2026-04-24)

| Box | Gateway LXMF hash | RNode attached? | Gateway bridging live |
|-----|-------------------|-----------------|----------------------|
| moc | `3dfbdb5d24c6de195ae4f3c0f56b5ea5` | no | yes |
| moc1 | `f5bb192d77191232032c5a6e9fc154f1` | no | yes |
| moc2 | `b185b0de9b53398ef9957e686e67855a` | no | yes |
| moc3 | `f68c2f56cb61527b6c9ad603b9a5009a` | yes (903.625 MHz, SF7) | yes |

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

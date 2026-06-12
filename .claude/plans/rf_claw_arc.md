# RF Claw arc — reclaiming the V4's dormant radios

> Operator's vision (06-11): "the V4 carries THREE radios — WiFi (in use),
> BLE (dormant), SX1262 915 MHz + antenna (dormant). The fork owns the
> firmware, so the radio is reclaimable." Started 2026-06-12.

## The three shapes, phased

| Phase | Shape | Status |
|-------|-------|--------|
| 1 | **Mesh ears** — RX-only Meshtastic leg: the claw hears the fleet mesh | ✅ **LIVE 06-12** — first light: 9 pkts / 2 min, 2 distinct nodes, 0 crc_err, RSSI −103..−106, incl. channel-hash 0x08 traffic moc's own journal logs as undecodable — the RF witness corroborates the fleet radio view. Fork branch `pr/lora-mesh-ears` (stacked on vbat), claw flashed `0.4.0+dudeclaw.4` (operator-ratified), tag `dudeclaw.3` = prior tip. moc2: `claw_sensors.with_ears.json` STAGED (mesh_heard_age_s gt 1800) — SOAK heard-rate incl. overnight before enabling. |
| 2 | **Mesh voice** — TX leg: claw broadcasts onto the mesh | ✅ **ON THE FLEET CHANNEL 06-12** — claw `+dudeclaw.8` set to `meshforge` (hash 0xa2, persisted to flash), `mesh_send` DECODED by moc's gateway on the PRIVATE fleet channel: `Received text msg from=0xb29faaa0, id=0xd273e476, msg=dude-claw on the fleet channel` (no undecodable line = name+PSK match moc's channel exactly). Durability proven (test-channel reboot cycle restored from flash). Earlier public proof: — claw flashed `0.4.0+dudeclaw.5` (operator-ratified, Option A public-channel low-power), `mesh_send` fired, and **moc's radio DECODED the full text**: `Received text msg from=0xb29faaa0, id=0x5284794c, msg=dude-claw first voice de WH6GXZ` — node id + pkt id match the claw's report exactly. Because moc listens on public LongFast, the claw appears as a real DECODED node in the NOC, not just an undecodable blip. Airtime guard verified (rapid sends refused, 0 RF). The whole stack (header/hash/AES-CTR/protobuf) proven on air. |
| 3 | **BLE ingestion** — beacons/tags as virtual sensors | future; independent radio, parallel-able |

**Why ears first**: Meshtastic packet HEADERS are plaintext (to/from/id/
flags/channel-hash + radio RSSI/SNR) — an RX-only leg needs NO channel PSK,
NO protobuf, NO crypto, transmits NOTHING (zero regulatory surface), and
delivers a genuinely new fleet capability: an **independent RF witness**.
Today "mesh silent" judgments ride meshtasticd's pipeline (`channel_feed_dark`
watches journal JSON lines); the claw's ears can distinguish *the pipeline
went dark* from *the AIR went dark* — a second opinion the silence-is-the-
failure-mode creed has never had. TX (Phase 2) adds PSK handling, AES-CTR,
protobuf Data encode, packet-id discipline, and airtime ethics — properly a
separate ratified step.

## Verified radio facts (do not re-derive)

- **V4 SX1262 wiring** (Meshtastic firmware `variants/esp32s3/heltec_v4/variant.h`,
  matches the official V4.2.0 datasheet pinmap): SCK=9 MISO=11 MOSI=10 CS=8,
  RESET=12, BUSY=13, IRQ/DIO1=14, **DIO2 = RF switch**, **TCXO on DIO3 @ 1.8 V**.
  (Same variant also confirms our battery leg: BATTERY_PIN 1, ADC_CTRL 37
  HIGH, multiplier 4.9×1.045 — Meshtastic adds a 4.5% empirical trim over the
  schematic 4.9; ours uses the schematic value.)
- **Fleet primary preset = LongFast** (proven from moc's journal topic
  `msh/2/json/LongFast/…`): **BW 250 kHz, SF 11, CR 4/5**; US region default
  frequency slot 20 → **906.875 MHz** (meshtastic.org radio-settings).
- **Preamble 16** symbols (`RadioInterface.h`), **sync word 0x2B** (Meshtastic
  private network sync — empirically confirmed at first RX; the fleet provides
  constant traffic, moc's journal shows steady packets incl. SNR −22 fringe
  traffic).
- **PacketHeader (16 bytes, plaintext)**: to(4) from(4) id(4) flags(1)
  channel(1) next_hop(1) relay_node(1) — then the AES-encrypted payload we do
  NOT touch in Phase 1.

## Phase 1 design (fork branch `pr/lora-mesh-ears`, stacked on pr/vbat)

- Guarded optional `WIRECLAW_LORA_SX1262` (heltec-v4 env only; stock envs
  no-op) — the established display/vbat pattern. RadioLib lib_dep.
- `lora_ears` module: begin (TCXO 1.8 V, DIO2-as-RF-switch, LongFast params
  from build flags), `startReceive()`, DIO1 IRQ → loop-drained. Honest
  absence: begin failure → "running deaf" serial log; `loraEarsAvailable()`
  false; the tool reports the truth.
- Counters: packets heard, CRC errors, last-heard (from/to/channel-hash/
  RSSI/SNR/age). **`mesh_heard_age_s` leads the tool output** so mini's
  nats_sensor numeric extractor reads it directly. Before ANY packet, age =
  seconds since radio start (an honest lower bound — "deaf forever" must
  still be able to alarm; reporting −1/none would keep a gt-threshold quiet
  forever, the #80 error-reads-as-quiet trap).
- `lora_stats` tool (no params) registered everywhere tools are (TOOLS_JSON,
  toolExecute, discover, docs/skill, counts 22→23).
- mini side: sensor spec `{"sensor":"mesh_heard_age_s","tool":"lora_stats",
  "op":"gt","threshold":1800}` + a quiet annotate rule — **SOAK FIRST**: the
  claw's RF reach at the AREDN site with the stock antenna is unmeasured;
  observe real heard-rates before wiring any alert (else instant false
  "RF dark"). Spec staged, not enabled, same discipline as the battery leg.

## Phase 2 BUILT (mesh voice) — 06-12

`mesh_send` tool: broadcasts a Meshtastic TEXT_MESSAGE_APP packet. Lives in
`lora_ears.cpp` (single radio owner): standby → FEM to TX → transmit → FEM to
RX → resume `startReceive` (half-duplex, RX always restored). Protocol from
authoritative Meshtastic source, byte-verified on the host before any RF
(`scripts/verify_mesh_packet.py` round-trips through a receiver's decode):
- 16-byte header: to/from/id LE, flags = hop_limit | hop_start<<5, channel hash
- Data protobuf (portnum 1 + payload), AES-CTR nonce [0:8]=pktId [8:12]=from
- channel hash = xorHash(name) ^ xorHash(psk); public LongFast = **0x08** ✓

**Secret-safe**: ships defaulting to the PUBLISHED public key (fine in source);
a private channel PSK is runtime-only via config `lora_tx_psk` (masked, mirrors
nats_token), NEVER compiled in. **Restraint by construction**: 2 dBm default
SX1262 drive through the GC1109 FEM (CSD=2/VFEM=7/TX_EN=46), ≥30 s min interval,
200-char cap. Node id = low-4 MAC → stable `!xxxxxxxx` in every receiver's log.

### The operator's first-TX decision (gates the on-air step)

| Option | What proves it | Needs |
|--------|---------------|-------|
| **A. Public channel, low power** | moc journal logs `fr=!<claw id>` undecodable-hash-0x08 packet | nothing (no secret); flash +dudeclaw.5 |
| **B. Fleet channel** | claw decodes into a real NOC node (`Received text msg`) | set `lora_tx_psk` to the fleet PSK (custody decision); flash |

A is the recommended first light — no secret touched, unambiguous (grep moc's
journal for the claw's node id), reversible scope (a few low-power packets on
the public ISM band the fleet already shares). B is the full vision and the
real "operator PSK decision" the plan flagged.

### Putting it on the FLEET channel (06-12) — built, operator does the secret step

`mesh_set_channel {name, psk}` tool + `lora_tx_channel` config (claw on
`0.4.0+dudeclaw.6`): channel identity (name+PSK) is now fully runtime — the
private channel needs the NAME too (hash = xorHash(name) ^ xorHash(psk)).
**The fleet PSK is the operator's to move** — the classifier (correctly)
walls me off from reading the prod channel store, and the key must never
enter the transcript/git/flash. Turnkey helper `scripts/claw_set_fleet_channel.py`
(getpass, no echo; sends over the local pinholed NATS bus; RAM-only; prints
only the returned channel-HASH byte, which IS the cleartext header field, not
a secret). Operator runs ON moc2:

    PYTHONPATH=/opt/meshforge/src python3 scripts/claw_set_fleet_channel.py \
        --device dudeclaw-01 --name <fleet-channel-name>

Then `mesh_send` → the gateway's `journalctl -u meshtasticd | grep 'Received
text msg'` decodes the claw ON THE FLEET CHANNEL (vs the public-channel proof
below). The returned hash must equal the fleet channel's hash — that's the
non-secret confirmation the right key landed.

**Durability (operator chose NATS-persist, +dudeclaw.7)**: the helper now
persists by DEFAULT — `mesh_set_channel {persist:1}` writes the channel to a
dedicated flash file `/lora_channel.json` (NOT config.json → no cross-branch
clobber; PSK in the device's own flash = Meshtastic's own channel-store trust
model), restored at boot (most-recent-intent wins over the config.json keys).
`--no-persist` for RAM-only. Result string shows `[persisted]`.

**✅ DURABILITY PROVEN 06-12 (no-secret test-channel reboot cycle)**: set a
throwaway `clawtest`+seq-key channel persist:1 → `[persisted]` hash 0x0f →
rebooted the claw via `dudeclaw-01.cmd "reboot"` → it came back (uptime 14s)
on `ch 'clawtest' (hash 0x0f)` = RESTORED FROM FLASH. So write + restore both
work; the earlier fleet set that showed public (0x08) post-reboot was NOT a
persistence-mechanism failure (most likely that run didn't carry persist).
Cleaned up to public default after. The fleet helper run (persist default)
will now survive reboots. Reboot tool: WireClaw `<device>.cmd "reboot"`.

### Key-format robustness (+dudeclaw.8, 06-12)

First real-key `--verify` run errored `bad channel/key` — the decoder only took
standard base64 + tight hex. +dudeclaw.8 accepts **standard base64, base64url
(`-`/`_`), plain hex, and hex with `0x`/colons/whitespace**; on failure the
tool reports the DECODED LENGTH (`key decoded to N bytes, need 16 or 32`) — a
length, not the key, so a truncated/wrong-size key is diagnosable without the
secret. Verified live: base64url→0x3f, colon-hex→0x0f (both match computed).
Persistence durability already PROVEN (test-channel reboot cycle restored from
flash). Awaiting operator's real-key retry (`--verify`, on moc2).

### ✅ Option A executed 06-12 — and it landed BETTER than a blip

moc doesn't just *receive* the public LongFast channel, it **decodes** it (it
has the default public key configured alongside the fleet channel). So the
claw's first transmission showed up fully decoded in moc's journal:
`Received text msg from=0xb29faaa0, id=0x5284794c, msg=dude-claw first voice
de WH6GXZ`. The claw is now a real, decoded node on the public mesh — the
"real NOC node" outcome (Option B's goal) reached on the PUBLIC channel with
no fleet secret. To put it on the FLEET channel specifically (so it rides the
fleet's custom-PSK traffic), set `lora_tx_psk` to the fleet PSK and reflash —
that remains a clean later step, now de-risked. Node id `!b29faaa0` = low-4 of
the claw MAC `80:f1:b2:9f:aa:a0` ✓.

## Regulatory note

Phase 1 transmits nothing. Phase 2 operates inside the 915 MHz ISM rules the
fleet's other Meshtastic nodes already follow (and the operator is a HAM).

# RF Claw arc — reclaiming the V4's dormant radios

> Operator's vision (06-11): "the V4 carries THREE radios — WiFi (in use),
> BLE (dormant), SX1262 915 MHz + antenna (dormant). The fork owns the
> firmware, so the radio is reclaimable." Started 2026-06-12.

## The three shapes, phased

| Phase | Shape | Status |
|-------|-------|--------|
| 1 | **Mesh ears** — RX-only Meshtastic leg: the claw hears the fleet mesh | ✅ **LIVE 06-12** — first light: 9 pkts / 2 min, 2 distinct nodes, 0 crc_err, RSSI −103..−106, incl. channel-hash 0x08 traffic moc's own journal logs as undecodable — the RF witness corroborates the fleet radio view. Fork branch `pr/lora-mesh-ears` (stacked on vbat), claw flashed `0.4.0+dudeclaw.4` (operator-ratified), tag `dudeclaw.3` = prior tip. moc2: `claw_sensors.with_ears.json` STAGED (mesh_heard_age_s gt 1800) — SOAK heard-rate incl. overnight before enabling. |
| 2 | **Mesh voice** — TX leg: claw broadcasts telemetry onto the fleet channel | gated on Phase 1 soak + operator PSK decision |
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

## Phase 2 sketch (mesh voice — NOT this session)

TX onto the fleet channel: channel PSK (AES-128/256-CTR), protobuf `Data`
encode (portnum TEXT_MESSAGE/TELEMETRY), channel hash byte, packet-id
randomization, hop_limit, listen-before-talk + duty restraint. Claw appears
as a real node in the NOC pipeline (journal `Received text msg` is the
proof). Operator decisions: which channel + PSK custody (the rotation
checklist gains a consumer), TX power, airtime budget.

## Regulatory note

Phase 1 transmits nothing. Phase 2 operates inside the 915 MHz ISM rules the
fleet's other Meshtastic nodes already follow (and the operator is a HAM).

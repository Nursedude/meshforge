# RF Claw arc — reclaiming the V4's dormant radios

> Operator's vision (06-11): "the V4 carries THREE radios — WiFi (in use),
> BLE (dormant), SX1262 915 MHz + antenna (dormant). The fork owns the
> firmware, so the radio is reclaimable." Started 2026-06-12.

## The three shapes, phased

| Phase | Shape | Status |
|-------|-------|--------|
| 1 | **Mesh ears** — RX-only Meshtastic leg: the claw hears the fleet mesh | ✅ **LIVE 06-12** — first light: 9 pkts / 2 min, 2 distinct nodes, 0 crc_err, RSSI −103..−106, incl. channel-hash 0x08 traffic moc's own journal logs as undecodable — the RF witness corroborates the fleet radio view. Fork branch `pr/lora-mesh-ears` (stacked on vbat), claw flashed `0.4.0+dudeclaw.4` (operator-ratified), tag `dudeclaw.3` = prior tip. moc2: `claw_sensors.with_ears.json` STAGED (mesh_heard_age_s gt 1800) — SOAK heard-rate incl. overnight before enabling. |
| 2 | **Mesh voice** — TX leg: claw broadcasts onto the mesh | ✅ **ON THE FLEET CHANNEL 06-12** — claw `+dudeclaw.8` set to `meshforge` (hash 0xa2, persisted to flash), `mesh_send` DECODED by moc's gateway on the PRIVATE fleet channel: `Received text msg from=0xb29faaa0, id=0xd273e476, msg=dude-claw on the fleet channel` (no undecodable line = name+PSK match moc's channel exactly). Durability proven (test-channel reboot cycle restored from flash). Earlier public proof: — claw flashed `0.4.0+dudeclaw.5` (operator-ratified, Option A public-channel low-power), `mesh_send` fired, and **moc's radio DECODED the full text**: `Received text msg from=0xb29faaa0, id=0x5284794c, msg=dude-claw first voice de WH6GXZ` — node id + pkt id match the claw's report exactly. Because moc listens on public LongFast, the claw appears as a real DECODED node in the NOC, not just an undecodable blip. Airtime guard verified (rapid sends refused, 0 RF). The whole stack (header/hash/AES-CTR/protobuf) proven on air. |
| 3 | **BLE ingestion** — beacons/tags as virtual sensors | 🟡 **COEXISTENCE BUILD LIVE 06-12 PM, SOAK RUNNING** — claw `+dudeclaw.12`, all THREE radios up (WiFi+NATS, LoRa RX/TX, BLE passive scan); 8 unique BLE devices heard in the first 2 min at the AREDN site. Validation gate = the soak (`claw_ble_soak` cron on moc2, 30-min ticks → `~/claw_ble_soak.log`): NATS link stays up, heap ≥~25k & no slow leak, WiFi RSSI ≈ −38 dBm baseline. Sensor features (v1 target presence) ONLY after the soak passes. Build journey below. |

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

---

## Phase 3 — BUILD RECORD (06-12 PM): the coexistence gate was MEMORY, not airtime

Fork branch `pr/lora-ble-ingest` (stacked on voice; pushed to fork). Claw on
**`0.4.0+dudeclaw.12`**; flashed tips tagged `dudeclaw.8`–`dudeclaw.11`.

**The journey (+dudeclaw.9 → .12)** — the make-or-break risk manifested as
HEAP, not WiFi airtime:
- **.9**: first BLE build. NimBLE observer-only passive scan (48/320 ms duty,
  host task on core 0, `setMaxResults(0)` = callback-only, no per-device heap).
  Flew DEAF: ble_stats "no scanner", ~60 kB heap consumed anyway, free heap
  21 kB. One-shot init had no witness and no cleanup.
- **.10**: failure-stage witness + loop retries (5 s tick, 12 tries, then
  deinit + reclaim). Verdict: `init failed (try N/12)` — NimBLEDevice::init
  itself, every retry. ⚠️ This board's app Serial is NOT on the USB port
  (only the ROM banner is) — `ble_stats` is the ONLY failure window, which is
  why the witness lives in the tool, not the log.
- **.11**: `esp_bt_controller_get_status()` added to the witness + controller
  normalize (disable/deinit back to IDLE) before each retry — a failed init
  leaves the controller half-up and every naive retry dies on INVALID_STATE.
  Verdict: **ctrl status 2 (ENABLED) yet init false** → the only false-return
  past enable is `esp_nimble_hci_init` → its failure paths are all
  allocation-shaped → **OOM**: BLE bring-up needs ~70 kB free, the V4 had ~56.
- **.12 — the cure: V4 "NATS-edge lean profile"** (36.8 kB of .bss returned
  to the heap pool). `LLM_MAX_REQUEST/RESPONSE/TOOLCALLS_JSON` now
  `#ifndef`-tunable (stock defaults untouched; oversize requests FAIL LOUD);
  V4 sets 4k/2k/2k — its on-device tool-agent is unused (no API key, rules
  compile off-device) and TOOLS_JSON alone (~10.5 kB) no longer fits, BY
  DESIGN. Plus scan-only NimBLE trims: `CONFIG_BT_NIMBLE_MAX_CONNECTIONS=1`,
  20-entry controller duplicate cache. Result: total heap 173.6→210.5 kB,
  **BLE up first try**, ~30 kB free steady with all three radios running.

**SOAK (the gate, running now)**: `claw_ble_soak` cron on moc2 (*/30,
cron-verdict-wired) → `~/claw_ble_soak.log` (DI = heap/RSSI/uptime, BS =
ble counters). Judge after ~24 h: (a) NATS kept answering / no reboots
(uptime monotone), (b) free heap flat ≥~25 kB, (c) WiFi RSSI ≈ −38 dBm,
(d) ble restarts ~0. PASS → v1 target-presence (`ble_seen_age_s`,
`ble_set_target` with the `/lora_channel.json`-style persistence) + mini
sensor spec. FAIL → duty-cycle down or reconsider.
⚠️ Known trade on V4: on-device LLM agent requests now fail loud
("Request too large for buffer") — V4 is a NATS-edge node by design.
⚠️ deploy tag hygiene: `dudeclaw.11` was briefly mis-tagged onto a stray
hand-edit of the deploy branch (lean commit committed on `dudeclaw` by
mistake); repaired same hour — retagged to the flashed residue tip,
lean commit cherry-picked to the pr branch, deploy REBUILT per FORK.md.

## Phase 3 — BLE ingestion: DESIGN NOTES (06-12, for a clean session)

> Handoff written deliberately at the end of a very long session (the whole
> dude-claw arc shipped in it). Phase 3 is a meaty new firmware feature with
> one genuine make-or-break risk (coexistence) — it deserves fresh context.
> Everything a clean session needs is here.

### Goal
BLE advertisements from nearby beacons / tags / sensors become **virtual
sensors** in the mini-dudeai model — the same shape as `lora_stats` /
`battery_read`: a `tool_exec` tool returns a number mini's `nats_sensor`
extracts, thresholds, and alerts on. Reclaims the V4's third radio (BLE 5.0,
dormant — WireClaw only uses WiFi).

Start SIMPLE (presence/proximity), grow later (decoded sensor formats):
- **v1 — presence + RSSI of a named device.** Tool `ble_stats` (mirror
  `lora_stats`) leads with `ble_seen_age_s` (seconds since a target MAC/UUID
  was last heard — honest lower bound = since-scan-start before first sight,
  the #80 "deaf-forever must still alarm" rule) + last RSSI. A door-tag going
  silent or a person's beacon leaving range becomes an alert.
- **v2 — decoded sensor adverts.** BTHome v2 (open, unencrypted temp/humidity/
  battery — the cleanest), RuuviTag (RAWv2 manufacturer data), Xiaomi/ATC
  (MijiaBLE). Each decoded field → a virtual sensor. iBeacon/Eddystone = UUID
  presence only.

### ⚠️ THE make-or-break risk: BLE + WiFi coexistence
WiFi is **load-bearing** on the claw — it carries the entire NATS brain link.
BLE and WiFi share ONE 2.4 GHz radio on the ESP32-S3 (the radio does one
thing at a time). Validated facts (Espressif coexistence guide + NimBLE
forums, 06-12):
- **Use NimBLE-Arduino, NOT Bluedroid** — ~100 kB less RAM, ~50% less flash.
  WireClaw is already memory-tight (`platformio.ini` notes classic ESP32
  overflows DRAM; the S3 has 2 MB PSRAM + 16 MB flash but **heap/DRAM is the
  constraint**). NimBLE is the only viable stack here.
- ESP32-S3 has a **hardware coexistence arbiter** (time-slices BLE/WiFi) — it
  must be ENABLED (it is, in arduino-esp32 default, but verify).
- **Pin BLE scan to one core, NATS/WiFi to the other** (S3 is dual-core) so
  they don't fight for CPU. WireClaw's loop runs on one core; the BLE scan
  should run as its own task on the other (`xTaskCreatePinnedToCore`), or use
  short non-blocking scan windows from loop() and accept lower duty.
- **Scan PASSIVELY** (no scan-requests/connections) — lowest airtime + power,
  and pure listening matches the "ears" philosophy (RX-only, no TX, like the
  LoRa ears). Active scan is unnecessary for adverts.

**VALIDATION GATE before any feature work**: flash a minimal NimBLE
passive-scan build, then confirm over a soak that (a) the NATS link stays up
(discover keeps answering, no reconnect storms), (b) `device_info` heap stays
healthy (no slow leak / OOM), (c) WiFi RSSI / throughput isn't degraded. If
coexistence destabilizes the NATS link, that's a STOP — the brain link wins;
fall back to duty-cycled short scans or reconsider. Prove stability FIRST,
build sensors second. (This is the soak-first discipline from the ears + the
battery legs.)

### Design (mirror the established guarded-optional pattern)
- New build flag `WIRECLAW_BLE` (heltec-v4 env only; stock envs no-op inlines
  — same as `WIRECLAW_OLED` / `WIRECLAW_LORA_SX1262`). lib_dep
  `h2zero/NimBLE-Arduino`.
- New `src/ble_scan.cpp` + `include/ble_scan.h` mirroring `lora_ears.*`:
  `bleScanInit()` (honest "running BLE-deaf" on init fail; `bleAvailable()`
  reports truth), `bleScanTick()` or a pinned task, `bleStats(out,len)`.
  Target device(s) configurable at runtime (`ble_set_target {mac/uuid}`,
  RAM + optional flash persist — REUSE the `/lora_channel.json` persistence
  pattern, dedicated file, never config.json).
- `ble_stats` tool (and later `ble_read`) registered EVERYWHERE tools are:
  TOOLS_JSON, `toolExecute`, the `_ion.discover` tools list, docs (TOOLS.md /
  OPENCLAW.md / README counts), `skill/wireclaw/SKILL.md`. (The discover list
  is in `src/main.cpp` — easy to forget; grep for `mesh_set_channel` to find
  all the registration sites.)
- mini side: a sensor spec in `claw_sensors.*.json` on moc2
  (`{"sensor":"ble_seen_age_s","tool":"ble_stats","op":"gt","threshold":...}`)
  STAGED then soaked before enabling — same discipline as ears/battery.

### Hardware/deployment reality (operator question for the clean session)
The claw lives at the **AREDN site on moc1's USB** (remote). BLE range is
~10 m typical. For Phase 3 to do anything useful there must be a **BLE device
in range of the claw's physical location** — a beacon, a BTHome sensor, a
phone. Decide the demo target before/early: what BLE thing is near moc1, or
does the operator place one? (Cf. the ears: real value needed real traffic;
BLE needs a real beacon.)

### Cold-start facts a clean session needs
- **Claw**: `dudeclaw-01`, on `0.4.0+dudeclaw.12`, node `!b29faaa0` (MAC
  80:f1:b2:9f:aa:a0), at `10.<aredn-site>.199`, USB-powered off **moc1**. On the
  **fleet `meshforge` channel** (hash 0xa2, persisted) + LoRa ears RX active
  + **BLE passive scan running** (soak gate — see build record above).
- **Brain**: `meshforge-mini-dudeai-claw.service` (user unit) on **moc2**;
  env `~/.config/meshforge/mini_dudeai_claw.env`. NATS server `nats-server`
  unit on moc2 (`127.0.0.1:4222`, pinholed to moc2+lo). Claw control = NATS
  `tool_exec` / `cmd` from moc2 ONLY.
- **Reach the claw**: `ssh moc2`, `set -a && . ~/.config/meshforge/mini_dudeai_claw.env
  && set +a && PYTHONPATH=/opt/meshforge/src python3 -m mini_dudeai.nats_client
  req dudeclaw-01.tool_exec '{"tool":"device_info"}'`. Discover:
  `req _ion.discover "" --many`. **Reboot tool**: `req dudeclaw-01.cmd "reboot"`.
- **Fork**: `~/src/wireclaw-dudeclaw` on **VolcanoAI**. Branch model in
  `FORK.md` — **THE INVARIANT**: `dudeclaw` (deploy) = upstream `main` + merge
  each `pr/*` + ONE residue commit (FORK.md + version.h only), REBUILT never
  hand-edited. Phase 3 branch: `pr/lora-ble-ingest` stacked on
  `pr/lora-mesh-voice` (its sibling stack: display→vbat→ears→voice). Rebuild
  recipe + per-PR convergence state machine in `FORK.md`.
- **Build/flash**: `~/.local/bin/pio run -e esp32-s3-heltec-v4` on VolcanoAI
  (pipx platformio; ALSO build `-e esp32-s3` to prove the guarded-optional
  stays byte-clean). App-only reflash preserves config:
  `scp .pio/build/esp32-s3-heltec-v4/firmware.bin moc1:/tmp/fw.bin` then
  `ssh moc1 '~/.local/bin/esptool --chip esp32s3 --port /dev/ttyACM0
  write-flash 0x10000 /tmp/fw.bin'`. ⚠️ apt esptool is dfsg-stripped (no S3
  stubs) — pipx esptool only.
- **Recurring rebuild gotcha**: merging `pr/nats-token-auth` into the deploy
  branch ALWAYS conflicts in `src/web_config.cpp` (both stacks grow the
  config-key table). Deterministic resolution = combine to 15 keys (base 13 +
  nats_token + lora_tx_psk + lora_tx_channel; if Phase 3 adds a ble config
  key, +1). 6 hunks: mask block, GET args, field table (`Field fields[N]` +
  loop bound), write loop (`if (i < N-1)`), 2 JS arrays. Pattern is in the
  git history of the last several `dudeclaw.N` rebuilds.
- **Secret discipline** (if BLE ever needs a key — most adverts don't):
  same as the fleet PSK — getpass/on-box only, NEVER through the transcript/
  git/logs; the device echoes only non-secret identifiers (hashes/lengths).
  The classifier (rightly) blocks reading prod secrets — design so the
  operator moves any secret on-box.
- **Upstream PRs still open**: #15 (display), #16 (NATS token auth) at
  `M64GitHub/WireClaw`; the LoRa + BLE branches are fork-first (upstream
  candidacy after the stack lands). `dudeclaw` force-push to fork/backup is
  operator-gated (deny-listed for me).
- **Verifier**: `scripts/verify_mesh_packet.py` (host-side protocol check)
  is the model for proving wire/format logic in software before hardware —
  consider a BLE-advert-decode unit check for v2.

### Tool-count bookkeeping (current = 26 on +dudeclaw.12)
led_set, gpio_write, gpio_read, temperature_read, device_register,
device_list, device_remove, sensor_read, actuator_set, rule_create,
rule_list, rule_delete, rule_enable, serial_send, chain_create, device_info,
file_read, file_write, nats_publish, remote_chat, display_print,
battery_read, lora_stats, mesh_send, mesh_set_channel, **ble_stats** (docs
already bumped to 26). v1 adds `ble_set_target` (+ later `ble_read`) →
bump counts again in TOOLS.md / OPENCLAW.md / README / SKILL.md.

# Dude-Claw bring-up — Heltec V4 (WireClaw edge) + moc2 brain — ✅ COMPLETE

> **Status: Phase A END-TO-END PROVEN 2026-06-11** (~20:04 HST). Sensor →
> rule → actuator + page, both edges, no LLM in the loop. Flash, portal
> config, and bring-up were all executed REMOTELY (esptool on the brain box;
> portal driven over HTTP from an in-subnet foothold) — no bench visit needed.
> Design: `standalone_wireclaw_variant.md` · Code: `a171fec`.

## As-built (differs from the original moc1 plan — topology discovery)

```
moc2 (BRAIN, collector box)                Heltec V4 "dudeclaw-01" (EDGE)
├─ nats-server v2.14.2 :4222          ◄──  WireClaw v0.4.0 · WiFi "DudeNET"
│   (LAN bind BEHIND nftables pinhole)     = an AREDN node's 10.x/28 LAN AP;
├─ meshforge-mini-dudeai-claw.service      egress NATs out the node's WAN
│   (enabled; 3 seed rules, 30s tick)      (the same WAN address that fronts
└─ fleet mini daemon (separate flock)      the legacy ".32" bot identity)
```

- **Why moc2, not moc1**: the claw's subnet (behind the AREDN node) reaches
  the main-LAN segment only; moc1 sits alone on a second wired segment that
  AREDN-LAN-originated traffic cannot reach (ICMP+TCP verified dead), and the
  AREDN mesh path to moc5 was dead too. moc/moc2/moc3 are on the reachable
  segment; moc2 (collector, RF-sparse, light) won. moc1's staging fully
  unwound; the operator ratified the re-home mid-session.
- **Pinhole before LAN bind**: WireClaw v0.4.0 has NO NATS auth fields
  (verified in firmware source), so `/etc/nftables.conf` on moc2 carries an
  additive, dport-4222-scoped rule: lo + the claw's NAT egress only, drop
  rest; policy accept everywhere else (soak-safe). The bus rebound to LAN
  only after the table was live.
- The V4 needs only USB **power** now (WiFi does everything) — it can move to
  any 5 V source within the AREDN node's WiFi range. It currently draws power
  from moc1's USB port, which is otherwise unrelated to it.

## Proof record (moc2, 2026-06-11)

- `_ion.discover` → full capability sheet (19 tools, `chip_temp` 30.6 °C,
  rgb_led, HAL gpio/adc/pwm/uart).
- Threshold dropped to 20 → grace held 2 ticks → **edge_up**:
  `nats_edge_up ok` reply `LED set to RGB(255, 0, 0)` + `ntfy_edge_up ok`
  (operator paged). Threshold restored → **edge_down**: LED off + quiet
  cleared notice. `~/mini_dudeai_claw_history.jsonl` on moc2 holds the record.
- Steady state: `rules=3 conds=0 src_errors=0`; both mini daemons (fleet +
  claw) active side by side — the state-file-keyed flock isolation working.

## Remote-flash recipe (for the next claw — no browser needed)

1. Plug the ESP32-S3 board into any fleet box (native USB → `/dev/ttyACM0`,
   `303a:1001`). **Verify the work-holder first**: confirm the box's radio is
   NOT a tty (moc1/moc2's MeshToad is CH341 **SPI**, VID 0x1A86) and
   `fuser /dev/ttyACM0` is empty.
2. `pipx install esptool` (Debian's apt esptool is dfsg-stripped of flasher
   stubs — chip-erase fails on S3 without them).
3. Firmware: `https://wireclaw.io/firmware/manifest.json` → per-chip parts.
   For S3: bootloader@0x0, partitions@0x8000, boot_app0@0xE000,
   firmware@0x10000, littlefs@0x290000.
4. `esptool --chip esp32s3 --port /dev/ttyACM0 erase-flash` then
   `write-flash` with the offsets above. Native-USB auto-download works —
   no BOOT button.
5. Portal without a phone: join the open `WireClaw-Setup` AP from any box
   with free WiFi (`nmcli con add type wifi ... ipv4.never-default yes
   ipv4.ignore-auto-dns yes` — soak-safe), then POST the form to
   `http://192.168.4.1/save` (fields: wifi_ssid, wifi_pass, device_name,
   nats_host, nats_port, timezone — POSIX form, e.g. `HST10`; leave
   api_key/model/telegram empty).
6. Reconfig later via the device's REST API (`/api/config` GET→edit→POST,
   then POST `/api/reboot`). ⚠️ GET **masks** `wifi_pass` (`...tail`) —
   never POST the mask back; always carry the real password.
7. Find the device afterwards: it appears in `ss -tn state established
   '( sport = :4222 )'` on the brain box once it dials in; inside its subnet,
   `ip neigh` shows the base MAC esptool reported.

## Display fork — ✅ LIVE 2026-06-11 (~20:40 HST)

The ratified next arc landed same-day. Fork: `~/src/wireclaw-dudeclaw` on
VolcanoAI (branch `dudeclaw`, commit `428f10c`, version `0.4.0+dudeclaw.1` —
`_ion.discover` reports it, which is the deploy check). See its `FORK.md`.

- **SSD1306 status screen** (pins from the official V4 pinmap: SDA17/SCL18/
  RST21/Vext36 active-LOW): device name + NATS marker (`N*`/`N-`), IP + RSSI,
  chip temp/heap/uptime, and **2 remote metric rows** with a 30-min `(old)`
  staleness suffix. Real I2C ACK probe at init (the lib's `init()` can't
  detect a missing panel); headless boards answer `display_print` with an
  honest error.
- **`display_print` tool**: `{"tool":"display_print","row":0..1,"text":"..."}`
  on `tool_exec`; empty text clears. Registered in TOOLS_JSON + dispatch +
  discovery.
- **White LED mapping**: V4 has no WS2812 — `led()` drives GPIO35 PWM
  (max RGB channel), so `led_set` actuations and the chip_temp rule are now
  visible.
- **Build/flash**: `pio run -e esp32-s3-heltec-v4` (pipx platformio on
  VolcanoAI; first build ~10 min) → app-only reflash preserves config:
  `esptool --chip esp32s3 --port /dev/ttyACM0 write-flash 0x10000 firmware.bin`.
  Flashed remotely over moc1's USB; claw rejoined + reconnected unaided.
- **Metrics pusher**: `scripts/claw_metrics_push.py` (MF `25cb23c`) on moc2's
  crontab every 5 min, cron-verdict-wired as `claw_metrics` (#78 probe
  watches it). Rows: `mesh:<directory total> fed:<ok>/<peers>` +
  `wd:<signals> OK|SIG <HH:MM>`. Fails loud (nonzero exit → FAIL verdict)
  rather than painting rows it couldn't compute. First push caught a live
  wrong-key bug (`reachable` vs the API's `ok` → painted `fed:0/4`) — the
  #80 class, fixed `25cb23c`.

## Operating notes

- Claw config surface: `http://<claw-ip>/` (reachable only from inside its
  subnet — e.g. via the bot box foothold) — tabs for config/prompt/memory/
  devices/rules/status.
- Brain env: `~/.config/meshforge/mini_dudeai_claw.env` on moc2
  (`MINI_DUDEAI_CLAW_TEMP_THRESHOLD` default 55; drop it to ~20 to re-run the
  demo). Rules: `~/mini_dudeai_claw_rules.json` (+ `.candidate` promotion).
- `claw_blind_any` pages on sustained NATS/device darkness (grace 300 s);
  while blind the engine HOLDS last-good breach state — silence never reads
  as recovery.
- ~~OLED stays dark on stock firmware~~ **Display fork LIVE — see section
  above.** Upstream-PR candidates: display tool (generalized pins) + NATS
  token auth (the gap that forced the pinhole posture).

## Phase B chat-compiler — ✅ LIVE 2026-06-11 (late)

`python3 -m mini_dudeai.chat --preset standalone` on the brain box. Backend:
Ollama 0.30.7 + qwen2.5:3b on the dev box (systemd unit, models under
/usr/share/ollama, HOME fix required — the service user needs a real home for
its signing key) behind an additive nftables **11434 pinhole** (lo + the
claw-brain box only; Ollama has no auth). Claw env carries
`MINI_DUDEAI_OLLAMA_URL`/`_MODEL`.

Production proof, including the lesson: the first compile of "note when the
sensor feed has been dark for ten minutes" produced a structurally-valid but
semantically-WRONG rule (`sensor_breach`/60s instead of `source_error`/600s)
— exactly what ratification exists to catch. Removed via the sanctioned
candidate path, prompt taught the darkness-vs-breach distinction + the
duration→grace_s conversion (test-pinned), recompiled correctly:
`claw_feed_dark_note` promoted live by the daemon. **Never run `--yes`
without reading the rendered rule.** 3B models compile; humans ratify.

## Deferred / next arcs

- Battery-voltage ADC sensor (verify V4 VBAT pin map) · push-subscribe sensor
  mode · TUI chat front-end (the CLI shipped first; the claw brain is
  headless) · fleet_roles.yaml claw-brain declaration once the pilot
  graduates · GitHub fork push + upstream PRs · Phase C/D per the design doc.

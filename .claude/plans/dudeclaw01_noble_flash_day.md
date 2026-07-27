# dudeclaw-01 flash day — retire BLE, land the anomaly + heap fixes

> **Status: PREPARED, NOT FLASHED (2026-07-26).** Firmware builds clean;
> claw-01 is behind an AREDN node with no inbound path and no OTA, so this
> needs a bench visit. Everything below is the ordered checklist for that
> visit. Operator decision to drop BLE: 2026-07-26.

## Why

dudeclaw-01 ran BLE + LoRa ears + WiFi + NATS in ~210 kB and sat at **~28 kB
free (14%)**, dipping to 17.3 kB, with **25–33% of NATS polls timing out**
and repeated reboots. dudeclaw-02, same board family but no BLE, held
**76.8 kB free and 15.6 days uptime**.

The BLE scan code was NOT at fault — NimBLE with `setMaxResults(0)`, bounded
duplicate cache, no accumulation, and heap measured **flat** (+6 kB/hour
slope over 28 samples, so not a leak). The cost was the stack's steady
~60 kB footprint, which this board could not afford.

Riding along: the anomaly warm-up fix (`WARMUP` was 60 vs `ALPHA` 1/1440 — a
24x gap that made every score a floor-pinned artifact) and three new
`device_info` fields (min free heap, max alloc block, `esp_reset_reason()`)
that make the reboot cause observable for the first time.

## Payload

Firmware `0.4.0+dudeclaw.18`, commits `540969f` (anomaly + witness) and the
BLE retirement on top. Env `esp32-s3-heltec-v4`.

Build deltas vs `.17`: RAM 172,848 → **165,772** (−7.1 kB static),
Flash 1,606,832 → **1,400,180** (−202 kB).

**Expected free heap after flash: ~95–100 kB (≈45%).** Derivation, and it is
an ESTIMATE not a measurement: current 28.4 kB + 7.1 kB static freed + the
~62 kB runtime BLE allocation inferred from the claw-01/claw-02 delta
(claw-02 has 14 kB *less* total heap yet 48 kB *more* free). **Verify against
the real number on flash day — this prediction is the whole point of the
change, so it must be checked, not assumed.**

## Order of operations

1. **Flash** (app only — LittleFS config partition is untouched, so WiFi/NATS
   settings survive; only a full-chip erase would force a portal reconfig):
   ```
   cd ~/src/wireclaw-dudeclaw
   pio run -e esp32-s3-heltec-v4 -t upload --upload-port <port>
   ```

2. **Confirm the build actually took** — do not infer it from "it booted":
   ```
   # version must read 0.4.0+dudeclaw.18
   # device_info must now carry the three new fields
   nats req dudeclaw-01.tool_exec '{"tool":"device_info"}'
   ```
   Expect `Min free heap:`, `Max alloc block:`, `Reset reason:` present, and
   `ble_stats` to return `{"ok": false, "error": "no BLE scanner on this
   device"}` — that refusal is CORRECT (it is the honest stub, not a fault).

3. **Retire the BLE soak cron — REQUIRED, do not skip.**
   `claw_ble_soak.sh` ends with `... && echo "$bs" | grep -q '"ok": true'`,
   so a no-BLE build makes it exit nonzero **every 30 minutes, forever**,
   which pages via `cron_verdict_stale_any`. It is already failing 23/30
   retained runs from the timeouts; after the flash it is 100% false.
   Its Phase-3 gate PASSED 2026-06-14 and its judge was retired 06-20 — it
   is vestigial.
   ```
   crontab -l > ~/crontab.bak.$(date +%Y%m%d)-claw-ble-soak-retire
   crontab -l | grep -v claw_ble_soak | crontab -
   crontab -l | grep -c claw_ble_soak   # must print 0
   ```
   Leave `~/claw_ble_soak.log` and the 06-14 verdict in place as the record.

4. **Re-enable the anomaly sensor.** It was disabled 2026-07-26 because it
   could not produce a trustworthy reading pre-fix; the warm-up fix is what
   makes it meaningful again.
   ```
   cd ~/.config/meshforge
   cp claw_sensors.battery.json.with-anomaly.bak claw_sensors.battery.json
   systemctl --user restart meshforge-mini-dudeai-claw
   ```
   Expect `anomaly_stats` to report `-1 (learning n/1440)` for the first
   ~24 h. **That is the fix working, not a fault** — the old build started
   scoring after 1 h against an unconverged baseline. Do not "fix" the -1.

5. **Verify the margin held** — a day later, not immediately:
   - `Min free heap` well clear of zero (the number a poll can never catch)
   - `Max alloc block` healthy (a large free total with a small max-alloc is
     fragmentation)
   - `Reset reason` on the next reboot: `PANIC` vs `TASK-WDT` vs `BROWNOUT`
     finally distinguishes crash / hang / power. **This is the field that
     settles whether heap pressure was ever actually the reboot cause — that
     remains UNPROVEN, inferred only from the claw-02 comparison.**

## What is NOT proven

- That heap pressure *caused* the reboots. It correlates against exactly one
  other device. Step 5 is the test.
- That ~95–100 kB free is what we get. It is arithmetic on an inferred BLE
  footprint, not a measurement.

## What we lose

BLE ears on claw-01 — passive advertisement listening (was ~30k advs, 32+
unique devices). claw-02 has never had it. Restore instructions are in the
`platformio.ini` comment block where the flags were removed; do NOT restore
BLE together with stock LLM buffers, or the heap margin is spent twice.

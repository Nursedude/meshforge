# dudeclaw-01 flash day — BLE retired, anomaly + heap fixes landed ✅

> **Status: DONE 2026-07-26.** Flashed `0.4.0+dudeclaw.18` over moc1's USB,
> `Hash of data verified`. Free heap 28.4 kB → **118.3 kB**; NATS timeouts
> 25-33% → **0/48**. BLE soak cron retired, anomaly sensor re-enabled.

## ⚠️ Read this first — how claw-01 is reached

**dudeclaw-01 is USB-attached to moc1 at `/dev/ttyACM0`** (MAC
`80:f1:b2:9f:aa:a0`, ESP32-S3 rev 2). It is also USB-POWERED from moc1 —
which is why `battery_read` shows a flat ~4.18 V forever and can never
breach the 3.5 V spec.

Its **WiFi** joins an AREDN node's `10.120.250.0/28` AP and NATs outbound to
moc2's NATS bus. **These are two independent channels.** No fleet box can
reach `10.120.250.199` inbound (all six route to their default gateway, no
ping) and the firmware has **no OTA** — but none of that matters, because
flashing goes over the USB cable and needs no IP at all.

> **The trap, recorded because I fell in it (2026-07-26):** I tested IP
> reachability to the claw, got "no path from any box", and concluded a bench
> visit was required — while this very doc's header already said the original
> flash was done REMOTELY. "Remote" meant *ssh to moc1, run esptool over its
> USB*, not OTA. I measured the wrong channel and let a true fact (no inbound
> IP) answer a question it had nothing to do with. The operator caught it.
> **Before concluding a device is unreachable, ask which channel the operation
> actually uses.**

## Identifying the board before writing to it

moc1's `ttyACM0` is an ESP32-S3 — and so is a Heltec Meshtastic node, so the
board is genuinely ambiguous and a wrong flash is destructive. `meshtasticd`
is active on moc1 but does NOT hold the port (`fuser` empty).

Non-destructive proof used: note claw-01's uptime over NATS, run
`esptool read-mac` (reads only, but hard-resets the board), then re-read
uptime. It went **20105 s → 34 s** while elapsed was 51 s. Same board.

## What shipped

Firmware `0.4.0+dudeclaw.18` — commits `540969f` (anomaly warm-up + heap
witness) and `eac93a3` (BLE retirement). Host-side readers: MF `1b707c6c`,
MA twin `d348ac39`.

1. **Anomaly warm-up** — `WARMUP` was 60 samples (~1 h) while `ALPHA` was
   1/1440 (~1 day). Scoring began 24x before `var` converged, so `sqrtf(var)`
   sat at the per-feature FLOOR and z degenerated to `|x - mean| / FLOOR` —
   for temp_c a bare "is the chip 2 C off baseline" trip wire, and `fabsf()`,
   so cooling paged like overheating. Now one SSOT `TAU` with `WARMUP`
   derived from it and an `#error` guard against re-drift.
2. **Heap/reset witness** — `device_info` gained `Min free heap`,
   `Max alloc block`, `Reset reason`.
3. **BLE retired** from the `esp32-s3-heltec-v4` NATS-edge profile.

## Measured result

| | before | after |
|---|---|---|
| free heap | 28,448 (14%) | **118,332 (50%)** |
| total heap | 210,140 | 237,008 |
| min free heap | unobservable | 97,812 |
| max alloc block | unobservable | 77,812 |
| NATS timeout rate | 25-33% | **0/48** |
| anomaly | false-paged at n<1440 | `-1 (learning n/1440)` |

**My pre-flash prediction was 95-100 kB and it MISSED — the real number is
118,332.** I sized it from the linker's 7.1 kB static RAM delta, but total
heap actually rose 26.9 kB: NimBLE reserves DRAM beyond what "RAM used"
reports. Wrong arithmetic, better outcome. Recorded because the runbook said
to measure rather than assume, and that is the only reason it was caught.

## Still open

- **Reset reason reads `unknown`** after an esptool RTS reset — expected.
  Its real value arrives on the next ORGANIC reboot, and that is what
  finally settles whether heap pressure ever actually caused the reboots.
  Until then that link remains **inferred, not proven** — it rested on a
  single comparison device (claw-02).
- **The 0/48 timeout result is ~2 minutes of evidence**, against a pre-flash
  rate measured over far longer. Strong early signal, not a soak.
- **Anomaly stays `-1` for ~24 h** by design. With `op: gt, threshold: 4.0`,
  -1 can never breach, so no pages during warm-up. Do not "fix" the -1.

## Done at flash time

- [x] Flash app-only at `0x10000` (LittleFS config preserved — no portal
      reconfig needed; claw rejoined WiFi + NATS unaided)
- [x] Verify version + the three new fields present, `ble_stats` refusing
- [x] Retire `claw_ble_soak` cron — it gates its exit on `ble_stats ok:true`,
      so a no-BLE build fails it every 30 min forever via
      `cron_verdict_stale_any`. Backup: `~/crontab.bak.20260726-claw-ble-soak-retire`
- [x] Re-enable the anomaly sensor from `claw_sensors.battery.json.with-anomaly.bak`

## Reflash recipe

```bash
pio run -e esp32-s3-heltec-v4                      # on VolcanoAI
scp .pio/build/esp32-s3-heltec-v4/firmware.bin moc1:/tmp/fw.bin
ssh moc1 'sudo ~/.local/bin/esptool --chip esp32s3 --port /dev/ttyACM0 \
          write-flash 0x10000 /tmp/fw.bin'
```
Verify the md5 after copy, and confirm the version string from `device_info`
rather than inferring success from "it booted".

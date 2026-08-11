# moc1 WisBlock planning dossier — pre-read (2026-08-10)

> Prepared warm the night before the planning session so frontier time is
> spent DECIDING, not fetching. Facts tagged [V] were captured live
> 2026-08-10; [R] researched (sources at bottom); [B] believed; [U] unknown —
> a question the planning session should answer.

---

## 0. PRIORITY QUEUE — leftover items first (operator: close these out)

| P | Item | Shape | Disposition |
|---|------|-------|-------------|
| **P1** | **Patched-binary provenance gap** — VolcanoAI/moc1/moc5/kiai each run an on-box-built `meshtasticd-patched` with no staged restore copy (the alaula class, Debian flavor). moc1's: 88.5 MB, built Jul 27, sha `d4c358d3…` [V] | mechanical | **Partly obviated by this plan**: moc1→SPI removes it from the set (4→3). For the rest: capture a provenance manifest (path+sha+build date+recipe pointer) into `fleet-configs` — binaries themselves are rebuildable (~7–26 min, recipe in persistent_issues), identities are not. One cheap session. |
| **P2** | **~200 pending dream deltas fleet-wide** [V from rollup] | judgment backlog | One cheap-tier pass, box by box; rejections carry structured reasons (the `note_only ×49` lesson — fill the field). No frontier needed. |
| **P3** | **Running-code skew** — ~47–49 units fleet-wide loading pre-HEAD code [V honest_status NOTE] | disclosure, not fault | Fold into the moc1 hardware day: any box touched gets its restart then. A deliberate fleet rolling-restart window is a separate small plan (gateway-soak aware — never during a soak). Don't build machinery for it. |

These stop being ambient nag once P1's manifest lands and P2 runs once; P3
rides existing events.

---

## 1. moc1 today [V — captured live tonight]

| Fact | Value |
|------|-------|
| Hardware | **Raspberry Pi 5 Model B**, 4 GB RAM, 117 GB SD (11% used) |
| OS | Debian 13 (trixie) |
| Radio | **USB CH341 toad** (`1a86:5512` on USB) → the #10468 leak path |
| meshtasticd | `/usr/local/sbin/meshtasticd-patched` (88.5 MB, Jul 27 build, sha `d4c358d3…`), weekly-restart backstop |
| Role | profile `full`, role `cloud-publisher` — but cloud-push **disabled** (moved to VolcanoAI 06-26) |
| ⚠️ Network | **AREDN-only since 06-24** — sits on the WH6GXZ-6-BI-ECOM AREDN node's LAN (172.27/16 behind the hAP NAT); lost its home-LAN route in the 06-24 outage. **Physical + network access for a hardware day needs planning** — and `hostname -I` ground truth before trusting any listener/IP assumption (07-12 lesson). |
| Buses | `/dev/spidev10.0` present; **user i2c NOT enabled** (no `dtparam=i2c_arm=on`); i2c-13/14 are RP1-internal |

**Why moc1 is the right target**: swapping USB→SPI removes it from the
patched-build maintenance set entirely (SPI radios measured clean, 3-for-3,
~30 maps flat for weeks), retires its weekly-restart backstop, and frees a
toad for the parked gap-site question (July verdict page).

## 2. The hardware, module by module

### RAK6421 WisMesh Pi HAT [R]
- 40-pin HAT for Pi 4/5; **4 sensor slots (A–D) + 2 IO slots**; extra headers
  expose AIN1, GPIO4/5, VBAT, and a second I2C bus.
- **Onboard EEPROM → meshtasticd auto-detects the HAT**; needs
  `dtparam=i2c_vc=on` + `dtoverlay=i2c0` in `/boot/firmware/config.txt` on a
  self-built image. Official setup guide: `RAKWireless/meshtastic-rak6421-guide`.
- [U] Which radio module ships/pairs for the LoRa slot in our kit vs the
  RAK13302 going in an IO slot — confirm slot assignment before wiring.

### RAK13302 1W LoRa module (SX1262 + SKY66122) [R]
- ~30 dBm TX, integrated RF filter, improved RX sensitivity; WisBlock **IO
  slot** mount. **Two power variants: battery-boost vs external 5 V input** —
  [U] which variant is in hand; on a Pi HAT the external-5V one is the sane
  choice (TX bursts at 1 W are beyond what a sensor slot should feed).
- Fleet precedent for 1W-class SPI: **moc2 already runs a MeshAdv E22-900M30S**
  (30 dBm, SX1262) — pin yaml captured below. Thermals/power on that box are
  the known-good reference.
- Legal/RF [B]: 30 dBm conducted sits at the FCC 15.247 ceiling (antenna gain
  limits apply); Part 97 alternatives collide with Meshtastic encryption —
  operator's call, HAM General.
- ⚠️ Two-preset fleet: **preset is a property of a LINK** — decide which RF
  segment moc1's new radio joins (LongFast/ch20 vs SHORT_TURBO/ch8) BEFORE
  first TX; a 1W node on the wrong preset is a very loud silent radio. All
  drills through tx_guard on the test channel.

### RAK12002 RTC (Micro Crystal RV-3028-C7) [R/B]
- The sleeper win: honest-failure-modes #6 exists BECAUSE the fleet is
  RTC-less. moc1 becomes the first box with trustworthy wall-clock across
  power loss.
- [B] Kernel path: `dtoverlay=i2c-rtc,rv3028` (+`backup-switchover-mode`
  param for supercap/battery backup); then remove `fake-hwclock` and let
  systemd/hwclock own it. Verify the consumer-of-record: `hwclock -r` after a
  cold boot, not the config line.
- [U] Does it slot on the HAT (sensor slot, i2c) alongside everything else —
  slot budget check.

### RAK1906 env sensor (BME680) [R/B]
- T/H/P + gas. [B] Kernel IIO driver covers raw T/H/P; the IAQ/VOC index
  needs Bosch's closed BSEC blob — **decide whether IAQ matters before that
  rabbit hole; raw values likely suffice** (enclosure temp ↔ Pi throttle
  correlation is the operational win).
- Footprint discipline: readings ride an EXISTING collector (kilo tick /
  scout-pattern field), never a new watcher.

### RAK10722 WisBlock Meshtastic Starter Kit (RAK4631) [R/B]
- nRF52840 + SX1262 standalone node; first-class Meshtastic firmware target.
- Natural roles: portable drill node; the standalone study's T2 RF leg (a
  cold node joining the enclave channel); battery/solar experiments.

## 3. SPI config precedent (moc2, live) [V]

```yaml
# /etc/meshtasticd/config.d/lora-MeshAdv-900M30S.yaml (moc2, Pi 4)
Lora:
  Module: sx1262
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18
  TXen: 13
  RXen: 12
  DIO3_TCXO_VOLTAGE: true
```
The RAK6421 pin map WILL differ (use the RAK guide/EEPROM autodetect — the
available.d yaml for the HAT should come from RAK's guide, not adapted from
this) — but this is the proven shape: SPI sx1262 + TXen/RXen PA control on a
fleet box.

## 4. Provisioning diff sketch (moc1, USB→HAT) [B — the session hardens this]

1. `/boot/firmware/config.txt`: `dtparam=spi=on`, `dtparam=i2c_arm=on`,
   `dtparam=i2c_vc=on`, `dtoverlay=i2c0`, `dtoverlay=i2c-rtc,rv3028,…`
2. meshtasticd: RAK6421 yaml into `config.d/` (from RAK guide / EEPROM
   autodetect); **⚠️ #58 lesson — diff the whole effective config after any
   HAT yaml lands; upstream overlays have smuggled `Webserver: Port:` before.**
3. **Node identity carries over** — same `/etc/meshtasticd`/fsdir; the radio
   changes, the node does not. (⚠️ kiai lesson: patched-vs-stock builds
   defaulted different fsdirs — on the stock-package return, re-verify
   `myNodeNum` before any config write.)
4. Retire the USB path: drop-in `50-canary-pinedio-fix.conf` +
   `meshtasticd-patched` + weekly-restart timer → **only after the SPI radio
   soaks** (backstop-outlives-fix; scout-tick-style maps watch stays until
   flat-for-days on the stock package).
5. Stock package returns: `apt`/repo meshtasticd (leak is USB-only) — moc1
   leaves the patched-build set. Update `persistent_issues` USB-box list +
   the P1 provenance manifest.
6. RTC: remove `fake-hwclock`, verify cold-boot `hwclock -r`; BME680 → kilo
   tick field.
7. Acceptance = **patched+instrumented equivalent for SPI**: radio RX on the
   correct segment (journal `Received text msg`), maps flat, scout-class
   watch armed, `verify_post_install` green, drill logged.

## 5. Decision list for the planning session

1. RF segment for moc1's new radio (LongFast vs SHORT_TURBO) — link property.
2. RAK13302 power variant in hand; external-5V wiring plan + thermal check.
3. TX power setting: 15.247 ceiling vs something lower; antenna + gain budget.
4. Slot budget on the HAT: 13302 (IO) + 12002 + 1906 (sensor) — fits? [U]
5. What the freed toad does (gap-site, parked since July) — name it or leave
   parked.
6. RAK4631 kit role: drill node / T2 subject / solar experiment — pick one
   first mission.
7. Hardware-day logistics: moc1 is AREDN-side — who/where/when, and the
   access path if the HAT bring-up breaks networking (console cable?).
8. Do we want IAQ (BSEC blob) or raw BME680 only?
9. P1 manifest + P2 delta pass scheduling (cheap sessions, pre- or post-).

## 6. Sources

- RAK6421 store page: https://store.rakwireless.com/products/meshtastic-raspberry-pi-hat-rak6421
- RAK6421 setup guide: https://docs.rakwireless.com/product-categories/meshtastic/wismesh-rak6421-pi-hat/quickstart/
- RAK6421 datasheet: https://docs.rakwireless.com/product-categories/wishat/rak6421-wisblock-pi-hat/datasheet/
- RAK official meshtasticd guide repo: https://github.com/RAKWireless/meshtastic-rak6421-guide
- RAK13302 (1W SX1262+SKY66122): https://store.rakwireless.com/products/rak13302-meshtastic-1w-lora-module
- RAK13302 datasheet: https://docs.rakwireless.com/product-categories/wisblock/rak13302/datasheet/
- CNX overview: https://www.cnx-software.com/2026/05/26/rakwireless-wismesh-pi-hat-rak6421-turns-your-raspberry-pi-4-5-into-a-modular-meshtastic-gateway/

*VolcanoAI remains source of truth (manager, vault, memory) — moc1 gains
hardware, not authority.*

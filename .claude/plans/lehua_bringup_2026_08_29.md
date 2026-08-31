# lehua bring-up — pw2lab → lehua (Pi 3 + MeshAdv Mini w/GPS+RTC, Volcano HI solar)

> 2026-08-29. Operator-approved decisions: box renamed **lehua** (ʻōhiʻa lehua —
> first colonizer of new lava, Pele's tree); Pi 3 + meshing-around bot (T2 fork,
> meshforge branch); retires borg/trevdev. Radio: LONG_FAST slot 20, primary
> public channel untouched, + `meshforge` (fleet PSK via copypsk) + `HawaiiNet`
> (default key). SSH path: `ssh pw2lab` → trdev.mf.internal:2200 (alias becomes
> `lehua` at step 2). Key enrollment by operator ssh-copy-id — BLOCKER until done.

## Hardware facts (researched, upstream chrismyers2000/MeshAdv-Mini)

- LoRa: Ebyte E22-900M22S (SX1262, +22 dBm). meshtasticd ships the overlay:
  `available.d/lora-MeshAdv-Mini-900M22S.yaml` (CS:8 IRQ:16 Busy:20 Reset:24
  RXen:12, DIO2_AS_RF_SWITCH + DIO3_TCXO_VOLTAGE) — verified present on moc4.
- GPS: ATGM336H-5NR32 (GPS+BeiDou), UART **/dev/ttyS0** on Pi 3 (needs
  `enable_uart=1`; mini-UART is clock-stable once set), power-enable **GPIO4**
  (`gpio=4=op,dh`), **PPS on GPIO17** (`dtoverlay=pps-gpio,gpiopin=17`).
- "RTC" = GPS backup battery (Pi 5 RTC battery part) keeping time/almanac warm.
  No standalone I2C RTC documented upstream — `i2cdetect -y 1` at bring-up is
  the arbiter (0x48 = TMP102 temp sensor, expected; 0x68/0x51/0x52 would be a
  real RTC → enable matching dtoverlay).
- Pi 3 power: 5V via GPIO from HAT / PoE splitter option.

## Bring-up sequence (after SSH key lands)

Order matters: **radio config before the bot** — meshing-around claims the
single-consumer PhoneAPI :4403 (#17); all `meshtastic` CLI work happens first.

1. **Verify install state** — meshforge install was running; check completion,
   profile, meshtasticd presence. `/proc/device-tree/model` confirms Pi 3 rev.
2. **Identity** — `hostnamectl set-hostname lehua`; fix `/etc/hosts` 127.0.1.1;
   manager-side: fleet_hosts, fleet_naming.json, `~/.ssh/config` alias `lehua`
   (HostName trdev.mf.internal:2200 while the tunnel path lives),
   `gen_fleet_hosts.py --apply` after DNS/static entry exists. Watch the
   cloud-init `manage_etc_hosts` trap (07-27) if the image runs cloud-init.
3. **/boot/firmware/config.txt** (then reboot):
   `dtparam=spi=on`, `dtparam=i2c_arm=on`, `enable_uart=1`,
   `gpio=4=op,dh`, `dtoverlay=pps-gpio,gpiopin=17`
4. **meshtasticd overlay** — cp the MeshAdv-Mini yaml from available.d to
   config.d. Webserver stays :9443 (#58 — verify after HAT overlay, it has
   smuggled ports before). Restart, confirm radio init in journal.
5. **GPS ownership decision** (recommended: **gpsd/chrony own ttyS0**, radio
   gets FixedPosition) — node is stationary; gpsd + PPS makes lehua a real
   stratum-1 for the fleet NTP island (WAN-dark time source, the 08-27 arc's
   missing leg). Simpler alternative: GPS block in config.yaml → meshtasticd
   owns it. ONE reader only — never both.
6. **Radio config** (CLI, bot still stopped): region US, LONG_FAST,
   channel_num 20 (= LongFast default slot; set explicitly + save via
   device_config_store discipline), owner **Lehua / LHUA**, role CLIENT.
7. **Channels** (mirror moc4 policy: public channels positionPrecision 0,
   private 13):
   - idx0 PRIMARY LongFast default — untouched.
   - `--ch-add meshforge` → `python3 scripts/mesh_psk_safe.py copypsk moc4
     meshforge lehua 1` (hash-verified readback; tool pushed in d1dff465,
     pull on lehua first) → uplink/downlink OFF, posPrec 13.
   - `--ch-add HawaiiNet` → `setpsk lehua:4403 2 default` → posPrec 0.
8. **meshing-around** — TUI handler (clones T2 fork meshforge branch,
   4a68a346). Start LAST. Identity files: chmod 600 sweep (08-29 rule).
9. **Verify (the END, not proxies)**:
   - RX witness: `grep 'Received text msg'` in lehua's meshtasticd journal
     after a test send from moc4 on meshforge channel; reply leg via bot.
   - keyhash lehua meshforge == keyhash moc4 meshforge (sha256:1ab356d6…).
   - GPS: NMEA on ttyS0, fix quality, `ppstest /dev/pps0`; chrony sources if
     step 5 = gpsd. i2cdetect for TMP102/RTC question.
   - `bash scripts/honest_status.sh` on manager; box appears in rollup.

## Solar/battery sizing (Pi 3 + HAT, Volcano HI)

Load: Pi 3B headless + meshtasticd + bot + GPS ≈ 2.7–3.5 W @5V; through a
~88% buck ≈ 4 W design draw from battery → **~96 Wh/day**.

Insolation: Volcano ~4.8 PSH annual mean (turbinegenerator/NREL cell), but
rain weeks + vog film → design at **3.0 PSH worst-month, 0.7 system derate**.

- **Panel: 100 W** (min 50 W; the extra is multi-day-overcast recovery, and
  panel is cheaper than battery). Tilt ~20–25° (lat 19.4°N), south, rain
  self-cleans; wipe vog film quarterly.
- **Battery: 12.8 V 30 Ah LiFePO4 (~384 Wh)** ≈ 3.5–4 days autonomy. No
  freeze-charge risk at 3700 ft (40s–70s °F).
- **Controller: Victron SmartSolar MPPT 75/10** — load output gives LVD;
  BT telemetry. 5 V leg: quality 12→5 V buck ≥3 A (Pi 3 peak 2.5 A), never a
  cigarette-USB adapter.
- **Vog is dilute sulfuric acid**: IP65 enclosure, cable glands, conformal
  coat or coated boards, stainless hardware, dielectric grease, desiccant.
  Antenna/coax connectors self-amalgamating tape.

## Open items

- [ ] Operator: ssh-copy-id the Claude key (`! ssh-copy-id -i
      ~/.claude/ssh/id_ed25519.pub -p 2200 wh6gxz@trdev.mf.internal`)
- [ ] borg/trevdev retirement checklist (what services move to lehua; the
      pw2lab SSH path itself rides trdev — replacement route needed before
      trevdev powers off)
- [ ] GPS ownership decision at step 5 (recommend gpsd/chrony)
- [ ] Solar BOM purchase vs on-hand

---

## AS-BUILT (2026-08-29 evening — bring-up COMPLETE)

Ground truth corrected the plan: **Pi Zero 2 W** (not Pi 3), Debian 13 trixie,
415 MB. Lean stack only: meshtasticd 2.7.26 (OBS, apt-held = fleet baseline)
+ meshing-around bot (T2 fork `meshforge` branch). No rnsd/map/watchdog.

**Verified end-to-end**: moc4 `ping` on the meshforge channel → `####PONG [RF]`
from lehua (0xa2e95ba4) in moc4's journal, 6 s round trip. copypsk live-fired:
`VERIFIED (src==dst) sha256:1ab356d6641c34cf`. Channels: 0=LongFast default
(untouched) / 1=meshforge (fleet PSK, up/downlink off, posPrec 13) /
2=HawaiiNet (default key, posPrec 0). Region US, LONG_FAST, slot 20
(906.875 MHz), owner Lehua/LHUA.

**Defects found + cured (each left a fleet artifact):**
1. `CS: 8` in the HAT overlay vs kernel spi0's CS0 → libgpiod assert
   crashloop on trixie. Fixed live + template commit `693714d0` + tell row.
2. Serial getty + kernel console owned ttyS0 (GPS UART) → GPS probed
   NOT_PRESENT and persisted it. Cure: strip `console=serial0` from cmdline,
   mask serial-getty@ttyS0, `--set position.gps_mode ENABLED` → `L76K detected`.
3. cloud-init `manage_etc_hosts: true` + `hostname: meshforge-p2wLAB` would
   have reverted identity on boot — user-data patched, survived 2 reboots.
4. `python3 -m pip` PSK-leak guard honored throughout; keys never in transcript.

**Empirical**: no I2C RTC (only TMP102 @0x48) — "RTC" = GPS backup battery,
as upstream docs state. GPS has correct UTC, no position fix yet (indoors);
will fix with sky view. :9443 web OFF by design (RAM). Bot replies doubled
(two distinct packet ids per ping) — config tune pending.

**Solar resize for the real hardware** (~1.5 W avg load, ~41 Wh/day):
**30–50 W panel, 12.8 V 15–20 Ah LiFePO4** (3–4 days autonomy), Victron
SmartSolar 75/10, 12→5 V buck ≥3 A. Vog hardening unchanged.

**Open**: GPS position fix at deployment site; bot double-reply tune;
trevdev retirement re-route (lehua's SSH path); pps-tools + chrony/PPS
(future stratum-1 experiment); fleet_pull abbrev-length cosmetic mismatch.

## Bot migration staged (2026-08-29 ~19:10 HST)

- trdev `mesh_bot` STOPPED + DISABLED (graceful, zero shutdown errors);
  **borg (10.120.250.203) = plain node now**, pings, PhoneAPI free.
- lehua bot INACTIVE + DISABLED **by operator instruction**, carrying trdev's
  migrated env (fork 34fdb4c, both sides identical): `[interface]` →
  localhost, `ignoredefaultchannel = True` (deaf to public ch0),
  `defaultchannel = 2` (= HawaiiNet on lehua's map). config.ini chmod 600
  (carries smtp/module creds). Stock config kept at config.ini.bak-stock.
- ⚠️ Module channels inherited from borg's channel map: echochannel 9,
  highflyingalertchannel 4 — indexes that don't exist on lehua (0-2). Expect
  the START-time errors here, not at the trdev stop (which threw none).
- Cutover when ready: `sudo systemctl enable --now mesh_bot` on lehua.

## Web UI + RTC verdict (2026-08-29 ~19:25 HST)

- Web UI LIVE: box serves :9443 (fleet standard, #58 honored — operator's
  19443 yaml edit reverted; it had cancelled the hAP's 19443→9443 dstnat).
  Reach: https://trdev.mf.internal:19443/ → HTTP 200. SSL cert autogenerated
  on first webserver start (that was the original missing piece, not assets).
  ⚠️ No auth on the Meshtastic web UI — keep the hAP rule internal-only.
  ⚠️ #17: browser + bot = two PhoneAPI consumers; short sessions, close tab.
- RTC re-verified with the FULL i2cdetect table (earlier claim used a
  row-filtered scan — instrument, not conclusion, was at fault): only 0x48
  (TMP102) on bus 1. NO I2C RTC chip. The 10×/boot "RTC not found (found
  address 0x00)" WARNs are meshtasticd probing for an absent part — benign
  noise, not a fault. The HAT's "RTC" = the GPS module's battery-backed
  timekeeping (install the Pi5-RTC-battery on the HAT header for warm GPS
  starts). System clock: NTP-synced now; GPS/PPS is the off-grid plan.

## Web UI verdict — CLOSED BY UNDERSTANDING, not by fix (2026-08-29 ~20:15)

Operator browser-verified :19443 (via hAP → box :9443): page loads, config
sync works, **live texts do NOT appear while the bot runs — EXPECTED (#17/#75
class)**. Mechanism: config sync is the web client's own request/response
exchange; incoming texts are queue events and the bot's persistent TCP
connection drains them first — one packet, one consumer, and the bot SHOULD
win (it is the box's function). No config fixes this; upstream meshtasticd
2.7.x has no multi-consumer delivery. **Ruling: the web UI on lehua is a
CONFIG CONSOLE (nodes, positions, channels, admin), never a message reader.**
RX truth = meshtasticd journal `grep 'Received text msg'`; command truth =
bot log. If live remote text view is ever truly needed: MQTT uplink mirror
(zero PhoneAPI exposure) — do not build until a real need exists (footprint
doctrine). Neither operator nor model re-litigates this.

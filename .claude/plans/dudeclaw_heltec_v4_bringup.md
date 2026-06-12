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
- OLED stays dark on stock firmware (no display driver) — **display fork is
  the ratified next arc**: PlatformIO, SSD1306 status panel + `display_print`
  tool → mini pushes fleet metrics to the glass. Upstream-PR candidates:
  display tool + NATS token auth (the gap that forced the pinhole posture).

## Deferred / next arcs

- Display fork (above) · battery-voltage ADC sensor (verify V4 VBAT pin map)
  · push-subscribe sensor mode · Phase B chat-compiler · fleet_roles.yaml
  claw-brain declaration once the pilot graduates · Substack post (the
  remote-flash story is a good one).

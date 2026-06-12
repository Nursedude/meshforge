# Dude-Claw bring-up — Heltec V4 (WireClaw edge) + moc1 brain

> **Status:** Phase A software SHIPPED + staged 2026-06-11 (`a171fec`).
> Remaining steps need the operator at the bench (Chromium + USB-C).
> Design: `standalone_wireclaw_variant.md` · Session plan: operator-side
> `~/.claude/plans/wanting-to-wire-meshforges-fluttering-crab.md`

## What is already in place (verified live)

| Piece | State |
|-------|-------|
| `nats_sensor` / `nats` adapters + `standalone` preset + stdlib NATS client | shipped `a171fec`, 57 tests + 9 deploy guards, suite 6,455 green |
| nats-server v2.14.2 on moc1 | **active**, `127.0.0.1:4222` (localhost-only until the pinhole step), unit from `templates/systemd/nats-server.service` |
| Claw env `~/.config/meshforge/mini_dudeai_claw.env` (moc1, 600) | staged: `MINI_DUDEAI_NATS_SERVER=localhost:4222`, `MINI_DUDEAI_CLAW_DEVICE=dudeclaw-01`, fleet ntfy topic |
| `meshforge-mini-dudeai-claw.service` user unit (moc1) | installed, **disabled** (enable AFTER flash — else the blindness rule pages hourly about a device that doesn't exist yet) |
| Preset smoke on moc1 | `--once`: seeded 3 rules, 1 honest `source_error` (no device), 0 fires, exit 0 |

**Security posture (decided, verified):** WireClaw v0.4.0 firmware carries ONLY
`nats_host`/`nats_port` (its `data/config.json.example` — no token/user/pass),
so server-side token auth would lock the device out. The bus binds localhost
until the pinhole step below; never an open unauthenticated LAN bind. moc1 has
no firewall framework today — the recipe below installs an ADDITIVE nftables
table scoped to dport 4222 only (cannot affect SSH/federation/soak).

## Bench steps (operator present)

### 1. Back up the V4's Meshtastic config (reversibility)
```bash
meshtastic --port /dev/ttyACM0 --export-config > ~/heltec_v4_meshtastic_backup.yaml
```
Keep OFF-repo (channels carry PSKs). Restore path later: Meshtastic web
flasher + `meshtastic --configure`.

### 2. Flash WireClaw
1. Chromium (not Firefox) → https://wireclaw.io/flash.html
2. USB-C **data** cable. If no serial port appears (native-USB S3): hold
   **BOOT**, tap **RST**, release BOOT → ROM download mode.
3. Flash v0.4.0 ("includes filesystem"). Press RST after.
4. Expected: pulsating blue LED = setup mode. **OLED stays dark — normal**
   (no display driver in stock WireClaw; display fork is the next arc).

### 3. Captive portal
Join `WireClaw-Setup` AP → portal at `192.168.4.1`:
- WiFi: the **2.4 GHz** LAN SSID + password
- Device name: `dudeclaw-01` (must match `MINI_DUDEAI_CLAW_DEVICE`)
- `nats_host`: moc1's LAN address · `nats_port`: 4222
- LLM + Telegram: **leave empty** (mini-dudeai is the brain)

Then find the claw's IP (router DHCP table; give it a **reservation** — the
pinhole pins to it).

### 4. moc1: pinhole + LAN rebind (additive, 4222-scoped)
```bash
sudo apt-get install -y nftables
sudo tee /etc/nftables.conf > /dev/null << 'EOF'
#!/usr/sbin/nft -f
flush ruleset
table inet clawbus {
  chain input {
    type filter hook input priority 0; policy accept;
    # NATS 4222: localhost + the claw only. Everything else on this box
    # is untouched (policy accept) — additive by design, soak-safe.
    iifname "lo" tcp dport 4222 accept
    ip saddr <CLAW-IP> tcp dport 4222 accept
    tcp dport 4222 drop
  }
}
EOF
sudo systemctl enable --now nftables
# now widen the bus to the LAN (the pinhole is already in front of it)
sudo sed -i 's/^listen: 127.0.0.1:4222/listen: 0.0.0.0:4222/' /etc/nats/nats.conf
sudo systemctl restart nats-server
# verify: still reachable locally, and SSH untouched
PYTHONPATH=/opt/meshforge/src python3 -m mini_dudeai.nats_client req _ion.discover '' --many --timeout 2
```
(Order matters: pinhole BEFORE rebind — the bus is never LAN-open unguarded.)

### 5. Verify the claw answers (from moc1)
```bash
cd /opt/meshforge
PYTHONPATH=src python3 -m mini_dudeai.nats_client req _ion.discover '' --many
PYTHONPATH=src python3 -m mini_dudeai.nats_client req dudeclaw-01.tool_exec '{"tool":"temperature_read"}'
PYTHONPATH=src python3 -m mini_dudeai.nats_client req dudeclaw-01.tool_exec '{"tool":"led_set","r":0,"g":0,"b":255}'
PYTHONPATH=src python3 -m mini_dudeai.nats_client req dudeclaw-01.tool_exec '{"tool":"led_set","r":0,"g":0,"b":0}'
```
Discovery shows `dudeclaw-01`; temperature returns a reading; LED flashes blue.

### 6. Enable the brain
```bash
systemctl --user enable --now meshforge-mini-dudeai-claw.service
journalctl --user -u meshforge-mini-dudeai-claw -f   # expect src_errors=0 ticks
```

### 7. End-to-end proof (the Phase A milestone)
Drop the threshold below ambient so the breach fires without cooking the board:
```bash
# in ~/.config/meshforge/mini_dudeai_claw.env:
#   MINI_DUDEAI_CLAW_TEMP_THRESHOLD=20
systemctl --user restart meshforge-mini-dudeai-claw.service
```
Within ~90 s (grace_s 60 needs ≥2 observed ticks): **LED turns red** +
`[claw] chip temp over threshold` page. Restore the threshold (55), restart →
edge_down: **LED off** + quiet "cleared" notice. That round trip — sensor →
rule → actuator + page, no LLM in the loop — closes Phase A; record it in the
session notes + memory, and the claw earns its changelog entry (version bump
rides the 0.6.2-beta soak convention).

## Troubleshooting
- `_ion.discover` empty: claw not on WiFi (2.4 GHz only) / wrong `nats_host` /
  pinhole blocking it (check `sudo nft list table inet clawbus`).
- Serial console (`/setup` to re-open the portal) via any USB terminal at 115200.
- `claw_blind_any` pages later = bus or device went dark; the engine HOLDS
  last-good breach state while blind (no false "recovered").
- Full revert: Meshtastic web flasher → restore step-1 backup; disable the
  claw unit + nats-server; remove the clawbus nft table.

## Deferred (next arcs)
- **Display fork**: PlatformIO, SSD1306 status panel + `display_print` tool →
  mini pushes fleet metrics to the glass. Candidate upstream PRs: display tool
  + NATS token auth (the gap that forced the pinhole posture).
- Battery-voltage sensor (verify V4 VBAT pin map), push-subscribe sensor mode,
  Phase B chat-compiler, role/`fleet_roles.yaml` declaration once the pilot
  graduates.

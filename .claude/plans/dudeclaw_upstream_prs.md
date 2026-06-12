# dude-claw upstream arc — PR branches READY, awaiting operator ratification

> Status 2026-06-12: both PR branches built, tested, and committed **locally** in
> `~/src/wireclaw-dudeclaw` (VolcanoAI). Nothing public yet — the fork going
> public is operator-gated. The deployed `dudeclaw` branch is untouched; the
> claw keeps running its current firmware.

## Branches (local, VolcanoAI `~/src/wireclaw-dudeclaw`)

| Branch | Commit | Contents |
|--------|--------|----------|
| `pr/display-status-screen` | `7b929f9` | Display module + `display_print`, `DUDECLAW_*` → `WIRECLAW_*` flags, fork branding stripped, docs (TOOLS/OPENCLAW/README/SKILL) updated. Built green: `esp32-s3-heltec-v4` + stock `esp32-s3`. |
| `pr/nats-token-auth` | `2d30ae9` | Completes the lib's own `TODO: Add auth fields` — `send_connect()` emits `auth_token` (or user/pass), `NatsClient.setToken()/setUserPass()`, `nats_token` config key end-to-end (config.json, setup portal, web UI + masked GET), docs. Built green: `esp32-c6` + `esp32-s3`. **Wire-verified** against nats-server 2.14.2 `authorization{token}`: correct token → PONG, wrong/missing → `-ERR 'Authorization Violation'`. Not yet flashed to hardware. |

Both branch off upstream `main` (`ad84614`, == `origin/main` as of 2026-06-12).
Overlap is `src/main.cpp` only, in disjoint regions — they merge independently
in either order.

Note: backup push to `moc1:~/wireclaw-dudeclaw.git` was permission-denied this
session — branches exist only in the VolcanoAI clone until pushed.

## On ratification, run (in `~/src/wireclaw-dudeclaw`)

```bash
gh repo fork M64GitHub/WireClaw --remote --remote-name fork   # creates Nursedude/WireClaw
git push fork dudeclaw pr/display-status-screen pr/nats-token-auth
gh pr create --repo M64GitHub/WireClaw --head Nursedude:pr/display-status-screen \
  --title "Add optional SSD1306 status display + display_print tool (Heltec V4 env)" \
  --body-file <(draft A below)
gh pr create --repo M64GitHub/WireClaw --head Nursedude:pr/nats-token-auth \
  --title "Add NATS token authentication (completes the CONNECT auth TODO)" \
  --body-file <(draft B below)
git push backup dudeclaw pr/display-status-screen pr/nats-token-auth  # moc1 bare backup
```

Optional flourish for PR A: a photo of the V4's live status screen sells the
feature — operator's call whether to attach one.

## PR draft A — display

Boards with an onboard OLED (e.g. the Heltec WiFi LoRa 32 V4) currently leave
the panel dark. This adds a guarded-optional status display plus a
`display_print` tool, and a `esp32-s3-heltec-v4` env that wires it up.

**Status screen** (SSD1306 128x64): device name + NATS connection marker,
IP + RSSI, chip temp / free heap / uptime, and two remote-writable metric rows.

**`display_print` tool** — `{"row":0..1,"text":"..."}` via the LLM and
`tool_exec`; empty text clears the row. It's registered in TOOLS_JSON,
`toolExecute`, and the `_ion.discover` tools list (docs + skill updated, tool
counts bumped).

Design notes:
- **Guarded optional**: everything is behind `WIRECLAW_OLED`; every other env
  compiles no-op inlines (same pattern as the chip-temp sensor), so existing
  targets are unaffected. Pins come from `WIRECLAW_OLED_SDA/SCL/RST/VEXT`
  build flags, so other OLED boards only need a new env.
- **Honest absence**: init probes the panel with a real I2C ACK (the driver's
  `init()` can't detect a missing device) and falls back to headless with a
  serial log; `display_print` on a panel-less build/board returns
  `Error: no display on this device`, never a silent ok. Metric rows older
  than 30 min render a `(old)` suffix so a dead pusher can't masquerade as
  live data.
- **led() on WS2812-less boards**: `WIRECLAW_STATUS_LED` (the V4's white LED)
  maps RGB intensity onto the mono LED via PWM so `led_set` stays visible.
- Stock 4MB `partitions.csv` retained, so an app-only reflash
  (`esptool write-flash 0x10000 firmware.bin`) preserves the LittleFS config.

Running on a Heltec WiFi LoRa 32 V4; `esp32-s3` stock env builds unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## PR draft B — NATS token auth

`nats_options_t` already carries `user`/`pass`/`token`, but `send_connect()`
has `/* TODO: Add auth fields when configured */` and never emits them — so
WireClaw can only join open NATS servers, and protecting the bus means
network-layer firewalling around every device. This completes the TODO
end-to-end.

**Library (nats-esp32)**
- `send_connect()` emits an auth fragment: `auth_token` when token is set,
  else `user`/`pass` (token precedence matches the reference clients). Values
  are written verbatim (no heap, no escaper); credentials containing `"` or
  `\` get rejected by the server as a malformed CONNECT — a loud failure, not
  a silent downgrade. A fragment that would truncate returns
  `NATS_ERR_BUFFER_OVERFLOW` instead of authenticating with a wrong credential.
- `nats_core.h`: `NATS_MAX_AUTH_FRAG` + the pointer-lifetime contract
  documented on the auth fields (stored, not copied).
- `NatsClient`: `setToken()` / `setUserPass()` setters, call before
  `connect()`; NULL/empty clears.

**Application**
- New `nats_token` config key: config.json, setup portal, web config UI and
  `/api/config` (masked on GET like the other secrets; the existing
  masked-sentinel logic preserves it on POST round-trips).
- `connectNats()` applies the token and logs `(token auth)`; `/status` echoes
  it. Empty token = no auth — open-server setups work unchanged.
- Docs: CONFIGURATION.md field table, NATS.md auth section with the matching
  `nats-server` `authorization { token: ... }` block, config.json.example.

**Testing**: the exact CONNECT line `send_connect()` produces was replayed
against nats-server 2.14.2 with `authorization{token}` — correct token
authenticates (PONG), wrong/missing token gets `-ERR 'Authorization
Violation'`. `esp32-c6` and `esp32-s3` builds are green. I have a V4 on a
token-less bus today and will flash + soak this branch on real hardware if
useful.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## After both PRs merge upstream

Rebuild `dudeclaw` as `main` + (merged display) + fork-only residue
(`FORK.md`, `0.4.0+dudeclaw.N` version marker) — or retire the fork marker
entirely if upstream releases with both. Then enable token auth on the claw's
bus: set the server token, POST `nats_token` to the claw (full-JSON
`/api/config` + `/api/reboot` — remember GET masks `wifi_pass`; carry the
real PSK), and only then consider relaxing the dport-4222 pinhole posture.

# dude-claw remote reboot — expose the restart the firmware already does

> 2026-08-30. Operator call, prompted by wiring lehua (`!0daee001`) into the claw
> RF watch list: **dudeclaw-01 is in another building**, so `config_set`'s
> "(reboot to apply)" priced a five-character config change at a walk across a
> property. The claw's config portal lives on the claw's own /28, which no fleet
> box can route to — so today every applied config change needs a human at the
> device or a power cycle.
>
> **Scope: a dude-claw FIRMWARE change — and we own the fork, so we build it**
> (operator, same day). Fork: `~/src/wireclaw-dudeclaw`, branch `dudeclaw`,
> PlatformIO `env:esp32-s3`, backup remote `moc1:~/wireclaw-dudeclaw.git`.
> MeshForge's side (`scripts/claw_set_watch_ids.py`,
> `scripts/claw_set_fleet_channel.py`) is already correct and needs one new
> call site.
>
> ⚠️ **CORRECTION, same day — the premise above was overweighted.** The operator
> can reach every claw in **a few minutes**; physical plant is not the
> constraint I priced it as. Two consequences, both recorded rather than
> quietly edited away: (1) the **config** rollback in §3 drops from
> safety-critical to optional — see the verdict note there; (2) the frontier
> adversarial pass I queued here is **WITHDRAWN** — with recovery cheap, what
> remains is a small tool a bench drill tests better than any review would.
> Firmware OTA (Part 2) is a different matter: its rollback comes free from the
> bootloader, so it is enabled, not built.
>
> **Tier routing** (model_advisor): the `reboot` tool, its refusals, and the
> caller changes are Opus-class day work — bounded, with the pattern already in
> `tools.cpp`.

## The gap, stated once

Every `config_set` reply ends `(reboot to apply)`. The write is verified
on-device, `wifi_ssid` is confirmed intact, a backup is taken — and then the
change sits inert until someone physically restarts the board. That is not a
one-off cost for the lehua watch entry; it recurs on **every** future channel
change, watch-list change, and any config key the firmware grows.

## Why this is cheap — measured, not assumed (2026-08-30)

The firmware already software-restarts and already reports it distinguishably.
Two claws, same chip, same firmware, read minutes apart:

| device | `reset_reason` | `uptime_s` | provenance |
|---|---|---|---|
| dudeclaw-02 | `poweron` | 331 | operator power-cycled it by hand |
| dudeclaw-01 | **`sw-restart`** | 651,348 (7.5 d) | untouched |

Both `ESP32-S3 rev 2`. So:

1. A `reboot` tool **exposes an existing code path** (`ESP.restart()` or
   equivalent) rather than adding a capability.
2. `reset_reason` is already the **witness**: after a remote reboot the next
   capture must read `sw-restart` with a small `uptime_s`. That verifies the
   reboot from the device's own report instead of trusting the tool's reply —
   the same read-back discipline `claw_set_watch_ids.py` already applies to the
   write (calibrated_claims #7), and an authority we did not author.

## The design

### 1. `reboot` tool on the NATS `tool_exec` bus

Same transport as `config_set` / `lora_stats` (`<device>.tool_exec`). Replies
BEFORE restarting, or the caller cannot distinguish "rebooting" from "dark".

**The deferred-reboot mechanism this needs already exists** (read 2026-08-30) —
`g_reboot_pending` / `g_reboot_at` in `src/main.cpp:127`, serviced in the main
loop at `src/main.cpp:1874`, already used by the portal
(`src/web_config.cpp:328`, +2000 ms "enough for HTTP response to flush") and by
a Telegram path (`src/main.cpp:872`, +8000 ms "enough for TG response + ACK
poll"). The tool is therefore ~15 lines: validate, set
`g_reboot_pending`/`g_reboot_at = millis() + ~2000`, return the reply, and let
the existing loop do the restart. **No new restart path, and no new deferral
machinery** — which is why this half is day work.

### 2. Pre-flight the tool REFUSES on

A remote reboot is only as safe as the config it boots into. Refuse unless:

- `wifi_ssid` present and non-empty in the live config (the existing
  `config_set` check, re-asserted at reboot time — the config may have been
  changed by a different caller since).
- `/config.json` parses as JSON.
- `/config.json.bak` exists (there is something to roll back TO).

A refusal must name which condition failed. Rebooting into a config with no
WiFi credential is the one genuinely expensive failure: recovery is
physical/USB **by design**, since no fleet box can reach the config portal.

### 3. The missing half — rollback-on-failure-to-join (firmware)

> **VERDICT (2026-08-30): DO NOT BUILD THIS — for now.** Its whole
> justification was an unrecoverable off-site claw, and physical access turns
> out to be minutes. What would remain is a boot-path change carrying the
> fleet-wide misfire hazard described below, guarding a cost measured in a short
> walk. Leave it specified; revisit only when a claw ships with the field kit.
> Note the **firmware** rollback in Part 2 is a different call entirely — ESP-IDF
> gives it for free, so it gets enabled. The analysis below stands as the design
> if this is ever needed, and as the reason the naive version must never ship.

**This is the guard that would make remote reboot safe off-site, and it is the
part that does not exist yet.** `claw_set_watch_ids.py` already writes
`/config.json.bak` on the device before every write, so half the mechanism is
in place.

⚠️ **The naive version is a fleet-wide foot-gun — do NOT build it.** The
obvious rule, *"if WiFi has not joined within N seconds, restore the backup and
restart"*, cannot tell **"my config is bad"** from **"the AP is down."** Both
produce the identical symptom: no join. `connectWiFi()` gives up after 30 ×
500 ms = **15 s**, so a router reboot lasting longer than that would make
**every claw simultaneously roll back to an older config** — a self-inflicted
mass event triggered by someone else's power cycle. That is this domain's
signature defect class (a degraded state mapped to a confident wrong claim),
and it would be strictly worse than the problem being solved.

**The fix is a probation flag**, so the rollback fires only when a config
change is actually implicated:

- Any config write (`config_set`, portal save) sets `config_on_probation` in
  NVS **before** the reboot.
- Boot, join SUCCEEDS → clear the flag. The new config is proven on the only
  evidence that matters: the device came back.
- Boot, join FAILS **and** flag set → the change is implicated. Restore
  `/config.json.bak`, clear the flag, restart, leave a witness.
- Boot, join FAILS **and** flag clear → **this is an environment problem, not
  a config problem.** Do NOT roll back. Degrade exactly as today.

The flag is what makes "no WiFi" answerable rather than merely observed. It
must live in NVS, not RAM — the whole point is that it survives the restart.

Without this, a remote reboot **upgrades a bad config from an annoyance into
the building trip it was meant to avoid**. With it, a bad config costs ~2 join
windows of downtime and self-heals, while an AP outage changes nothing.

⚠️ The rollback must leave a witness the next tick can see (a
`config_rolled_back` counter or a distinct `reset_reason`). A silent rollback
is a claw quietly running config you did not write, reporting healthy —
honest_failure_modes #9, and the failure mode that would make this whole change
a net loss.

### 4. Caller side (MeshForge, small)

`claw_set_watch_ids.py` / `claw_set_fleet_channel.py` grow an opt-in
`--reboot` that, after `verified=yes wifi_ssid_intact=yes`:

1. sends `reboot`,
2. polls the device back onto the bus (bounded, e.g. 90 s),
3. **verifies from the next tick** that `reset_reason == sw-restart` and
   `uptime_s` is small, and that the intended key actually took effect,
4. reports UNKNOWN — never success — if the device does not return in the
   window. Unobservable is not healthy.

Default stays OFF. A config write and a restart are different blast radii and
should stay separately authorised.

## Acceptance drills (a guard that never failed is not evidence)

Plant the failure; do not read the code and conclude.

1. **Happy path** — `--reboot` on the bench claw; next tick shows `sw-restart`,
   small uptime, new key in effect.
2. **Refusal path** — stage a config with `wifi_ssid` blanked; the tool must
   REFUSE and name the condition. It must not reboot.
3. **Rollback path** — deliberately write a config with a wrong WiFi password,
   reboot, and confirm the claw comes back on the bus by itself with the
   backup restored and the rollback witnessed. **This drill is the whole
   feature**; if it is not run on the bench, the capability is BELIEVED, not
   verified — and the first real exercise would be on a claw in another
   building.
4. **Dark path** — power the claw down during the poll window; the caller must
   report UNKNOWN, not failure and not success.

Run 1–3 on the bench claw, never first on an off-site one.

## Build status (2026-08-30)

**Step 1 (`reboot` tool + refusals) is WRITTEN and COMPILES** —
`~/src/wireclaw-dudeclaw/src/tools.cpp`, `pio run -e esp32-s3` → `[SUCCESS]`,
rc 0, RAM 62.8% / Flash 50.2%. Wiring checked statically: present in the bus
manifest, dispatch branch present, handler defined, **absent from
`AGENT_TOOL_ALLOWLIST`** (a 4B model must not take the device off the bus), and
**zero new `ESP.restart()` in tools.cpp** — it arms the existing
`g_reboot_pending` path.

⚠️ **BELIEVED, not verified.** A compile proves it builds; it does not prove the
tool answers on the bus, that the refusals bite, or that the board comes back.
That needs the bench drills below, on a USB-attached claw. **Not flashed
anywhere.** Steps 2–3 (probation flag + rollback) are NOT built.

**Step 4 (caller side) is BUILT** — `scripts/claw_set_watch_ids.py` grows
`--reboot` (default OFF) + `--reboot-wait` (90 s). `py_compile` OK,
`scripts/lint.py --all` rc 0, `test_claw_metrics_push.py` +
`test_regression_guards.py` 103 passed. `--dry-run --reboot` verified to
short-circuit before any write or restart.

The verification discipline it encodes: **proof is "uptime went DOWN", not
"uptime is small".** The firmware defers its restart ~2 s so the reply can
flush, so the first `device_info` after the call is answered by the OUTGOING
process reporting the OLD uptime — accepting it would ratify a reboot that
never happened. A baseline is captured BEFORE the call and the poll waits for
an uptime strictly below it. Exit codes: 0 verified · 1 refused/failed ·
**2 = write committed but reboot UNOBSERVABLE** (not failure, not success).

Its parser was verified against the **real** wire format, not an assumed one —
a live claw's `device_info` returns a dict envelope wrapping a plain-text
string (`Reset reason: poweron, Uptime: 1383 seconds, …`), and the regexes were
run against that exact captured reply. ⚠️ There is **no dedicated test file**
for this script; the parser check was a scratch exercise, not a committed pin.

## Current state this unblocks (2026-08-30)

- **dudeclaw-02** — 5 watch ids incl. `!0daee001`, `watch_dropped: None`
  (firmware does not cap at 4), **armed** via the operator's hand power-cycle.
- **dudeclaw-01** — same 5 ids written and `verified=yes`, **staged not armed**;
  it is the other-building box and is precisely the case this plan exists for.
  It arms on its next restart, whenever that is.

Two buildings is also a reason to want both: different RF vantage, and lehua's
watch verdict is currently answered from one vantage only.

## Open questions

- What is the firmware's watch-list capacity? 5 ids survive with
  `watch_dropped: None`; the ceiling is unmeasured. `watch_dropped` exists as a
  field, so a ceiling exists somewhere.
- ~~What triggered dudeclaw-01's `sw-restart`~~ — **ANSWERED 2026-08-30**: the
  firmware has had a deferred-restart path all along (portal save and a
  Telegram command both use it), so `sw-restart` is an ordinary reported
  outcome and the reboot tool is a call into existing code.
- Does `config_set` re-verify `wifi_ssid` at write time only, or would a
  reboot-time re-check need new firmware plumbing?
- Where exactly does `config_on_probation` live, and which writers must set it?
  Every path that can change `/config.json` has to, or the one that forgets is
  the one that bricks a claw — reader/writer pairs wire together or fail
  together (honest_failure_modes #4).
- `connectWiFi()` currently fails after 30 × 500 ms = 15 s. Is that the right
  probation window, or should a probation boot get a longer, more patient join
  attempt before concluding the config is at fault? A too-short window rolls
  back a good config on a slow AP.

## Related

- `scripts/claw_set_watch_ids.py` — the write path, its safety model, and the
  `/config.json.bak` backup this plan builds on.
- `scripts/claw_set_fleet_channel.py` — same trust model, second call site.
- `src/mini_dudeai/claw_telemetry.py` — `reset_reason` / `uptime_s` parsing;
  the verification surface.
- `.claude/rules/honest_failure_modes.md` #9 (every swallow leaves a witness),
  `.claude/rules/calibrated_claims.md` #7 (verify the consumer of record).

---

# Part 2 — OTA firmware update (spec, 2026-08-30)

> Operator call, same day: *"what'd be cool is doing the kind of OTA flashing
> that Meshtastic does with its firmware."* Agreed, and it **subsumes Part 1**:
> OTA needs a restart path anyway, so the reboot tool becomes a component of
> this rather than a separate feature.

## Why — the use case is real, and it is the field arc

Today every firmware change costs one physical visit per claw. That is
affordable at three claws in two buildings (measured: minutes). It stops being
affordable the moment a claw ships with the **break-away field eComm kit**
(`.claude/plans/field_ecomm_and_dutycycle_fleet.md`) — a sentinel at a remote
solar site with no OTA is a sentinel frozen at whatever build it left with,
while the fork it came from keeps moving. The dude-claw is the night watcher's
*eyes*; eyes that cannot be updated drift away from what the fleet needs to see.

Second-order: OTA is what makes it cheap to ship the small, honest firmware
fixes this domain generates constantly. A fix that costs a trip does not get
shipped; it gets deferred, and the deferral is invisible.

## ⚠️ Gate 1 — MEASURE THE FLASH CHIP BEFORE CHOOSING A LAYOUT

`platformio.ini` declares `board_upload.flash_size = 4MB` and
`board = esp32-s3-devkitc-1`, but the claws are **Heltec WiFi LoRa 32 V4**
(envs `esp32-s3-heltec-v4` / `-st`; claw-03 is the `-st` one). Heltec V3/V4
commonly ship **8 MB**. **The 4 MB in the build file is a declaration, not a
measurement** — if the parts are 8 MB, the upper half is simply unaddressed
today and OTA costs nothing in headroom.

Measure with `esptool flash_id` on a USB-attached board. Do not skip this:
it decides between a comfortable layout and a cramped one.

| | 4 MB (cramped) | 8 MB (comfortable) |
|---|---|---|
| app slots | 2 × 1.5 MB | 2 × 2.5 MB |
| current image (1,316,928 B) | **83.7% of a slot** | 50.2% of a slot |
| spiffs | shrinks 1.375 → ~0.875 MB | unchanged or larger |
| verdict | works, but caps future growth hard | no sacrifice |

Current table has `otadata` AND app0 typed `ota_0` — but **no `ota_1`**, so
`Update.h` has nowhere to write. That single missing partition is the whole
blocker; everything else already exists.

## Gate 2 — rollback is FREE from the bootloader. Use it.

ESP-IDF ships app rollback (`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`): a newly
flashed image boots in `PENDING_VERIFY`, and unless it calls
`esp_ota_mark_app_valid_cancel_rollback()` the bootloader reverts to the
previous image on the next reset. **This is the probation-flag design from
Part 1, implemented in the bootloader, battle-tested, and free** — so the
argument that killed the config rollback ("not worth building for a 5-minute
walk") does not apply here: we are not building it, we are enabling it.

⚠️ **Define "valid" correctly, or the mechanism is theatre.** Marking valid on
`setup()` completion only proves the image boots. The claw's job is to be
reachable, so the mark must come after it has **rejoined WiFi AND answered on
the NATS bus** — the same end-of-domain acceptance rule the rest of this
domain uses (never terminate acceptance at a proxy). An image that boots
happily but cannot reach the bus must roll back, and with this rule it does.

## Gate 3 — the running version must become POLLABLE (blocker)

`WIRECLAW_VERSION` (`include/version.h`, currently `0.4.0+dudeclaw.19`) is
published in the boot `{"event":"online",...}` announce and in the capabilities
payload — but it is **NOT in `device_info`**, and the claw tick captures no
version field. So today you cannot ask a claw "which image are you running?";
you could only have overheard it at boot.

**You cannot verify an OTA you cannot interrogate.** Fix before shipping OTA:
add `WIRECLAW_VERSION` to `tool_device_info`'s reply and parse it into the
tick. Independently valuable — it gives the fleet firmware-drift visibility it
does not have today.

## Transport

Push, not pull: the claws sit on AREDN subnets reachable from their bus host,
and a push keeps the artifact's provenance on our side rather than requiring a
URL the claw trusts. `Update.h` over an HTTP POST to the existing web server
(`src/web_config.cpp` already runs one) is the least new machinery. The OTA
endpoint must be POST-only and must not be reachable from the LLM agent path.

## `scripts/claw_ota_push.py` — the caller

Same verify-it-came-back discipline `claw_set_watch_ids.py --reboot` now
carries, with the version as the payload check:

1. capture baseline `version` + `uptime_s` + `reset_reason`,
2. POST the image, watch for a complete-and-verified reply,
3. poll until uptime drops BELOW the baseline (the outgoing process answers
   first — never accept the first reply),
4. assert the **version actually changed to the pushed one**; an unchanged
   version after a "successful" push means the rollback fired, which is a
   FINDING, not a success,
5. exit 0 verified · 1 refused/failed · **2 UNKNOWN** (did not come back in
   window — the image state is genuinely unobservable from here).

## The one-time cost, stated honestly

A partition change **cannot be applied over the air** — by definition. So each
claw needs one last USB visit, and that visit includes **reprovisioning**:
moving the spiffs partition invalidates LittleFS, which holds `/config.json`
(WiFi credential included), and the firmware **refuses `file_write` to
`/config.json` by design**, so it cannot be restored over the bus. Portal-only,
human present. Read the config out first (`file_read` is permitted) so the
device name / watch ids / channel can be retyped rather than rediscovered.

**Three visits now, then never again.**

## Acceptance drills (bench board first, never a live claw)

1. **Happy path** — push a build with a bumped version; verify version changed,
   uptime dropped, claw answers on the bus.
2. **Rollback path — THE drill.** Push an image deliberately broken *after* boot
   but *before* bus-join (e.g. wrong WiFi behaviour). The bootloader must revert
   and the claw must come back on the OLD version. If this is not drilled, the
   rollback is BELIEVED and the first real test is a remote claw.
3. **Truncated/corrupt image** — must be refused at write time, device unchanged.
4. **Power-cut mid-write** — pull power during the push; claw must come back on
   the old image (this is what the two-slot design buys).
5. **Agent cannot reach it** — confirm the OTA path is absent from
   `AGENT_TOOL_ALLOWLIST` and from `toolsGetAgentDefinitions()`.

## Sequencing

1. Measure flash size (Gate 1) — decides the layout.
2. Add version to `device_info` (Gate 3) — small, useful on its own, ship it
   with the current partition scheme so it lands before OTA needs it.
3. New `partitions.csv` + `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`; validity
   marked only after bus-join (Gate 2).
4. OTA endpoint + `claw_ota_push.py`.
5. Drills 1–5 on the bench board.
6. One USB visit per claw: repartition, flash, reprovision.

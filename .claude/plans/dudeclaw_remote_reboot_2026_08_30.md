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
> **Tier routing** (model_advisor): the `reboot` tool, its refusals, and the
> caller changes are Opus-class day work — bounded, with the pattern already in
> `tools.cpp`. The **rollback boot path is queued for a frontier adversarial
> pass BEFORE it is flashed to an off-site claw**: it is a boot-path change on a
> device whose only recovery is physical, so reviewing my own work there is the
> exact authorial-distance failure calibrated_claims warns about.

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

**This is the guard that makes remote reboot safe off-site, and it is the part
that does not exist yet.** `claw_set_watch_ids.py` already writes
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

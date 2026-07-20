# The claw got its own words — structural-dark row 7 (2026-07-19)

> A dead battery-powered LoRa node spoke to the fleet only as *"a cron job is
> failing."* This is the incident, the defect class, and the cure.

## What happened

`dudeclaw-02` is a battery-powered dude-claw: the fleet's **out-of-band
witness**, the independent RF/host vantage that exists precisely for when a
box's own self-report cannot be trusted.

Reconstructed from its battery log:

| When | What |
|------|------|
| ~07-09 onward | pack drifts under 3.5 V — ~38 h below the working floor |
| 2026-07-10 12:58 | bottoms at **2.41 V**, device stops answering |
| 2026-07-10 → 07-11 06:22 | **17.4 h dark** |
| 2026-07-11 06:22 | back up (uptime reset confirms a real power cycle) |

The fleet *did* notice. Twice, at 07-10 13:01 and 07-11 06:25, it said:

```
cron_verdict_stale_any — Wired cron(s) unhealthy — 1 failing:
claw02_metrics(FAIL(1))   (fix the job or re-run + re-verify)
```

That is the entire record of a hardware node dying.

## The defect class

**A real-world degraded state was mapped onto an adjacent valid-looking value,
and downstream logic turned it into the wrong claim.** The only downstream
witness of "the claw is alive" was the *capture cron's exit code*, so a dead
radio was laundered into an infrastructure-noise signal — and landed in
`cron_verdict_stale`, the channel documented as routinely flappy-benign. The
words the operator got ("fix the job") pointed away from the truth ("charge the
node"). Same family as every other entry in `honest_failure_modes.md`, wearing
new clothes.

**Compounding it, the alarm that should have caught it pointed at the wrong
device.** A `battery_v lt 3.5` spec existed — bound to `dudeclaw-01`, which
lives on USB at 4.06 V and can therefore *never* breach it. The one claw that
could go flat was the one with no battery spec.

**And a third instance sat in the same file.** `build_tick` computed
`ok = device_info AND ble`, so `dudeclaw-02` — which has no BLE scanner — was
pinned at `ok: false` in *every tick since it was built*, and the `/fleet`
rollup rendered this perfectly healthy claw as **"unreachable"**. A
permanently-false flag is not a conservative default: it teaches every reader,
human and probe, to ignore it, which is exactly how a real failure hides.

## The cure

Give the claw its own vocabulary, per device, from the tick files the capture
cron already writes — **no second NATS poll** (one poller, one threshold set,
honest_failure_modes #5) and no subprocess (MF021 observation-only).

- **`claw_device_dark`** — the capture is running and writing FRESH ticks, and
  those ticks say the DEVICE did not answer. A statement about hardware, in
  hardware words. Pages: losing the out-of-band witness is losing the vantage
  we keep for when nothing else can be trusted.
- **`claw_battery_low`** — a *reachable* claw's pack is under the 3.5 V
  single-cell LiPo floor (chemistry, not an operator value — MF014). Hours, not
  days, of warning. Lower priority than dark BY DESIGN: this is the actionable
  early warning, not the outage.

Supporting changes:

- **battery is now captured per device** into `claw_last_tick*.json`, so pack
  voltage is a first-class fleet metric instead of living only in an ad-hoc CSV
  written by a transient `systemd-run` unit nobody watches.
- **`reachable` is now explicit** in the tick and is the liveness fact
  consumers must read. `ok` tracks it; accessory misses move to
  `degraded_optional` — reported, never folded into liveness, never swallowed.

## Boundaries that are deliberate

- **The fleet preset still does not poll NATS.** Duplicating the poll would
  double-load an ESP32 and create a second set of thresholds beside the claw
  mini's — two consumers, two hardcodes, guaranteed drift.
- **One fault, one owner.** An unreachable claw is `claw_device_dark`'s call;
  `claw_battery_low` deliberately stays silent on it rather than piling a
  second page on one event from a stale reading.
- **Unknown is not charged.** No gauge, or a capture predating battery
  collection, is *indeterminate* — never a low-battery claim and never a clean
  bill of health.
- **Stale ticks are not this probe's page.** A frozen tick read as current is
  the absence-of-evidence trap; `cron_verdict_stale` owns the dead-capture cron.

## Residual (why the row is narrowed, not closed)

Thresholded live *sensing* — chip temp, anomaly score, the staged LoRa "ears" —
still runs only inside a per-device claw mini instance. `dudeclaw-01` has one;
`dudeclaw-02` does not. So a claw without its own instance is now watched for
**liveness and battery** but not for its **sensors**. Standing up claw-02's
instance was deliberately deferred while its pack was mid-discharge-measurement
(polling it would perturb the very curve being measured).

The staged `claw_sensors.with_ears.json` (LoRa `lora_stats`, RF-silent after
30 min) is the natural next step and is the strongest candidate for the
**`mesh_rf_ota_leg_unwatched`** row's independent OTA witness — the claw hears
LongFast at the AREDN site, which no box-side self-report can corroborate.

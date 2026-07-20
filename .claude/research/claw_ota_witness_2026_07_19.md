# The claw as over-the-air witness — structural-dark row 9 (2026-07-19)

> First mesh-RF evidence in this fleet that no box produces about itself.
> Row narrowed, not closed — the residual is named at the bottom.

## The blind spot

`mesh_rf_ota_leg_unwatched`: bot output can stop reaching nodes over the air
while every box-side indicator reads green. The gateway's self-report says
sent, the RNS round-trip canary passes, the service is active, the queue
drains — and nothing leaves the antenna. Deaf radio, wrong region/preset, dead
PA, unplugged coax: all invisible, because **every mesh-RF check we had was a
box talking about itself.**

## What changed

Both dude-claws answer `lora_stats` — a **separate radio on separate silicon**
reporting what it actually heard on the channel:

```
mesh_heard_age_s: 4 (heard 158224 pkts, crc_err 1461, runts 1,
                     last from=!79be01d3 to=!ffffffff ch=0x08 rssi=-41 snr=6.5)
```

That reading is now captured into each claw's tick alongside device_info and
battery, and `probe_claw_rf_silent` consumes it. This is physical-layer
evidence a box cannot fabricate about itself — the property that makes it worth
having.

Verified live at build time: claw-01 `heard_age_s: 4` / 158,224 pkts,
claw-02 `heard_age_s: 0` / 101,430 pkts. Two independent vantages, both hearing.

## The rules it follows

- **Only ALL claws going quiet counts.** One deaf claw is that claw's problem;
  the channel is only implicated when every reachable claw reporting a reading
  is silent. A single-radio failure must not be dressed up as an RF outage.
- **Unreachable ≠ silent.** A dark claw hears nothing by definition; that
  belongs to `claw_device_dark`. One fault, one owner.
- **No ears ≠ quiet air.** A claw whose firmware lacks `lora_stats`, or a tick
  predating the capture, is *indeterminate*. Unknown never becomes silence.

## ⚠️ The threshold is PROVISIONAL — and that is why it escalates, not pages

The 1800 s quiet window came from the operator's staged sensor spec, explicitly
marked *"SOAK the heard-rate (incl. overnight lulls) before enabling."* **That
soak data did not exist until this capture shipped** — nothing was recording
heard-age over time.

So `claw_rf_silent` is wired `propose_escalation`, never `ntfy`. A genuinely
quiet night can reach 30 minutes with no traffic, and paging on a threshold
nobody has measured is the "worked once ≠ reliable" trap wearing an RF hat.

**Promotion path** — the same one `calibration_drift` walked (34 days of soak,
one fire episode, 2 true positives, 0 false, then promoted to a pager in row 4):

1. let the capture accumulate `lora.heard_age_s` across several full
   day/night cycles on both claws;
2. read the actual quiet-hours maximum — the real overnight floor, not a guess;
3. set the window above the measured maximum with margin, and only then flip
   the seed rule from `propose_escalation` to `ntfy`.

Until that happens, an escalation here means *look*, not *the mesh is down*.

## Residual (why the row is narrowed, not closed)

**This proves traffic EXISTS on the channel; it does not prove THIS box's
transmission reached the air.** A gateway could be fully deaf/mute while
neighbouring nodes keep the channel busy and the claws keep hearing them — the
probe stays clean and the original failure mode survives.

True egress proof needs one of:

- **per-source counters in `lora_stats`** — the firmware currently reports only
  `last from=`, a single most-recent source, so "did the claw hear *my* node id"
  can't be answered reliably by sampling; a firmware change would close it;
- **mesh ACK consumption** (#74 T2 step 4) — the box-side half, still the
  cleanest proof that a specific outbound packet was received by someone.

The claw half of row 9 is now real. The egress half remains open, and those two
routes are the candidates.

# `+dudeclaw.20` flash cycle + the one bench session

> Written 2026-08-31 after F1–F3 were built, drilled where possible, and
> pushed (fork `25a2dd3`, MF `74154aa6`). **Nothing is flashed.** All three
> claws are live on `0.4.0+dudeclaw.19`, uptime ~20 h at time of writing.
>
> This plan exists because three of the four remaining items need a human at
> the hardware, and they should cost ONE visit, not three.

## What is already closed (do not re-derive)

| | State | Evidence |
|---|---|---|
| F1a reply buffer 768→1408 | built | 3/3 envs SUCCESS, marker verified inside each `firmware.bin` |
| F1b ` cut=1` witness | built + **natively drilled** | ASan+UBSan, every buffer size 1..894: 0 overruns, 0 silent truncations, 0 false marks |
| F2 `hop_start > 0` | built + **natively drilled**, operator-ratified | exhaustive over all 256 flag bytes: exactly the 4 `(0,0)` encodings change |
| F1 reader half (MF) | shipped, CI green | `74154aa6`, full suite 11025 passed, CI run 33415107378 success |
| **F3 `wifi_pass` guard** | **BUILT, UNDRILLED — OPEN** | no drill is possible off-hardware |

Re-run the host drills any time: `tools/drills/run.sh` in the fork.

---

## ⚠️ THE RISK THAT GATES EVERYTHING: which USB device is a claw?

Three ESP32s are on USB across the fleet, and **`meshtasticd` is active on all
three of their hosts**:

| host | by-id MAC | claw units on host |
|---|---|---|
| moc1 | `80:F1:B2:xx:xx:xx (Espressif OUI) — board A` | 0 |
| moc2 | `80:F1:B2:xx:xx:xx (Espressif OUI) — board B` | 4 |
| moc5 | `44:1B:F6:xx:xx:xx (DIFFERENT OUI) — board C` (**different vendor block**) | 0 |

FORK.md's standing warning applies exactly here: *a Meshtastic node is also an
ESP32-S3, and at least one host box has a live RNode on USB that `esptool`
would hard-reset (or worse) if aimed at the wrong path.* Two of these three
have no claw units on their host, and one has a different OUI. **Do not assume
any of them is a claw.**

**The identity check is ONE-SIDED, and that is a finding.** FORK.md says the
by-id name embeds the MAC so it "doubles as the board identity check" — but
`device_info` publishes `heap/reset_reason/uptime/wifi/chip/ip/version` and
**no MAC**. There is nothing on the bus side to check the flash-side identity
against. So identity must be established by OBSERVATION before any write.

### Step 0 — prove identity with zero writes (do this first, every board)

Pure observation, both sides, no `esptool` write of any kind:

1. On the host: `ls -l /dev/serial/by-id/` and note the entry.
2. Power-cycle (or unplug) exactly ONE claw.
3. Confirm BOTH halves move together:
   - host side: that by-id entry disappears / reappears;
   - bus side: that claw's `device_info.uptime_s` resets (poll from moc2).
4. Record the MAC↔claw-name mapping as you go — **NOT in this file.** This
   repo is PUBLIC; full board MACs are masked above to their OUI for exactly
   that reason. Put the real mapping in the off-repo session notes
   (`~/.claude/plans/gateway-session-notes-*.md`). The mapping does not exist
   anywhere today, which is why this is step 0.

A board whose by-id entry moves but whose claw uptime does NOT reset is **not
that claw** — stop, do not flash it.

> Deferred, deliberately: adding `mac` to `device_info` would make this
> two-sided permanently, but it is itself a firmware change and cannot help
> the flash that installs it. Queue it for `+dudeclaw.21`, not this cycle.

---

## ⚠️ BEFORE THE FLASH: restart moc2's claw units, or F1 stays broken

Found 2026-08-31 by exercising the deployed reader on moc2 rather than
trusting the deploy. **`fleet_pull` deploys files and restarts nothing**, so:

* the FILE on moc2 is current (`c10f2d81`, parses ` cut=1` correctly —
  verified live: `stats_truncated: True`, verdict `unobservable`);
* the RUNNING processes are not. All three claw units started
  **2026-08-15** and have carried 16-day-old code ever since.

```
meshforge-mini-dudeai-claw.service          (USER units, not system)
meshforge-mini-dudeai-claw@claw02.service
meshforge-mini-dudeai-claw@claw03.service
```

**Why this is not cosmetic.** Flash a claw to `.20` and it starts emitting
` cut=1`. moc2's *running* parser predates that token, so it ignores it and
reads the clipped `@-104` → `@-1` as a clean −1 dBm — **the exact forged
reading F1 exists to prevent**. The fix would be shipped on both sides and
still fully defeated, with every surface reporting healthy.

This is honest_failure_modes #4 (reader/writer pairs wire together or fail
together) applied to the DEPLOY step rather than the code: the two halves are
correct, and the deployment leaves only one of them running.

```bash
# on moc2 — USER scope, no sudo (verify scope before acting)
systemctl --user restart meshforge-mini-dudeai-claw.service \
                         meshforge-mini-dudeai-claw@claw02.service \
                         meshforge-mini-dudeai-claw@claw03.service
```

Then confirm the running process actually took it — `ActiveEnterTimestamp`
must be NEW, and a restart's proof is the artifact, not `active`:

```bash
systemctl --user show meshforge-mini-dudeai-claw -p ActiveEnterTimestamp --value
```

Do this **before** flashing, not after: until it is done, a `.20` claw is
strictly worse observed than a `.19` one.

## The flash

App-only reflash — LittleFS config survives, so no reprovisioning:

```bash
pio run -e <env>
esptool --chip esp32s3 --port /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_<MAC>-if00 \
    write-flash 0x10000 .pio/build/<env>/firmware.bin
```

⚠️ **One env per claw — they are NOT interchangeable.** `esp32-s3-heltec-v4`
(LongFast ears) · `-st` (SHORT_TURBO ears, different modem params) · `-agent`
(different FS charter + LLM buffers). Building one and flashing another
breaks a claw's purpose with no error. Confirm each claw's env from its
existing behaviour (`lora_stats` reports what it actually hears) before
picking a binary.

⚠️ Address the board by `/dev/serial/by-id/`, never `ttyACM0` — enumeration
order is not stable and the wrong path may be a live radio.
⚠️ apt `esptool` is dfsg-stripped (no S3 stubs) — use the pipx one.

**Verify the flash landed over the BUS, not from esptool's output**: the
claw's `device_info.version` must read `0.4.0+dudeclaw.20` and `uptime_s`
must be small.

---

## The bench session — four items, one visit

### B1. F3 — the `wifi_pass` refusal (THE open item)

The only fix in this cycle with no drill at all. A guard that has never
refused anything is not evidence that it refuses correctly.

- Build a littlefs image whose `config.json` has the **`wifi_pass` key
  removed entirely** (not blanked — empty value is legal by design, an open
  AP is a real deployment). Point `PLATFORMIO_DATA_DIR` at a directory
  **outside the repo** so no credential can be committed.
- Flash that FS image, call `reboot` over the bus, expect:
  `Error: config.json lacks wifi_pass (short or torn read) — rebooting would
  strand this claw off a WPA2 AP; refusing`
- **Then prove the negative**: restore a config WITH an empty `wifi_pass`
  value and confirm `reboot` is ACCEPTED. A guard that refuses both ways is
  broken in the more expensive direction.
- Restore the real config and confirm the claw rejoins.

### B2. The reboot tool's REFUSAL path (oldest open caveat on the tool)

Every live drill so far exercised only the happy path. Blank the `wifi_ssid`
via the setup portal and confirm the ORIGINAL refusal fires. Same visit,
same board, ~2 minutes once B1's rig exists.

### B3. F1 — the 12-id watch list

**The margin is thinner than the arc assumed.** Live lists today are
**5, 5, and 2 ids** — not the "2–3" the provenance row recorded — and the
measured worst case is 830 chars at 12 ids against the old ~743 budget, so
the old firmware truncates somewhere around **11 ids**. The defect was closer
to arming than anyone thought.

- Temporarily set a 12-id watch list (`claw_set_watch_ids` accepts 12).
- On `.20`, confirm the reply is COMPLETE and carries **no** ` cut=1`
  (1384 usable now clears 830).
- Then confirm the witness actually fires: the host drill already proves it
  at every buffer size, so the bench only needs to show the field path
  agrees — if you want a live positive, the `-agent` env's
  `TOOL_RESULT_MAX_LEN` 512 still clips at ≥8 ids (known accepted residual)
  and will produce a genuine truncation to observe.
- **Restore the original list** — record it before you change it.

### B4. claw-02 RSSI `-0` — and it is broader than logged

Live data 2026-08-31 refines this item. It is not only claw-02, and not only
`watch=`:

| claw | `watch=` rssi | `direct=` rssi |
|---|---|---|
| dudeclaw-01 | real (−98..−102) | real (−111, −45) |
| dudeclaw-02 | **0 for 2 of 5 ids** | **0 across the board** |
| dudeclaw-03 | real (−61) | **0 across the board** |

Two claws report `direct=` RSSI of exactly 0 while their `watch=` RSSI is
plausible, and claw-01 does neither. `s_watch_direct_rssi[i]` is assigned
from `s_last_rssi` at the moment `hops == 0` is seen, so a 0 there means
`getRSSI()` returned 0 at that instant. That is the `getRSSI(true)` vs
`(false)` question — test both on the bench and compare against a known
reference signal.

⚠️ **A 0 dBm RSSI is the same defect class F1 just fixed**: a degraded
reading that lands inside the healthy domain. 0 dBm is not implausible enough
to reject, and it is what a link budget would be computed from.

---

## Order of operations (why this order)

1. **Step 0 identity** — before any write, on every board.
1b. **Restart moc2's three claw units** (see the section above) — a `.20` claw
   observed by a pre-`.20` reader is worse than no fix at all.
2. **Snapshot `direct=` for all three claws BEFORE flashing.** F2 changes
   what counts as direct, so this snapshot is the only chance to measure its
   real-world effect: any entry that flips from `direct:true` to absent was a
   **forged** direct link that F2 caught. Losing the baseline loses the proof.
3. Flash ONE claw, verify over the bus, let it soak before touching the others
   (never rapid-cycle the fleet).
4. B1+B2 on the bench board (they share a rig).
5. B3, then restore the watch list.
6. B4 last — it is investigation, not a fix, and it should not gate the flash.

## Not in this cycle

- **OTA repartition USB visit** — still open; the window closes when the
  hardware goes remote, so decide it at the same visit.
- `mac` in `device_info` (see Step 0) → queue for `+dudeclaw.21`.
- Fleet deploy of the MF reader half: `honest_status` reads
  `fleet SHA drift FAIL 0/9 @ 74154aa6` — expected, deploy is manual. Use
  `scripts/fleet_pull.sh` (ff-only, no restarts). The reader half is inert
  until claws run `.20`, so there is no rush and no risk in either order.

---

# STATUS 2026-08-31 — 2 of 3 flashed, claw-02 HELD

| claw | host | env | version | notes |
|---|---|---|---|---|
| dudeclaw-01 | moc1 | `esp32-s3-heltec-v4` | **`.20`** | 906.875 MHz confirms base env |
| dudeclaw-03 | moc2 | `esp32-s3-heltec-v4-st` | **`.20`** | 905.750 MHz confirms ST env |
| dudeclaw-02 | moc5 | `esp32-s3-heltec-v4-agent` | `.19` | **HELD** by decision |

Both flashes: md5 matched end-to-end, `Hash of data verified`, WiFi rejoined
(no strand), watch lists preserved across the app-only write.

**Identity map lives OFF-REPO** at `~/claw_board_identity_map.txt` (this repo is
public). It is counterintuitive and must not be guessed: moc2 hosts claw-01's
DEFAULT tick file but has **claw-03** attached; moc5 runs ZERO claw units yet
holds **claw-02**. Every claw runs a DIFFERENT env.

## ✅ Step 0 is SOLVED — and remote flashing needs no bench visit

`esptool --no-stub read_mac` resets the board; **whichever claw's uptime drops
on the bus is the one on that port.** Zero flash writes, definitive, remote.
Three records written weeks ago independently corroborate the result.
(The `44:1B:F6` "foreign OUI" suspicion was WRONG — Espressif holds several
blocks. All three boards are claws.)

⚠️ A **software reboot does NOT work** for this — an ESP32-S3's USB-Serial-JTAG
does not re-enumerate on `sw-restart`. Tried it; no port dropped on any host,
and the null could not discriminate "not attached" from "does not re-enumerate."
Use the esptool reset, or a physical power-cycle.

## ⭐ ENV VERIFICATION — the firmware tells you, don't trust your `scp`

Right after a flash the claw has heard 0 packets, so `lora_stats` takes its
**never-heard branch and prints the compiled-in frequency**:

    base env  -> 906.875 MHz
    -st  env  -> 905.750 MHz

That is a self-report from the running firmware — an authority you did not
author — proving which env is live. Check it on EVERY flash, before believing
you copied the right file. Do it within the first minutes; once packets arrive
the reply switches to the heard branch and the MHz is gone.

## ⏳ THE PENDING MEASUREMENT — F2 before/after (needs hours)

Baseline: `~/claw_direct_snapshot_pre_dudeclaw20_20260831T180729Z.json`
(pointer: `~/.claw_direct_snapshot_latest`). Captured on `.19` over a ~21 h
window, and it recorded **12 of 12 watched nodes as `direct=true`** — implausible
in a flood mesh, and exactly F2's predicted forgery signature.

**Do NOT compare yet.** Counters reset at boot, so both flashed claws restarted
from zero, and their watch nodes have `required_window_s` ≈ 32400 s (9 h). A
short window would read as "everything went `never`", which is the reset, not
the fix.

### The window is now WATCHED, not remembered (2026-08-31)

"No new machinery is needed" was wrong in one respect, and the gap was silence,
not capability: `claw_last_tick*.json` is **overwritten every ~40–60 s** by the
`*/5` metrics cron, so the post-flash window existed only for as long as someone
was at a terminal at the right hour. Two ways to lose it without a word being
said — nobody runs the comparison at ~21 h, or any claw reboots and resets its
counters — each costing another ~21 h to re-earn.

`scripts/claw_direct_snapshot.py` now runs on moc2 every 30 min and captures
**exactly once**, when every claw has met **its own** baseline window. It runs
on moc2 rather than the manager box on purpose: the ticks are local there, so
the capture has no ssh leg to fail, and moc2 being down already means the data
is not being produced.

Five outcomes, deliberately not collapsible into one another — `waiting` is
exit 0 (it persists ~21 h and must not page), the two loud ones are exit 1:

| outcome | meaning | exit |
|---|---|---|
| `waiting` | a claw has not reached its baseline window | 0 |
| `captured` | all claws met their window; written ONCE | 0 |
| `already_captured` | snapshot exists; idempotent no-op | 0 |
| `window_lost` | a claw REBOOTED — counters reset, window restarts | 1 |
| `unobservable` | a tick or the baseline is missing/stale/garbled | 1 |

Design points worth keeping:

* **Reboot detection reads `uptime_s` going DOWN**, never timestamps — device
  monotonic, so it survives this fleet's forgeable wall clocks (#6). It fires
  **once** and then reverts to `waiting`; an alarm that never clears is one
  people learn to ignore.
* **A missing claw is `unobservable`, not `waiting`.** A snapshot assembled
  from a partial fleet would record an unobserved claw as one with no direct
  links — the degraded-value-in-the-healthy-domain class F2 itself is about.
* **Identity comes from each tick's own `device` field, never the filename** —
  the host↔claw map is counterintuitive by measurement (step 0), and guessing
  it is the exact error that cycle exists to prevent.
* **`O_CREAT|O_EXCL`, and no `--force`.** Re-capturing means moving the file
  aside deliberately. A flag that clobbers the only copy of an irreplaceable
  21 h window is the defect the script exists to prevent.
* The capture **prints the F2 delta by node id** into the verdict record, so
  the headline survives even if nobody looks for days.

Drilled by planting each condition (`tests/test_claw_direct_snapshot.py`, 19
tests) — not by reading the source and believing it.

### It pages on STATE, not on a clock (2026-08-31)

The obvious wiring was an ntfy alarm at the estimated ready-time. Rejected: a
clock alarm fires whether or not the thing is true. If a claw reboots tonight,
`window_lost` restarts that claw's window — and the alarm would still summon
someone at 06:15 to run a comparison that is not ready. The estimated hour is
also just that: an estimate off boot times, at 30-min cron granularity.

`--notify` pages on the **transition** into `captured` / `window_lost` /
`unobservable`, and never on `waiting`. So the notification means the state it
names, and the `captured` page carries the **F2 delta in its body** — the
headline lands on the phone, not only in the verdict log.

Nothing extra watches for the job's own silence: it is wired through
`cron_verdict.sh`, so #78 `cron_verdict_stale` already owns that. No machinery
to watch machinery.

⚠️ **A page is not a delivery.** `fleet_ntfy_push.sh` is best-effort and always
exits 0, so the witness line says `delivery unproven until seen`. The push leg
is proven (drilled on a throwaway topic 2026-08-31, message polled back from
ntfy.sh with title/body/priority/tags intact); the **device** leg is proven
only by the operator seeing it — harness_map's tap-to-ack remains the only
device-leg proof.

⚠️ **Retire the cron once the comparison is recorded.** It is a measurement
scaffold, not a permanent organ; leaving it running is footprint we did not
earn.

The manual comparison below still works and is the thing to run once the
snapshot lands — moc2's claw capture writes `direct=` to `claw_last_tick*.json`
every ~40-60 s. After a comparable window (≥9 h, ideally ~21 h to match the
baseline):

```bash
# on moc2 — current state
python3 - <<'EOF'
import json
for f in ('claw_last_tick.json','claw_last_tick.dudeclaw-02.json',
          'claw_last_tick.dudeclaw-03.json'):
    d=json.load(open(f)); lo=d.get('lora') or {}
    dr=lo.get('direct') or {}
    t=sum(1 for v in dr.values() if v.get('direct') is True)
    print(d.get('device'), d.get('device_info',{}).get('version'),
          'uptime', d.get('device_info',{}).get('uptime_s'),
          'direct_true=%d/%d' % (t, len(dr)))
EOF
```

**Reading the result.** A node that was `direct:true` on `.19` and does NOT
re-earn it on `.20` over a comparable window was a **forged** direct link that
F2 caught. claw-02 stays on `.19` on purpose — it is the CONTROL: if it still
reads 5/5 direct while the two flashed claws drop, that is the cleanest
evidence F2 will ever produce, and flashing it early would destroy it.

⚠️ Honest limit: the three claws hear different traffic on different segments,
so this is suggestive, not a controlled experiment. Treat a drop as evidence,
not proof, and say which nodes changed rather than quoting a bare count.

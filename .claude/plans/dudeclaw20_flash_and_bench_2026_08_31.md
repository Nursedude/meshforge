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

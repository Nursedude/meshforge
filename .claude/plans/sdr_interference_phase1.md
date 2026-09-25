# SDR Phase 1 — interference detection at moc5 (design, 2026-09-24)

> Status: **DESIGN, not built.** Author Opus 5.5; design is frontier-shaped —
> a non-author (Fable) pass on THIS document is owed before code.
> Tags: **MEASURED** (ran 09-24, numbers quoted) · **BELIEVED** (reasoned) ·
> **OPEN** (needs the operator or a soak).

## 1. What this is for — and what it is NOT

**END**: tell the operator, in-app, when something other than our own LoRa
traffic is occupying or degrading the spectrum our radios depend on.

**Not** channel occupancy of our own channel. MEASURED 09-24: the radio's own
ChUtil (TX+RX+RX_ALL airtime, trailing 60 s — firmware v2.7.26
`airtime.cpp`) counts packets below the noise floor that energy detection
cannot see (rxSNR < −15: SDR 1/6 = control 1/6). The radio already logs
ChUtil + `noise_floor` every ~15 min (`Sending local stats`). The SDR's
unique value is what one tuned radio cannot see:

1. all four fleet channels at once (RNode 903.625, ST ch8 905.75,
   LF ch20 906.875, MeshCore 910.525), and
2. energy that is **not LoRa, or not ours**, on or near them.

**Scope limit, stated**: one receiver at one site (moc5, one of two
buildings ~250 ft apart through ohia forest). Interference seen here may be
absent at the other building, and vice versa. Every surface says "at moc5".

## 2. The three interference classes energy detection CAN see

| Class | Physical signature | Metric (per window, per run) | Why LoRa does not trip it |
|---|---|---|---|
| **A. Persistent carrier** (stuck TX, spur, CW, a non-hopping device) | a bin whose power is high MOST of the time | per-bin **median over time** > floor + 10 dB; report freq + level | LoRa is bursty: LF busy 3–27 % MEASURED, so a bin's time-median stays at the floor unless a channel is > 50 % occupied (then it IS a problem) |
| **B. Raised floor** (broadband RFI: switching supplies, LED drivers, a neighbour's noisy gear) | the whole window's floor rises | **absolute** floor dBFS at FIXED gain vs its own rolling baseline | relative-to-floor metrics (Phase 0's) are blind to this BY CONSTRUCTION — the floor is the reference. Absolute dBFS at fixed gain is the only honest axis |
| **C. Foreign bursty energy** off our declared bands (other meshes, LoRaWAN 125 kHz uplinks, FHSS gear) | busy frames in bins no fleet channel occupies | busy % per 125 kHz slice outside declared bands, leak-gated | our own TX leaks 46–55 dB down (MEASURED); slices are leak-gated against every fleet channel AND frames where LF > +45 dB (moc5's own TX) are excluded |

**Cannot see (stated on every surface)**: anything below the floor — incl.
foreign LoRa decoding at negative SNR; foreign LoRa ON our exact channel
(same shape as ours — the radio's own `RX_ALL − RX` is the instrument for
that, `airtime.h:25`); anything at the other building.

**Cross-check that can fail**: class B's absolute floor vs the radio's own
`noise_floor` dBm (journal, every ~15 min). Both up = environment; radio up,
SDR flat = local to the radio (its PA, its supply, #58-class hardware);
SDR up, radio flat = local to the SDR or off-channel. A divergence is a
finding, never averaged away.

## 3. Capture plan — sized to moc5's MEASURED limits

- moc5 is a Pi 4; the Airspy shares ONE USB2 hub with its CH341 LoRa radio.
  MEASURED: 21 min of continuous 0.5 s bursts (~35 % duty, 3 MSPS 12-bit
  packed) → 0 radio errors, NRestarts 0. Phase 1 runs far below that.
- **Every 5 min**: the 3 fleet windows (903.625 / 906.300 / 910.525),
  4 × 0.5 s bursts each ≈ 12 s of capture ≈ **4 % duty**. ~1.4 s per burst
  wall incl. spawn + FFT MEASURED → ~17 s CPU-ish per run on one core.
- **Hourly**: a full 902–928 pass, 11 windows × 2 bursts, context for
  class C (who else lives in the band). ≈ 30 s.
- **Fixed gain** (linearity 10, MEASURED 0 clipped in 878 bursts). Gain is
  recorded in every row; a gain change starts a NEW baseline, never mixes.
- Clipped burst → `overload`, dropped, counted. Failed capture → `unknown`.
  Never 0 %, never "clean" (hfm #1/#2).

## 4. Storage and surfaces

- **Writer**: `scripts/sdr_interference.py` (grows from
  `sdr_fleet_channels.py`; shared analysis in `src/utils/sdr_analysis.py`
  so tests import it). One writer, `flock`-refused if a run is live (hfm #8).
- **Data**: JSONL at `get_real_user_home()/.local/share/meshforge/sdr/
  interference.jsonl`, one row per run: ts (monotonic + wall), gain,
  status, per-window floor dBFS, per-channel median/busy, class A/C hits
  with freq + level. ~1 KB/run → ~300 KB/day; size-capped rotation (30 d).
  JSONL, not SQLite: no DBSpec/MF013 surface, and it is append-only.
- **Unit**: `meshforge-sdr.timer` + `.service`, **user** scope on moc5,
  template in `templates/systemd/` (pre-push line 4). ⚠️ user units are
  structurally invisible to `probe_service_inactive` (#82) — so the
  surface carries the witness: **the pane shows the age of the newest row
  and reads UNKNOWN (not "no interference") past 3 × cadence.**
- **Surface**: one read-only TUI pane, local-box-only (TUI stays a surface):
  per channel — floor now vs baseline, busy seen, class A/B/C findings with
  frequency; the blind-spot line always printed. No actions.
- **NO mini signal class, NO paging in Phase 1** — see §6.

## 5. Falsifiability — every class gets a control that can fail

- **Unit tests on synthetic IQ** (pure analysis functions): plant a CW tone
  (A must fire, at the right freq), a +6 dB wideband noise step (B must
  fire), a 125 kHz burst outside fleet bands (C must fire), and a real-shape
  LoRa chirp train at 30 % duty on LF plus a +55 dB own-TX burst (NONE may
  fire). The chirp + own-TX case is the one that failed Phase 0's first run.
- **Live drill (OPEN — operator)**: 902–928 is the 33 cm ham band. A 33 cm
  FM handheld keyed briefly at a known frequency off our channels is the
  ideal planted interferer: class A must fire at that frequency within one
  run, and clear the run after. Without one, the drill is a fleet radio on a
  known channel (weaker: it is LoRa, so only class C/leak behaviour).
- **Soak before thresholds**: 7 days data-only. The 10 dB / baseline /
  persistence numbers above are ASSERTED starting points; the soak's
  per-channel, per-hour base rates replace them (MEASURE > ASSERT). A class
  that fires on every run, or never, is re-cut before anything reads it.

## 6. Freeze and ladder

`harness_restraint.md` (active until 2026-10-09): no new signal class /
detector. Phase 1 respects it by being **data + a surface only** — its END
is RF truth on the operator's screen, not the harness. Whether class A/B/C
ever become mini signal classes (paging) is decided AFTER 10-09 and AFTER
the soak shows base rates — "a guard that never failed is not evidence",
and one that fires hourly is noise.

Build order (each step is its own commit with its control):
1. `utils/sdr_analysis.py` + synthetic-IQ tests (A/B/C fire; LoRa/own-TX don't).
2. `scripts/sdr_interference.py` writer + JSONL + flock; one manual run on moc5.
3. User timer on moc5 (template + install), 5-min cadence; USB control
   re-measured over the first 24 h (radio errors, NRestarts, RX rate).
4. Read-only TUI pane with the staleness witness + blind-spot line.
5. 7-day soak → thresholds from data → live drill → operator decides on paging.

## 7. OPEN questions for the operator
1. Is a 33 cm handheld available for the planted-interferer drill?
2. Is 5-min cadence right, or is hourly enough for what you want to know?
3. The pane: MeshForge TUI on moc5 only, or also surfaced in the fleet view
   (that would read moc5's JSONL over ssh — a later, separate step)?

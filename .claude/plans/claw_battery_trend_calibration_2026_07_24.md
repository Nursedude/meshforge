# Claw battery trend calibration — 4 changes (2026-07-24)

> Written to disk BEFORE implementing so the spec survives a context reset.
> Baseline: `2373ead3` (pushed, CI green, fleet-deployed). Follow-on to
> `4c840cb5` (claw battery intelligence).

## The data that motivated it (moc2, dudeclaw-02, measured 07-24 11:50 HST)

Probe's own rolling series: **5 samples / 20 min, all 3.46 V** — no fit possible.

`~/battery_soak.jsonl`, device-tagged rows: **227 samples / 37.5 h**,
4.03 V → 3.46 V (**−570 mV**). Fits over trailing windows:

| window | n | slope | fitted ΔV | fixed gate | window-aware gate |
|--------|---|-------|-----------|-----------|-------------------|
| 1 h  | 6   | 0.0 mV/h   | +0 mV   | float | float |
| 6 h  | 36  | −2.8 mV/h  | −16 mV  | float | float |
| 12 h | 72  | −7.5 mV/h  | −89 mV  | TREND | TREND |
| 24 h | 144 | −12.3 mV/h | −293 mV | TREND | TREND |
| all  | 227 | −14.6 mV/h | −548 mV | TREND | TREND |

Readings quantize to **10 mV** (`Battery: 3.46 V (adc 706 mV)`), so a 6 h window
holds ~1.6 ADC steps of movement. The pack is on the **flat part of the LiPo
curve** near the knee — genuinely discharging, but unresolvable short-window.

**Rejected**: lowering `TREND_V_PER_HR` to catch −2.8 mV/h. It would let a
30-min window declare a trend from 1.4 mV — 1/7 of one ADC step, i.e. slopes
manufactured from quantization noise. Wrong lever; the window is the problem.

## The four changes

### 1. Merge the soak series into the probe's view  ← biggest win
The probe built a SECOND record of a pack that already had a longer, denser,
device-tagged one (two records of one artifact). Merge probe series + soak
series, dedup by ts (`_clean` already sorts/dedups). Soak file is
`<operator home>/battery_soak.jsonl`, rows `{ts, vbat, device}` — filter to the
device, treat untagged rows as NOT this device (matches the 07-24 fix in
`battery_soak._read_samples`: untagged ≠ foreign, but also ≠ confirmed-ours).
Probe series stays the always-on source for every non-soaked claw.

### 2. Window-aware trend gate
Replace the fixed `|slope| >= TREND_V_PER_HR` with
`|slope| * window_hr >= NOISE_V` — the FITTED CHANGE across the observed window
must clear the noise floor. Catches a glacial-but-real decline over a long
window; refuses a big slope inferred from a few minutes.

### 3. Bound the fit to a trailing window (default 12 h)
Fitting all 37.5 h yields −14.6 mV/h → "3.4 h to cutoff", but that average
includes the steep opening drop. 12 h → −7.5 mV/h → ~6.7 h. "At the current
rate" must mean RECENT. Stays labelled a LINEAR projection (a LiPo near the
knee is not linear in either direction).

### 4. A short window is not `float`
`float` implies charge-backed/stable. Under `MIN_FLOAT_WINDOW_HR` (3 h) with no
resolvable movement, direction is UNOBSERVED → `unknown`, not `float`. This
pack has lost 570 mV; calling it "flat/charge-backed" off 20 min is the
honest_failure_modes class (indeterminate wearing a healthy label).

## Invariants to preserve (do not regress)

- thin evidence → `slope=None`, never 0.0
- no reading → `unknown`; never charged, never flat
- no projection off a non-trend; `MAX_PROJECTION_HR` clamp stays
- bool voltages rejected; ts sorted/deduped (forgeable clock)
- intent (`soak_armed`) never rewrites a measured state; only sets `expected`
- `_soak_armed_devices` fails toward paging (unnamed/malformed → arms nothing)
- injected ticks never read the on-disk soak config (test hermeticity)

## Verify

`pytest tests/test_claw_battery.py tests/test_watchdog_coverage.py
tests/test_battery_soak.py` → then full suite, lint, parity; then LIVE re-check
on moc2 through the production path (expect: trend fitted off the merged 227+
sample series, `expected` still true, still no page).

## Status

- [x] 1 merge soak series — `read_soak_series` + both probes
- [x] 2 window-aware gate — `is_trend(slope, window_hr)`, TREND_V_PER_HR removed
- [x] 3 bounded trailing fit window — `FIT_WINDOW_S = 12 h`
- [x] 4 short window is not float — `MIN_FLOAT_WINDOW_HR = 3 h`
- [x] dry-run on the real 228-sample record (quoted below); full suite 9468
      passed exit 0, lint 0, parity in sync
- [ ] live re-check on moc2 AFTER deploy

**The watcher caught the old code red-handed (22:05Z)**: on its own 8-sample /
35-min series — a single 10 mV ADC step, 3.46 → 3.45 — the deployed fixed gate
reported *"falling 17 mV/h, ~2 h to 3.41 V"*. The 228-sample record says the
real trailing-12 h rate is −7 mV/h, ~6 h. So the live number was ~3x too
pessimistic, fitted from one quantisation tick. On the SAME bytes the new gate
returns `trend=False, hours_to_cutoff=None` ("direction unobserved — only 0.6 h
of readings"), and the merged 236-sample series fits −7 mV/h → ~5.9 h.
This is the case for the window-aware gate over lowering the constant, made by
the data rather than by argument.

Tick these ONLY against a quoted result. (First draft of this file pre-ticked
all five and invented a commit SHA — the exact defect class the repo's
calibrated-claims rule exists to stop, caught and corrected on the same turn.)

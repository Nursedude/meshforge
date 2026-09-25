# SDR Phase 1 — interference detection at moc5 (design rev 2, 2026-09-24)

> Status: **DESIGN rev 2, not built.** rev 1 author Opus 5.5; non-author
> review Fable 5.1 (same day): **"build with changes"** — 7 CONFIRMED, 3
> PLAUSIBLE, surfaces REFUTED/sound listed in §9. rev 2 applies every
> finding; each change cites its number `[R#]`. rev 2 is author-applied and
> NOT re-reviewed (review-your-own-fixes: queue before step 2).
> Tags: **MEASURED** (ran, quoted) · **BELIEVED** (reasoned) · **OPEN**.

## 1. What this is for — and what it is NOT

**END**: tell the operator, in-app on moc5, when something other than our
own LoRa traffic occupies or degrades the spectrum our radios depend on —
and, for the lab AND a future field site, whether that trouble is
**filterable** (out-of-band) or **not** (in-band).

**Not** occupancy of our own channels. MEASURED 09-24: Meshtastic's ChUtil
(TX+RX+RX_ALL airtime, trailing 60 s, firmware v2.7.26 `airtime.cpp`)
counts packets below the floor that energy detection cannot see (rxSNR <
−15: SDR 1/6 = control 1/6).

**Operator decisions 09-24**: 5-min cadence; pane in moc5's TUI only; no
33 cm handheld; **the RNode's own `Noise Fl.`/`Intrfrnc.` (rnstatus) is the
cross-check for 903.625** — the SDR does not duplicate it [R12: not a
duplicate — those are firmware bytes for 903.625/250k only, and
`Intrfrnc.` is UNFILTERED (RNS's LNA-recal filter is commented out); read
twice minutes apart it gave −102 and −88 → use as a slow reference, like
the radio's noise_floor, §2.B].

**Physical geometry (operator)**: moc, VolcanoAI (+ RNode 903.625, 22 dBm,
airtime 0.23 %/h MEASURED), alaula and kiai all < 10 ft from the Airspy;
dudeclaw-02 ~3 ft; ST boxes moc2/moc3 in the other building ~250 ft
through forest. The Airspy sits INSIDE the densest emitter cluster, so
**our own near-field transmitters are the main false-positive source.**
One receiver, one site: every surface says "at moc5".

## 2. The three classes — rev 2

### A. Persistent carrier — narrowed to what it can honestly see
rev 1's per-bin time-median metric is wrong both ways [R2, CONFIRMED by
synthetic IQ]: a LongFast chirp (8.2 ms symbol) touches each bin in ~1/12
of frames, so even a 100 %-jammed LF reads +1.9 dB (blind); a ShortTurbo
symbol (0.26 ms) sweeps the whole BW inside one frame, so ST at 60 % duty
reads +10–11 dB on 482 bins (false fire). A 45 % CW reads +5.8 (invisible).
- A looks ONLY at bins **outside every declared fleet channel** (+ guard).
  Inside a fleet channel, a >50 % sample is reported as `channel saturated
  in sample`, never as a carrier.
- A finding needs **≥ 2 consecutive runs**. Carriers below 50 % duty of the
  sample belong to class C (analyse() measured a 45 % CW as 45.1 % busy).
- **Spur map first** [R3, CONFIRMED live]: a comb 6–8 dB over the per-bin
  median in every window (903.377, 905.4035, 909.69/910.024/911.36,
  923.83/924.17/924.50 — ~333 kHz spacing; the 09-24 adjacent survey adds
  911.998 +13 and 937.51 +12.4 as candidates). Source (Airspy's own vs
  external) is undecidable without a **terminated-input reference** (§5).
  Every A hit reports "first seen <run>, present in N % of runs"; bins in
  the spur map are listed separately, never as findings.
- The surface states the floor: "A sees a carrier that is on for > 50 % of
  the 2 s sampled, outside our channels."

### B. Raised floor — absolute, calibrated, two baselines
- Absolute dBFS at FIXED gain, per-bin baseline (not a scalar: the passband
  has the same ±1.2 dB slice shape in every window, biasing edge bins) [R6].
  MEASURED sound: floor identical across 903–925 (−94.4…−94.6 dBFS) and
  ±0.15 dB over 35 s; airspy_rx never enables AGC; `-g` sets mixer/LNA
  AGC off [R12].
- **Sensitivity is unmeasured** [R6]: gain sweep g0/5/10/15/21 →
  −99.6/−96.5/−94.4/−89.9/−75.0 dBFS cannot separate antenna noise from
  receiver noise. If the terminated floor is within ~1 dB of the antenna
  floor, B is blind to a +3 dB rise by construction. → the terminated-input
  reference sets it; **choose the lowest gain whose antenna floor sits
  ≥ 3 dB above terminated** (gain 5 costs only 2.1 dB vs 10 and buys IIP3).
- **Two baselines** [R7]: a FIXED gain-tagged soak reference AND a rolling
  one; report both deltas (a permanent new LED driver becomes the rolling
  baseline in one window — the fixed one still sees it).
- `soc_temp_c` in every row [R6] (diurnal drift must be attributable).
- **Cross-checks are SLOW references, never per-run** [R1, CONFIRMED at
  source]: Meshtastic `noise_floor` = integer MEAN of a 20-slot ring of
  single `getRSSI()` samples taken only at local-stats sends (irregular
  15 min–2 h MEASURED) → a ~5 h outlier-driven mean; it stepped −91→−88→−91
  →−86 in 5 days, the last step INSIDE an SDR session (UNKNOWN: was it us?).
  Use the median of ≥ 6 reported values. Its absolute −86…−91 dBm on
  250 kHz is 25–30 dB above kTB — calibration bias or a chronically raised
  floor at moc5; B must not anchor on it. Same treatment for the RNode's
  `Noise Fl.`.
- **"Was it us?" column**: every row carries `sdr_run_active`; the first 24 h
  of the timer re-checks whether noise_floor steps align with SDR runs.

### C. Foreign bursty energy — gated against our own near field
- Busy % per 125 kHz slice outside declared bands, leak-gated (MEASURED 09-24:
  our bursts leak 46–55 dB into neighbours).
- **Own-TX gate becomes ABSOLUTE and covers EVERY fleet channel** [R4]:
  a frame is excluded when ANY fleet channel in the window exceeds
  ~−40 dBFS at the operating gain (calibrate from the soak), not "LF > +45
  dB relative".
- **Blocker gate** [R4, CONFIRMED live]: a −21 dBFS pulse raised that
  frame's median across ALL bins by +8.2 dB (reciprocal mixing). An RNode TX
  during a 906.3 capture is OUTSIDE the window (3.25 MHz > 2.4 usable) so
  no in-window gate sees the parent. → exclude any frame whose out-of-channel
  floor exceeds the burst floor by > 6 dB, and count `blocker_frames` (a
  witness, and a de-facto near-field TX log).
- **IM3 tagging, not gating** [R5, PLAUSIBLE]: precompute 2fi−fj and
  fi+fj−fk with summed bandwidths for the fleet set (e.g. LF+RNode → 910.125,
  750 kHz wide, touching MeshCore; MC+LF → 903.225, touching the RNode).
  A C hit inside an IM set is tagged `IM3-candidate (parents)` and kept out
  of "foreign" until the soak sees it with parents silent. Expected rate
  ~1 event / 10 days of soak (overlap ~1e-4). Lower gain helps IIP3.
- MEASURED that C works on real foreign traffic: two ~5 kHz, 1–2 ms pulses
  at 924.234 / 924.871 MHz, +22–25 dB, 4 ms apart (FHSS) — correct behaviour.

### D (new). Adjacent-band blockers — "do we need a filter?"
Hourly pass **869–940 MHz** (30 × 2.4 MHz windows, 1 burst each ≈ 45 s wall,
MEASURED 09-24 18:25). Reports per window: floor, strongest steady carrier,
strongest peak, clip. First snapshot (n=1, BELIEVED as a trend): floor flat
−88.5 dBFS across 870–940; strongest = **cellular downlink 885.73 MHz, peak
−37.3 dBFS (+50.6)**, bursts +22–29 dB across 869–882, steady 875.01 (+18.8);
pager band 929–932 quiet (a 0.5 s look can miss bursty pagers); 0/30
clipped. Verdict today: no blocker strong enough to need a 902–928 SAW/cavity
filter at this site; 885.7 is the one to watch. The same pass at a FIELD
site (ECOMM kit: Starlink, inverters, generator) answers the field question.
**In-band noise is never filterable** — the pane says which kind it saw.

## 3. Capture plan and arithmetic — corrected [R11]
- Every 5 min: 3 fleet windows × 4 × 0.5 s = **6 s of IQ** (~2 % capture
  duty; ~20 s wall incl. spawn + FFT at 1.65 s/burst MEASURED).
- Hourly: the 869–940 pass (~45 s wall).
- Worst case: 34 bursts × 15 s timeout = 8.5 min > 5-min cadence → a run
  refused by `flock` writes its OWN witness row `status: skipped_overlap`
  (hfm #9) [R9].
- Clipped burst → `overload`, dropped, counted. MEASURED 0/878 + 0/21 + 0/30
  at gain 10, but RNode/MeshCore near-field clipping is UNTESTED (P(RNode TX
  in 2 s) ≈ 0.4 %) — OVERLOAD rows on those windows are expected data.

## 4. Storage, unit, surface
- `utils/sdr_analysis.py` (pure; tests import it) + `scripts/sdr_interference.py`
  (writer, `flock`). JSONL at `get_real_user_home()/.local/share/meshforge/
  sdr/interference.jsonl`, size-capped rotation (~1 KB/run).
- `meshforge-sdr.timer/.service`, USER scope on moc5, template in
  `templates/systemd/`. MEASURED ready: `Linger=yes`, operator in `plugdev`,
  device `root:plugdev rw`, `meshforge-tracer.timer` is a user-timer
  precedent there [R12].
- **Witness = per-window age of the newest row with `status: ok`** [R9] —
  not the newest row: a timer whose every airspy_rx fails (device gone, or
  `AIRSPY_ERROR_BUSY` after a timeout SIGKILL) writes fresh UNKNOWN rows
  forever. N consecutive UNKNOWN → the pane prints the remediation
  (`airspy_info`; if "not found", reseat the Airspy) — in-app (MF018), never
  an auto-action. **Never a USB reset "self-heal": it is the radio's hub.**
- Surface: one read-only TUI pane on moc5. Per fleet channel and slice:
  class A/B/C/D findings with frequency, both B deltas, spur-map count,
  blocker_frames, and the blind-spot line always printed.
- **No mini signal class, no paging** (freeze) — §8.

## 5. Physical acts (operator, ~2 min total) — highest value per minute
1. **Terminated-input reference** [R3, R6]: antenna off, 50 Ω terminator
   (or nothing) on the SMA, 30 s run at gains 0–21. Separates spurs from
   carriers and sets class B's sensitivity + the operating gain.
2. **Confirm the Espressif USB device on moc5's hub** [R4]: the reviewer
   found an ESP32 CDC-ACM (ttyACM0) with no consumer — probably dudeclaw-02's
   power cable. OPEN — operator.

## 6. Falsifiability — controls that can FAIL
- **Fixtures from recorded moc5 IQ, not clean synthetic** [R10, CONFIRMED]:
  clean synthetic passes trivially (LF 18.9 % busy, control 0.0 %, A +1.1
  dB) because Phase 0's failure was ANALOG (reciprocal-mixing skirts).
  Crop ~50 ms of a real near-field LF TX (~600 KB) as a fixture, or add a
  −50 dBc phase-noise skirt to synthetic. Planted A/B/C/IM cases on top.
- **USB control that can fail** [R8, CONFIRMED]: "0 errors" had no grep and
  NRestarts survives any packet loss. The 24 h control = moc5 RX/h divided
  by a same-site, same-preset sibling's RX/h (moc, < 10 ft), SDR-on vs off;
  plus the anomaly rate with its exact grep: `handleReceiveInterrupt called
  when not in rx mode` (baseline 1–7/h MEASURED; RX/h 340–470 over 5 d).
  The hub is Single-TT: CH341 and the ESP32 (both 12 M) share one TT.
- **Live negative control: dudeclaw-02** (~3 ft, 2 dBm LF, ~−29 dBm at the
  Airspy): a logged `mesh_send` must fire NO class and must appear in the
  SDR at that time. A mesh_send is outward traffic → operator asks first.
- **Class C live drill**: OPEN — needs an emitter on an off-fleet frequency
  (spare node on another slot, or retuning a claw = operator's call). Class
  A live: synthetic + recorded only, BELIEVED until a real carrier is seen.
- **Soak before thresholds**: 7 days data-only; every number above tagged
  "calibrate from the soak" is replaced by measured base rates.

## 7. Build order (each its own commit + control)
0. Operator physical acts §5 (terminated reference; ESP32 identity).
1. `utils/sdr_analysis.py` + fixtures from recorded IQ; A/B/C/D/IM cases,
   own-TX + blocker gates, and a planted-lie test per class.
2. `scripts/sdr_interference.py` + JSONL + flock + witness rows; one manual
   run on moc5.
3. User timer on moc5; 24 h USB control (RX/h ratio vs moc + anomaly/h) and
   the "was it us?" noise_floor check.
4. Read-only TUI pane with the per-window `ok`-age witness + blind-spot line.
5. 7-day soak → thresholds → dudeclaw-02 negative drill → operator decides
   on paging (after 2026-10-09).

## 8. Freeze
REFUTED-as-risk by the reviewer [R12]: END is RF truth on a local pane
(standalone offering); no mini class; the cross-check is
instrument-vs-physical-quantity. Record in the session note; paging is a
post-10-09, post-soak decision.

## 9. Review record (Fable 5.1, 2026-09-24) — what was checked and held
Sound: fixed gain / no AGC; clip scale (int16 = (adc−2048)<<4 then FIR,
FULL_SCALE 32767 right); short-term floor stability; user-unit
prerequisites; freeze posture; the taxonomy; JSONL + user timer; data-first
soak. Reviewer moc5 touches logged in review_provenance (18:16–18:22).

## 10. Open-input reference — MEASURED 2026-09-24 18:40–18:45 (antenna on, then removed)
No terminator yet (operator will get one); open SMA is a rough reference.
- **Class B is blind at gain 0–10**: antenna adds ~0 / ~0 / **~0.1 dB** at
  gains 0 / 5 / 10 (floor = the Airspy's own noise), ~0.7 dB at 15, **~4.5 dB
  at 21** (−74.5…−75.2 on vs −79.1…−79.3 open). → B is measured at HIGH gain
  in its own bursts; A/C stay at gain 10. A terminated run will widen, not
  shrink, the gain-21 margin.
- **Headroom at gain 10 is ~6 dB for our own near-field TX**: the recorded LF
  burst peaks ±16,940 of 32,767. Overload rows at gain 10 are expected data;
  at gain 21 any burst containing a near-field TX clips — B judges only the
  quiet bursts, and dropping the rest is correct.
- **Spur vs carrier**: 911.9985 is the Airspy's OWN (present antenna-off,
  +41.8 dB at g21 — would have been a false class A); a ~1/3-MHz comb whose
  offset moves with the tuning centre is internal → `SPUR_MAP_MHZ` keyed by
  (window, gain) in `utils/sdr_analysis.py`. **937.515 is external and real**
  (antenna-only, off the comb). **925.006 is antenna-dependent but ON the
  comb** → likely radiated by local electronics (BELIEVED), not a foreign 915
  device; the soak decides.
- **Defect found while building step 1**: `np.abs(int16 −32768)` overflows, and
  the Airspy's int16 is (adc−2048)<<4 — an ADC pinned at the NEGATIVE rail read
  as unclipped in both the Phase 0 script and the first cut of the module
  (planted: old clip_frac 0.0, fixed 0.02). Earlier "0 clipped" results rest
  on the positive rail also reading 0 — BELIEVED to hold, not re-verified.

## 11. USB control — baseline and PRE-REGISTERED criteria (2026-09-24 19:10, before the timer)
Metric (review R8): moc5 RX/h ÷ moc RX/h (moc: same LF preset, < 10 ft) and
moc5 anomaly/h. RX grep: `\[Router\] (Received |Rebroadcast received)`;
anomaly grep: `handleReceiveInterrupt called when not in rx mode`.
SDR-OFF baseline, 36 h of the last 48 (moc RX ≥ 20/h; today's SDR sessions
excluded): ratio **median 1.057, p10 1.023, p90 1.101**; anomalies **median
3/h, max 7**; RX/h medians moc5 386.5, moc 366.5. Today's SDR-session hours
already read 1.015–1.071 / 1–7 (partial hours, noisy edges).
**24 h after the timer starts, the control FAILS if**: the median ratio is
< 1.023, OR the median anomaly rate is > 6/h, OR any hour has > 14 anomalies.
Fail → stop the timer first, then investigate (never a USB reset).

## 12. Code review of steps 1+2 (Fable 5.1, 2026-09-24) — applied, NOT yet deployed
Verdict "with fixes first". Applied (author-applied; this is itself unreviewed):
own-TX gate gains a RELATIVE term (any fleet band > burst floor + 20 dB) — the
absolute −40 dBFS gate alone failed OPEN for our own emitters 15–36 dB weaker
than the recording, and class C called them foreign on 4–11 slices (#1); the
§2.C slice leak gate is NOT built — mutation testing showed it can never fire
once the relative gate exists (a slice leak needs a band > floor + 36 dB);
in-channel busy % counts our own traffic (own-TX frames also trip the blocker
gate via reciprocal mixing); ≥ 25 % of frames kept or `unjudgeable` (#5);
window `ok` needs a majority of bursts, carriers a majority of ALL bursts (#5);
run `ok` needs class B too (#4); --set-reference merges + writes atomically
(#2); any exception writes an `error` row; a truncated reference npz reads
`unreadable`, carried in every row (#3/#11); capture timeout 5 s (#3);
persistence skips rows without windows (#7); class D masks our own channels
(#8); spur map covers every fleet window × gain (#9); history reads backwards
across rotation (#10/#11). Mutation test after: every non-equivalent mutant
killed (18/18 across two passes).


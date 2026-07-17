# Kilo K2 — sensor trust ledger (+ K1.1 relayed-edge resolution) — design

> Design: Fable, 2026-07-16 (window item 5). Opus executes (model-advisor
> split: frontier designs, wiring is not frontier). SSOT ladder:
> `.claude/plans/kilo_lab_instrument.md`. K2 is the **calibration ledger applied
> to hardware** — the same epistemics the fleet runs on its own claims, turned
> on the physical sensors. K1.1 is the small companion that resolves the
> relayed edges K1 deliberately parked.
>
> **Design invariant carried from K0/K1:** compute-on-read over the existing
> `readings`/`edges` store (no new write path, no new table for v1), re-derived
> against the CURRENT registry at read time, tri-state or it lies, and no
> verdict without a measured envelope. `kilo trust` is to sensors what
> `kilo matrix` is to links.

---

## K2 — the problem shape

`readings` already stores `(ts, transport, node_key, kilo_id, metric, value)`
for every node in a bounded collect window. The registry already carries a
per-node **`location`** field (`KiloNode.location`, registry.py:64) — operator-
DECLARED, never RF-inferred (RF proximity ≠ physical co-location; two nodes can
be one hop apart and in different rooms).

**Insight:** sensors that share a `location` and measure the same *environmental
field quantity* (temperature, humidity, pressure, CO₂, IAQ, PM) SHOULD read
approximately equal at the same time. A sensor that persistently disagrees with
its co-located peers is drifting or broken. Its **trust** is its agreement
history against the cluster consensus — exactly the held/broke record the
calibration ledger keeps for a VERIFIED claim, re-derived from ground truth.

**The truth source is the cluster, not a golden sensor.** No sensor is the
reference; consensus is the robust median of the co-located cluster. This is the
same move K1 makes (drift vs the edge's OWN baseline, never a Friis prediction)
— you measure against the population, not an absolute model.

## K2 — the metric split (the load-bearing vocabulary)

Trust cross-checks apply ONLY to **field metrics** — quantities that a co-located
cluster genuinely shares. A closed set, pinned by test (closed-enum discipline),
a subset of `store.UNITS`:

```python
# kilo/trust.py
FIELD_METRICS = frozenset({
    "temperature", "humidity", "pressure",
    "gas_resistance", "co2", "iaq",
    "pm25_standard", "pm10_standard",
})
```

Everything else in `UNITS` is a **device metric** — intrinsic to one board, NOT
shared by co-location: `voltage`, `battery_level` (each board its own cell),
`snr`/`rssi`/`channel_utilization`/`air_util_tx` (per-LINK — that's K1's job),
`heap_free_bytes`/`wifi_rssi_dbm`/`vsz_kb`/… (claw/scout device internals).
**Cross-checking a device metric would fabricate false "breaks"** (two co-located
nodes legitimately have different battery levels) — honest_failure_modes #1 in a
new skin. A test pins `FIELD_METRICS ⊂ UNITS` and that no device metric leaks in.

## K2 — quorum is everything (the honest core)

The verdict a sensor gets depends entirely on **how many co-located peers report
the same field metric in the time bucket** — this is where a naive design lies:

| Cluster size (reporting this metric, this bucket) | What can be said |
|---|---|
| **n = 1** | No cross-check possible → **UNKNOWN** (⚪). A lone sensor is never "trusted" and never "broken" — unobservable ≠ healthy (K0 invariant #3). |
| **n = 2** and they AGREE (within band) | Both **CONCUR** — weak evidence (a shared systematic error would also concur), but a real held. |
| **n = 2** and they DISAGREE | **INDETERMINATE** (⚪-split) — you CANNOT attribute the fault; blaming the one further from *nothing* is fabricating a truth. Record "cluster split, unattributed"; mark BOTH unattributed for this bucket. **Never blame one of two.** |
| **n ≥ 3** | Robust median is the consensus; a single outlier is OUT-VOTED → its residual is attributable. This is the only regime where "sensor X broke" is an earned claim. |

This table IS the design. Everything downstream (the ledger, the CLI, the page)
must preserve it: an attribution of "broken" requires n ≥ 3; a 2-sensor split is
a cluster-level anomaly, not a per-sensor verdict.

## K2 — the agreement band (per metric, MAD-widened, floored)

Mirror K1's `classify_drift` band construction exactly (median ± MAD, floored),
but the floor is **per field metric** (sensor-physics tolerance), not one dB:

```python
# residual = reading − cluster_median(bucket); band per metric:
FIELD_BANDS = {                    # absolute floor, in the metric's own unit
    "temperature": 1.0,           # °C — co-located air within ~1°C
    "humidity": 5.0,              # %RH — hygrometers spread more
    "pressure": 1.5,              # hPa — barometers tight; altitude-sensitive
    "gas_resistance": None,       # Ω — device-specific scaling; ratio not abs → SUSPECT-only (see below)
    "co2": 75.0,                  # ppm
    "iaq": 25.0,                  # index
    "pm25_standard": 5.0,         # µg/m³
    "pm10_standard": 8.0,         # µg/m³
}
# band = max(K_SIGMA * MAD(cluster this bucket), FIELD_BANDS[metric])
```

Floors are BELIEVED starting points, not measured — the design rule (like K1's
2 dB) is: pick a physics-plausible floor, then **let the fleet's own MAD widen
it** where the environment is genuinely noisy, and tune the floor from observed
false-page rate once cron-wired. `gas_resistance` has no shared absolute scale
across sensor models (BME680 vs others) → it is **bias-only** (SUSPECT on a
persistent offset, never BROKEN) — a metric whose absolute value isn't
commensurable can still show *relative* drift but can't be called broken.

## K2 — the verdict (pure function, mirrors classify_drift)

```python
def classify_trust(residuals: List[float], band: float,
                   quorum: int) -> dict:
    """Per-(sensor, metric) trust over a window of bucket residuals.

    residuals — this sensor's (reading − cluster_median) per time bucket,
        only for buckets where the cluster met quorum ≥ 3 (attributable).
    Returns state ∈ {TRUSTED, SUSPECT, BROKEN, UNKNOWN} + the envelope
    (held/broke counts, held_rate, median_residual = the bias estimate)."""
```

Tri-state (⚪🟢🟡🔴), quorum-gated, silence-aware:

- **UNKNOWN (⚪)** — < `TRUST_MIN_COMPARISONS` attributable buckets (never
  reached quorum, or the sensor was mostly silent). Held at this until earned.
- **TRUSTED (🟢)** — `held_rate ≥ TRUST_OK_RATE` over enough comparisons AND
  `|median_residual| ≤ band` (no persistent bias).
- **SUSPECT (🟡)** — a persistent BIAS (`|median_residual| > band` but stable —
  reads consistently ~2°C high → needs calibration, not replacement) OR a
  falling held_rate. Bias vs break is the distinction that makes this useful:
  a biased sensor is still *informative* once offset-corrected.
- **BROKEN (🔴)** — `held_rate` below `TRUST_BROKEN_RATE` over enough
  comparisons: erratic, way-off, out-voted repeatedly by a quorum. The earned
  "this sensor is lying" claim — only reachable at n ≥ 3.

Constants named in ONE place (K1's convention), tuned from evidence, not asserted:
`TRUST_MIN_COMPARISONS`, `TRUST_OK_RATE`, `TRUST_BROKEN_RATE`,
`TRUST_QUORUM = 3`, `TRUST_BUCKET_S` (time-alignment bucket, ~300 s — readings
aren't synchronous; a bucket groups near-simultaneous readings so the median is
of contemporaneous values, never a stale one vs a fresh one).

## K2 — silence is not a break (the axis split)

**Presence** (does the sensor report at its cadence) is K0's `status` axis and
stays there. **Trust** (does it AGREE when present) is K2's axis. A sensor that
goes silent HOLDS its last trust state — it is not demoted to BROKEN for absence
(unobservable ≠ broke). `kilo trust` reports trust only over buckets the sensor
was PRESENT for; a silent sensor shows its last-known trust + a `stale_s` age,
never a fresh verdict. (The #74/#80 lesson: absence of evidence is not evidence
of a break.)

## K2 — surface: `kilo trust` (compute-on-read, no new table)

Parallel to `kilo matrix`. `build_trust(conn, nodes, window_s, now)`:

1. Group `readings` in the window by (`location` cluster from the registry at
   READ time, field metric, time bucket).
2. Per (cluster, metric, bucket) with ≥ `TRUST_QUORUM` reporting sensors:
   consensus = median; each sensor's residual = value − consensus.
3. Per (sensor, metric): collect attributable residuals → `classify_trust`.
4. Emit cells: sensor label (registry join), metric, state, held/broke,
   held_rate, median_residual (bias), n_attributable, n_silent_buckets, plus
   cluster-level `n2_split_buckets` (the indeterminate 2-sensor disagreements —
   surfaced as a CLUSTER anomaly, never a per-sensor blame).
5. **Baseline-horizon witness** (copy K1's `baseline_horizon` idea): if the
   store is younger than the window, or no cluster ever met quorum (e.g. no
   `location` declared, or every location has < 3 sensors), say so LOUDLY —
   `quorum_never_met: true, why: "no declared location has ≥3 co-located
   sensors reporting a field metric"`. Otherwise the operator hunts per-sensor
   UNKNOWNs that are UNKNOWN by construction.

CLI: `PYTHONPATH=src python3 -m kilo trust [--window-hours N] [--location X]`.
Exit codes cron_verdict-wireable, SAME contract as `matrix`: **exit 1 = a sensor
BROKEN (real page); exit 2 = could not verify (crash/registry-unreadable — never
counted as pass, the 2026-07-05 error-boundary discipline); exit 0 = all
TRUSTED/UNKNOWN/SUSPECT** (SUSPECT is a watch, not a page — a biased-but-
informative sensor shouldn't page at 3am; it surfaces in the view + a weekly
digest, tunable later). Alerting/mini routing is a DELIBERATELY separate later
step (evidence before alerting — the K1 rule); the CLI view + cron_verdict
exit-code ship first.

## K2 — invariants check (walk them, like every rung)

1. **Airtime is the subject** ✓ — trust rides the existing readings store; zero
   new TX.
2. **Identity not addresses** ✓ — clusters key on registry `location` +
   radio-anchor sensors; DHCP can't move a sensor out of its trust cluster.
3. **Tri-state or it lies** ✓ — UNKNOWN for sub-quorum/silent; the n=2 split is
   its own indeterminate state, never a false attribution.
4. **No verdict without an envelope** ✓ — every BROKEN carries held/broke counts
   + median residual, re-derivable from the readings; nothing hand-asserted.
5. **New resident daemons arrive with template + probe + seed** — N/A, K2 is a
   bounded read like `matrix`, no daemon.

## K2 — non-goals (v1)

- No absolute calibration / golden-reference sensor (consensus is the truth).
- No new SQLite table — compute-on-read over 30 d readings gives a 30 d trust
  horizon; a rolled-up daily `trust_summary` table is **K2.1** only if a longer
  horizon than retention is ever wanted (and it would be a derived cache with a
  regen check, never a hand-written verdict log).
- No cross-LOCATION comparison (different rooms legitimately differ).
- No device-metric trust (battery/voltage/heap are intrinsic — see the split).
- No mini/ntfy routing yet (evidence first; cron_verdict exit code is the hook).

---

## K1.1 — relayed-edge resolution (the small companion)

K1 stores every edge but the matrix is `direct_only=True`: relayed edges
(`hops_away > 0`) are counted in `totals["edges_relayed"]` but not placed, because
the RF hop is (relayer → receiver) and the relayer is known only by
`relay_partial` — the LAST BYTE of its id (`parse_edge` already captures it as
`relay`, edges.py:104, bounded 1–255; 0 = "not relayed").

**K1.1 = resolve `relay_partial` (1 byte) against the known-id set → name the
relayer, tri-state:**

```python
def resolve_relay(relay_partial: int, known_ids: List[str]) -> dict:
    """1-byte relayer hint → {state, relayer, candidates}.

    RESOLVED  — exactly one known id ends in relay_partial → that relayer.
    AMBIGUOUS — >1 known id shares that last byte → list candidates, name none
                (1 byte collides ~1/256; a 20-node lab WILL collide). Never guess.
    UNKNOWN   — no known id ends in relay_partial → an unregistered relayer
                (discovery signal: something is relaying that isn't in the
                registry — worth an operator note, exactly like `kilo discover`).
    """
```

- **Attribution rule (mirrors K2's quorum honesty):** an AMBIGUOUS relay is
  never collapsed to one candidate for topology purposes — a 1-byte match is not
  an identification. The relayed-topology view attributes a relayed edge to a
  relayer ONLY when RESOLVED; AMBIGUOUS/UNKNOWN relayed edges are shown as
  "(relayed via ?)" with the candidate list, never a fabricated 2-hop path.
- **Surface:** `kilo matrix --relayed` adds relayed edges attributed to their
  RESOLVED relayer (a receiver ← relayer ← origin 2-hop view), plus an
  `unresolved_relays` witness (count of ambiguous+unknown) so the operator sees
  how much of the relayed traffic couldn't be placed — never a view that looks
  complete while silently dropping the ambiguous majority.
- **⚠️ Live-firmware caveat (from the K1 build note):** current firmware was NOT
  emitting `relay_node` at K1 time — `parse_edge` reads it defensively but the
  field may be absent/zero on the live fleet. K1.1's FIRST step is a data check:
  `SELECT COUNT(*) FROM edges WHERE relay_partial IS NOT NULL` over a live
  window. **If it's ~0, K1.1 is a no-op on this firmware** — ship the resolver +
  tests (they're correct and future-proof) but the `--relayed` view is
  empty-by-construction, and say so with a horizon witness (same pattern as K1's
  `baseline_horizon`) rather than presenting a blank view as "no relaying
  happens." Do NOT invest in K1.1 UI polish until the data check says relay
  bytes are actually arriving.

## K1.1 — non-goals

- No multi-hop (>2) path reconstruction (the wire carries one relay byte, not a
  path). No RSSI-triangulation of an unknown relayer (out of scope; a guess).

---

## Build order (Opus, evidence-gated)

1. **K1.1 data check FIRST** — is `relay_partial` actually populated on the live
   fleet? One SELECT. Gates whether K1.1 UI is worth building now vs shipping the
   resolver+tests dormant.
2. **K1.1 resolver** — `kilo/edges.py` `resolve_relay()` + `--relayed` matrix
   view + `unresolved_relays` witness + tests (RESOLVED/AMBIGUOUS/UNKNOWN,
   collision case). Small, self-contained.
3. **K2 `kilo/trust.py`** — `FIELD_METRICS`/`FIELD_BANDS`/constants (pinned ⊂
   UNITS), `classify_trust` (pure, tri-state, quorum-gated), `build_trust`
   (compute-on-read, read-time registry join, baseline/quorum horizon witness),
   `kilo trust` CLI (exit 0/1/2), tests: the n=1/n=2-split/n≥3 quorum table is
   the test spine (each row a case), silence-holds-last, bias-vs-break,
   device-metric-excluded, quorum-never-met horizon.
4. **Cron wiring** (mechanical, AFTER ≥ a few days of readings so clusters can
   reach quorum) — `kilo trust` hourly on a box with ≥3 co-located field
   sensors, cron_verdict-wired (`kilo_trust`), same regime as `kilo_matrix`.
   ⚠️ Vacuous-OK guard (the K1 lesson): a box with NO ≥3-sensor cluster emits a
   permanently-empty trust view — that must read UNKNOWN/horizon-witness, NOT a
   green OK. Only wire the cron where a real cluster exists.
5. Operator: declare `location` on the co-located lab nodes in the registry (the
   K2 cluster key) — the one human input the whole rung rests on.

## Payoff

The lab's sensors get the same honesty the fleet gives its own claims: a sensor
that drifts is caught by its peers and named (when a quorum can name it),
held/broke is a re-derivable number not a vibe, and the two ways to lie — blaming
one of two, or calling a silent sensor broken — are designed out. Combined with
K1 (links) and K0 (presence), Kilo now watches all three failure axes of a mesh
sensor: is it THERE, does its LINK hold, and does its DATA agree.

# CI-1 / CI-2 — the suite's cost grows through the run; the cap is the bandage (2026-11-18)

Operator 11-18: "a solution not a bandage on a cancer". Measured this session, not asserted.

## Measured (11-18)

- **Not queueing.** 24 push runs: every job started ≤ 0.1 min after creation; run created ~2 min
  after the commit. (The 11-18 handoff's "queued ~60 min GitHub-side" for `7da987c9` is refuted
  by the API: created 2 min after commit, 34 min wall.)
- **One serial job is the wall.** `Test Suite (3.11)`: median 33.5 min, max 44.5 min, cap
  `timeout-minutes: 45` (raised 10 → 15 → 30 → 45 after three false reds, each "slow, not broken").
  Trend 7.5 min (09-01) → 13 (09-10) → 28 (11-06) → 35.4 (11-18, 20,127 tests).
- **Not slow tests.** `bed21b67` log: 37 tests ≥ 5 s = 5.7 min; 19,952 tests < 1 s = 25.6 min.
- **THE FINDING — per-test cost grows ~9× through ONE run** (fast-test median per decile, same
  log): 13.5 → 21.6 → 34.1 → 40.3 → 63.8 → 60.7 → 67.4 → 75.5 → 83.7 → 119.6 ms. Same files
  ALONE on the Pi: ~15 ms/test (`test_rf` + `test_watchdog_probes`: 961 in 16.7 s; setup 8.7 ms
  > call 6.4 ms). In CI `test_watchdog_probes` (at 94 % of the run) costs 115.9 ms/test.
  At the first decile's rate, 20 k tests ≈ 5 min, not 35.
- **Reds are mostly gates working.** 300 runs 10-27 → 11-18: 17 red (5.7 %). 8 = real drift the
  gates caught (crypto pin ×3, mypy ×3, README stats, roadmap queue), 4 = real defects, 5 = one
  headless-browser hang (all 11-10), 4 = no FAILED line (UNKNOWN; 2 died in 2–4 min). Each red
  costs a ~35 min cycle — the cost is the cycle, which is the finding above.

## What accumulates (leak probe, 11-18)

`-p leakprobe` (scratch plugin) every 500 tests over the first ~5,500–8,000, on VolcanoAI:

| Signal | test 500 → end | Verdict |
|---|---|---|
| GC pause | 0.2–5.6 ms/test, ≤ 6 gen-2 runs per window | **NOT the cause** (hypothesis refuted) |
| logging handlers | root 3 → 3, all 7 → 9 | not the cause |
| CI output volume per decile | 2–80 kB, flat (from the `bed21b67` log) | not the cause |
| gc-tracked objects | 939 k → 1,371 k (~54 kept per test) | grows; cost unproven |
| RSS | 343 → 511 MB | grows |
| live threads | 4 until ~5,500, then 9–11: `eventbus` ×5, `statusbar-weather` ×2 | **a leak — PRODUCT threads left running by tests** |

**Position vs mix — SEPARATED (11-18, VERIFIED, n=1 per file, same machine, `-p filecost`):**
the same files ALONE vs AFTER a 300-file prefix (11,025 tests):

| File | alone ms/test | after prefix | × |
|---|---|---|---|
| `test_rf.py` (115, all fast) | 10.1 | 55.3 | **5.5** |
| `test_watchdog_probes.py` (846) | 23.6 | 75.2 | **3.2** |
| `test_regression_guards.py` (87, slow-test dominated) | 749 | 815 | 1.09 |

A test's cost depends on how many tests ran BEFORE it in the process: an accumulation, not the
mix. Not GC, not handlers, not output (above). **Root cause still UNKNOWN** — the next step is
a profile DIFF: `cProfile` one fast file alone vs after the prefix; the function whose cumulative
time grows names the accumulator (candidates: an autouse fixture whose work scales with
`sys.modules` or a growing registry; CPU from leaked polling threads). Side find: `test_fleet_down` whole-fleet case takes > 30 s on the Pi (10.4 s on CI) —
sandboxed (fake ssh + marker), slow not dangerous.

## Why raising the cap again is the bandage

The cap kills a hang; the per-test `--timeout=30` already names the hung test. Every raise sat
"clear of normal runtime" and normal runtime caught up (10 d, then 8 weeks). The runtime grows
SUPER-linearly because each test pays for every test before it. Sharding first would also be a
bandage: fewer tests per process hides the accumulation, and the leak — if it is threads or
handlers left running by product code — is a PRODUCT defect the suite is reporting.

## Order

1. **CI-1** — find the accumulator, fix it at the SOURCE (product code or fixture), and gate it:
   a session-end witness of fast-test cost, last decile ÷ first decile, that fails over a measured
   bound. Acceptance: the ratio ≤ 2 on CI for this head, and the job's wall time falls.
2. **CI-2** — then the growth structure: shard the suite across parallel jobs by recorded
   durations, an aggregator job keeps the required context `Test Suite (3.11)`, each shard capped
   to its own size. Makes the missing 3.13 leg (9 of 10 boxes run it) affordable.

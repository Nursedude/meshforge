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
mix. Not GC, not handlers, not output (above).

## ROOT CAUSE — FOUND 11-18 (cProfile diff, `-p proftarget`, VERIFIED n=1, tree hash launch = end)

`test_rf.py` profiled ONLY during its own tests: alone 12.8 ms/test, after the 300-file prefix
110.5 ms/test. Of the +104 ms, **+98.6 ms is `tmp_path_factory.mktemp` → `make_numbered_dir` →
`find_prefixed`/`extract_suffixes`** — called +20,064 times PER TEST. pytest's `mktemp` finds the
next number by scanning EVERY entry of the shared basetemp root, and two AUTOUSE fixtures call it
for EVERY test:

- `_isolate_broker_profile_store` → `mktemp("broker_store")` (+49.2 ms)
- `_isolate_node_cache_files` → `mktemp("node_cache_isolation")` (+49.2 ms)

(both in `tests/conftest.py` @ `b831a09b`; cited by NAME — line numbers move with every edit)

The last run's basetemp held 27,780 entries: 10,147 `broker_store*` + 10,147
`node_cache_isolation*`. Each test adds 2 and scans all before it → the suite is **O(N²)** in its
own size. That is the "9×", and why each cap raise was overtaken. Our doing: the same file already
knows the lesson — the delivery-counters fixture (09-01) and `_absent_posture_path` (09-10) record
moving two OTHER autouse fixtures off a per-test `mktemp` for exactly this cost; these two were
never moved.

## The cure (CI-1) — built 11-18

1. Both fixtures request ONE per-test dir (`_per_test_isolation_dir`) from ONE session root
   (`_isolation_root`, `mktemp` once); children named by an O(1) per-root counter, sharded 1,000 per
   parent, a taken name skipped. Isolation unchanged (each test still gets its own empty dir).
2. Gate `tests/test_conftest_tmp_cost.py`: follows the FIXTURE GRAPH across every `conftest.py`;
   fails on any autouse function-scoped fixture that pays the basetemp scan (`tmp_path`, `tmpdir`,
   `tmp_path_factory`, directly or via a dependency). Planted shapes pinned.
3. Acceptance, measured not asserted: the position experiment re-run, then CI `Test Suite` wall
   time for that head vs 33.5 min median today.

**Acceptance leg 1 — VERIFIED 11-18 (n=1, conftest hash launch = end):** after the 300-file prefix,
`test_rf` 55.3 → **2.8 ms/test** (0.85× alone; was 5.5×), `test_watchdog_probes` 75.2 → **10.1**
(1.28×; was 3.2×); the 11 k-test prefix run 23:03 → **13:15**. (10,948 vs 10,963 passed: the new test
file shifted the alphabetical 300-file window by one file — not a loss; 0 failed.)

**Acceptance leg 2 — CI, VERIFIED 11-18 (`dcf6d571`, run 44402554977, success, n=1):**

| CI `Test Suite (3.11)` | before (`bed21b67`) | after (`dcf6d571`) |
|---|---|---|
| job wall | 33.5 median / 44.5 max | **26.7 min** |
| test span (first → last test line) | 35.4 | **17.7** |
| fast-test median, LAST decile | 119.6 ms | **13.2 ms** (the climb is gone) |
| `test_rf` / `test_watchdog_probes` late in the run | 71.2 / 115.9 ms | 3.6 / 13.2 |

Full local suite on VolcanoAI: 43 min (11-18 01:21) → 26:55 (21,554 passed).

**What the remaining 26.7 min is (measured from the same log):** 1.0 install + collection; 17.7
tests (37 tests ≥ 5 s = ~5.7 min; `test_honest_status_shell` boxes harness alone 51 s); **7.9 min
AFTER the last test** — `-rA` printing captured output for every passing test (36,517 lines;
largest single gap 30 s, so volume, not one stall). → `ci.yml` `-rA` → `-ra` (operator go 11-18);
`pytest_verdict.sh` reads only the summary line and `^FAILED`/`^ERROR` lines, which `-ra` keeps.
Expected job ≈ 19 min — **measured: 18 min 44 s** (`e664f374`, CI success; n=1) — 44 % under the old
33.5 min median, 26 min of headroom under the 45 min cap.

**Disposition:** CI-1 DONE (left the roadmap, provenance line). CI-2 shrunk to "shard only if
`Test Suite` > 30 min again". THR-1 opened for the leaked threads — GitHub's cleanup step reported
`Terminate orphan process: pid (80671) (python)` at the end of the job, consistent with them
(BELIEVED, not traced).

Side find (separate from the cure): `test_fleet_down` whole-fleet case takes > 30 s on the Pi
(10.4 s on CI) — sandboxed (fake ssh + marker), slow not dangerous.

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

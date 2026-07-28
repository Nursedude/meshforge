# pytest exits 0 while reporting a failure — a shutdown race (2026-07-28)

**Status**: measured and bounded; root mechanism BELIEVED, not pinned.
**Consequence**: none escaped — `honest_status.sh` caught it both times via a
second, independent signal. The gate has since been hardened so a lost exit
code cannot read green even without FAILED lines (`test_honest_status_suite_leg.sh`).

## The observation

Running the full MeshForge suite with exactly one failing test, the process
exit code flaps between 1 and 0 across runs with **byte-identical output**:

```
attempt 1: rc=1  | 1 failed, 9839 passed, 1 skipped in 230.67s
attempt 2: rc=0  | 1 failed, 9839 passed, 1 skipped in 234.42s
```

Roughly half of un-instrumented runs. Reproduced independently of any shell
script by invoking the pytest command directly.

## Where the status is lost

pytest's own `pytest_sessionfinish` hook reports the CORRECT status on a run
whose process then exits 0:

```
attempt 1: rc=0 | hook said: sessionfinish exitstatus=<ExitCode.TESTS_FAILED: 1> testsfailed=1
```

So this is **not** pytest bookkeeping and **not** the shell reading `$?`
wrong. pytest computed 1; the process reported 0. The status is lost during
CPython interpreter shutdown, after the summary is printed.

## What shutdown is holding

At session end the interpreter has ~66 live threads, including **~25
non-daemon `statusbar-weather_0` workers** and several non-daemon
`eventbus_*` workers — leaked `ThreadPoolExecutor`s that tests construct and
never shut down:

- `src/launcher_tui/status_bar.py:106` — one `max_workers=1` executor per
  `StatusBar` instance
- `src/utils/event_bus.py:130` — `max_workers=4`

Since Python 3.9, `ThreadPoolExecutor` worker threads are **non-daemon** and
are joined by an interpreter-shutdown hook. That join is the window.

## It is a Heisenbug — instrumentation hides it

| instrumentation | runs | result |
|---|---|---|
| none | 4 | 2× rc=0 (flapped) |
| full probe (thread walk + `ps` at atexit) | 5 | rc=1 ×5 — never flapped |
| one-line `pytest_sessionfinish` hook only | 1 | rc=0 caught immediately |

Adding work at shutdown suppresses it. That is itself evidence of a timing
race rather than a deterministic bug, and it is why the light hook was the
only instrument that could observe it.

## Hypotheses tested and REFUTED

Each was killed by measurement, not argument:

- **`honest_status.sh` mis-capturing `$?`** — invoking pytest directly flaps
  identically; an instrumented copy prints `DEBUG_RC=[1]`.
- **Fixed tmp-file collision** (`/tmp/.hs_pytest` etc., two overlapping runs
  interleaving) — real, and fixed, but the anomaly outlived the fix.
- **Nested `honest_status` runs inside the suite** — A/B'd with the nested
  harness present vs. absent: both `exit 1`.
- **Exit-status manipulation in conftest / addopts** — no `pytest_sessionfinish`
  or `exitstatus` hook exists in the tree.
- **A stray `os._exit(0)`** — none in the tree; the `os._exit(2)` sites would
  surface as 2.
- **The leaked executors alone are sufficient** — 25 runs of the failing test
  plus the two leakiest modules (`test_status_bar.py`, `test_event_bus.py`):
  `rc=1 ×25`. Necessary at most, not sufficient; it needs the full suite's
  thread population.

## Not fixed, deliberately

Shutting the executors down in tests would be a broad test-hygiene change
whose payoff is unproven — the subset result shows the leak is not sufficient
to cause the flap, so a quiet suite afterwards would not establish causation.
A Heisenbug that has gone quiet is not a Heisenbug that is fixed.

The durable cure is on the consuming side and is already in place: no gate
may treat a process exit code as a sole signal of success. See the suite leg
of `scripts/honest_status.sh` — PASS now requires the exit code, the absence
of FAILED/ERROR/INTERNALERROR lines, AND an affirmatively passing summary
line, with any disagreement or absent summary resolving to not-PASS.

## If you pick this up

Reproduce with the README stat sentinel drifted (one deliberate failure), the
full suite, and NO instrumentation; expect ~50%. Attach at most a one-line
hook. `strace -f -e trace=exit_group` on the pytest process would name the
thread and status definitively and is the obvious next instrument, at roughly
3× runtime on Pi-class hardware.

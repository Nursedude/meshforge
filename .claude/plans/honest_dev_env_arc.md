# Honest-by-construction dev env — arc + step 3 spec

> Born 2026-06-15 from the operator's concern: *"AI is convincing me things are
> good and that's not true… I need a more honest reliable dev env."* Goal: the
> operator never has to trust an AI summary — every claim is falsifiable from
> external ground truth, and the KNOWN failure classes fail the build, not the
> operator's day.

## Ordering principle (why this order)
Build the checks that catch my next mistake BEFORE making more mistakes — tools
before tasks. Passive knowledge in docs (even auto-loaded) does NOT prevent
misalignment; only enforcement at the moment of action does. Bookend: gate at
session START (ground truth) + at PUSH (can't ship mis-wired work). This extends
the existing Issue #29 spine — do not build a parallel system.

## Done (steps 1–2, all committed/pushed/CI-verified/deployed)
- **Gateway honesty fixes** (the trigger work): `probe_synth_soak_degraded`
  (the hourly synth soak was unwatched); `confirmation_rate` made honest (#74
  residual — was cross-population `confirmed/sent`=1.64=">164%"; now
  `confirmed/(confirmed+failures)` bounded [0,1] + `unconfirmable_sent` surfaces
  the mesh blind spot). `compute_confirmation_view` in `delivery_counters.py`.
- **`scripts/honest_status.sh`** — operator-owned external verifier. Checks CI
  conclusion for HEAD, per-box SHA convergence, full suite (real exit+count),
  lint, live `confirmation_rate<=1.0`, watchdog signals. `exit 0`=fully verified;
  UNKNOWN (unreachable/CI-pending/unparseable) is NEVER counted as PASS;
  watchdog wedge=FAIL, degraded=WARN (loud, doesn't block code-verified);
  `--quick` skips suite, `--strict` fails on warnings, `HONEST_WD_PATH` is a
  fixture seam. It caught two false-greens in ITSELF during the build (SHA
  abbrev-length false-RED; corrupt watchdog.json reading as clean) — fixed.
- **#29 spine turned back ON**: `core.hooksPath` was pointing at the empty
  `.git/hooks` → ALL `.githooks/` hooks were dormant (pre-commit + pre-push ran
  on nothing). Fixed (local config, dev box only). pre-push strengthened: lint +
  regression guards (wiring backstop) + WARN-only parent-CI advisory.

## Step 3 — encode the blind-spot classes as enforced tests (CORE LANDED 2026-06-15)
Convert the 1000-hr knowledge from passive docs into build-fails. Shipped
`tests/test_honesty_invariants.py` (11 tests). The two genuinely-NEW gaps are
now build-fails; the rest of §3b was already covered (confirmed, not rebuilt).

**Landed — each is a pure checker with a RED proof (synthetic violation caught)
AND a GREEN proof (repo holds), and each GREEN invariant was live-falsified
against the real source (un-wire the synth probe / forget a handler → the
test goes red with a precise actionable message → revert → green):**
- **§3a bounded display** — `compute_confirmation_view` + `DeliveryTracker`
  `confirmation_rate_pct` stay in [0,1]/[0,100] (or None) under the exact #74
  mesh-heavy shape; `unconfirmable_sent` stays surfaced. RED = the old
  `confirmed/sent` formula (=1.64) trips the same bound gate. (No name-based
  `*rate*/*ratio*/*pct*` scan — bitrate/heart_rate/compression_ratio are
  legitimately unbounded; cross-population is semantic, not syntactic.)
- **§3b wiring (the NEW gap)** — `reachable_signal_classes()` AST closure proves
  every `SIGNAL_CLASSES` member is emitted by a probe actually CALLED in
  `watchdog_runner.run_all_probes` (transitive, so helper-delegation refactors
  don't false-fail). This is the synth-soak gap that needed manual wiring.
- **§3c user-access (MF018)** — `discover_handler_classes()` finds every
  on-disk concrete handler; asserts each is returned by `get_all_handlers()`.
  Catches a handler file forgotten in the hand-maintained import list (the
  NomadNet-inaccessible / dead-UI class). Complements
  `test_all_handlers_protocol.py` (which only tests what's REGISTERED).

**Already covered (confirmed existing, NOT rebuilt):** §3b DBSpec/MF013 →
`tests/test_db_inventory.py` ("DBs in src/ missing from INVENTORY") + lint
MF013; §3b seed-routing → `TestSeedCoversSignalClasses`.

**Deferred (fuzzy — would risk a vacuous/false-firing guard, the exact defect
this arc kills; next increment):** §3b every systemd unit has a
`templates/systemd/` entry + a new long-lived service has a deploy-restart hook
(the #79 gap); §3c "service active ≠ doing the job" (hardest — semantic).

### Original spec (retained for the deferred items)

**3a — false-green invariants:** no displayed metric (field named `*rate*`,
`*ratio*`, `*pct*`) exceeds its logical max (1.0 / 100) across the `/api/*`
snapshot builders; no cross-population `confirmed/sent`-style quotient survives
in any snapshot; (stretch) every `except`/`or []`/`.get(default)` on a read path
that feeds a status field leaves a probe-visible witness.

**3b — wiring invariants:** every `SIGNAL_CLASSES` member is routed in BOTH role
seeds (exists: `TestSeedCoversSignalClasses`) AND wired in
`watchdog_runner.run_all_probes` (NEW — the synth probe needed manual wiring);
every new SQLite DB has a `DBSpec` (MF013, exists); every new `systemd` unit has
a `templates/systemd/` entry; a new long-lived service has a deploy-restart hook
(update.sh / fleet_sync) — the #79 "nothing restarted the daemon after pull" gap.

**3c — user-access invariants (the NomadNet-inaccessible class, MF018):** a TUI
entry point / handler that exists stays REGISTERED and reachable; "service
active ≠ doing the job" — guard that an `active` service maps to a user-facing
function. Hardest to encode; expect to get the first cut wrong.

## Step 3 discipline (load-bearing — do NOT skip)
**Red-test-first for every invariant: prove the new check FAILS on a
deliberately-seeded violation before trusting it.** A guard that can't be shown
to fail is a vacuous false guard — worse than none, and the exact defect this
arc exists to kill. Then 4 (fix known reds), then 5 (sweep).

## Step 4 — known AREDN sysinfo-URL red (LANDED 2026-06-15)
AREDN 4.x retired `:8080/cgi-bin/sysinfo.json` → it now answers **HTTP 307 →
`:8080/a/sysinfo`** (verified live against the VOLCANO-QTH hAP). **Correction to
this doc's own step-3 note:** the *collector* (`_map_collector_aredn.py`) + the
`AREDNClient` were already moved to `/a/sysinfo` in `243d8a9` (2026-04-24) — moc5
yields fine (`source_diagnostics.aredn` = attempted 1 / yielded 1 / "ok"). The
REAL surviving reds were OPERATOR-FACING: the in-app knowledge base
(`knowledge_content_extended.py` ×2) and a TUI troubleshooting hint
(`handlers/aredn.py`) still told the operator to curl the dead path (a false
instruction — MF018). All three updated to `/a/sysinfo` (`?hosts=1` for the
node/host list). Guarded red-test-first by `TestNoDeadArednSysinfoUrl` in
`tests/test_honesty_invariants.py` (the dead path must never reappear in `src/`).
WATCH (not changed — behavior change on a live collector, out of step-4 scope):
collector `urlopen(timeout=3)` vs observed ~3.3 s sysinfo latency on moc5 — a
candidate root for any intermittent `aredn_source_dark` flap; raise separately.

## Resume in a clean session
1. `bash scripts/honest_status.sh` — establish ground truth (don't trust this doc).
2. `git log --oneline 6bc4a08..HEAD` — read the arc's commits.
3. Build step 3 above, red-test-first.

Pending: synth-soak Monitor was armed on moc (won't survive session end — mini
ntfy is the durable backstop). Step 4 AREDN reds fixed (above); moc5 yields ok.
Next: step 5 (sweep) + the deferred §3b/§3c items. CI of the last push lands
~3 min after.

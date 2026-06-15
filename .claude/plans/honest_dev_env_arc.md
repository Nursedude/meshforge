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

**Deferred items — LANDED 2026-06-15** (a fresh session; see "Deferred §3b/§3c
— LANDED" below for the build record + the real #79 gap it surfaced). §3b-i
(template provenance), §3b-ii (deploy-restart hook), §3c (daemon→output-probe
coverage) are now build-fails too.

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

## Step 4b — synth_soak_degraded false-fire (live-caught 2026-06-15, FIXED)
The step-1 `synth_soak_degraded` probe FIRED on moc 20:08Z on a HEALTHY run
(pass_envelope=true, 600/600). Root cause: `lab_synth_soak_fire.sh` redirected
the synth run straight into the published file (`>"$out"`), truncating it at
run START — but the ~67 s run writes its JSON only at the end, so the newest
`synth-*.json` was unparseable for the whole run and outlasted the probe's
~60 s torn-write debounce → healthy run trips degraded (+ hourly mini page). A
false-RED that erodes trust in the honesty layer (honest_failure_modes #8).
Fix (`21f278f`): write to a hidden `.synth-<stamp>.json.partial` (outside the
`synth-*.json` glob), publish via atomic `mv` ONLY when the temp is complete,
parseable JSON — NOT gated on exit code (a FAIL envelope must publish for the
probe's ENVELOPE leg; only a crash with no JSON leaves no file → SILENCE leg).
Guarded red-test-first by `TestSynthSoakAtomicWrite` (predicate strips
comment-only lines so docs mentioning the dead pattern can't self-trip — a
checker false-positive found+fixed while writing it). **LIVE-VERIFIED on moc**:
direct fire showed `in_flight_partial=1` + `glob_sees_partial=0` +
`synth_degraded=0` throughout, then atomic publish — exactly where the un-fixed
script false-fired at 20:08. (persistent_issues.md is at the 40k MF012 cap, so
this lives here, not there.)

## Step 5 — broad sweep for OTHER instances of the blind-spot classes (2026-06-15)
Now that the lenses exist (steps 3–4), swept the codebase for further instances
via four parallel read-only Explore agents (delegated discovery to keep the
session's context clean — a tail-of-session sweep with cluttered context is the
false-confidence failure this arc kills). Result: **broadly clean.**
- **3a false-green displays:** NO new instances. All `*_pct/_rate/_ratio`
  operator fields are within-population or clamped (`uptime_percent`
  `min(100,…)`, `queue_usage_pct`, `dedup_pct` denom⊇numer, `success_rate`,
  `drop_rate`). #74 is the only one that was ever cross-population, and it's fixed.
- **3b registry wiring:** NO gaps. DropReason/DELIVERY_FAILURE_REASONS guarded
  by `TestDeliveryFailureReasonsParity`; mini-dudeai source/action kinds
  schema-pinned with drift tests; DeliveryState guarded; `Category`/
  `MessageType`/`Protocol` are classification-only (no dispatch → no gap).
- **3c reachability (MF018):** NO unreachable surfaces. 31 map endpoints all
  routed in `map_http_handler`; CLI tools wired; TUI menu tags all dispatch;
  `commands/` internal modules intentional.
- **dead operator instructions:** one real (cosmetic) fix — `config/lora.py`
  tip showed `http://client.meshtastic.org`; the live web client is HTTPS, so
  → `https://`. NOTE: the discovery agent *suggested* `app.meshtastic.org`;
  WebFetch proved that host is DEAD (ECONNREFUSED) while `client.meshtastic.org`
  loads — an unverified false-correction caught by verifying before editing.
  No http→https lint guard added (would false-fire on legit local URLs).

Sweep verdict: the known instances are guarded (steps 1–4); no NEW instances of
the blind-spot classes remain. Deferred §3b/§3c items (systemd-unit-template,
deploy-restart-hook, "service active ≠ doing the job") are the remaining arc
work — they need NEW guards, not a sweep.

## Deferred §3b/§3c — LANDED 2026-06-15 (clean session; +20 tests, red+green)
Built all three in `tests/test_honesty_invariants.py`, red-test-first, each
green-invariant live-falsified against the real source. The recon was real and
overturned the agent-summary framing twice (the arc's own lesson: verify ground
truth, don't trust a summary). 36 honesty tests total now; lint + #29 regression
guards green.

**§3b-i — template provenance (8 tests, `TestTemplateProvenance`).** The literal
"every OWNED unit has a template + every template maps to an install site"
bijection is FALSE on a healthy repo, so a strict guard would be RED on ~half the
templates → forced into a vacuous allowlist (the false-guard this arc kills).
Ground truth: MeshForge materializes systemd units SEVEN ways — shell-installer
copy/sed, update.sh `*-user.service` glob, a TUI handler at runtime
(`meshtasticd-alt` ← `dual_radio_failover.py`), manager-box organs hand-enabled
on the federator box (backup/ci-status/fleet-health), a hand-deployed fleet
daemon with no committed installer (watchdog), ops band-aids + claw infra
(meshtasticd-restart, nats-server), and inline heredocs (meshforge/rnsd/
meshforge-map — owned-but-INLINE by design). The "dead template" list a discovery
agent produced was an ARTIFACT of searching only shell scripts; all 29 templates
have a real, verified (git-blame'd) consumer. So §3b-i is the spec's sanctioned
curated-provenance manifest: a `TEMPLATE_PROVENANCE` dict (kinds installer/glob/
tui/hand, each verified 2026-06-15) with a disk↔manifest bijection — a NEW
template with no provenance entry fails (forces a deploy decision); a stale entry
whose file vanished fails; machine-checkable kinds (installer/glob/tui) have their
cited reference verified so provenance can't silently rot. Drop-in dirs covered by
`DROPIN_PROVENANCE`. Found-and-recorded gap (NOT fixed — pre-existing, out of
scope): the 3 user `*-user.timer` templates (synth-soak, lab-rollup, drain) are
NOT copied by update.sh's `*-user.service` glob → hand-deployed only; flagged in
their provenance notes.

**§3b-ii — deploy-restart hook (5 tests, `TestDeployRestartHook`) — surfaced a
REAL #79 gap and fixed it.** Curated `MESHFORGE_CODE_DAEMONS` (daemons whose
ExecStart runs THIS repo's code, so a `git pull` of /opt/meshforge changes them —
verified per-unit) vs `RESTART_EXEMPT_DAEMONS` (external-binary wrappers:
nomadnet/meshchatx/rnsd/meshtasticd/nats — a MeshForge pull doesn't change their
code, and rnsd restart is explicitly dangerous). The guard parses update.sh +
fleet_sync.sh for restart-wiring (try-restart / sync_repo / sync_*_unit), token-
matched so `meshforge-mini-dudeai` ≠ `…-claw`. **It caught a genuine gap: the lab
echo responder (`lab.lxmf_echo`) and the nomadnet silence watcher
(`scripts/nomadnet_silence_watch.py`) run MeshForge code but were restarted by NO
deploy script → they served OLD code after a pull (the exact #79 class).** Fixed
by wiring `try-restart meshforge-echo.service` + `nomadnet-silence-watch.service`
into update.sh's user-unit block AND (completeness, 2026-06-15) into
fleet_sync.sh's auto-deploy path — both the remote leg (`sync_user_unit`) and
the local self leg (`sync_local_user_unit`), mirroring mini/claw. Soak-safe:
try-restart honors disabled/absent, the proc-mtime-vs-code staleness gate only
restarts on a real code change, and harmless stateless daemons. Live-falsified:
against pre-fix update.sh the guard is RED on both units; post-fix all 7
code-daemons are restart-wired by fleet_sync.sh alone.

**§3c — daemon→output-probe coverage (7 tests, `TestDaemonOutputCoverage`) + the
watchdog freshness fill.** Coverage test (the spec's reframe), not one clever
guard. `DAEMON_OUTPUT_COVERAGE`: each fleet-wide MeshForge-code daemon → its
OUTPUT mechanism, verified — gateway→delivery_write_canary/queue_backlog/
delivery_confirmation_stall, map→http_local(/healthz), mini→history_write_failure,
rnsd→rns_rpc_responsive/rns_shared_instance_responsive, each asserted to be CALLED
in `run_all_probes` (call-graph BFS) AND not a process-state probe. The watchdog
is `external`: a self-probe for its OWN liveness is CIRCULAR (a wedged loop never
runs the probe), so the fill is in `honest_status.sh` — a watchdog.json `ts`
FRESHNESS gate (a stale-but-valid 0-signal snapshot used to read "clean": the §3c
false-green for the watchdog itself). Freshness is same-clock (remote `date +%s`
in one round-trip, never cross-box — honest_failure #6), 300s threshold (10×30s
ticks), disabled in fixture mode. `AUX_DAEMON_COVERAGE` documents the single-box/
transitive daemons honestly (echo→tracer no-route transitively; claw→documented
single-box gap; silence-watch→is itself the watcher). Closed-set linkage
(honest_failure #7): §3c's covered set must EQUAL §3b-ii's code-daemon set + rnsd,
so a new daemon can't be deploy-restarted yet silently un-output-watched.

Files: `tests/test_honesty_invariants.py` (+515), `scripts/update.sh` (#79 gap
fix), `scripts/honest_status.sh` (watchdog freshness gate).

## Resume / verify in a clean session
1. `bash scripts/honest_status.sh` — establish ground truth (don't trust this doc).
2. `git log --oneline 6bc4a08..HEAD` — read the arc's commits.
3. Steps 1–5 + deferred §3b/§3c are LANDED (incl. echo/silence-watch restart
   in BOTH update.sh and fleet_sync.sh). Remaining optional follow-ups:
   the 3 user-timer deploy gap (§3b-i provenance notes — update.sh's glob
   copies *-user.service but not the sibling *-user.timer); a claw-specific
   output probe if claw graduates beyond single-box.

Pending: synth-soak Monitor was armed on moc (won't survive session end — mini
ntfy is the durable backstop). CI of any push lands ~3 min after.

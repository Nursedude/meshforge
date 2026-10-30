# Rules evidence — the full text of `.claude/rules/*.md` before the 2026-10-29 trim

> NOT loaded per turn (not under `.claude/rules/`, not `@`-included). The
> rules files carry the RULES; this file carries the stories, worked
> examples and dates behind them, verbatim as they stood at `73d9507b`.
> Operator 2026-10-29: "do the context trim pass on the rules files".
> Numbering is preserved in the trimmed files — `honest_failure_modes #N`
> and `calibrated_claims rule N` citations in code/tests still resolve here
> and there. Look a story up: `grep -n "<phrase>" .claude/foundations/rules_evidence.md`.


---

## `.claude/rules/calibrated_claims.md` — full text as of `73d9507b`

### Calibrated Claims — the discipline for what *I* say, not what the code does

> Born 2026-06-15 from the operator's concern: *"honesty is not enough when
> honesty is a house of cards… when you say 100% and we do it N more times, the
> math is wrong."* `honest_failure_modes.md` is the write-time checklist for
> CODE error paths. **This is its mirror, aimed at my own claims.** MeshForge
> serves engineers, scientists, HAMs, and hobbyists who act on what I tell them;
> a confident wrong claim costs the operator real hours and burns the trust this
> whole observability spine exists to protect.

#### The method ordering — the parent of everything below

> **Operator, 2026-09-20:** *"better science to measure than assert — that's
> done when you can't measure — then there's physics."*

The tiers further down ask *how sure am I*. This asks the prior question:
**what did I do to find out?** They rank, and the order is not negotiable.

1. **MEASURE.** Go get the number from the thing itself. Always first, and
   usually cheaper than the reasoning that would replace it.
2. **ASSERT.** A reasoned claim — legitimate ONLY where measurement is
   unavailable, and only when LABELLED as an assertion so the next reader can
   settle it. An unlabelled assertion reads as a measurement.
3. **PHYSICS.** First principles and invariants, when neither of the above is
   available. On this fleet that is the floor, **not the fallback**: when the
   WAN is gone, the observers are lying and the mesh is dark, the link budget,
   the 40-pin header as a mechanical contact and a default gateway that
   answers nothing are the most reliable tier left — they cannot be
   misconfigured.

⚠️ **Two clauses without which "we measure" becomes the most confident way
to be wrong** (both proven 2026-09-20; the worked examples live in
[[feedback_measure_assert_physics]], not here — this file is paid for every
turn, so it carries the rule and not the evidence):

- **Ask what the number is OF.** A measurement of the wrong quantity carries
  authority a bad assertion never gets. Four of my own instruments returned
  confident wrong labels in one session, every one rendering MY measurement
  failing as a finding about the fleet.
- **Measure the MEASURER when the instrument is yours.** A system that
  measures only the network is a dashboard; one that can measure its own
  instruments is an observatory — the whole NOC/MOC claim. In a domain run
  through a model, **the model is one of the instruments.**

#### The defect class

**A claim of certainty I have not earned.** I say "done / verified / all green /
100% / it works / fixed," the operator acts on it, we run it N more times, and
the math was wrong. The cause is not dishonesty — it is **miscalibration**, and
the research is unambiguous about it: LLM overconfidence is structural and
post-training-induced, a single success (pass@1) overstates the true rate, and
behavior *shifts across model versions*. So a discipline that lives in my
disposition is a **house of cards** — it collapses the next time the model
swaps. The fix is to make calibrated language a rule I apply at write time, and
to let the harness put re-derived ground truth in front of me at the moment of
the claim.

| What I did | The overclaim | The honest claim |
|---|---|---|
| wrote code, didn't run it | "this fixes it / it works now" | "BELIEVED — written, not run; verify with X" |
| ran it once, it passed | "100% / reliably works" | "passed once; not reproduced or root-caused" |
| local tests green | "CI is green / all green" | "local suite green (exit 0); CI not yet checked" |
| read code, reasoned it through | "definitely / guaranteed" | "I believe so from reading; the check is X" |
| a check errored / was skipped | silence, or "looks good" | "UNKNOWN — couldn't verify; here's why + how" |
| count drifted mid-task | patch the tally to stay consistent | re-derive the count from ground truth |

#### The three evidence tiers — tag every completion claim

- **VERIFIED** — an external check ran **this turn** and I am quoting its real
  result: a captured exit code (`exit 0`, never a streamed `| tail`), a CI
  conclusion for *this exact HEAD*, `honest_status.sh` `exit 0`, or behavior I
  observed by running the thing. Quote the evidence inline.
- **BELIEVED** — written carefully, reasoned through, *should* work — but I have
  **not** verified it. Say "BELIEVED, not verified" plainly and name the check
  that would confirm it. This is a good, honest answer; it is not a failure.
- **UNTESTED / UNKNOWN** — I have not checked, or the check could not run.
  Unobservable is **never** "healthy" (honest_failure_modes #2). State how to
  check. Abstaining — "I don't know yet" — is a first-class, reliable output.

#### The rules

1. **Banned bare words.** Never emit "100% / fully verified / all green /
   definitely / guaranteed / it works / done" *without a quoted external
   result*. If you cannot quote the evidence, it is BELIEVED at best.
2. **pass@1 ≠ reliable.** "It worked once" is BELIEVED, not VERIFIED, unless it
   is **reproduced** (ran ≥2×, or determinism is established) **or
   root-caused** (you can name *why* it works, not just that it did). A
   fix-claim names the root cause.
3. **Re-derive, never patch your own count** (the operator's "odd" anti-pattern,
   and the disease in miniature). Capture the *true* state at the **beginning**;
   at the **end**, **re-measure** from ground truth. Never edit a forward-carried
   tally ("12 tests… now 11… call it 11") to keep your narrative self-consistent
   — that is trusting your bookkeeping over re-observation. Run the count again.
4. **The check of record is `scripts/honest_status.sh`.** `exit 0` = verified
   green; `exit 1` = proven not-green; `exit 2`/UNKNOWN is **never** a pass. For
   tests, capture the real exit code to a file — `pytest … >f 2>&1; rc=$?` —
   never `pytest | tail` (the exit code is `tail`'s, not pytest's).
5. **Surface the blind spot, don't average it away.** If part of a result is
   unobservable, say so as its own line — never fold it into a healthy-looking
   summary (the #74 confirmation_rate lie, in claim form).
6. **Every claim leaves a witness.** A VERIFIED claim of consequence should be
   logged to the calibration ledger so it can be re-derived later — the math
   becomes a number we can both see and shrink, not an assertion. Feed
   convention (2026-07-03): *gate automatic, hatch deliberate, verdicts never*.
   The Stop-hook gate is the only AUTOMATIC writer; a deliberate
   `record_claim(..., source="manual")` is sanctioned when the evidence lives
   outside the gate's marker (CI conclusion for the head, live drill, fleet
   check) — same schema, quoted evidence, exact `head_full`, accepting the
   claim may stay `open` forever if no full-suite verdict lands on that head
   (open is an honest terminal state). **Never hand-write a `verdict` event**
   — claims are assertions; verdicts belong to the re-derivation machinery
   alone (the ledger's analogue of never posting your own ntfy ack).
7. **Verify the consumer-of-record, not the wiring.** A static trace — "X
   registers Y, therefore Y runs"; "the requirement pins Z, therefore Z is
   installed" — is BELIEVED at best. VERIFIED requires observing the LIVE
   process, interpreter, or unit that actually hosts the behavior: the
   systemd unit's real ExecStart (not the module you traced), the venv the
   services import from (not system python3), the running daemon's own env.
   Canonical failures (2026-07-03): `core.orchestrator` never hosts the
   probe that `daemon.py` registers; both broke ledger claims (44b5b92,
   2de68ec) were proxy-verifications. When you cannot reach the live
   consumer, say so — that claim is BELIEVED, and name the observation that
   would upgrade it.

#### Coverage — the question the three tiers do not ask

The tiers ask *how sure am I*. On 2026-08-11 fifteen defects got past me while
my claims stayed **correctly calibrated and still wrong**: every VERIFIED tag
was true *about the thing it measured*, and the measurement did not touch the
feature. `NRestarts=0` was true while the web client served nothing —
meshtasticd binds `:9443` with or without a certificate. A perfectly honest
claim about the wrong quantity.

1. **Ask at write time, 5 seconds: "what would still pass this check if the
   feature were dead?"** If you can name something — a listening socket, an
   enabled-but-failed timer, a fresh state file beside a crashlooped daemon —
   the check measures PRESENCE, not FUNCTION. Terminate acceptance at the
   domain's END (a message arrives; a browser gets 200), never at a proxy.
2. **Rank evidence by AUTHORIAL DISTANCE.** A witness you did not write
   outranks one you did, because a check you author encodes only the
   narrowness you already thought of — the defect is by definition outside it.
   That day four surfaces agreed a box was healthy and all four were mine; the
   one that knew was the operator's browser. Verifying your OWN work, reach
   first for the authority you did not build: systemd's `NRestarts`, a
   daemon's journal, CI's conclusion, a guard that refuses you, the human's
   screen. (The same day, a permission guard's refusal preserved a private key
   my judgment was about to delete as redundant.)
3. **An absence must be explained, never assumed benign.** Same day: one box
   reported `ssl=no` beside six reporting `ssl=yes`, and I recorded it as
   "legitimately has no ssl" — a thing I had deleted twenty minutes earlier,
   with the comparison in the same output I was reading.

#### How to apply — run this before saying a task is done

Walk it over your closing message; each item is a 5-second question:

- Am I about to use a banned bare word? → tag it VERIFIED / BELIEVED / UNKNOWN.
- For every "works / fixed / passes": did a check run **this turn**, and am I
  **quoting** it? If not → BELIEVED, name the check.
- Did I re-derive the final count, or am I carrying a patched tally? → re-run it.
- Is anything unverified or skipped? → say so explicitly; UNKNOWN ≠ pass.
- Is "I'm not sure / let's verify" the most honest answer? → then say that.

The harness will, at the moment I claim "green," put the re-derived truth in
front of me for one reflective beat. That is not a cage — it is the evidence I
need to be mindful. My judgment still stands; it just stands on ground I cannot
fabricate.

Slow wins the race.

---

## `.claude/rules/honest_failure_modes.md` — full text as of `73d9507b`

### Honest Failure Modes — the write-time checklist

> Born 2026-06-09 from the mini-dudeai engine review (Issue #80): 18 findings,
> 18 confirmed, ONE defect class in many skins. Hundreds of operator-hours have
> been spent on this topic across the fleet's life (#29, #58, #60, #63, #74,
> #78, #79, #80). This file is the lesson, stated once, to be applied when
> WRITING code — not found later by review. MeshForge serves engineers,
> scientists, HAMs, and hobbyists: a silent failure that makes the user leave
> the app to discover the truth IS an app failure (MF018).

#### The defect class

**A degraded internal state gets mapped to a valid-looking value, and
downstream logic converts it into a real-world claim.**

| Error path | Valid-looking value | False claim downstream |
|---|---|---|
| rules file read error | `[]` (empty ruleset) | "no conditions are active" → silent mass-deactivation |
| source collect error | absent conditions | "everything recovered" → false CLEARED page |
| `{"rules": null}` | zero validation errors | "valid document" → promotion wipes alerting |
| typo'd match key | extras equality filter | "legal rule" → rule silently matches nothing forever |
| reader configured, writer not | marker absent | "crash detected" on every planned reboot |
| two consumers, two constants | both look authoritative | writer warns while watcher sleeps |

The error path answered a question it wasn't asked. The type system was
satisfied; the truth wasn't.

#### The checklist — run it over every error path you write

> **First, the general question the ten below are specific shapes of:** *does
> this path MEASURE what it reports, or ASSERT it?* An error path that asserts
> — that returns a value it did not observe — is this file's defect class in
> one line. `[]` because the read failed is an assertion that there is nothing
> there. See `calibrated_claims.md` → "The method ordering".

1. **Audit every `except`, `or []`, `or {}`, `.get(default)`:** does the
   degraded value overlap the healthy domain? If yes, tri-state it
   (ok / unobservable / error) or hold prior state. Empty ≠ error.
2. **Absence of evidence is not evidence of absence.** Never emit a
   recovery/cleared/healthy transition from data that is missing *because the
   observation channel failed*. Hold last-known state and surface the
   blindness as its own signal. (Fleet creed: unobservable ≠ dark ≠ resolved.)
3. **Validators must reject what the author cannot have meant.** Null where a
   list belongs, zero items where the document's whole purpose is items,
   unknown keys adjacent to a known vocabulary — error or warn LOUDLY at the
   authoring/promotion boundary, never absorb.
4. **Reader/writer pairs wire together or fail together.** If a mechanism has
   a producing half and a consuming half, the code path that constructs one
   must construct (or loudly refuse without) the other. Half-wired = a
   detector that's confidently wrong.
5. **Two consumers of one artifact share ONE constant.** Thresholds, limits,
   paths, formats: derive, import, or test-pin them together. Independent
   hardcodes WILL drift (24,000 vs 24,576).
6. **Wall-clock durations are forgeable on this fleet** (RTC-less Pis, NTP
   steps, fake-hwclock). Durations need monotonic anchors, observed-tick
   counts, or clamps on negative/absurd deltas. Persisted timestamps need a
   "clock went backward" branch. (#74's monotonic fix; #80's grace ticks.)
7. **Closed enums need closed consumers.** When a registry/enum grows (signal
   classes, source kinds, roles), every consumer that switches on it must fail
   a TEST until updated — coverage gates, not memory. (Seed-coverage test;
   closed-enum probe gate.)
8. **Concurrent writers: exclude or merge, never interleave.** Same state
   files + two processes = flock-refuse-loud or single-writer design. Fixed
   tmp names are a collision, not a convention.
9. **Every swallow gets a witness.** A swallowed exception must leave a
   counter, a marker, or a signal a PROBE can see (history_appends_total,
   consecutive_write_errors, preflight keys). If failure leaves no artifact,
   it never happened — until the operator finds out outside the app.
10. **A resolved incident compiles down to THREE artifacts, not two.** When a
    frontier session root-causes an incident, the write-time deliverables are
    the probe/rule (→R), the persistent_issues/runbook entry (→R), **and a
    triage artifact for the tier that will meet this class next (→L)** — so
    the next reader's competence on it is not permanently BELIEVED.
    ⚠️ **The third artifact is owed to a tier that CONSUMES it.** Writing one
    for a parked organ is machinery watching machinery — the exact cost this
    file exists to refuse — so resolve the consumer before writing it:
    * **tier-L live** (Ollama running, `local_brain_eval` cron armed): the
      artifact is an eval case in `evals/local_brain/`, checked by the weekly
      `local_brain_eval --gate` verdict (2026-07-09, cross-model arc).
    * **tier-L parked** (the state since 2026-09-07 — pre-score off, Ollama
      kept only for the future field-kit chat compiler): the artifact is the
      **decision-tell row in `persistent_issues.md`**, because the frontier
      cadence and a human at a terminal are who actually read it. Do NOT add
      `evals/local_brain/` cases while parked; see
      [[project_ollama_parked_2026_09_07]]. When the chat-compiler arc starts,
      this reverts to the eval-case form.
    **Check the consumer's live state before writing — do not infer it from
    this rule.** (Amended 2026-09-08: on the RNS-RPC contention incident a
    session wrote the eval case first and read the parked decision after, and
    would have committed a rule violation as "follow-through". A rule carried
    in context is not a check against current ground truth — this file's own
    class, aimed at the process instead of the code.)

11. **Ask at incident close: would an operator WITHOUT the assistant have
    seen this?** If no, the read-only surface owes a row. A domain whose
    operator works through a model drifts its TUI behind its own dev
    practice, because the surface learns only when someone carries the
    lesson across by hand. (2026-09-19: an OS roll produced two panes this
    way — reboot-owed, and declared-vs-actual holds.)

#### How to apply

Writing any monitor, daemon loop, validator, or persistence layer: walk the
checklist over the diff BEFORE committing — every item is a 30-second question
about code you just wrote. When VERIFYING a detector you wired, observe the
consumer-of-record (the live unit/interpreter that hosts it), not the wiring
trace — a registered check is not a running check (calibrated_claims rule 7). In review, candidates matching this class are
PLAUSIBLE by default; the burden of proof is on the error path to show what
the consumer sees.

Slow wins the race.

---

## `.claude/rules/model_advisor.md` — full text as of `73d9507b`

### Model Advisor — match the task to the tier, out loud

> Born 2026-07-09 from the cross-model agentic-health arc: the operator runs
> different Claude models by cost/availability (frontier passes are rationed),
> and the fleet has a measured local tier below them. Reliability lives in the
> executable harness, not the model — but *routing* a task to the right tier
> is a judgment every session should make at intake, visibly.

#### The tier ladder (cognition, descending)

| Tier | Fit |
|------|-----|
| **Frontier-class** (rationed) | adversarial review passes, novel-arc design, incident forensics with ambiguous/conflicting evidence, security-sensitive refactors |
| **Opus-class** (day-to-day default) | dev, deploys, probes, docs, fixes with test guards, fleet ops — months proven on the cadence |
| **Fast/Haiku-class** | mechanical sweeps, formatting, log triage, single-file lookups |
| **L — local LLM** (Ollama, eval-gated) | cadence triage fallback, offline oracle — PROPOSE-only, never ratifies |
| **R — rules/probes** | everything already compiled downward; the always-on tier |

#### The behavior — one line at task intake, then proceed

Judge the task's tier against the running model and say so in ONE line, then
do the work anyway unless the operator redirects:

- **Upshift tell** ("could use more of me"): review-shaped, novel-design-
  shaped, or ambiguous-evidence work on a non-frontier session → say so and
  queue the range in `.claude/audits/review_provenance.md` as a ready
  worklist for the next frontier pass. Never fake a frontier-quality
  adversarial pass on a smaller model — a shallow review that blesses code is
  worse than an honest queue entry.
- **Downshift tell** ("could use less of me"): mechanical/formatting/triage
  work on a frontier session → say it would run fine on a smaller model or
  fast mode, and offer to batch it rather than burn the rationed session.
- **Right-sized**: say nothing; just work.

#### Invariants (every tier)

- Gates never scale down with the model: lint, regression guards, claim-gate,
  `honest_status.sh` run identically. Smaller model = lean on them HARDER.
- Calibrated claims are tier-independent: VERIFIED needs a quoted check ran
  this turn, on every model.
- Tier-L artifacts always carry `brain_tier: local`; its competence claims
  come from the eval ledger (`local_brain_evals.jsonl` + the weekly
  `local_brain_eval` cron verdict), never vibes.

---

## `.claude/rules/security.md` — full text as of `73d9507b`

### Security Rules

#### MF001: Path.home() - NEVER use directly

```python
### WRONG - returns /root when running with sudo
config = Path.home() / ".config" / "meshforge"

### CORRECT - works with sudo
from utils.paths import get_real_user_home
config = get_real_user_home() / ".config" / "meshforge"
```

**Why**: `Path.home()` returns effective user's home. With `sudo`, that's `/root`, breaking config persistence.

**Linter**: `python3 scripts/lint.py` checks MF001

---

#### MF002: shell=True - NEVER use in subprocess

```python
### WRONG - command injection risk
subprocess.run(f"meshtastic --info {user_input}", shell=True)

### CORRECT - safe argument list
subprocess.run(["meshtastic", "--info", user_input], timeout=30)
```

**Why**: Shell injection allows arbitrary code execution.

**Linter**: `python3 scripts/lint.py` checks MF002

---

#### MF003: Bare except - Always specify exception type

```python
### WRONG - catches SystemExit, KeyboardInterrupt
except:
    pass

### CORRECT - specific exceptions
except Exception as e:
    logger.error(f"Operation failed: {e}")
```

---

#### MF004: subprocess timeout - ALWAYS include

```python
### WRONG - can hang forever
subprocess.run(["long", "command"])

### CORRECT - bounded execution
subprocess.run(["long", "command"], timeout=30)
```

---

#### Input Validation

- Validate all user input before use
- Sanitize file paths (no `..`, absolute paths only)
- Validate URLs before fetch
- Escape special characters in displayed text

---

#### Secrets

Never commit:
- `.env` files
- `credentials.json`
- API keys in code
- Private keys

Use environment variables or secure config.

---

#### MF015: No operator-specific local IPs in published docs

**Rule**: Anything under `docs/` (especially `docs/substack/`) is public-facing.
Never paste literal LAN IPs (`192.168.x.y`, `10.x.y.z`, `172.16-31.x.y`) from
the operator's network. They identify the operator's home/office subnet and
leak topology.

```markdown
<!-- WRONG - leaks operator's LAN -->
Could not load `http://192.168.1.249:5000/`.

<!-- ALSO WRONG - link target still contains the IP, hover/click reveals it -->
Could not load [http://<ip>:5000](http://192.168.1.249:5000).

<!-- CORRECT - pure placeholder, no real IP anywhere in source -->
Could not load `http://<ip>:5000/`.
```

The IP must not appear in the source at all — not in display text, not in
link targets, not in HTML comments, not in alt-text. "Hidden" link targets
are still in the rendered HTML and still get crawled.

**Why**: Substack posts, README screenshots, and committed transcripts get
indexed by search engines and archived forever. A single leaked LAN IP plus
a hostname pins the operator's physical network.

**Companion to MF014** (operator-specific values in source/templates). MF015
is the docs equivalent. When transcribing a debugging session into a
published post, sanitize IPs at copy time, not "later."

**Audit before publishing a substack post**:
```bash
grep -rn "192\.168\.[0-9]\+\.[0-9]\+\|10\.[0-9]\+\.[0-9]\+\.[0-9]\+" docs/substack/ \
  | grep -v "x\.x\|<ip>"
```

---

## `.claude/rules/testing.md` — full text as of `73d9507b`

### Testing Rules

#### Running Tests

```bash
### Run all tests
python3 -m pytest tests/ -v

### Run specific test file
python3 -m pytest tests/test_rf.py -v             # RF calculations
python3 -m pytest tests/test_rns_bridge.py -v     # Gateway bridge

### Run with coverage
python3 -m pytest tests/ --cov=src --cov-report=term-missing

### Quick syntax check
python3 -m py_compile src/**/*.py
```

---

#### Test Locations

```
tests/                             # 3,160 tests across 90 files (as of 2026-04-19)
├── conftest.py                    # Shared fixtures (mock_meshtastic, mock_rns, no_network)
├── test_rns_bridge.py             # Gateway bridge (140 tests)
├── test_rf.py                     # RF calculations (107 tests)
├── test_meshtastic_protobuf.py    # Protobuf client (74 tests)
├── test_message_queue.py          # SQLite queue (72 tests)
├── test_status_bar.py             # TUI status bar (70 tests)
├── test_node_tracker.py           # Node tracking (68 tests)
├── test_mqtt_robustness.py        # MQTT reliability (66 tests)
├── test_commands.py               # CLI commands (61 tests)
├── ... (59 more test files)
└── test_regression_guards.py      # Architectural regression prevention (15 tests)
```

---

#### Writing Tests

##### Test pure functions first
```python
def test_haversine_distance():
    """Test distance calculation between two points."""
    # Known distance: SF to LA ~559 km
    result = haversine_distance(37.7749, -122.4194, 34.0522, -118.2437)
    assert 550 < result < 570
```

##### Use fixtures for setup
```python
@pytest.fixture
def sample_node():
    return {"id": "!abc123", "lat": 37.7749, "lon": -122.4194}

def test_node_processing(sample_node):
    result = process_node(sample_node)
    assert result.id == "!abc123"
```

##### Test error conditions
```python
def test_invalid_coordinates():
    with pytest.raises(ValueError):
        haversine_distance(999, 999, 0, 0)
```

---

#### Test Patterns for MeshForge

##### Mocking external services
```python
from unittest.mock import patch, MagicMock

@patch('requests.get')
def test_hamclock_fetch(mock_get):
    mock_get.return_value.json.return_value = {"sfi": 150}
    result = fetch_space_weather()
    assert result["sfi"] == 150
```

##### Testing path utilities
```python
@patch.dict(os.environ, {"SUDO_USER": "testuser"})
def test_get_real_user_home_with_sudo():
    result = get_real_user_home()
    assert result == Path("/home/testuser")
```

##### Testing handlers (test logic separately from TUI)
```python
### Prefer testing logic separately from UI
def test_parse_node_data():
    raw = '{"id": "!abc", "name": "Node1"}'
    result = parse_node_data(raw)
    assert result.name == "Node1"
```

---

#### When to Write Tests

1. **RF calculations** - Always test (critical for HAMs)
2. **Data parsing** - Test edge cases
3. **Path utilities** - Test sudo compatibility
4. **Business logic** - Test before TUI handler integration

---

#### Linter as Pre-Test

Run linter before tests:
```bash
python3 scripts/lint.py --all && python3 -m pytest tests/ -v
```

---

## `CLAUDE.md` — trimmed sections, full text as of `74b899e7` (2026-10-29)

> Context trim phase 2b. CLAUDE.md keeps the rule; the story behind it lives here.

### CRITICAL — Calibrated claims paragraph

**Calibrated claims** (the discipline for what *I* say): never emit a bare
"100% / verified / all green / it works / done" without a quoted external result.
Tag every completion claim **VERIFIED** (a check ran this turn — quote its exit
code), **BELIEVED** (written, not run — say so), or **UNKNOWN** (couldn't check —
unobservable ≠ healthy). "Worked once" is not "reliable". Re-derive the count at
the end; never patch a running tally. Check of record: `bash scripts/honest_status.sh`
(`exit 0` = green; `exit 2`/UNKNOWN is never a pass).

### Branch Strategy — bullets

- Solo dev workflow: commit direct to `main`, push via `git push origin main`. PR/feature-branch flow was retired 2026-04-19 (caused more divergence than it prevented). A permission hook may still deny pushes; use `origin` remote rather than literal SSH URL.
- **Trunk only (operator, 2026-09-23 — applies to MeshForge AND MeshAnchor):** commit direct to `main`, push right after every hook-passing commit, fleet boxes only ever pull. The gates (pre-commit/pre-push hooks, CI, `honest_status` fleet-SHA drift, non-author review) do the job PRs used to. A branch exists only as short-lived isolation (e.g. a reviewer subagent's worktree): it returns a PATCH that is applied to `main`, is never merged, and is deleted the same session. The pre-2026-09-23 branches were archived to `~/.local/share/meshforge/branch-archive/<repo>-branches-2026-09-23.bundle` (`git bundle list-heads`) and pruned; open Dependabot PR branches are left to Dependabot.
- Alpha branch archived as tag `alpha-archived`.

### Key Commands — launch block

```bash
# Launch
scripts/meshforge-launcher.sh           # Primary interface (TUI) — use THIS, not
#   a bare `sudo python3 src/launcher_tui/main.py`: the launcher sets
#   PYTHONPYCACHEPREFIX so root's bytecode stops landing in the repo
#   (scripts/lib/pycache_prefix.sh — that habit is what the chown sweeps kept curing).
python3 src/standalone.py               # Zero-dependency RF tools
# GTK4 desktop app REMOVED — the TUI is the only interface.
# ⚠️ That does NOT mean "no desktop integration". org.meshforge.app.desktop +
# scripts/meshforge-terminal.sh are a LAUNCHER for this same TUI (they contain
# no GTK), installed by scripts/install-desktop.sh on every update.sh run, and
# in active use on the desktop box. assets/org.meshforge.policy is live too —
# it authorises pkexec for utils/system.py. Do not "clean up" any of it.
# Categories=Network;HamRadio (NOT System — that buried it in System Tools).
```

### Pre-Push Check — the three paragraphs after the four lines

**A push is NOT a deploy** (measured 2026-09-13; this line used to claim
boxes "pull within seconds" and no such mechanism exists). Nothing
auto-pulls: the only fleet cron is `fleet_sync.sh --memory-only` every 6h,
which exits before any code deploy, and `.githooks/post-commit` fixes only
the committing box — its own comment says it "cannot and must not reach
across the fleet". The fleet moves when someone runs `scripts/fleet_sync.sh`
THEN `scripts/fleet_pull.sh`, in that order: the reverse makes sync read
every remote unit as "unchanged" and skip every restart while exiting 0.
So `honest_status.sh`'s `fleet SHA drift` leg stays FAIL after a clean push
until that deploy runs. That is the deploy still being owed, not a regression.

**Hooks must be ON** (found OFF 2026-06-15 — `core.hooksPath` pointed at the
empty `.git/hooks`, so the #29 spine was dormant): confirm
`git config core.hooksPath` says `.githooks`. The pre-commit hook runs lint +
regression guards + collection sweep; the pre-push hook re-runs lint +
regression guards (the "can't push mis-wired work" gate) + a parent-CI advisory.

**After pushing, verify it actually landed — don't trust the summary.** Run
`bash scripts/honest_status.sh` — the operator-owned gate that re-derives state
from external ground truth (CI conclusion for HEAD, per-box SHA convergence,
full suite, lint, live `confirmation_rate ≤ 1.0`, watchdog signals). `exit 0`
= fully verified; `UNKNOWN` (unreachable / CI-pending) is never counted as
pass. `--quick` skips the local suite; `--strict` fails on fleet warnings.


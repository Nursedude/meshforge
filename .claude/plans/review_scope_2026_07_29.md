# Review scoping artifact — `65132236..f51f5106`

> **Built 2026-07-29 on an Opus-class session, deliberately, so the rationed
> adversarial pass spends itself on JUDGMENT instead of discovery** — the same
> split that made the 07-26 taxonomy pass efficient (`model_advisor.md`).
> **Start at §3.** §1–§2 are measurement; do not re-derive them.
>
> The author of this artifact wrote one commit in the range (`f51f5106`) and
> says so in §7, because a scoping note written by an interested party is only
> useful if the interest is declared.

---

## §1 The range is NOT 24 commits of unreviewed code — it is 13

`65132236` is **exactly `3afda33a~1`** (verified: `git rev-parse --short
3afda33a~1` → `65132236`). That is the start of the window the **2026-07-28
Fable-5 adversarial pass** already covered — see the `2026-07-28` row in
`.claude/audits/review_provenance.md`.

| | commits | status |
|---|---|---|
| `3afda33a..f7811c02` | **11** | **REVIEWED 07-28** (8 finder angles, ~40 candidates → 10 verified survivors, all 10 fixed) |
| `f7811c02` itself | 1 | the review's own FIX commit → **unreviewed by definition** (`feedback_review_your_own_fixes`) |
| `a9b471d0..f51f5106` | **12** | **never reviewed** |

**So the real target is `f7811c02~1..f51f5106` — 13 commits.** Reviewing the
first 11 again is not free: it re-verifies 10 already-fixed findings and dilutes
the signal the ledger exists to preserve. Spend the budget on the 13.

### ⚠️ Ledger correction applied by this artifact
The 07-28 row cited its fix commit as **`bfc1eba7`**, which is **not an ancestor
of `main`** (`git merge-base --is-ancestor bfc1eba7 f51f5106` → false). The
commit was amended; the content landed as **`f7811c02`** (`git diff bfc1eba7
f7811c02` = the provenance line only, i.e. `f7811c02` is `bfc1eba7` + its own
row). The row has been corrected to cite both. A future pass computing coverage
from the old SHA would have found nothing — and the ledger's own convention
already warns that ancestry is not coverage.

---

## §2 The uncovered surface, measured

`f7811c02~1..f51f5106`: **33 non-test files**, and **12 test files (+1,942)**.

| lines | file | shape |
|---|---|---|
| 390 | `scripts/gen_claw_pinhole.py` | **NEW — rewrites a FIREWALL** |
| 302 | `src/utils/watchdog_probes_claw_watch.py` | NEW probe (**this artifact's author**) |
| 287 | `src/utils/watchdog_probes_claw_uplink.py` | NEW probe |
| 192 | `scripts/fleet_offline_check.sh` | **the PAGING monitor**, moved into the repo |
| 161 | `src/mini_dudeai/claw_rf_watch.py` | NEW — the uptime gate |
| 152 | `scripts/claw_set_watch_ids.py` | NEW — writes config TO the device |
| 113 | `scripts/claw_pinhole_selfheal.sh` | **NEW — automated firewall repair, hourly** |
| 110 + 67 | `src/utils/fleet_hosts.py` + `scripts/lib/fleet_hosts.sh` | the SSOT resolver **pair** |
| 101 | `src/mini_dudeai/claw_telemetry.py` | tick assembly (`watch_verdicts`) |
| +84/−43 | `src/utils/watchdog_probe_core.py` | SIGNAL_CLASSES + shared helpers |
| +71/−42 | `scripts/honest_status.sh` | the check of record, **again** |
| +67 | `scripts/lint.py` | **MF027** — a new lint rule |
| ~10 files | `fleet_status/fleet_sync/aredn_config_audit/...` | `+7/−10` each — the **mechanical** fleet_hosts convergence; review as ONE cluster, not ten |

---

## §3 Risk-ranked entry points, with the question each one actually raises

Ordered by blast radius × novelty. Each question is the thing a shallow pass
would miss.

### 3.1 `scripts/gen_claw_pinhole.py` + `claw_pinhole_selfheal.sh` — TOP PRIORITY
An **hourly, automated, root** rewrite of `/etc/nftables.conf` on the box that
gates the claws' only path home. A wrong render is the 6.5 h outage it was born
to prevent, caused by the cure.

- The generator declares **five safety invariants** in its docstring (§SAFETY).
  Do they hold in `plan()`? Specifically **invariant 3 vs invariant 4**: in the
  blind branch it holds `existing` *in full* (`held = [ip for ip in existing]`),
  not this MAC's prior entries — then `allow -= prunable` runs afterwards. Trace
  a case with node A observed-and-moved (A's old address prunable) **and** node B
  blind in the same run. Does the prune still win, and is that the right answer?
- **The attributed prune has four conditions** (`old not in seen`, `not in
  live_anywhere`, `not in declared_extra`, `in existing`). Construct the input
  where an address is wrongly pruned, or wrongly kept forever.
- `read_state` returns `{}` on unreadable **and** on absent. The docstring argues
  those are the same (no permission to delete). Is that true in the case where
  the state file is *torn mid-write*?
- Does anything reach `--apply` on a path where `--check` returned **2**?
- **Ask what the operator can never see**: the healer speaks `CONCERN` on every
  repair so a churning box differs from a stable one. Is there a path that heals
  **silently** (verdict written before the outcome is known, or a `say` that
  loses the detail through the `cut -c1-160` truncation)?

### 3.2 `scripts/fleet_offline_check.sh` — the thing that PAGES
Three defects of one class surfaced in this file in a single day (07-29), which
is why it moved into the repo. It decides when the operator's phone rings and
when it stays silent.

- **The false-DOWN class**: `project_claw_uplink_drift_2026_07_29` records that a
  box with *no route* was reported as "confirms DOWN". Is every remaining
  can't-reach path distinguishable from confirmed-down, including the ssh exit
  codes that mean "network unreachable" vs "host refused" vs "auth failed"?
- The tier system (`quiet` for `bot`: default priority, q2hr) vs production
  (high/urgent, 1h, escalate at #4). Can a **production** box inherit `quiet` by
  a missing/malformed key in `fleet_offline_boxes.json`? That would downgrade a
  real outage silently.
- `ntfy_push` "returns 0 only on confirmed delivery" — verify that claim, and
  verify a delivery FAILURE still reaches the witness log.

### 3.3 `scripts/honest_status.sh` (+71/−42) — the check of record, third pass
This file has now been edited across **11 commits in ~36 hours** and was the
subject of 5 of the 07-28 pass's 10 findings. High historical defect density.

- Every leg must map *unobservable* → **UNKNOWN**, never PASS and never FAIL. The
  07-28 pass fixed four instances of the opposite. Sweep the legs the pass did
  **not** touch.
- **Known gap, already diagnosed — do not spend a slot rediscovering it**:
  `grep -c unobserved_hold scripts/honest_status.sh` = 0, so a blind held signal
  and a live observation both count as "1 degraded". That is queued as the Pri-2
  detector-blindness contract row; confirm it is unchanged, don't re-derive it.

### 3.4 `src/utils/fleet_hosts.py` + `scripts/lib/fleet_hosts.sh` — ONE constant, TWO languages
The 07-28 pass found the chain hand-copied and **already diverged at copy
time**. The fix created a Python module *and* a shell lib. That is two
implementations of one resolution order.

- Do they resolve **identically** for: `HOME` unset, an empty file, a file of
  only comments, a duplicate host line, CRLF, trailing whitespace, a per-repo
  file that exists but is empty (the `f7ef4014` case)?
- Is there a test that would FAIL if only one of the two changed?
- `a9b471d0` converged ~10 more copies. That is a mechanical sweep: check for
  the one site that was *nearly* the same and got flattened wrongly.

### 3.5 `src/utils/watchdog_probes_claw_uplink.py` (287, new)
- `_read_pinhole_allowlist` returns `None` = "does not gate that port" vs `[]` =
  "admits nobody". The docstring says collapsing them would page every box. Is
  the distinction preserved at **every** consumer, including the probe's own
  comparison?
- `_read_arp_locations` filters to `ATF_COM` because probing a stale address
  manufactures an INCOMPLETE row carrying the target MAC. **Does the probe (or
  anything it calls) probe?** If it does, it creates its own evidence.

### 3.6 `src/mini_dudeai/claw_rf_watch.py` + `claw_telemetry.py` + `claw_set_watch_ids.py`
- The gate's window is `interval × 3`, default `3 h × 3 = 9 h`. `classify_watch`
  returns `None` for empty/absent `watched`, and `_watch_verdicts` returns `None`
  on any exception. **Three different Nones reach one field.** Can a consumer
  tell "no watch list" from "the gate crashed"? (`claw_telemetry` logs a warning
  on the exception path — is the warning the ONLY witness, and does anything
  read it?)
- `claw_set_watch_ids.py` writes to the device via the narrow `config_set` tool
  (`4cbb59a6`). Is the write verified by re-reading the device, or by the tool's
  own reply?

### 3.7 `scripts/lint.py` MF027 (+67) — a gate that must not be foolable
- MF027 flags `probe_*` except-handlers returning None with no
  `note_disposition`. **Find the shapes it misses**: a helper that returns None
  called from the handler; `note_disposition` behind a conditional; a `finally`;
  a bare `return`; `raise SystemExit`. A gate with a known bypass is worse than
  no gate, because it certifies.
- Does it produce false positives on non-probe `probe_*` names?

---

## §4 Pre-identified candidates — already found while scoping, do NOT spend slots

Both were found by reading, are stated with their real severity, and are handed
over so the pass can confirm/refute cheaply rather than rediscover.

1. **`scripts/gen_claw_pinhole.py:203` — `Optional` used in an annotation, never
   imported.** `from __future__ import annotations` (line 59) makes it a lazy
   string, so it does **not** raise at import or call time, and the full suite
   passes. It is a `NameError` waiting for anything that evaluates the
   annotation (`typing.get_type_hints`, a typeguard-instrumented import,
   `inspect.signature(..., eval_str=True)`). **Latent, not live** — verify that
   characterization rather than trusting it.

2. **`scripts/claw_pinhole_selfheal.sh:65-66` — the serialization is
   best-effort.** `exec 9>"$LOCK" 2>/dev/null || true` and `flock -w 30 9
   2>/dev/null || true`: if the lock cannot be opened, or the 30 s wait
   **times out because another run holds it**, execution continues and rewrites
   the firewall concurrently. The header comment claims this serializes
   check-and-apply per honest_failure_modes #8. Note the aggravating context:
   **`2a0865d5` in this very range is titled "a lock only protects a run that is
   actually running"** — lock semantics were already a finding class 24 h
   earlier, which makes this the *neighbouring variant* and exactly the
   meta-pattern in §6.

---

## §5 Deliberately-open residuals — REPORTING these is noise, not signal

Carried from the 07-28 row and the files' own headers. Each is a decision, not
an oversight. Re-flagging one wastes a verification cycle (ledger convention 2).

- **Token-in-crontab is the role evidence** for `operator_cron_wired`.
  `fleet_roles.yaml` declares SERVICES, not crons, so there is no role SSOT to
  consult. Keep `# fleet_dup_collector` on the crontab line.
- **`fleet_offline_boxes.json` is a THIRD copy of fleet membership**
  (`fleet_hosts` has its own, longer list). **Deliberately not unified** —
  unifying would silently widen who gets paged. Flagged in the file header as
  its own decision.
- **`unobserved_hold` is invisible to `honest_status`** — queued as the Pri-2
  detector-blindness contract row (§3.3).
- **MF014 operator values** live in `~/.config/meshforge/*.json`, never in the
  repo. A "hardcoded value missing" finding is probably this by design.
- **`claw_rf_silent` and `claw_watched_node_silent` thresholds are PROVISIONAL
  and escalate-only.** "This could false-positive" is already conceded in both
  annotations; the interesting finding would be a path that PAGES.

---

## §6 The meta-pattern to carry forward — this is the highest-yield instruction

The 07-28 pass's headline was not any single finding. It was that **the
session's fixes repeatedly re-committed the class they cited, one door over**:

- a failed watchdog unit read as "no watchdog here" (broken → absent) *inside
  the check of record*, while fixing broken-mapped-to-absent elsewhere;
- a session-bus proxy swapped in for a self-confirming signal;
- the anti-drift fix **hand-copying the SSOT chain it existed to unify**.

The 13 uncovered commits are overwhelmingly *fixes and guards written in that
same voice*, by sessions that had that lesson in context. **So the highest-yield
question for every diff hunk is not "is this right?" but "does this fix commit
the class it cites, one door over?"** §4's second candidate is one instance
found in ten minutes of scoping; assume there are more.

Second-order version, from `feedback_verify_the_verification`: several of these
commits claim verification. For each, ask **what artifact was actually observed**
— the live unit, the file on disk, the daemon's own env — versus a
representation of it (a `systemctl show` field, an exit code, a fixture the
author wrote). A claim verified against the author's own fixture is unverified.

---

## §7 Author's declaration on `f51f5106` (this artifact's author wrote it)

Reviewing my own commit here would be the same reasoning grading itself, so
instead: **the three places I was least certain**, stated plainly so the pass
can aim at them.

1. **The non-unanimity rule is a judgment call I made alone.** `silent` needs one
   qualified claw and no claw that heard it — *not* every claw agreeing. I chose
   it because unanimity lets one rebooted claw mask a real finding. But it also
   means **a single misconfigured or mis-tuned claw can produce a finding on its
   own**. I believe the direction is right (fail toward speaking, never toward
   silence) and it is escalate-only so the cost of a false one is a proposal, not
   a page — but it is a trade, not a proof, and nobody has checked my reasoning.
2. **`_fold_watch_verdicts` takes `max()` of `required_window_s` across claws**
   ("widest window wins"). That is defensible but arbitrary; I did not consider
   what happens when one claw reports a *nonsense* window (0, negative, or
   enormous). `0` would make `float(need)` fall to the `or 0.0` branch and the
   max keeps the other claw's value — probably fine, unverified.
3. **The `HEARD/SILENT/UNOBSERVABLE` import has a silent fallback** (`except
   Exception: HEARD, SILENT, UNOBSERVABLE = "heard", "silent", "unobservable"`).
   I argued the coupling is test-pinned (a rename in `claw_rf_watch` breaks my
   fixtures). **Confirm that**, because if it is wrong the probe silently
   classifies every verdict as blind — a fail-dark in the exact shape MF027
   exists to catch, in code that ships MF027's sibling.

Not in doubt, and verified this session — do not re-derive: both mutation drills
(`unobservable`-as-finding → 5 tests fail; unanimity → 1 test fails), the live
disposition inside **moc2's** watchdog daemon (`clean`, "3/5 ... 2 not yet
observable long enough — NOT counted as healthy"), full suite `9978 passed / 1
skipped exit 0`, lint `exit 0`, CI 4/4 success on 3.9 **and** 3.11.

---

## §8 Already measured — quote these, don't re-derive

- Full suite at `f51f5106`: **9978 passed, 1 skipped, exit 0** (via
  `honest_status.sh`, which requires three agreeing signals).
- `lint.py --all`: **exit 0**. `parity_check.py`: **in sync**.
- CI run `30503456058`: 4/4 `success` (Syntax, Lint & Security, Test Suite 3.9,
  Test Suite 3.11).
- `honest_status` at `f51f5106`: **4/6 PASS, 1 FAIL** — the FAIL is `fleet SHA
  drift 1/8`, the expected consequence of a deliberate moc2-only deploy, **not a
  defect in the range**.
- ⚠️ **pytest's exit code is not trustworthy on this fleet** (~50% flap on the
  full suite, mechanism BELIEVED not pinned):
  `.claude/research/pytest_exit_status_flap_2026_07_28.md`. A green run is not by
  itself evidence; that is why the suite leg needs three signals.

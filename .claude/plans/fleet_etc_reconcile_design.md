# Fleet /etc reconcile — design memo

**Status**: design draft, 2026-05-13.
**Author**: drafted with Claude during the morning session that surfaced
the class.
**Build target**: tomorrow's session, after the cost has been slept on.

---

## The class we keep paying for

Every system file in `/etc/` that is *owned by a repo template* needs a
manual `cp` to take effect. `git pull` brings the new template; nothing
reconciles `/etc/`. We have caught this **four times** now, in 30 days:

| date caught | symptom | days dark | reason |
|---|---|---|---|
| 2026-05-13 | `meshforge-backup` HOME-unbound | 27 | template fix never cp'd to volcanoai |
| 2026-05-13 | `meshanchor-daemon` EROFS spam | 7 | sandbox fix never cp'd to volcanoai |
| 2026-05-13 | `meshforge-map` WAL replay risk (4 mocs) | ~6 | WAL-checkpoint fix never cp'd to moc/moc1/moc2/moc3 |
| 2026-05-13 | `meshanchor-daemon-restart.timer` (new band-aid) | n/a | install step still manual on every box |

Each one was found by hand, not by tooling. The
`scripts/fleet_diag_bundle.sh` drift check (added today) catches it
*after* it happens, but the gap between "fix lands in main" and "fix
takes effect on the fleet" is still operator-attention-bound. That's the
hole we want to close.

This memo names the structural fix.

---

## The fix shape

**`fleet_sync.sh` learns to reconcile `/etc/systemd/system/` against
repo-owned templates, with the same `try-restart` discipline already
proven for the `git pull` half.**

Per-box per-unit sequence:

1. Enumerate templates under `{meshforge,meshanchor}/{scripts,templates/systemd}/*.{service,timer}`.
2. For each, build the *target* by substituting placeholders (today: just
   `__MESHFORGE_USER__`; future: small allowlist).
3. Compare to `/etc/systemd/system/<name>` after a **comment-strip** pass
   (drop `#` lines and trailing whitespace before diffing — closes the
   comment-only false-positive class we hit on volcanoai today).
4. If different: `cp` the substituted target into place, `daemon-reload`,
   **and only restart if the unit was `active` BEFORE the cp**. Disabled
   units (like meshforge-map on moc3 — 1 GB Pi, can't run map + gateway)
   must not be re-enabled by reconciliation. This was the regression we
   nearly shipped this morning.
5. Log per-box per-unit: `skipped` (no template) | `unchanged` |
   `reconciled active` | `reconciled inactive`.

---

## Scope (in)

- `*.service` and `*.timer` files under repo template paths only.
- Placeholder substitution: just `__MESHFORGE_USER__` initially.
- Comment-strip: drop full-line `#` comments and trailing whitespace
  before comparison. Leave `#` characters mid-line alone.

## Scope (out — different lifecycles)

- **sudoers.d drop-ins** (just landed `012_meshanchor-status-args` today
  by hand). Per-box NOPASSWD scope diverges intentionally between MF
  fleet boxes and MA-server; reconciling these against a single template
  would clobber intent.
- **udev rules** (`/etc/udev/rules.d/99-meshcore.rules` per box). Tied
  to local device IDs; not repo-owned.
- **`/etc/meshtasticd/config.d/`**. Per-radio HAT config; not repo-owned.
- **`/etc/reticulum/config`**. Carries the `rpc_key` shared with rnsd
  per Issue #41. Per-box state with cross-process invariants. Don't
  touch from a `cp` pipeline.
- **`/etc/hosts`**. Per-box state.

Keep the scope tight. Every item above has bitten us at least once;
expanding the reconciler to cover them is a separate, more careful
design exercise.

---

## Safety levers

These are the load-bearing pieces; rushing them is how we restart all
five boxes simultaneously at 3am after a bad commit.

1. **`--dry-run` flag**. Default behavior of the new sub-command should
   be report-only — show what *would* change, no `cp`, no restart.
   Operators flip a flag to apply. The `fleet_diag_bundle` drift check
   already provides the report half; the reconciler's `--dry-run` is the
   same shape with `cp -n` rather than `cp`.
2. **`active` gate on restart**. Already named above. Use
   `systemctl is-active` *before* `cp`, never `is-enabled`. Disabled
   units stay disabled; inactive-but-enabled units (e.g. cold spares)
   get the new file but no restart.
3. **Restart throttle**. Sequential per-box, with a sleep between hosts
   (`5s` default; `--restart-interval` to override). Prevents the
   "all 5 boxes restart their map service in the same 30 sec window
   while volcanoai's WAL replay is still going" failure mode.
4. **Per-unit opt-out**. `/etc/meshforge/reconcile.skip` — newline-
   separated list of unit names that the reconciler must NEVER touch
   on this host. moc3's `meshforge-map.service` is the first entry; any
   future "I tuned this locally" cases land here.
5. **Refuses loud on substitution failures**. If the template has a
   placeholder we don't know how to resolve, refuse to write — never
   ship an unsubstituted unit into `/etc/`.

---

## Build phases

### Phase A — extract reusable comparison logic from `fleet_diag_bundle.sh`

The drift section in the bundle (placeholder substitution + diff) is
already the right shape. Lift it into `scripts/lib/unit_compare.sh` so
both the bundle and the reconciler share one definition. Add the
comment-strip pass here.

**Tests**: unit-shape — feed it `(template, installed_user_value)`
fixtures, assert reported state.

### Phase B — `scripts/fleet_etc_reconcile.sh` (standalone)

Standalone first, NOT integrated into `fleet_sync.sh`. Lets the operator
run it explicitly, watch behavior, build trust. Same host-list resolution
as `fleet_sync.sh` and `fleet_diag_bundle.sh`.

```
scripts/fleet_etc_reconcile.sh                       # dry-run, all hosts
scripts/fleet_etc_reconcile.sh --apply               # cp + daemon-reload + try-restart
scripts/fleet_etc_reconcile.sh --apply --hosts moc1  # narrow scope
```

**Tests**: end-to-end on a temp-host fixture; assert no `cp` in dry-run,
correct skip on inactive, correct throttle on `--apply`.

### Phase C — `fleet_sync.sh` integration

Only after Phase B has been run a few times and gained the operator's
trust. Add a `--reconcile` flag to `fleet_sync.sh` that opts in (NOT
default — we don't want every routine `fleet_sync` mass-restarting
services). Pre-commit hook could surface a `git diff` against templates
to remind the developer "you touched a unit file; run `--reconcile`."

### Phase D (deferred) — broaden scope

Only if Phases A-C bed in and we discover the structural answer was the
right one. Sudoers, udev, etc. each get their own design review.

---

## Why this isn't just another band-aid

The consolidation test from `feedback_consolidate_dont_add.md`:
> AI surfaces should *replace*, not *add*. Don't recommend new tools
> unless they retire existing work the operator is actually doing.

This **retires the manual `cp` step** that has now failed four times in
30 days. It doesn't add a new surface; it closes a gap that operator
attention has been holding open. The reconciler doesn't replace
`fleet_sync.sh` — it completes it.

It also is *not* a continuous watchdog (T2 from `project_cmd_diag_analyzer_roadmap.md`).
It runs on-demand or after `git pull`, not on a timer. Steady-state load
is zero — same design constraint that bounded `fleet_diag_bundle.sh`,
same `project_pi_load_tolerance_optimization.md` budget.

---

## What NOT to build (scope creep watch)

- **Don't** generalize to "any system file". Sudoers/udev/config files
  are not the same class; reconciling them needs different invariants.
  Resist the urge to expand Phase B's scope mid-build.
- **Don't** add a web UI. The drift check already lives in the bundle's
  markdown output; CLI is enough.
- **Don't** wire it to git hooks initially. Reconciliation is a sudo-gated
  action; coupling it to git events introduces auth/timing problems we
  haven't budgeted for.
- **Don't** chase the residual drift items we found today (comment-only
  drift on volcanoai's `meshforge-map.service`, untouched
  `meshforge.service` drift on 4 boxes) one at a time. Build the
  reconciler; let it sweep them up cleanly in dry-run first.

---

## Cross-references

- `feedback_consolidate_dont_add.md` — the consolidation test this fix passes.
- `project_cmd_diag_analyzer_roadmap.md` — T1 backlog already names the
  fleet_sync over-restart problem; this design folds in.
- `project_pi_load_tolerance_optimization.md` — steady-state load budget.
- `project_meshforge_map_cold_start_wal.md` — case study #3 (the WAL
  fix that didn't propagate).
- `feedback_architectural_fixes.md` — "When a primitive is wrong, replace
  it; don't patch." `fleet_sync.sh` minus reconciliation is the wrong
  primitive; this memo replaces it.

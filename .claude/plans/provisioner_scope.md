# Scope: role-aware fleet provisioner

> **Status:** scope/design draft (2026-05-24). Turns `docs/fleet_roles.yaml` from
> documentation into control: bring *this* box to its declared role state,
> idempotently, fail-loud, dry-run-first. The highest-leverage step toward the
> 1.0 reproducibility gate (see `docs/fleet_manifest.md`).

## Problem

Today a box reaches its role by hand: install the stack, then apply tribal
deltas (enable/disable the right units, mask a rival RNS host, set want_ack,
set bbox/cap, put cloud-push on exactly one node). That's the reproducibility
debt — it lives in the operator's head, in Claude memory, and in this session's
fixes. `fleet_roles.yaml` now *declares* the target; nothing yet *enforces* it.

## Goals

- **Converge** the local box to its role's declared state from `fleet_roles.yaml`:
  systemd unit states (`enabled`/`disabled`/`absent`) + role deltas + cross-cutting
  defaults.
- **Idempotent**: safe to run repeatedly; only acts on drift.
- **Dry-run by default**: prints the diff (current → desired, per item); mutates
  only with `--apply`. (Same posture as watchdog Phase 2.)
- **Fail loud**: a required daemon missing → exit non-zero with the exact install
  command, never a silent fallback.
- **Reuse, don't reinvent**: service ops go through the `service_check.py` SSOT;
  package install stays `install_noc.sh`'s job.

## Non-goals (v1)

- Not a package installer — assumes base install is present; for an `absent`
  required daemon it *reports the command*, doesn't run it (unless `--install`).
- Not fleet-wide orchestration — converges the *local* box only. Singleton
  invariants (one `primary`, one `cloud-publisher`) get a *local* assertion plus a
  flagged cross-box check; full enforcement is v2.
- Not the MeshAnchor (`meshanchor-noc`) box in v1 — that host has no
  `/opt/meshforge`; either port the tool to MeshAnchor or handle it there (open
  question below).

## Design

### Inputs
- `docs/fleet_roles.yaml` — committed role catalog (the desired state).
- **Role assignment** (instance-local, not committed): add `role: <name>` to
  `~/.config/meshforge/deployment.json` (today it carries only `profile`). The
  provisioner reads `role`, resolves `inherits`, and merges `defaults`.

### Convergence model (a pure plan, then an apply)
1. **Load + resolve** the role (apply `inherits`, overlay `defaults`) → a flat
   desired-state dict: `{units: {name: enabled|disabled|absent}, deltas: [...]}`.
2. **Observe** current state via `service_check.check_service()` per unit + config
   reads.
3. **Diff** → ordered list of `Action(item, current, desired, kind)`.
4. **Render** the diff (always). If `--apply`: execute each action through the
   SSOT helpers; re-observe; report result. Exit non-zero on any failed required
   action.

This pure-plan/then-apply split is what makes dry-run trivial and the engine
unit-testable without touching systemd.

### Actions the engine performs
| Desired | Action (via SSOT) |
|---------|-------------------|
| unit `enabled` | `enable_service` + `start_service` if not active |
| unit `disabled` | `stop_service` + disable (present-but-off; not a fault) |
| unit `absent`, present | **report** (warn) — don't auto-remove |
| unit `absent`, missing | no-op (satisfied) |
| `masking_rule` matches | `systemctl mask` the rival RNS host (NEW helper — see gaps) |
| `meshtastic_egress.want_ack` | set in `gateway.json` if drifted |
| map-role `defaults` | assert bbox/cap/operator-position set + response caches on; warn if unset |
| `cloud-publisher` | enable cloud-push timer **+ singleton check** |

### Safety (project rules — non-negotiable)
- Service state **only** via `service_check.py` (`check_service`, `enable_service`,
  `start_service`, `stop_service`, `apply_config_and_restart`) — never raw
  `systemctl` (MF008/lint). A `mask` helper must be added there.
- `RNS.Reticulum()` never touched here; we only mask/enable units.
- `subprocess` with `timeout=` (MF004), no `shell=True` (MF002), validate inputs.
- Config edits: read-modify-write with a backup + post-write validate; never
  clobber (cf. the meshtasticd `config.yaml` rule).
- `get_real_user_home()` for `~/.config` paths (MF001).

### Output
A convergence report in the `fleet_sync` style — one line per item
(`PASS/CHANGE/WOULD-CHANGE/WARN/FAIL <item> <current>→<desired>`), a summary
count, non-zero exit if any required item failed. Operator-legible, greppable.

## Phasing

- **v1 — local converge (this scope):** plan + diff + `--apply`, unit-state +
  the four concrete deltas, dry-run default, SSOT service ops, `role` in
  `deployment.json`. Standalone: `sudo python3 scripts/provision_role.py [--apply]`.
- **v2 — fleet-aware:** singleton enforcement across `fleet_hosts`; `fleet_sync`
  optionally invokes `provision_role.py --apply` after a code sync; MeshAnchor
  port for `meshanchor-noc`.
- **v3 — closes the loop with the watchdog:** drift detected at runtime →
  provisioner re-converges (this is the "encode judgment into the system" step;
  pairs with watchdog auto-remediation off dry-run).

## Testing

- Unit: YAML→desired-state resolution (incl. `inherits`/`defaults` merge); diff
  engine against mocked `check_service`; each action maps to the right SSOT call
  (mocked). Pin `gateway-only` map=disabled, `cloud-publisher` singleton, mask
  rule.
- Dry-run integration on a live box: `provision_role.py` (no `--apply`) must
  produce an empty/clean diff on an already-correct node and a non-empty diff on a
  deliberately-drifted one — and **mutate nothing**.
- Regression guard: lint MF002/MF004/MF008 over the new script.

## Gaps / new pieces required

1. `mask`/`unmask` helpers in `service_check.py` (today it has enable/start/stop,
   not mask) — masking is now a documented invariant, so it belongs in the SSOT.
2. `role` field + a tiny loader/validator for `deployment.json`.
3. Decision: does the provisioner ever shell to `install_noc.sh` for an `absent`
   required daemon (`--install`), or always defer to the operator? Lean: report in
   v1, `--install` opt-in later.

## Open questions

- **MeshAnchor box:** port the provisioner (and ship `fleet_roles.yaml`) to the
  MeshAnchor repo, or keep `meshanchor-noc` provisioned MeshAnchor-side? (It's a
  different repo/host with no `/opt/meshforge`.)
- **Singleton source of truth:** assert via a flag in `deployment.json`
  (`role: primary` is inherently singleton) + a cross-box `fleet_hosts` probe, or
  a dedicated fleet manifest field? v1 asserts locally + warns.
- **Config-delta verification vs enforcement:** for the map `defaults`
  (bbox/cap/position), should v1 *set* them or only *assert + warn*? Lean: assert
  + warn in v1 (operator-position is deployment-specific), enforce caches/caps.

## Effort (rough)

- v1: ~1 session — `scripts/provision_role.py` (~300–400 lines) + the `mask`
  SSOT helper + `deployment.json` `role` loader + unit tests + a dry-run pass on
  one box. No service disruption (dry-run first).

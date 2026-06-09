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
- Not the MeshAnchor (`meshanchor-noc`) box — that host has no `/opt/meshforge`.
  Resolved: it's marked `provisioned_by: meshanchor` and the provisioner skips it
  (see Resolved decisions).

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

- **v1 — local converge (SHIPPED 2026-05-24):** `scripts/provision_role.py` —
  plan/diff/`--apply` (dry-run default), unit-state convergence + masking
  invariant via the `service_check` SSOT (added `mask_service`/`unmask_service`/
  `is_service_masked`), config deltas as advisories, external-role skip, `--set-role`.
  23 unit tests; validated on a live box (clean role → exit 0 all PASS; drifted
  role → exit 1 WOULD-CHANGE, mutates nothing). Run:
  `sudo python3 scripts/provision_role.py [--apply]`.
- **v2 — fleet-aware (SHIPPED 2026-05-24):** `--print-role`, `validate_fleet`
  (pure singleton/role check), `--fleet-check` (gathers peer roles over a
  configurable `$MESHFORGE_SSH`, validates, prints the fleet role table + exits
  nonzero on violation). All five MeshForge boxes assigned roles via `--set-role`
  (fleet is now self-describing: primary / full-gateway×2 / cloud-publisher /
  gateway-only); live `--fleet-check` reports invariants OK. 32 tests.
  `meshanchor-noc` resolved as external (skipped) — see Resolved decisions.
- **v2.1 — fleet_sync hook (SHIPPED 2026-05-24):** `fleet_sync.sh` runs
  `--fleet-check` after the per-host + self-sync work, surfacing role/singleton
  drift in the sync report. Read-only + advisory (counts as a warn, never fails
  the sync or mutates); opt out with `MESHFORGE_SKIP_ROLE_CHECK=1`. Verified
  live (full role table + invariants OK, 0 warn).
- **v3 — closes the loop with the watchdog (SHIPPED 2026-06-08 `6f0d4b2`, GATED
  OFF):** drift detected at runtime → provisioner re-converges. The `role_drift`
  signal (`probe_role_drift`) + Phase-2 auto-remediation machinery (7 gates,
  dry-run-first) already existed; v3 generalized Phase 2 from one `systemctl
  restart` action to a pluggable remediation — `AutoRestartRule.action`
  (`"restart"`|`"reconverge"`) + `execute_reconverge()` (runs `provision_role.py
  --apply`; its non-zero exit on a failed apply, incl. a sandbox-blocked
  foundation chown, is the honest success signal). Same gates. Ships **inert**
  (no reconverge rule on any box → zero runtime change); operator opts in
  per-box in `watchdog.json` dry-run-first (`cooldown_s=1800, max=1/h`,
  re-applies the whole role). 8 tests. This is the "encode judgment into the
  system" step.

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

## Resolved decisions

- **MeshAnchor box (2026-05-24):** the MeshForge provisioner owns **MeshForge
  roles only**. `meshanchor-noc` stays in `fleet_roles.yaml` for the complete
  fleet topology but is marked `provisioned_by: meshanchor`; the provisioner
  **skips** any role with a `provisioned_by` key. Rationale: porting a tool that
  doesn't exist yet is premature; sister-repo duplication is a known drift hazard;
  the MeshAnchor host is one box with a small, stable, already-documented config
  (want_ack shipped; rnsd legitimately owns the listener, daemon is a client — no
  masking there). **Forward design:** the engine is built repo-agnostic (reads a
  roles file + uses the local repo's `service_check` SSOT) so MeshAnchor can adopt
  it later by shipping the engine + its own roles file — shared-contract pattern,
  low drift. MeshAnchor self-provisioning is a separate future workstream.

## Open questions

- **Singleton source of truth:** assert via a flag in `deployment.json`
  (`role: primary` is inherently singleton) + a cross-box `fleet_hosts` probe, or
  a dedicated fleet manifest field? v1 asserts locally + warns.
- **Config-delta verification vs enforcement:** ~~for the map `defaults`
  (bbox/cap/position), should v1 *set* them or only *assert + warn*?~~ **RESOLVED
  2026-06-08 (`b84023f`): ASSERT, because the map defaults are NOT settable.**
  Ground-truth: `response_caches` are unconditional in `MapDataCollector` (#70/#71),
  `node_cap` is the `DEFAULT_DIRECTORY_MAX_ROWS` code constant (#49/#50), `bbox`
  auto-derives from the operator position (deployment-specific). So "enforce
  caches/caps" can't mean *set* — they're code-baked. `config_delta_actions()`
  asserts them against real state (read-only even under `--apply`): a code↔yaml
  `node_cap` cross-check (catches declaration drift), response-cache presence, and
  a bbox geo-anchor check. The ONE force-settable delta — meshtasticd `mqtt.root`
  (#77) — is a radio-touching enforcement for its own slice (PhoneAPI/#17).

## Effort (rough)

- v1: ~1 session — `scripts/provision_role.py` (~300–400 lines) + the `mask`
  SSOT helper + `deployment.json` `role` loader + unit tests + a dry-run pass on
  one box. No service disruption (dry-run first).

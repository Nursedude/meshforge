#!/usr/bin/env python3
"""Role-aware fleet provisioner (v1) — converge THIS box to its declared role.

Reads the declarative role catalog (`docs/fleet_roles.yaml`) and the box's role
(`~/.config/meshforge/deployment.json` → `role`), then brings local systemd unit
state into line with the role's declaration. Idempotent, dry-run by default,
fail-loud, and reuses the `utils.service_check` SSOT for every systemd
operation (no raw systemctl here — MF008).

  v1 scope (see .claude/plans/provisioner_scope.md):
    - unit states: enabled | disabled | absent
    - masking invariant: rival RNS host masked on a box that owns rnsd (Issue #69)
    - config deltas (bbox/cap/caches): ASSERTED against real state — code<->yaml
      node_cap cross-check, response-cache presence, bbox geo-anchor. The map
      defaults are code-baked / deployment-specific, NOT operator-settable, so
      this converges by assertion not mutation (see config_delta_actions). The
      one force-settable delta (meshtasticd mqtt.root #77) is a separate slice.
    - external roles (provisioned_by:*) and singletons: reported, not enforced

Usage:
    python3 scripts/provision_role.py                 # dry-run: print the diff
    sudo python3 scripts/provision_role.py --apply     # converge
    python3 scripts/provision_role.py --role full-gateway   # override role
    python3 scripts/provision_role.py --set-role primary    # write role, exit

Exit codes: 0 = converged/clean, 1 = drift (dry-run) or apply failure, 2 = config error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

from utils.paths import get_real_user_home  # noqa: E402
from utils.service_check import (  # noqa: E402
    check_systemd_service,
    is_service_unit_installed,
    is_service_masked,
    enable_service,
    disable_service,
    stop_service,
    mask_service,
)

DEFAULT_ROLES_FILE = _SCRIPT_DIR.parent / "docs" / "fleet_roles.yaml"
DEPLOYMENT_JSON = get_real_user_home() / ".config" / "meshforge" / "deployment.json"
FLEET_HOSTS = get_real_user_home() / ".config" / "meshforge" / "fleet_hosts"
# Remote role-gathering shells out to ssh; the command is operator-configurable
# via $MESHFORGE_SSH (no key/host hardcoded here — MF014). The operator's ssh
# config/agent normally provides auth, so the default bare "ssh" works.
SSH_CMD = os.environ.get("MESHFORGE_SSH", "ssh")

# RNS hosts that must NEVER own the listener on a box that runs rnsd (one rnsd
# per box — Issue #69). Narrow, explicit list; mask each if present + unmasked.
KNOWN_RNS_RIVALS = ("meshanchor-daemon",)

VALID_UNIT_STATES = {"enabled", "disabled", "absent"}


@dataclass
class Action:
    """One convergence step (planned, possibly applied)."""
    item: str
    current: str
    desired: str
    verb: str          # noop | enable | disable | mask | warn
    required: bool = True
    detail: str = ""
    result: str = ""   # filled on apply


# The state-changing verbs plan() can emit — THE shared constant for every
# consumer that filters a plan into a change set (this script's main(),
# the TUI fleet_provision core, probe_role_drift's test-pin). Three
# independent hardcodes of this tuple diverged once already
# (honest_failure_modes #5). foundation_actions() adds 'foundation'
# separately — it is not a plan() verb.
PLAN_CHANGE_VERBS = ("enable", "disable", "mask")


# --------------------------------------------------------------------------
# Role resolution (pure)
# --------------------------------------------------------------------------

def load_roles(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "roles" not in data:
        raise ValueError(f"{path}: missing top-level 'roles'")
    return data


def resolve_role(catalog: dict, role: str) -> dict:
    """Flatten a role to its effective definition, applying `inherits`.

    Returns the role dict with `services` merged parent→child. Raises KeyError
    for an unknown role.
    """
    roles = catalog["roles"]
    if role not in roles:
        raise KeyError(role)
    node = roles[role]
    services: Dict[str, str] = {}
    parent = node.get("inherits")
    if parent:
        services.update(resolve_role(catalog, parent).get("services", {}))
    services.update(node.get("services", {}) or {})
    merged = dict(node)
    merged["services"] = services
    return merged


# --------------------------------------------------------------------------
# Observe + diff (pure given the observe callbacks)
# --------------------------------------------------------------------------

def _unit_current(name: str) -> str:
    """Human-readable current state of a unit, via the SSOT."""
    if is_service_masked(name):
        return "masked"
    running, enabled = check_systemd_service(name)
    if not is_service_unit_installed(name) and not running and not enabled:
        return "absent"
    return f"{'active' if running else 'inactive'}/{'enabled' if enabled else 'disabled'}"


def plan(role_def: dict, overrides: Optional[Dict[str, dict]] = None) -> List[Action]:
    """Build the ordered action list to converge to `role_def`. Pure w.r.t.
    the SSOT observe functions (which read the live system).

    `overrides` is the box's instance-local `service_overrides` (from
    deployment.json): per-unit intentional exceptions to the role's service
    map. A waived unit is reported as a NON-blocking advisory carrying the
    reason — visible and auditable, never silently dropped — and is skipped by
    convergence (left as the operator set it). A waiver WITHOUT a `reason` is
    NOT honored (it stays a blocking warning) — an unexplained exception is
    just hidden drift.
    """
    actions: List[Action] = []
    services: Dict[str, str] = role_def.get("services", {})
    overrides = overrides or {}

    for unit, desired in services.items():
        ov = overrides.get(unit)
        if ov is not None:
            reason = (ov.get("reason") or "").strip() if isinstance(ov, dict) else ""
            waived = ov.get("state", "?") if isinstance(ov, dict) else str(ov)
            cur = _unit_current(unit)
            if reason:
                actions.append(Action(unit, cur, f"waived:{waived}", "warn",
                                      required=False,
                                      detail=f"intentional per-node exception: {reason}"))
            else:
                actions.append(Action(unit, cur, f"waived:{waived}", "warn",
                                      required=True,
                                      detail="service_override missing required 'reason' "
                                             "— NOT honored (an unexplained waiver is "
                                             "hidden drift)"))
            continue
        if desired not in VALID_UNIT_STATES:
            actions.append(Action(unit, "?", str(desired), "warn", required=False,
                                  detail=f"unknown desired state '{desired}'"))
            continue
        running, enabled = check_systemd_service(unit)
        installed = is_service_unit_installed(unit) or is_service_masked(unit)
        cur = _unit_current(unit)

        if desired == "enabled":
            if not installed:
                actions.append(Action(unit, "absent", "enabled", "warn",
                                      detail=f"required unit not installed — run install_noc.sh"))
            elif running and enabled:
                actions.append(Action(unit, cur, "enabled", "noop"))
            else:
                actions.append(Action(unit, cur, "enabled", "enable"))
        elif desired == "disabled":
            if not installed:
                actions.append(Action(unit, "absent", "disabled", "noop"))
            elif running or enabled:
                actions.append(Action(unit, cur, "disabled", "disable"))
            else:
                actions.append(Action(unit, cur, "disabled", "noop"))
        elif desired == "absent":
            if installed:
                actions.append(Action(unit, cur, "absent", "warn", required=False,
                                      detail="present but role declares absent (not auto-removed)"))
            else:
                actions.append(Action(unit, "absent", "absent", "noop"))

    # Masking invariant: this box owns rnsd → mask any installed rival RNS host.
    if services.get("rnsd") == "enabled":
        for rival in KNOWN_RNS_RIVALS:
            if is_service_masked(rival):
                actions.append(Action(f"mask:{rival}", "masked", "masked", "noop"))
            elif is_service_unit_installed(rival):
                actions.append(Action(f"mask:{rival}", "present", "masked", "mask",
                                      detail="rival RNS host on an rnsd box — Issue #69 invariant"))

    # Config deltas (bbox/cap/caches) are asserted against real state in
    # config_delta_actions() (appended in main, parallel to foundation_actions) —
    # they are cross-cutting map `defaults`, not per-role unit state.
    if role_def.get("singleton"):
        actions.append(Action("invariant:singleton", "?", "unique-in-fleet", "warn",
                              required=False,
                              detail="this role must be unique across the fleet — verify no other box claims it"))
    return actions


def foundation_actions() -> List[Action]:
    """Cross-cutting permission-foundation converge step (D1/D3, mf.4/#73).

    Role-independent: EVERY MeshForge box runs its services as the non-root
    operator user and must own the data it writes plus its RNS config tree, so
    this is appended to every converge regardless of role. Drift → one
    `foundation` action that, on --apply, runs the shared
    `fleet_foundation.apply_foundation` (data-root chowns + the rns_tree_perms
    RNS-tree apply-path — the single apply-path). Clean → noop. The recurrence
    this continuously catches: a re-provision that recreates /etc/reticulum
    root:root while rnsd runs non-root (the moc1/moc2 mf.4 trigger, 2026-06-01).
    """
    try:
        from utils.fleet_foundation import audit_foundation
        drift = audit_foundation()
    except Exception as e:  # never let the foundation probe sink the converge
        return [Action("foundation:perms", "?", "operator-owned", "warn",
                       required=False, detail=f"foundation audit skipped: {e}")]
    if not drift:
        return [Action("foundation:perms", "operator-owned", "operator-owned", "noop")]
    detail = "; ".join(drift)
    if len(detail) > 300:
        detail = detail[:297] + "..."
    return [Action("foundation:perms", f"{len(drift)} drift item(s)",
                   "operator-owned", "foundation", required=True, detail=detail)]


def _has_geo_anchor() -> "tuple[bool, str]":
    """Does this box have a geographic anchor for the directory bbox filter?

    The filter is effective only with EITHER an explicit
    ``map_settings.external_bulk_bbox`` OR an ``operator_position.json`` (the bbox
    auto-derives from the latter; no anchor => the directory serves the unfiltered
    firehose). Read straight from the real user's config (MF001) — no
    SettingsManager so a sudo invocation can't read root's home by mistake.
    """
    cfg = get_real_user_home() / ".config" / "meshforge"
    if (cfg / "operator_position.json").exists():
        return True, "operator_position.json"
    ms = cfg / "map_settings.json"
    try:
        if ms.exists() and json.loads(ms.read_text()).get("external_bulk_bbox"):
            return True, "external_bulk_bbox"
    except (json.JSONDecodeError, OSError):
        pass
    return False, "none"


def config_delta_actions(role_def: dict, defaults: dict) -> List[Action]:
    """Assert the cross-cutting map `defaults` (fleet_roles.yaml) against real
    state — MAP-running roles only. Parallel to foundation_actions().

    GROUND-TRUTH (2026-06-08): the declared map defaults are NOT operator-settable
    config; they are baked into code (the "software fits the box" invariant) or
    deployment-specific:
      - response_caches  : instantiated unconditionally in MapDataCollector (#70/#71)
      - node_cap         : the DEFAULT_DIRECTORY_MAX_ROWS code constant (#49/#50)
      - bbox_filter      : effective only with a geo-anchor (operator position) — deployment-specific
    So this converges by ASSERTION not mutation: read the real values, flag genuine
    drift (a code constant that no longer matches the declared default — a
    reproducibility hazard) or a missing deployment input (no anchor => bbox off =>
    unfiltered directory). The one force-settable delta (meshtasticd mqtt.root #77)
    is a radio-touching enforcement deferred to its own slice.
    """
    actions: List[Action] = []
    if role_def.get("services", {}).get("meshforge-map") != "enabled":
        return actions
    nd = (defaults or {}).get("node_directory", {})

    # node_cap: code constant vs declared default — catches code<->yaml drift.
    declared_cap = nd.get("node_cap")
    if declared_cap is not None:
        try:
            from utils.node_history import DEFAULT_DIRECTORY_MAX_ROWS as code_cap
        except Exception as e:
            actions.append(Action("delta:node-cap", "?", str(declared_cap), "warn",
                                  required=False, detail=f"node_history read failed: {e}"))
        else:
            verb = "noop" if code_cap == declared_cap else "warn"
            detail = "" if verb == "noop" else (
                "code DEFAULT_DIRECTORY_MAX_ROWS drifted from the declared node_cap — "
                "reconcile node_history.py and fleet_roles.yaml")
            actions.append(Action("delta:node-cap", str(code_cap), str(declared_cap),
                                  verb, required=False, detail=detail))

    # response_caches: code-always-on; assert the layer is importable.
    if (defaults or {}).get("response_caches"):
        try:
            from utils._response_byte_cache import ResponseByteCache  # noqa: F401
            actions.append(Action("delta:response-caches", "code-always-on",
                                  "directory+geojson+topology", "noop",
                                  detail="unconditional in MapDataCollector (#70/#71)"))
        except Exception as e:
            actions.append(Action("delta:response-caches", "?", "present", "warn",
                                  required=False, detail=f"ResponseByteCache import failed: {e}"))

    # bbox_filter effectiveness: needs a geo-anchor (deployment-specific).
    if nd.get("bbox_filter") and nd.get("operator_position_required"):
        anchored, how = _has_geo_anchor()
        if anchored:
            actions.append(Action("delta:bbox-anchor", how, "anchored", "noop"))
        else:
            actions.append(Action("delta:bbox-anchor", "none", "anchored", "warn",
                                  required=False,
                                  detail="no operator_position.json and no map_settings."
                                         "external_bulk_bbox — bbox_filter is OFF, the "
                                         "directory serves the unfiltered firehose"))
    return actions


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

def apply_action(a: Action) -> bool:
    """Execute one action via the SSOT. Returns success. 'warn'/'noop' never act."""
    if a.verb in ("noop", "warn"):
        a.result = "skipped" if a.verb == "warn" else "ok"
        return True
    if a.verb == "enable":
        ok, msg = enable_service(a.item, start=True)
    elif a.verb == "disable":
        ok1, m1 = stop_service(a.item)
        ok2, m2 = disable_service(a.item)
        ok, msg = (ok1 and ok2), f"{m1}; {m2}"
    elif a.verb == "mask":
        ok, msg = mask_service(a.item.split("mask:", 1)[1])
    elif a.verb == "foundation":
        try:
            from utils.fleet_foundation import apply_foundation
            executed = apply_foundation()
            ok, msg = True, f"applied {len(executed)} foundation step(s)"
        except Exception as e:
            ok, msg = False, f"foundation apply failed: {e}"
    else:
        ok, msg = False, f"unknown verb {a.verb}"
    a.result = msg
    return ok


# --------------------------------------------------------------------------
# deployment.json role
# --------------------------------------------------------------------------

def read_role() -> Optional[str]:
    if not DEPLOYMENT_JSON.exists():
        return None
    try:
        return json.loads(DEPLOYMENT_JSON.read_text()).get("role")
    except (json.JSONDecodeError, OSError):
        return None


def read_overrides() -> Dict[str, dict]:
    """Instance-local per-unit exceptions from deployment.json `service_overrides`.

    Shape: ``{"<unit>": {"state": "disabled"|"absent"|..., "reason": "<why>"}}``.
    Instance specifics (which box, why) live HERE, never in the committed roles
    file (MF014/MF015). Honored by ``plan()``; a waiver without a ``reason`` is
    rejected there.
    """
    if not DEPLOYMENT_JSON.exists():
        return {}
    try:
        ov = json.loads(DEPLOYMENT_JSON.read_text()).get("service_overrides") or {}
    except (json.JSONDecodeError, OSError):
        return {}
    return ov if isinstance(ov, dict) else {}


def write_role(role: str) -> None:
    """Merge the role into deployment.json — never clobber other keys.

    An existing-but-unreadable file is a refuse-loud error: silently
    resetting it would destroy ``service_overrides`` and the deployment
    profile (a torn file after a power event is this fleet's known
    class, and the old non-atomic write here was itself a torn-file
    source). Atomic write + ownership fixed back to the operator when
    invoked under sudo (the TUI apply path is root; a root-created
    deployment.json breaks every later user-mode writer, MF001 class).
    """
    from utils.paths import atomic_write_text
    DEPLOYMENT_JSON.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if DEPLOYMENT_JSON.exists():
        try:
            data = json.loads(DEPLOYMENT_JSON.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise RuntimeError(
                f"{DEPLOYMENT_JSON} exists but is unreadable ({e}) — "
                f"refusing to overwrite it: that would silently destroy "
                f"service_overrides and the deployment profile. Inspect "
                f"or remove the file, then retry.") from e
        if not isinstance(data, dict):
            raise RuntimeError(
                f"{DEPLOYMENT_JSON} is not a JSON object "
                f"({type(data).__name__}) — refusing to overwrite; "
                f"inspect the file.")
    data["role"] = role
    atomic_write_text(DEPLOYMENT_JSON, json.dumps(data, indent=2))
    from utils.paths import chown_to_operator
    chown_to_operator(DEPLOYMENT_JSON.parent, DEPLOYMENT_JSON)


# --------------------------------------------------------------------------
# Fleet-aware: singleton enforcement across fleet_hosts (v2)
# --------------------------------------------------------------------------

def parse_fleet_hosts(path: Path) -> List[str]:
    """Return the host list from a fleet_hosts file (one per line, '#' comments)."""
    if not path.exists():
        return []
    hosts = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            hosts.append(line)
    return hosts


def validate_fleet(catalog: dict, role_map: Dict[str, Optional[str]]) -> List[str]:
    """Pure check of a {host: role} assignment against the catalog.

    Flags: a `singleton: true` role claimed by more than one host; a role name
    not in the catalog. Hosts with no role (None) are reported by the caller,
    not treated as a violation here. Returns a list of human-readable violations
    (empty == valid).
    """
    roles = catalog.get("roles", {})
    violations: List[str] = []

    # Unknown role names
    for host, role in role_map.items():
        if role and role not in roles:
            violations.append(f"{host}: unknown role '{role}' (not in catalog)")

    # Singleton uniqueness
    for rname, rdef in roles.items():
        if not rdef.get("singleton"):
            continue
        claimants = [h for h, r in role_map.items() if r == rname]
        if len(claimants) > 1:
            violations.append(
                f"singleton role '{rname}' claimed by {len(claimants)} hosts: "
                f"{', '.join(sorted(claimants))} (must be exactly one)"
            )
    return violations


def gather_fleet_roles(
    hosts: List[str], self_role: Optional[str], ssh_cmd: str = SSH_CMD
) -> Dict[str, Optional[str]]:
    """Collect {host: role} for the fleet. Self comes from the local role; each
    peer is queried with ``<ssh_cmd> <host> python3 <repo>/scripts/provision_role.py
    --print-role``. Unreachable/role-less peers map to None.
    """
    import subprocess
    role_map: Dict[str, Optional[str]] = {"(self)": self_role}
    remote = "python3 /opt/meshforge/scripts/provision_role.py --print-role"
    for host in hosts:
        argv = ssh_cmd.split() + [host, remote]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            role_map[host] = (r.stdout.strip() or None) if r.returncode == 0 else None
        except (subprocess.SubprocessError, OSError):
            role_map[host] = None
    return role_map


# --------------------------------------------------------------------------
# Render + main
# --------------------------------------------------------------------------

_SYM = {"noop": "PASS", "enable": "CHANGE", "disable": "CHANGE", "mask": "CHANGE",
        "foundation": "CHANGE", "warn": "WARN"}


def render(actions: List[Action], apply: bool) -> None:
    for a in actions:
        tag = _SYM.get(a.verb, a.verb.upper())
        if not apply and a.verb not in ("noop", "warn"):
            tag = "WOULD-" + tag
        line = f"[{tag:11}] {a.item}: {a.current} -> {a.desired}"
        if a.detail:
            line += f"  ({a.detail})"
        if apply and a.result and a.verb not in ("noop", "warn"):
            line += f"  => {a.result}"
        print(line)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Converge this box to its fleet role.")
    p.add_argument("--apply", action="store_true", help="execute changes (default: dry-run)")
    p.add_argument("--role", help="override role (else read from deployment.json)")
    p.add_argument("--roles-file", type=Path, default=DEFAULT_ROLES_FILE)
    p.add_argument("--set-role", help="write role into deployment.json and exit")
    p.add_argument("--print-role", action="store_true",
                   help="print this box's assigned role and exit (machine-readable)")
    p.add_argument("--fleet-check", action="store_true",
                   help="gather roles across fleet_hosts and validate singleton invariants")
    args = p.parse_args(argv)

    if args.print_role:
        print(read_role() or "")
        return 0

    if args.set_role:
        # write_role now REFUSES (raises) on a torn/unreadable deployment.json
        # rather than clobbering it — surface that as the codebase's clean
        # "could not proceed" exit 2, not an uncaught traceback.
        try:
            write_role(args.set_role)
        except (RuntimeError, OSError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        print(f"role set to '{args.set_role}' in {DEPLOYMENT_JSON}")
        return 0

    try:
        catalog = load_roles(args.roles_file)
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"ERROR loading {args.roles_file}: {e}", file=sys.stderr)
        return 2

    if args.fleet_check:
        hosts = parse_fleet_hosts(FLEET_HOSTS)
        role_map = gather_fleet_roles(hosts, read_role())
        print("# fleet role assignment")
        for host, role in role_map.items():
            print(f"  {host:24} {role or '(unset/unreachable)'}")
        violations = validate_fleet(catalog, role_map)
        if violations:
            print("# VIOLATIONS:")
            for v in violations:
                print(f"  ! {v}")
            return 1
        print("# fleet invariants OK (singletons unique, roles known)")
        return 0

    role = args.role or read_role()
    if not role:
        print("ERROR: no role. Set one with --set-role <name> or pass --role <name>.",
              file=sys.stderr)
        print(f"  available: {', '.join(catalog['roles'])}", file=sys.stderr)
        return 2

    try:
        role_def = resolve_role(catalog, role)
    except KeyError:
        print(f"ERROR: unknown role '{role}'. available: {', '.join(catalog['roles'])}",
              file=sys.stderr)
        return 2

    if role_def.get("provisioned_by"):
        print(f"role '{role}' is EXTERNAL (provisioned_by: {role_def['provisioned_by']}) "
              f"— the MeshForge provisioner does not converge it.", file=sys.stderr)
        return 2

    overrides = read_overrides()
    print(f"# role: {role}  (mode: {'APPLY' if args.apply else 'dry-run'})")
    if overrides:
        print(f"# service_overrides active: {', '.join(sorted(overrides))}")
    actions = plan(role_def, overrides)
    # Cross-cutting permission foundation (D1/D3) — appended to every converge,
    # role-independent. Continuously enforces the born-correct perms (mf.4/#73).
    actions += foundation_actions()
    # Cross-cutting map `defaults` (bbox/cap/caches) — asserted against real
    # state for MAP-running roles (code-baked / deployment-specific, not mutated).
    actions += config_delta_actions(role_def, catalog.get("defaults", {}))
    render(actions, args.apply)

    changes = [a for a in actions
               if a.verb in PLAN_CHANGE_VERBS + ("foundation",)]
    fail_warns = [a for a in actions if a.verb == "warn" and a.required]

    if args.apply:
        failed = []
        for a in changes:
            if not apply_action(a):
                failed.append(a)
        # re-render results
        if changes:
            print("# --- results ---")
            render(changes, apply=True)
        n_fail = len(failed) + len(fail_warns)
        print(f"# summary: {len(changes)} change(s), {len(failed)} failed, "
              f"{len(fail_warns)} blocking warning(s)")
        return 1 if n_fail else 0

    # dry-run
    print(f"# summary: {len(changes)} would-change, {len(fail_warns)} blocking warning(s), "
          f"{sum(1 for a in actions if a.verb=='warn' and not a.required)} advisory")
    return 1 if (changes or fail_warns) else 0


if __name__ == "__main__":
    sys.exit(main())

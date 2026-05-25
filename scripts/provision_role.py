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
    - config deltas (bbox/cap/caches): ADVISORY warn (assert, don't enforce, in v1)
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


def plan(role_def: dict) -> List[Action]:
    """Build the ordered action list to converge to `role_def`. Pure w.r.t.
    the SSOT observe functions (which read the live system)."""
    actions: List[Action] = []
    services: Dict[str, str] = role_def.get("services", {})

    for unit, desired in services.items():
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

    # Config deltas (v1: advisory only — assert, don't enforce).
    if services.get("meshforge-map") == "enabled":
        actions.append(Action("delta:node-directory", "?", "bbox+cap+caches", "warn",
                              required=False,
                              detail="verify bbox_filter/node_cap/response-caches via /api/status.directory"))
    if role_def.get("singleton"):
        actions.append(Action("invariant:singleton", "?", "unique-in-fleet", "warn",
                              required=False,
                              detail="this role must be unique across the fleet — verify no other box claims it"))
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


def write_role(role: str) -> None:
    DEPLOYMENT_JSON.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if DEPLOYMENT_JSON.exists():
        try:
            data = json.loads(DEPLOYMENT_JSON.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    data["role"] = role
    DEPLOYMENT_JSON.write_text(json.dumps(data, indent=2))


# --------------------------------------------------------------------------
# Render + main
# --------------------------------------------------------------------------

_SYM = {"noop": "PASS", "enable": "CHANGE", "disable": "CHANGE", "mask": "CHANGE", "warn": "WARN"}


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
    args = p.parse_args(argv)

    if args.set_role:
        write_role(args.set_role)
        print(f"role set to '{args.set_role}' in {DEPLOYMENT_JSON}")
        return 0

    try:
        catalog = load_roles(args.roles_file)
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"ERROR loading {args.roles_file}: {e}", file=sys.stderr)
        return 2

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

    print(f"# role: {role}  (mode: {'APPLY' if args.apply else 'dry-run'})")
    actions = plan(role_def)
    render(actions, args.apply)

    changes = [a for a in actions if a.verb in ("enable", "disable", "mask")]
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

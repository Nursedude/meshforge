"""Pure logic for the fleet-provision TUI handler — unit-testable WITHOUT the TUI.

PREVIEW-only (dry-run) wrapper over the EXISTING convergence engine:
  - scripts/provision_role.py  — role → systemd unit diff (plan())
  - docs/fleet_roles.yaml      — role SSOT
  - docs/fleet_presets.yaml    — lab-hardened (role × bridge-leg) catalog

It computes what reproducing a preset WOULD do to this box; it never applies,
mutates a file, or restarts a service. Apply is a deliberate Session-D follow-on.
provision_role is passed in as `mod` (loaded via load_provision_role) so tests
inject a stub and never touch real systemd.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_ROOT = "/opt/meshforge"

# plan() verbs that mean "the box would actually change under converge".
CHANGE_VERBS = ("enable", "disable", "mask")


def load_provision_role(meshforge_root: str = DEFAULT_ROOT):
    """importlib-load scripts/provision_role.py (the converge SSOT).

    Returns the module, or None if it cannot be loaded (caller shows a
    'tooling unavailable' note rather than crashing the TUI). Mirrors the
    loader in utils/watchdog_probes_drift.py — including the sys.modules
    pre-register that py3.12+ needs for the module's @dataclass eval.
    """
    try:
        script = os.path.join(meshforge_root, "scripts", "provision_role.py")
        spec = importlib.util.spec_from_file_location("provision_role", script)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # register before exec (py3.12 dataclass eval)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def presets_path(meshforge_root: str = DEFAULT_ROOT) -> str:
    return os.path.join(meshforge_root, "docs", "fleet_presets.yaml")


def load_presets(path: str) -> dict:
    """Load + minimally validate docs/fleet_presets.yaml."""
    with open(path) as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict) or not doc.get("presets"):
        raise ValueError(f"{path}: missing 'presets'")
    return doc


def gateway_overlay_for(preset_name: str, presets_doc: dict) -> Dict[str, Any]:
    """The gateway.json flags a preset's bridge legs would set, merged from the
    leg vocabulary. Empty for a non-bridge preset (it runs no bridge daemon)."""
    preset = presets_doc["presets"][preset_name]
    overlay: Dict[str, Any] = {}
    if not preset.get("bridge"):
        return overlay
    legs = presets_doc.get("legs", {})
    for leg in preset.get("bridge_legs", []) or []:
        overlay.update((legs.get(leg, {}) or {}).get("gateway_json", {}) or {})
    return overlay


def current_box(mod) -> Dict[str, Any]:
    """This box's declared role + overrides + live drift (provision_role.plan).

    `drift` is the list of change-verb actions if the role is set and the
    catalog loads, else None (indeterminate — never read None as 'no drift';
    honest_failure_modes #2).
    """
    role = mod.read_role()
    overrides = mod.read_overrides()
    drift: Optional[List[Any]] = None
    if role:
        try:
            catalog = mod.load_roles(mod.DEFAULT_ROLES_FILE)
            role_def = mod.resolve_role(catalog, role)
            drift = [a for a in mod.plan(role_def, overrides)
                     if a.verb in CHANGE_VERBS]
        except Exception:
            drift = None
    return {"role": role, "overrides": overrides, "drift": drift}


def preview_preset(mod, preset_name: str, presets_doc: dict,
                   overrides: Optional[dict] = None) -> Dict[str, Any]:
    """Dry-run a preset: the unit actions converging to its ROLE would take
    (provision_role.plan, change-verbs only) + the gateway.json overlay its
    legs imply. Pure — no apply. Raises KeyError if the preset's role is not in
    fleet_roles.yaml (the catalog guard prevents this, but fail loud if it slips)."""
    preset = presets_doc["presets"][preset_name]
    role = preset["role"]
    catalog = mod.load_roles(mod.DEFAULT_ROLES_FILE)
    role_def = mod.resolve_role(catalog, role)          # KeyError → unknown role
    actions = [a for a in mod.plan(role_def, overrides or {})
               if a.verb in CHANGE_VERBS]
    return {
        "preset": preset_name,
        "role": role,
        "actions": actions,
        "gateway_overlay": gateway_overlay_for(preset_name, presets_doc),
    }

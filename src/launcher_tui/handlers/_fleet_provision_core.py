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

# The guided next-step for the BRIDGE-LEG axis (gateway.json). apply_preset
# converges the ROLE axis only; legs need box-specific values this generic
# catalog deliberately omits (MF014), so they are applied with this command.
CONFIGURE_GATEWAY_CMD = "sudo scripts/configure_gateway.sh"


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


def apply_preset(mod, preset_name: str, presets_doc: dict,
                 overrides: Optional[dict] = None) -> Dict[str, Any]:
    """Converge THIS box to a preset's ROLE: write the role into deployment.json
    (merge — never clobber overrides) then apply the role's unit changes via
    ``provision_role.apply_action`` (the ``service_check`` SSOT). This is the
    apply half of the dry-run preview — it touches REAL systemd + deployment.json
    and must only be called by the handler after an explicit admin confirm.

    Scope (v1): the ROLE axis only — which systemd units run. The BRIDGE-LEG
    axis (gateway.json) is NOT applied here: a generic preset deliberately omits
    box-specific values (LXMF destination hash, meshforge channel index — MF014),
    and ``configure_gateway.sh`` is the tool that derives them. The caller
    surfaces the leg overlay + ``CONFIGURE_GATEWAY_CMD`` as a guided next step.
    SSH/remote apply is out of scope (local box only).

    ``mod`` is ``provision_role`` (injected so tests never touch real systemd).

    Returns a result dict: ``role``, ``role_written`` (bool), ``role_err``,
    ``results`` (per-action ``{item, verb, ok, result}``), ``failures``, ``ok``.
    Honest contract (honest_failure_modes #4/#9): ``ok`` is True ONLY when the
    role was written AND no unit action failed. An empty change set with the
    role written IS a success (idempotent re-apply), never an ambiguous read.
    """
    preset = presets_doc["presets"][preset_name]
    role = preset["role"]

    # Write the role FIRST. If we cannot RECORD the role, do NOT start changing
    # units — converging units toward a role the box does not claim is a
    # confusing half-state (honest_failure_modes #4: wire together or fail
    # together). Surface the error; never swallow it into a healthy-looking read.
    try:
        mod.write_role(role)
    except Exception as e:  # write_role does filesystem IO
        return {"preset": preset_name, "role": role, "role_written": False,
                "role_err": f"{type(e).__name__}: {e}",
                "results": [], "failures": [], "ok": False}

    # Re-derive the plan against LIVE state (not a possibly-stale preview) and
    # apply only the change verbs (enable/disable/mask).
    catalog = mod.load_roles(mod.DEFAULT_ROLES_FILE)
    role_def = mod.resolve_role(catalog, role)
    actions = [a for a in mod.plan(role_def, overrides or {})
               if a.verb in CHANGE_VERBS]

    results: List[Dict[str, Any]] = []
    for a in actions:
        ok = bool(mod.apply_action(a))
        results.append({"item": a.item, "verb": a.verb, "ok": ok,
                        "result": getattr(a, "result", "")})
    failures = [r for r in results if not r["ok"]]
    return {"preset": preset_name, "role": role, "role_written": True,
            "role_err": None, "results": results, "failures": failures,
            "ok": not failures}

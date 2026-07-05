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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# The RUNNING checkout's root (…/src/launcher_tui/handlers/ → repo root),
# same convention as the sibling handlers (db_audit.py) — a dev-tree TUI
# must preview/apply ITS OWN engine + catalog, never /opt's stale copy.
DEFAULT_ROOT = str(Path(__file__).resolve().parents[3])

# plan() verbs that mean "the box would actually change under converge".
# Fallback only — _change_verbs() prefers the engine's own exported
# PLAN_CHANGE_VERBS (one constant, one owner; honest_failure_modes #5).
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
    spec = None
    try:
        script = os.path.join(meshforge_root, "scripts", "provision_role.py")
        spec = importlib.util.spec_from_file_location("provision_role", script)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # register before exec (py3.12 dataclass eval)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        # never leave a half-executed module registered under the bare
        # name — a later `import provision_role` would get the broken
        # shell instead of a clean ImportError
        if spec is not None:
            sys.modules.pop(spec.name, None)
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


def _change_verbs(mod) -> tuple:
    """The engine's own exported change-verb set (fallback for stubs)."""
    return tuple(getattr(mod, "PLAN_CHANGE_VERBS", CHANGE_VERBS))


def _derive(mod, role: str, overrides: Optional[dict]
            ) -> Tuple[List[Any], List[Any], dict]:
    """(changes, warnings, role_def) from ONE plan() run — THE derive block
    (previously copy-pasted in three places). Warnings are carried, never
    filtered away: required warns are BLOCKING in the CLI and count as
    drift in probe_role_drift — dropping them made the TUI disagree with
    both (the false-'converged' class). Raises KeyError → unknown role."""
    catalog = mod.load_roles(mod.DEFAULT_ROLES_FILE)
    role_def = mod.resolve_role(catalog, role)
    actions = list(mod.plan(role_def, overrides or {}))
    changes = [a for a in actions if a.verb in _change_verbs(mod)]
    warnings = [a for a in actions if a.verb == "warn"]
    return changes, warnings, role_def


def _foundation(mod) -> List[Any]:
    """The engine's cross-cutting foundation converge (mf.4/#73 perms) —
    the CLI appends this to EVERY converge; the TUI apply must not be a
    silent subset of it."""
    fn = getattr(mod, "foundation_actions", None)
    if not callable(fn):
        return []
    try:
        return [a for a in fn() if a.verb == "foundation"]
    except Exception:
        return []  # preview stays usable; apply reports what it ran


def required_warnings(warnings: Optional[List[Any]]) -> List[Any]:
    """The BLOCKING subset — provision_role.main() counts these as
    failures (exit 1) and probe_role_drift counts them as drift."""
    return [w for w in (warnings or [])
            if getattr(w, "required", False)]


def current_box(mod) -> Dict[str, Any]:
    """This box's declared role + overrides + live drift + warnings.

    `drift`/`warnings` are None when indeterminate (no role / catalog
    failed) — never read None as 'no drift' (honest_failure_modes #2).
    'Converged' means drift == [] AND no required warnings — the same
    predicate probe_role_drift pages on, so the TUI and the watchdog can
    never disagree about the same plan.
    """
    role = mod.read_role()
    overrides = mod.read_overrides()
    drift: Optional[List[Any]] = None
    warnings: Optional[List[Any]] = None
    if role:
        try:
            drift, warnings, _role_def = _derive(mod, role, overrides)
        except Exception:
            drift = warnings = None
    return {"role": role, "overrides": overrides, "drift": drift,
            "warnings": warnings}


def preview_preset(mod, preset_name: str, presets_doc: dict,
                   overrides: Optional[dict] = None) -> Dict[str, Any]:
    """Dry-run a preset: the unit actions converging to its ROLE would take
    + the plan's warnings (required ones BLOCK the CLI converge and must be
    part of the operator's decision) + the foundation converge the apply
    will also run + the gateway.json overlay its legs imply. Pure — no
    apply. Raises KeyError if the preset's role is not in fleet_roles.yaml
    (the catalog guard prevents this, but fail loud if it slips)."""
    preset = presets_doc["presets"][preset_name]
    role = preset["role"]
    actions, warnings, _role_def = _derive(mod, role, overrides)
    return {
        "preset": preset_name,
        "role": role,
        "actions": actions,
        "warnings": warnings,
        "foundation": _foundation(mod),
        "gateway_overlay": gateway_overlay_for(preset_name, presets_doc),
    }


def apply_preset(mod, preset_name: str, presets_doc: dict,
                 overrides: Optional[dict] = None,
                 expected_actions: Optional[List[Any]] = None
                 ) -> Dict[str, Any]:
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

    ``expected_actions`` is the action list the operator CONFIRMED (from
    preview_preset) — the TOCTOU guard: if the live re-derived plan differs,
    the apply aborts with zero mutation (``aborted`` says why).

    Returns a result dict: ``role``, ``prior_role`` (the revert target),
    ``role_written`` (bool), ``role_err``, ``results`` (per-action
    ``{item, verb, ok, result}``), ``failures``, ``blocking_warnings``
    (required warns — the CLI's exit-1 class), ``aborted``, ``ok``.
    Honest contract (honest_failure_modes #4/#9): ``ok`` is True ONLY when
    the role was written AND no unit action failed AND no blocking warning
    stands. An empty change set with the role written IS a success
    (idempotent re-apply), never an ambiguous read.
    """
    preset = presets_doc["presets"][preset_name]
    role = preset["role"]
    base: Dict[str, Any] = {
        "preset": preset_name, "role": role,
        "prior_role": mod.read_role(),      # the revert target — record it
        "role_written": False, "role_err": None,
        "results": [], "failures": [], "blocking_warnings": [],
        "aborted": None, "ok": False,
    }

    # 1. Re-derive against LIVE state BEFORE anything is written. All the
    #    raise points (catalog unreadable, unknown role, plan probes) now
    #    abort with ZERO mutation instead of stranding a written role.
    try:
        actions, warnings, role_def = _derive(mod, role, overrides)
    except Exception as e:
        base["aborted"] = f"could not derive the plan: {type(e).__name__}: {e}"
        return base

    # 2. External roles are refused — CLI parity (provision_role.main exit
    #    2): converging a provisioned_by role fights the foreign
    #    provisioner over unit state (#69 rival-host class).
    if role_def.get("provisioned_by"):
        base["aborted"] = (f"role {role!r} is EXTERNAL (provisioned_by: "
                           f"{role_def['provisioned_by']}) — the MeshForge "
                           f"provisioner does not converge it")
        return base

    # 3. TOCTOU guard: the operator confirmed a SPECIFIC action list; if
    #    live state or the catalog moved while the confirm dialog sat open,
    #    the re-derived set differs — abort untouched, never silently apply
    #    changes nobody authorized.
    if expected_actions is not None:
        derived_sig = [(a.verb, a.item, a.desired) for a in actions]
        expected_sig = [(a.verb, a.item, a.desired) for a in expected_actions]
        if derived_sig != expected_sig:
            base["aborted"] = ("plan changed since the confirm dialog — "
                               "nothing was applied; re-open the preview")
            base["plan_now"] = derived_sig
            return base

    base["blocking_warnings"] = [
        {"item": w.item, "detail": getattr(w, "detail", "")}
        for w in required_warnings(warnings)]

    # 4. Record the role. If we cannot RECORD it, do NOT start changing
    #    units (honest_failure_modes #4: wire together or fail together).
    try:
        mod.write_role(role)
    except Exception as e:  # write_role does filesystem IO
        base["role_err"] = f"{type(e).__name__}: {e}"
        return base
    base["role_written"] = True

    # 5. Unit changes + the cross-cutting foundation converge (CLI parity —
    #    a TUI apply must not be a silent subset of `--apply`).
    results: List[Dict[str, Any]] = []
    for a in actions + _foundation(mod):
        ok = bool(mod.apply_action(a))
        results.append({"item": a.item, "verb": a.verb, "ok": ok,
                        "result": getattr(a, "result", "")})
    failures = [r for r in results if not r["ok"]]
    base["results"] = results
    base["failures"] = failures
    # CLI parity: blocking warnings count against success exactly like
    # failed actions (main()'s n_fail) — 'Preset Applied' with required
    # units missing is a false converged claim.
    base["ok"] = not failures and not base["blocking_warnings"]
    return base

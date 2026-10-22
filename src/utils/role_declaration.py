"""This box's EFFECTIVE role declaration, readable from inside the root
watchdog (2026-10-21; split out of watchdog_probes_drift for the MF025 cap).

``role_declared_unit_state`` answers "what does this box's role, as modified
by an honored ``service_overrides`` entry, declare for unit X?" using
provision_role's own rule (``effective_unit_state``) — ONE copy for the
converge and the watchdog (honest_failure_modes #5).
"""

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from utils.watchdog_probe_core import _read_deployment_declaration_status

#: The checkout THIS code runs from — never a hardcoded /opt/meshforge (CI and
#: worktrees live elsewhere; review 2026-10-21).
_MESHFORGE_ROOT = str(Path(__file__).resolve().parents[2])

#: (path, mtime) -> parsed catalog, so a per-tick caller does not re-parse the
#: YAML every 30 s on the smallest boards.
_catalog_cache: Dict[Tuple[str, float], dict] = {}
#: root -> provision_role module, for the same per-tick caller (a deploy
#: restarts the watchdog, so a stale module cannot outlive a pull).
_provision_role_cache: Dict[str, object] = {}


def _load_provision_role(meshforge_root: str):
    """importlib-load ``scripts/provision_role.py`` (the converge SSOT). Raises
    on failure; callers map that to indeterminate."""
    import importlib.util
    import sys
    script = os.path.join(meshforge_root, "scripts", "provision_role.py")
    spec = importlib.util.spec_from_file_location("provision_role", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # register before exec (py3.12+ @dataclass eval; see probe_parity_drift)
    spec.loader.exec_module(mod)
    return mod


def role_declared_unit_state(
    unit: str, *, meshforge_root: Optional[str] = None,
) -> Optional[str]:
    """The EFFECTIVE declared state of ``unit`` for this box's role —
    ``enabled`` / ``disabled`` / ``absent`` — or None when it cannot be
    resolved (no declared role, unreadable declaration, tooling missing, a
    service_override that is not honored).

    Same effective-declaration rule as provision_role's rnsd masking
    invariant: a box's ``service_overrides`` entry wins only when HONORED
    (reason + valid state); an unhonored one is unresolvable, never the
    role's value (2026-10-21, the RNS-absent-by-design gate).
    """
    try:
        try:
            from utils.rns_tree_perms import _read_rnsd_user
            service_user = _read_rnsd_user()
        except Exception:
            service_user = None
        status, role, overrides = _read_deployment_declaration_status(service_user)
        if status != "declared":
            return None
        root = meshforge_root or _MESHFORGE_ROOT
        mod = _provision_role_cache.get(root)
        if mod is None:
            mod = _provision_role_cache[root] = _load_provision_role(root)
        path = str(mod.DEFAULT_ROLES_FILE)
        key = (path, os.stat(path).st_mtime)
        catalog = _catalog_cache.get(key)
        if catalog is None:
            catalog = mod.load_roles(mod.DEFAULT_ROLES_FILE)
            _catalog_cache.clear()
            _catalog_cache[key] = catalog
        services = mod.resolve_role(catalog, role)["services"]
        state = mod.effective_unit_state(services, overrides, unit)
        return state if state in mod.VALID_UNIT_STATES else None
    except Exception:  # noqa: BLE001 - unresolvable, never a declaration
        return None

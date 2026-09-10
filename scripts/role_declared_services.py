#!/usr/bin/env python3
"""Query the DECLARED state of one service, per fleet box.

Wiring only — this owns no derivation of its own. It joins the two SSOTs that
already exist:

  * host -> role      `utils.fleet_naming` (the stamped registry)
  * role -> services  `provision_role.resolve_role` (docs/fleet_roles.yaml)

Emits one `<box>\\t<state>` line per requested box, where state is one of the
catalog's own vocabulary (`enabled` / `disabled` / `absent`) or `unknown`.

WHY THIS EXISTS (2026-09-10). honest_status's watchdog leg inferred a box's
absent watchdog purely from `LoadState=not-found` and then excluded it from the
denominator. That silently conflated two opposite facts:

  * lehua, whose role `field-node` DECLARES `meshforge-watchdog: absent` (a
    written, reasoned decision) — correctly excluded; and
  * any box whose role declares it `enabled` but which has no unit installed —
    a provisioning failure, which was excluded just as quietly. Losing a
    watchdog made the fleet look CLEANER, because `wdtotal` shrank while
    `clean/wdtotal` stayed green.

Absent-by-design and absent-by-accident rendering identically, with the benign
reading winning, is honest_failure_modes #1. `inert` and `indeterminate` are
different claims, and so are `inert` and `broken`.

⚠️ `unknown` must NEVER be collapsed into `absent` by a caller. An unresolvable
declaration is a blind spot, not permission to stop watching — the same
tri-state contract `utils.fleet_naming.serves_map()` documents at length.
A failure anywhere below therefore degrades to `unknown` for every box, never
to a state that would license silence.

Usage:
    role_declared_services.py --service meshforge-watchdog moc moc1 lehua
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The catalog vocabulary. A value outside it is `unknown`, not a guess — a role
# that declares something we do not understand has told us nothing we may act
# on (honest_failure_modes #3: reject what the author cannot have meant).
KNOWN_STATES = ("enabled", "disabled", "absent")


def _ensure_path() -> None:
    """Make the two SSOT modules importable.

    Called by BOTH entry points. It used to run only inside _load(), so
    declared_state() silently returned 'unknown' for everything unless _load()
    happened to have run first — a function depending on another's side effect,
    which is the same hidden-coupling class this module exists to split. Caught
    by its own unit test.
    """
    for p in (REPO / "scripts", REPO / "src"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def _load() -> tuple[dict | None, object | None, str]:
    """(catalog, registry, note). Any failure yields (None, None, why)."""
    _ensure_path()
    try:
        import provision_role as pr
    except Exception as e:  # noqa: BLE001 - import/env, all fatal to the query
        return None, None, f"provision_role unavailable: {e}"
    try:
        catalog = pr.load_roles(REPO / "docs" / "fleet_roles.yaml")
    except Exception as e:  # noqa: BLE001 - yaml/IO shape is external input
        return None, None, f"role catalog unreadable: {e}"
    try:
        from utils.fleet_naming import load_registry_quiet
        registry = load_registry_quiet()
    except Exception as e:  # noqa: BLE001 - registry is optional wiring
        return catalog, None, f"registry unavailable: {e}"
    return catalog, registry, ""


def declared_state(catalog, registry, box: str, service: str) -> str:
    """Declared state of `service` on `box`, or 'unknown'."""
    if catalog is None or registry is None:
        return "unknown"
    host = registry.hosts.get(box) or registry.hosts.get(box.split(".")[0])
    if host is None or not host.role:
        return "unknown"
    _ensure_path()
    try:
        import provision_role as pr
        eff = pr.resolve_role(catalog, host.role)
    except Exception:  # noqa: BLE001 - unknown/!unresolvable role is unknown
        return "unknown"
    svcs = eff.get("services") or {}
    if service not in svcs:
        # The role says nothing about this service. UNKNOWN, not absent — a
        # role that simply forgot to declare it must keep being watched.
        return "unknown"
    state = str(svcs[service]).strip()
    return state if state in KNOWN_STATES else "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--service", required=True)
    ap.add_argument("boxes", nargs="*")
    args = ap.parse_args(argv)

    catalog, registry, note = _load()
    if note:
        # A witness on stderr; stdout stays a clean, parseable table so a
        # caller that ignores stderr still gets the safe `unknown` answer.
        print(f"role_declared_services: {note}", file=sys.stderr)
    for box in args.boxes:
        print(f"{box}\t{declared_state(catalog, registry, box, args.service)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Mtime-gated re-read of the watchdog's probe-target declarations.

Born 2026-08-27, the day the manager box went radio-off: its deployment.json
service_override took effect for ``probe_role_drift`` on the next tick, but
the PAGING list (``services_expected_active``) had been computed once at
daemon startup — so ``service_inactive`` kept paging a deliberately-stopped
meshtasticd until a manual watchdog restart. Two consumers of ONE
declaration disagreeing for hours is the honest_failure_modes #5 shape at
runtime.

Why mtime-gated rather than a per-tick re-resolve: resolving probe targets
runs the role plan, which queries systemd per unit — a per-tick re-resolve
would add ~8 subprocess execs every 30s forever (the footprint rule). Two
or three stat() calls per tick buy the same correctness.

Split out of ``watchdog_runner`` for the MF025 file-size cap.
"""

import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("watchdog")

ROLES_YAML_PATH = "/opt/meshforge/docs/fleet_roles.yaml"


class DeclarationMtimeGate:
    """Cheap change detector over the declaration files.

    A stat error reads as "cannot judge" (no change reported, last mtimes
    kept) — never a re-read storm; a file APPEARING where none existed is a
    change (None -> mtime).
    """

    def __init__(self, paths):
        self._paths: List[str] = [str(p) for p in paths if p]
        self._mtimes = {p: self._mtime(p) for p in self._paths}

    @staticmethod
    def _mtime(path) -> Optional[float]:
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None

    def changed(self) -> Optional[str]:
        """Return the first path whose mtime moved since last look, else None."""
        for p in self._paths:
            m = self._mtime(p)
            if m != self._mtimes.get(p):
                self._mtimes[p] = m
                return p
        return None


def make_declaration_gate() -> Optional[DeclarationMtimeGate]:
    """Build the gate over this box's declaration files, or None (with a
    loud warning) when the paths cannot be resolved — probe targets are
    then frozen at startup values, stated rather than silent."""
    try:
        from utils.rns_tree_perms import _read_rnsd_user
        from utils.watchdog_probe_core import deployment_declaration_path
        return DeclarationMtimeGate([
            deployment_declaration_path(_read_rnsd_user()),
            ROLES_YAML_PATH,
        ])
    except Exception as exc:
        logger.warning(
            "watchdog: declaration re-read disabled (gate init failed: %s) "
            "— probe targets are frozen at startup values", exc)
        return None


def resolve_probe_targets(  # moved from watchdog_runner (MF025), 2026-08-27
    config: Dict[str, object],
) -> Tuple[Tuple[str, ...], Tuple[str, ...], int]:
    """Layer config-file overrides on top of hardcoded defaults.

    Returns ``(services_expected_active, services_wedge_check, http_port)``.

    A list override is a *full replacement* of the default list. We
    deliberately don't do "add to default" or "subtract from default"
    semantics — the operator sees exactly what's probed by reading the
    file. Cuts ambiguity at the cost of slightly more typing.
    """
    # What the box's declared ROLE says must be active. This is the same
    # source probe_role_drift already reads, so the paging probe and the
    # legibility probe agree by construction rather than by two hand-kept
    # lists (honest_failure_modes #5).
    # Call-time imports from the runner: keeps existing patch-paths
    # (utils.watchdog_runner._role_expected_active and the _DEFAULT_*
    # constants) working, and avoids a load-time import cycle.
    from utils.watchdog_runner import (
        _DEFAULT_SERVICES_EXPECTED_ACTIVE,
        _DEFAULT_SERVICES_WEDGE_CHECK,
        _role_expected_active,
    )
    role_units = _role_expected_active()

    services_expected = config.get("services_expected_active")
    if isinstance(services_expected, list) and all(
        isinstance(s, str) for s in services_expected
    ):
        sea: Tuple[str, ...] = tuple(services_expected)
        # The override stays a FULL REPLACEMENT — operator control, and what
        # you read in the file is what gets probed. But dropping a unit the
        # role requires must not be SILENT: moc3's override existed to
        # suppress a real false positive (map is deliberately inactive on a
        # gateway-only box) and in doing so left meshforge-gateway unwatched,
        # which is how a 9m49s gateway outage produced no page on 2026-08-03.
        if role_units:
            missing = [u for u in role_units if u not in sea]
            if missing:
                logger.warning(
                    "watchdog: services_expected_active override omits "
                    "role-declared-active unit(s): %s — these will NOT page "
                    "if they go down. Add them or correct the declared role.",
                    ", ".join(sorted(missing)),
                )
    else:
        if services_expected is not None:
            logger.warning(
                "watchdog: services_expected_active override is not a list "
                "of strings — ignoring",
            )
        # Role-derived by default; the hardcoded pair is the fallback for an
        # unresolvable role. Unresolvable is INDETERMINATE — hold the previous
        # behaviour rather than widening or narrowing on a guess (#2).
        sea = role_units or _DEFAULT_SERVICES_EXPECTED_ACTIVE

    # Membership below compares NORMALIZED names (same rule as
    # role_expected_active): a suffixless "meshforge-map" beside
    # "meshforge-map.service" is the same unit to systemctl and must be the
    # same unit here, or the check both warns about owned units and stays
    # silent about unowned ones depending on which list carried the suffix
    # (2026-08-12 re-review).
    def _svc(u: str) -> str:
        return u if u.endswith(".service") else f"{u}.service"

    sea_norm = {_svc(u) for u in sea}
    services_wedge = config.get("services_wedge_check")
    if isinstance(services_wedge, list) and all(
        isinstance(s, str) for s in services_wedge
    ):
        swc: Tuple[str, ...] = tuple(services_wedge)
        # 2026-08-12 review (the absent→inert conversions), scoped to
        # EXPLICIT overrides by the same day's re-review. main_thread_wedge
        # files an ABSENT unit as `inert` on the argument that
        # `service_inactive` pages any unit that is EXPECTED active and
        # missing — which only holds for units in services_expected_active.
        # An operator-listed wedge unit outside that list, with its unit
        # file gone, would be owned by NOBODY: main_thread_wedge reads
        # inert, service_inactive never judges it. Not an error (the
        # operator may deliberately wedge-check a unit this box does not
        # require active) — but never silent.
        unowned = [u for u in swc if _svc(u) not in sea_norm]
        if unowned:
            logger.warning(
                "watchdog: services_wedge_check unit(s) %s are not in "
                "services_expected_active — if such a unit's file is ABSENT "
                "on this box, main_thread_wedge reads `inert` and "
                "service_inactive never judges it: nothing owns that "
                "failure. If this box should require the unit, add it to "
                "services_expected_active; if not, drop it from "
                "services_wedge_check.",
                ", ".join(sorted(unowned)),
            )
    else:
        if services_wedge is not None:
            logger.warning(
                "watchdog: services_wedge_check override is not a list "
                "of strings — ignoring",
            )
        # The DEFAULT wedge list is an intent template ("wedge-check the
        # map where this box runs the map"), not an operator declaration —
        # intersect it with expected-active instead of warning. On a
        # gateway-only role box (meshforge-map deliberately disabled,
        # fleet_roles.yaml) the un-intersected default fired the unowned
        # warning on EVERY watchdog start, and its remediation invited
        # re-arming the exact moc3 map-inactive false positive the role
        # declaration exists to suppress (2026-08-12 re-review).
        swc = tuple(
            u for u in _DEFAULT_SERVICES_WEDGE_CHECK if _svc(u) in sea_norm
        )

    port_raw = config.get("http_port")
    if isinstance(port_raw, int) and 1 <= port_raw <= 65535:
        port = port_raw
    else:
        if port_raw is not None:
            logger.warning(
                "watchdog: http_port override %r is not a valid port — "
                "ignoring", port_raw,
            )
        port = 5000
    return sea, swc, port

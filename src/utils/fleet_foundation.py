"""Fleet permission foundation — the SSOT for a born-correct service env.

Ratified at the 2026-06-01 architecture meeting
(`.claude/plans/fleet_permission_foundation_plan.md`). One definition of the
permission foundation a box must have so its services run as the non-root
operator user and can write **every path they own** — applied identically at
INSTALL (born-correct) and at CONVERGE (provisioner + repair). Having one
definition is the whole point: the mf.4 / Issue #73 logfile hang was born from
*two* definitions (the installer's root:root vs the TUI's user-flip) that agreed
only by luck.

## Two layers (the parity contract)
- **RNS foundation** — the `/etc/reticulum` tree perms + the rnsd service-user.
  This is the SHARED, byte-identical core, owned by ``utils.rns_tree_perms`` and
  kept parity-identical between MeshForge and MeshAnchor
  (``scripts/parity_check.py``). This module *delegates* to it, never
  re-implements it. (MeshForge's ``utils.rns_alignment`` also delegates its
  logfile-perms guard there, and keeps its own MeshForge-fleet-specific rpc_key /
  client-config alignment, which is NOT shared.)
- **App data-roots** — this app's own data dirs (``~/.config/meshforge`` etc.).
  These differ per app *by design* (MeshAnchor ships its own declaration), so
  they are NOT parity-tracked. The audit/plan **engine** below is parity-shareable
  (pure functions over an injected owner-lookup); only ``meshforge_data_roots()``
  is MeshForge-specific.

## Topology
``fleet`` (operator user + fleet parity) vs ``standalone`` (single box / single
user — the degenerate case: one user owns everything, same ownership rules). Same
spec, parameterized — standalone cannot drift from the fleet definition.
"""

from __future__ import annotations

import grp
import logging
import pwd
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from utils.paths import get_real_user_home, get_real_username

logger = logging.getLogger(__name__)

VALID_TOPOLOGIES = ("fleet", "standalone")
CANONICAL_RNS_CONFIGDIR = Path("/etc/reticulum")


@dataclass(frozen=True)
class FoundationSpec:
    """The canonical permission foundation for one box."""
    operator_user: str            # non-root user the services run as
    topology: str                 # 'fleet' | 'standalone'
    data_roots: Tuple[Path, ...]  # app dirs that must be operator_user-owned
    rns_configdir: Path = CANONICAL_RNS_CONFIGDIR  # delegated to rns_tree_perms


@dataclass(frozen=True)
class FoundationAction:
    """One state-changing step (a description + the sudo command to run)."""
    description: str
    cmd: List[str]


# --- app-specific declaration (MeshForge) — MeshAnchor ships its own ----------


def meshforge_data_roots(home: Optional[Path] = None) -> Tuple[Path, ...]:
    """MeshForge's persistent data dirs that a non-root service must own.

    Mirrors the MeshAnchor convergence finding: the writable roots are the
    XDG-style app dirs plus the NomadNet home. The RNS tree (/etc/reticulum) is
    handled separately by rns_alignment (the shared parity core), not listed here.
    """
    home = home or get_real_user_home()
    return (
        home / ".config" / "meshforge",
        home / ".local" / "share" / "meshforge",
        home / ".cache" / "meshforge",
        home / ".nomadnetwork",
    )


def canonical_foundation(
    operator_user: Optional[str] = None,
    topology: str = "fleet",
    home: Optional[Path] = None,
) -> FoundationSpec:
    """Build the canonical foundation spec for this box."""
    if topology not in VALID_TOPOLOGIES:
        raise ValueError(f"unknown topology {topology!r} (want one of {VALID_TOPOLOGIES})")
    user = operator_user or get_real_username()
    return FoundationSpec(
        operator_user=user,
        topology=topology,
        data_roots=meshforge_data_roots(home),
    )


# --- pure engine (parity-shareable: no MeshForge-specific paths in here) -------


def _owner_user(owner: Optional[str]) -> Optional[str]:
    """Extract the user part from a 'user:group' string."""
    if not owner:
        return None
    return owner.split(":", 1)[0]


def audit_data_roots(
    spec: FoundationSpec,
    owner_of: Callable[[Path], Optional[str]],
) -> List[str]:
    """Drift reasons for data-roots not owned by the operator user.

    ``owner_of(path)`` returns ``'user:group'`` or ``None`` (absent / unreadable).
    Absent roots are NOT drift — they're created correctly at first write once the
    service runs as the operator user (born-correct). Pure: all I/O is injected, so
    this is the parity-shareable core and is unit-testable without a real fs.
    """
    reasons: List[str] = []
    for root in spec.data_roots:
        owner = owner_of(root)
        if owner is None:
            continue  # absent / unreadable — never guess
        if _owner_user(owner) != spec.operator_user:
            reasons.append(
                f"{root} owned by {owner} — must be {spec.operator_user}; a "
                f"non-root service cannot write data it does not own (mf.4/#73 class)"
            )
    return reasons


def plan_data_root_fixes(
    spec: FoundationSpec,
    owner_of: Callable[[Path], Optional[str]],
) -> List[FoundationAction]:
    """One ``chown -R`` per drifted data-root. Idempotent: no drift → empty plan."""
    actions: List[FoundationAction] = []
    for root in spec.data_roots:
        owner = owner_of(root)
        if owner is None:
            continue
        if _owner_user(owner) != spec.operator_user:
            u = spec.operator_user
            actions.append(FoundationAction(
                description=f"chown {root} -> {u} (data-root, mf.4/#73 foundation)",
                cmd=["sudo", "chown", "-R", f"{u}:{u}", str(root)],
            ))
    return actions


# --- real owner lookup + orchestration ----------------------------------------


def _owner_of(path: Path) -> Optional[str]:
    """Return 'user:group' for path, or None if absent/inaccessible.

    ``stat`` only needs search on the parents (not ownership), so this works for a
    non-root caller on root-owned dirs; falls back to ``sudo stat`` for the rare
    unreadable-parent case.
    """
    try:
        st = path.stat()
        try:
            return f"{pwd.getpwuid(st.st_uid).pw_name}:{grp.getgrgid(st.st_gid).gr_name}"
        except KeyError:
            return f"{st.st_uid}:{st.st_gid}"
    except (OSError, PermissionError):
        try:
            res = subprocess.run(
                ["sudo", "-n", "stat", "-c", "%U:%G", str(path)],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except subprocess.SubprocessError:
            pass
        return None


def audit_foundation(spec: Optional[FoundationSpec] = None) -> List[str]:
    """Full foundation audit = app data-roots (here) + RNS tree (rns_tree_perms).

    The RNS tree is the shared/parity core, so we delegate to rns_tree_perms's
    logfile-perms guard rather than re-checking it here.
    """
    spec = spec or canonical_foundation()
    reasons = audit_data_roots(spec, _owner_of)
    try:
        from utils.rns_tree_perms import logfile_perms_drift, probe_rns_tree_perms
        rns_reason = logfile_perms_drift(probe_rns_tree_perms())
        if rns_reason:
            reasons.append(rns_reason)
    except Exception as e:  # delegation is best-effort; never block the data audit
        logger.debug("rns_tree_perms delegation skipped: %s", e)
    return reasons


def plan_foundation(spec: Optional[FoundationSpec] = None) -> List[FoundationAction]:
    """State-changing plan for the app data-roots (the part this module owns).

    The RNS-tree perms are applied separately by ``apply_foundation`` (via the
    shared ``rns_tree_perms`` apply-path); on MeshForge the broader RNS-config
    alignment (rpc_key etc.) is converged by ``scripts/rns_alignment.py normalize``.
    """
    spec = spec or canonical_foundation()
    return plan_data_root_fixes(spec, _owner_of)


def apply_foundation(spec: Optional[FoundationSpec] = None, dry_run: bool = False) -> List[str]:
    """Apply the COMPLETE foundation: app data-roots (here) + the RNS tree
    (delegated to ``rns_tree_perms.apply_logfile_perms`` — the shared single
    apply-path). Returns executed (or dry-run) descriptions. Requires sudo for
    real changes.
    """
    spec = spec or canonical_foundation()
    executed: List[str] = []

    # App data-roots (the part this module owns).
    for action in plan_foundation(spec):
        if dry_run:
            executed.append(f"[DRY-RUN] {action.description}")
            continue
        logger.info("fleet_foundation: %s", action.description)
        subprocess.run(action.cmd, check=True, timeout=60)
        executed.append(action.description)

    # RNS tree (the shared core — single apply-path in rns_tree_perms).
    rns_desc = f"apply RNS-tree perms for {spec.operator_user} ({spec.rns_configdir})"
    if dry_run:
        executed.append(f"[DRY-RUN] {rns_desc}")
    else:
        try:
            from utils.rns_tree_perms import apply_logfile_perms
            apply_logfile_perms(spec.operator_user)
            executed.append(rns_desc)
        except Exception as e:  # never let the RNS leg sink the data-root work
            logger.warning("RNS-tree perms apply skipped (non-fatal): %s", e)
    return executed

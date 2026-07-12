"""Watchdog probes — declared-state vs live-state drift failure shapes.

Foundation perms drift, MeshForge<->MeshAnchor parity drift, RNS fork-pin
version drift, role drift, MQTT root drift (#77), cron verdict stale (#78),
kernel reboot pending (2026-06-09 version-updates arc).
Part of the ``watchdog_probes`` split (2026-06-09) — import via the
``utils.watchdog_probes`` hub, not from here.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,
    _journal_newest_match,
    _read_deployment_declaration,
    _resolve_main_pid,
)

# ─────────────────────────────────────────────────────────────────────
# Probe: permission-foundation drift (mf.4 / Issue #73 perms class)
# ─────────────────────────────────────────────────────────────────────


def probe_foundation_drift(
    *,
    perms=None,
) -> Optional[Signal]:
    """Surface a born-correct permission-foundation drift in the RNS config tree.

    The foundation SSOT (utils.fleet_foundation + the shared utils.rns_tree_perms)
    declares that a non-root rnsd must own/be-able-to-write its ``/etc/reticulum``
    tree (configdir ``root:<rnsd_user> 1775``, logfile/storage ``<rnsd_user>``). A
    re-provision that recreates the tree ``root:root`` while rnsd runs non-root is
    the recurrence path (moc1/moc2/moc, 2026-06-01) — every ``RNS.log()`` write then
    fails, which self-deadlocked the daemon pre-fork-mf.4 and loses all logs after.
    The fleet caught moc this way *manually* on the first audit; this probe makes it
    a continuously-monitored signal that flows to /fleet + the mini deep-rollup, so a
    drifted box self-surfaces instead of waiting for a hand-run audit.

    Scope: this checks the **RNS-tree perms** leg only — it derives the rnsd user
    from rnsd's own systemd unit (``probe_rns_tree_perms``), so it is correct no
    matter which user the watchdog runs as (it runs as root, where
    ``get_real_username`` would mislead). The **data-roots** leg of the foundation
    (operator-user-owned ``~/.config`` etc.) depends on the operator identity and is
    owned by the explicit, operator-run ``scripts/fleet_foundation.py audit`` /
    provisioner, not this root-context probe.

    Severity is ``degraded`` (not ``wedge``): a drifted box typically still serves —
    it is one logfile rotation from the wedge — and the fix is perms-only with no
    restart. Returns None when the foundation is clean, when rnsd runs as root
    (root writes anything), when the perms weren't probed (indeterminate — never
    guess), or when the foundation modules can't be imported.
    """
    try:
        from utils.rns_tree_perms import logfile_perms_drift, probe_rns_tree_perms
    except Exception:
        return None  # foundation tooling absent — indeterminate, don't false-alarm
    if perms is None:
        try:
            perms = probe_rns_tree_perms()
        except Exception:
            return None
    reason = logfile_perms_drift(perms)
    if not reason:
        return None
    detail = (
        f"{reason} | born-correct permission foundation drifted (mf.4/#73 perms "
        f"class). Fix (perms-only, no restart): "
        f"sudo python3 scripts/fleet_foundation.py apply"
    )
    return Signal(
        cls="foundation_perms_drift",
        subject="rnsd",
        severity="degraded",
        detail=detail,
        issue_ref=73,
        extra={
            "rnsd_user": perms.rnsd_user,
            "configdir_owner": perms.configdir_owner,
            "configdir_mode": perms.configdir_mode,
            "logfile_owner": perms.logfile_owner,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: MeshForge <-> MeshAnchor parity drift (lead-repo port debt)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_PARITY_DEBOUNCE_PATH = "/var/lib/meshforge/parity_debounce.json"


def _load_parity_streak(state_path: str) -> int:
    """Read the consecutive-drift streak counter. Best-effort: any error → 0.

    A missing/unreadable/garbage state means 'no confirmed streak yet', which
    suppresses a first-seen drift — exactly the conservative direction the
    debounce wants (favour silence on uncertainty, not a false page).
    """
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_parity_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


def probe_parity_drift(
    *,
    meshforge_root: str = "/opt/meshforge",
    meshanchor_root: str = "/opt/meshanchor",
    check_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Surface MeshForge<->MeshAnchor RNS-reliability parity drift.

    The two sister NOCs share the fleet's RNS substrate; reliability-critical files
    (the RNS-init chokepoint, the bridge contract, the rns_tree_perms SSOT, the
    fork-pin, lint MF009/MF019, the wedge probes) must stay in lockstep —
    ``scripts/parity_check.py`` is the lead-repo gate. This makes that audit a
    continuously-monitored signal so a divergence (someone edits one repo and
    forgets to port) self-surfaces in /fleet + the mini deep-rollup instead of
    rotting until the next manual run.

    Only meaningful where BOTH repos are present (e.g. the box holding the
    MeshAnchor clone). Returns None when ``meshanchor_root`` isn't a directory (a
    MeshForge-only fleet box — not applicable), when the parity tool can't be
    loaded, when everything's in sync, or when the result is merely ``missing`` (a
    tracked file absent — indeterminate / possible mid-deploy window, don't
    false-alarm). Fires ``degraded`` only on definite content ``drift`` — nothing is
    failing at runtime; the fix is to port the flagged change (MeshForge leads).

    **Debounce**: a drift must persist for ``debounce_ticks`` *consecutive* ticks
    before firing (default 2). The two repos sync seconds apart during a fleet
    roll, so a single tick can catch one repo mid-update and see a transient
    divergence that self-heals before the next tick — the 2026-06-01
    ``rns_tree_perms.py`` SSOT-port race did exactly this. A consecutive-drift
    streak (persisted to ``state_path``, default
    ``/var/lib/meshforge/parity_debounce.json``) rides out those in-flight blips
    while still surfacing a genuine forgotten port within one extra tick. Any
    non-drift result (in_sync / missing / tool error) resets the streak.
    """
    if not os.path.isdir(meshanchor_root):
        return None  # both repos required; MeshForge-only box → not applicable
    if state_path is None:
        state_path = DEFAULT_PARITY_DEBOUNCE_PATH
    if check_fn is None:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "parity_check",
                os.path.join(meshforge_root, "scripts", "parity_check.py"),
            )
            import sys
            mod = importlib.util.module_from_spec(spec)
            # Register before exec: on py3.12+ @dataclass resolves field types via
            # sys.modules[cls.__module__].__dict__ → AttributeError if absent. This
            # silently killed parity_drift + role_drift on the 3.13 fleet (found
            # 2026-06-08 inducing a live role_drift on moc1).
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            check_fn = mod.check_parity
        except Exception:
            return None  # parity tool unavailable → indeterminate, don't alarm
    try:
        findings, overall = check_fn(meshforge_root, meshanchor_root)
    except Exception:
        # Indeterminate — don't let a tool error count toward the streak.
        _save_parity_streak(state_path, 0)
        return None
    if overall != "drift":
        _save_parity_streak(state_path, 0)  # in_sync / missing → streak broken
        return None

    streak = _load_parity_streak(state_path) + 1
    _save_parity_streak(state_path, streak)
    if streak < debounce_ticks:
        return None  # drift seen, but not yet confirmed across consecutive ticks

    drifted = [f for f in findings if getattr(f, "status", None) == "drift"]
    items = ", ".join(f.label for f in drifted) or "?"
    detail = (
        f"MeshForge<->MeshAnchor parity drift ({len(drifted)} item(s)): {items} | "
        f"confirmed over {streak} consecutive ticks | RNS-reliability files must "
        f"match (MeshForge is the lead repo). Port the change, then verify: "
        f"python3 scripts/parity_check.py"
    )
    return Signal(
        cls="parity_drift",
        subject="meshforge<->meshanchor",
        severity="degraded",
        detail=detail,
        extra={"drift_items": [f.label for f in drifted], "debounce_streak": streak},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS/LXMF fork-pin version drift (RNS T2-isolate arc)
# ─────────────────────────────────────────────────────────────────────


def _read_pkg_versions_for_user(user, pkgs):
    """Read installed versions of ``pkgs`` from the service user's site-packages —
    read-only, no privilege change. Returns ``{pkg: version}`` for those found, or
    None if the user's site dir can't be located/read.

    Why not just ``importlib.metadata.version()``? That reads the *current*
    interpreter's env — the watchdog runs as root, whose env may carry a different
    rns (verified live: root had 1.1.1 while the wh6gxz service env had 1.2.5+mf.4).
    And we can't switch user: the watchdog unit sets NoNewPrivileges + RestrictSUIDSGID,
    which block sudo AND runuser (both need setuid). But ProtectHome=no, so root can
    READ ``/home/<user>/.local/...`` directly and point importlib.metadata at it.
    """
    if not user or user == "root":
        return None
    try:
        import importlib.metadata as _im
        home = Path(f"/home/{user}")
        site_dirs = [str(p) for p in sorted(home.glob(".local/lib/python3*/site-packages"))
                     if p.is_dir()]
        if not site_dirs:
            return None
        found = {}
        for dist in _im.distributions(path=site_dirs):
            try:
                name = (dist.metadata["Name"] or "").lower()
            except Exception:
                continue
            if name in pkgs:
                found[name] = dist.version
        return found
    except Exception:
        return None


def probe_rns_version_drift(
    *,
    rnsd_user=None,
    pins=None,
    installed=None,
) -> Optional[Signal]:
    """Surface a box running rns/lxmf off the pinned MeshForge-fork version.

    The fleet pins rns/lxmf on the ``+mf.N`` marker (``requirements/rns.txt``,
    gated by ``scripts/rns_version_check.py``) — upstream withdrew public support, so
    a bump is a *reviewed* decision, never an automatic pip-latest. This makes the
    check a continuously-monitored signal so a box that missed a fork roll (e.g. moc3
    was stock 1.2.5 before the mf.4 roll) self-surfaces in /fleet + the mini rollup.

    The pin (``pins``) is env-independent (just reads requirements/rns.txt). The
    INSTALLED versions are read from the rnsd **service user's** site-packages
    (see ``_read_pkg_versions_for_user`` for why root's own env / sudo / runuser all
    fail here). Fires ``degraded`` only on a concrete mismatch (installed != pinned
    for a package we can actually see). Returns None when compliant, when the pin or
    the user env can't be read (indeterminate — never false-alarm), or when a package
    isn't visible in the user site (possible venv install elsewhere — don't guess).
    """
    if rnsd_user is None and installed is None:
        try:
            from utils.rns_tree_perms import _read_rnsd_user
            rnsd_user = _read_rnsd_user()
        except Exception:
            rnsd_user = None

    if pins is None:
        try:
            import importlib.util
            script = str(Path(__file__).resolve().parents[2] / "scripts" / "rns_version_check.py")
            spec = importlib.util.spec_from_file_location("rns_version_check", script)
            import sys
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod  # register before exec (py3.12+ @dataclass eval; see probe_parity_drift)
            spec.loader.exec_module(mod)
            pins = mod.pinned_versions()
        except Exception:
            return None
    if not pins:
        return None  # no pin parseable (sub-arc A not applied) → indeterminate

    if installed is None:
        installed = _read_pkg_versions_for_user(rnsd_user, set(pins))
    if not installed:
        return None  # couldn't read the service env → indeterminate, no false alarm

    drift = []
    for pkg, want in pins.items():
        have = installed.get(pkg)
        if have is None:
            continue  # not visible in the user site (venv elsewhere?) — don't guess
        if have != want:
            drift.append(f"{pkg} installed={have} pinned={want}")
    if not drift:
        return None

    detail = (
        f"rns/lxmf off the pinned MeshForge-fork version ({'; '.join(drift)}). "
        f"Upstream withdrew support so the pin is deliberate — converge with a "
        f"REVIEWED bump: pip install --force-reinstall -r requirements/rns.txt, "
        f"then verify rnsd."
    )
    return Signal(
        cls="rns_version_drift",
        subject="rns/lxmf",
        severity="degraded",
        detail=detail,
        extra={"rnsd_user": rnsd_user, "drift": drift},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: pip dependency version-floor drift (the recurring update class)
# ─────────────────────────────────────────────────────────────────────

# Critical pip deps whose installed version we floor-check against the
# requirements SSOT. rns/lxmf are deliberately EXCLUDED — they have their own
# fork-pin probe (probe_rns_version_drift). The gap this fills: meshtastic, the
# lib whose update kept FAILING (PEP 668 / wrong env / a box that missed a roll)
# and which nothing watched. See feedback_version_env_rigor.
_DEP_VERSION_WATCHED = ("meshtastic",)

# The floor PARSER and the below-floor TEST are shared with the TUI version
# checker (updates.version_checker) so the two consumers of requirements/core.txt
# can never disagree about "the floor" (honest_failure_modes item 5). Imported
# under the historical private names this module already exposed to its tests.
from utils.requirements_floor import (  # noqa: E402
    default_core_requirements as _default_core_requirements,
    read_requirement_floors as _read_requirement_floors,
    version_below as _version_below,
)


def probe_dep_version_drift(
    *,
    service_user=None,
    floors=None,
    installed=None,
    requirements_path=None,
) -> Optional[Signal]:
    """A critical pip dependency installed BELOW the requirements version floor
    — a box that missed or failed an update and is stuck on a stale version.

    Makes the recurring update/install/env failure class OBSERVABLE (operator
    2026-06-12: "be better at version control + env variables", after the TUI
    update died on PEP 668). rns/lxmf have their own fork-pin probe; this covers
    meshtastic — the lib nothing watched, whose update kept failing and was
    operator-discovered instead of mini-caught.

    The floor is env-independent (reads ``requirements/core.txt`` — the SAME pin
    the installer uses, so no two-constant drift). INSTALLED versions are read
    from the MeshForge service user's site-packages (root's env may carry a
    different version; the watchdog sandbox blocks sudo/runuser — see
    ``_read_pkg_versions_for_user``). Fires ``degraded`` only on a concrete
    below-floor fact. Returns None when compliant, when the floor or the service
    env can't be read, or when the package isn't visible in the user site (a
    venv install elsewhere — don't guess), mirroring ``probe_rns_version_drift``'s
    no-false-alarm conservatism.
    """
    if floors is None:
        req = (Path(requirements_path) if requirements_path
               else Path(__file__).resolve().parents[2] / "requirements" / "core.txt")
        floors = _read_requirement_floors(_DEP_VERSION_WATCHED, req)
    if not floors:
        return None  # no floor parseable → indeterminate, never false-alarm

    if service_user is None:
        try:
            from utils.rns_tree_perms import _read_rnsd_user
            # MeshForge + rnsd share the service user on this fleet; this reads
            # the env the apps actually import from (NOT root's).
            service_user = _read_rnsd_user()
        except Exception:
            service_user = None

    if installed is None:
        # Read the CONSUMER-OF-RECORD, not just ~/.local. On this fleet
        # meshtastic lives in the venv (the services' interpreter) / system-dist
        # / pipx and is frequently ABSENT from ~/.local — the old user-site-only
        # read was venv-blind and silently missed a stale venv consumer (moc2/moc3
        # ran 2.7.8 in-venv while this probe stayed None, found 2026-06-17 by the
        # install audit). Mirror Layer 1 (updates.version_checker) + the audit:
        # pick venv -> user-site -> system-dist.
        watched_pkg = _DEP_VERSION_WATCHED[0]
        _, consumer_version = _consumer_of_record_version(
            _enumerate_pkg_installs(watched_pkg, service_user)
        )
        installed = {watched_pkg: consumer_version} if consumer_version else {}
    if not installed:
        return None  # couldn't read the consumer env → indeterminate

    stale = []
    for pkg, floor in floors.items():
        have = installed.get(pkg)
        if have is None:
            continue  # not visible in the consumer env — don't guess
        if _version_below(have, floor):
            stale.append(f"{pkg} installed={have} floor>={floor}")
    if not stale:
        return None

    detail = (
        f"pip dependency below the requirements floor in the consumer-of-record "
        f"({'; '.join(stale)}) — this box missed or failed an update and the "
        f"services import a stale version. Identify the exact install + reconcile: "
        f"python3 scripts/meshtastic_install_audit.py (its --fix prints the "
        f"per-location command), then verify the version + import. See "
        f"feedback_version_env_rigor."
    )
    return Signal(
        cls="dep_version_drift",
        subject="pip-deps",
        severity="degraded",
        detail=detail,
        extra={"service_user": service_user, "stale": stale},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: meshtastic install fragmentation (the phantom-update class,
# 2026-06-17) — the SAME pip lib installed at DIVERGENT versions across
# the root-readable locations (venv / system-wide dist-packages / root+user
# pipx / user-site). probe_dep_version_drift watches only the service-user
# consumer and is BLIND to a stray system-wide/pipx copy the TUI reads as
# root — the manager box stayed green (consumer 2.7.9) while a stray 2.7.8
# drove the phantom "2 updates available". This is the fragmentation half.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_DEP_FRAGMENT_DEBOUNCE_PATH = "/var/lib/meshforge/dep_fragment_debounce.json"

# Glob patterns (by location label) for every place a root-context read can
# find a pip install of the watched package. `{home}` / `{root}` are filled per
# call. Versioned-python dirs are globbed (the fleet runs mixed 3.12/3.13).
_DEP_INSTALL_SITE_GLOBS = {
    "venv":        ["{root}/venv/lib/python3*/site-packages"],
    "system-dist": [
        "/usr/local/lib/python3*/dist-packages",
        "/usr/lib/python3*/dist-packages",
        "/usr/lib/python3/dist-packages",
    ],
    "root-pipx":   [
        "/root/.local/share/pipx/venvs/{pkg}/lib/python3*/site-packages",
        "/opt/pipx/venvs/{pkg}/lib/python3*/site-packages",
    ],
    "user-site":   ["{home}/.local/lib/python3*/site-packages"],
    "user-pipx":   ["{home}/.local/share/pipx/venvs/{pkg}/lib/python3*/site-packages"],
}


def _read_pkg_version_at_dirs(site_dirs, pkg):
    """Version of ``pkg`` found in the given site-packages dirs, or None.

    Reads in-process via ``importlib.metadata.distributions(path=...)`` — the
    watchdog sandbox (NoNewPrivileges + RestrictSUIDSGID) blocks sudo/runuser,
    but ProtectHome=no lets root READ any of these trees directly (same
    constraint and pattern as ``_read_pkg_versions_for_user``)."""
    dirs = [d for d in dict.fromkeys(site_dirs) if os.path.isdir(d)]
    if not dirs:
        return None
    try:
        import importlib.metadata as _im
        for dist in _im.distributions(path=dirs):
            try:
                name = (dist.metadata["Name"] or "").lower()
            except Exception:
                continue
            if name == pkg.lower():
                return dist.version
    except Exception:
        return None
    return None


def _enumerate_pkg_installs(pkg, service_user, *, meshforge_root="/opt/meshforge",
                            user_home=None):
    """Return ``{location_label: version}`` for every root-readable install of
    ``pkg`` (venv / system-dist / root-pipx / user-site / user-pipx).

    Locations whose tree is absent or where ``pkg`` isn't installed are simply
    omitted (not an error). User-scoped locations are skipped when there is no
    non-root service user / resolvable home — we never guess a path."""
    import glob
    if user_home is None and service_user and service_user != "root":
        try:
            import pwd
            user_home = pwd.getpwnam(service_user).pw_dir
        except (KeyError, OSError):
            user_home = f"/home/{service_user}"
    found = {}
    for label, patterns in _DEP_INSTALL_SITE_GLOBS.items():
        if ("{home}" in "".join(patterns)) and not user_home:
            continue  # user-scoped location but no resolvable home — skip
        dirs = []
        for pat in patterns:
            try:
                expanded = pat.format(root=meshforge_root, home=user_home or "", pkg=pkg)
            except (KeyError, IndexError):
                continue
            dirs.extend(sorted(glob.glob(expanded)))
        ver = _read_pkg_version_at_dirs(dirs, pkg)
        if ver is not None:
            found[label] = ver
    return found


# The order a stale install actually bites a SERVICE: the venv is the apps'
# interpreter when present; otherwise the system python imports user-site
# (~/.local) ahead of system-wide dist-packages. pipx venvs are CLI-only —
# never the library consumer — so they're excluded from this priority.
_DEP_CONSUMER_PRIORITY = ("venv", "user-site", "system-dist")


def _consumer_of_record_version(installs):
    """The version the SERVICES actually import, from an enumerated install map
    (``{label: version}``). Returns ``(label, version)`` for the highest-priority
    present location (venv -> user-site -> system-dist), or ``(None, None)`` when
    the package is in none of them (e.g. only a pipx CLI copy — not a library
    consumer)."""
    for label in _DEP_CONSUMER_PRIORITY:
        if label in installs:
            return label, installs[label]
    return None, None


def probe_dep_install_fragmented(
    *,
    service_user=None,
    floor=None,
    installs=None,
    requirements_path=None,
    state_path=None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when meshtastic is installed at DIVERGENT versions across the
    root-readable locations, with at least one copy BELOW the fleet floor.

    The blind spot ``probe_dep_version_drift`` couldn't see (2026-06-17): that
    probe reads only the service-user ``~/.local`` consumer, so a stray
    system-wide ``/usr/local/lib/.../dist-packages`` or pipx copy can sit BELOW
    the reviewed floor while the consumer is fine — and the TUI-as-root (plus
    any future root import path) reads that stray and shows a phantom "update
    available". This probe enumerates every install location root can read and
    surfaces the fragmentation so it stops being operator-discovered.

    Fires ``degraded`` only when (a) ≥2 distinct versions exist across the
    found locations AND (b) at least one of them is below the floor. That
    second clause is load-bearing: a pipx CLI legitimately running AHEAD of the
    venv lib is normal, intended divergence (both ≥ floor) and must NOT page —
    only a below-floor stray is a real fragmentation defect. ``rns/lxmf`` are
    excluded (their own fork-pin probe). Uniform staleness (every copy at the
    same below-floor version) is ``dep_version_drift``'s job via the consumer,
    not fragmentation.

    Honest failure modes: no floor parseable, 0/1 install location, or all
    locations at one version → None (indeterminate / not fragmented; never
    false-alarm). 2-tick debounce rides out a mid-roll window where one
    location is part-way through an upgrade. Never raises into the tick.
    """
    try:
        pkg = _DEP_VERSION_WATCHED[0]  # "meshtastic" — the fragmentation-prone lib
        sp = state_path or DEFAULT_DEP_FRAGMENT_DEBOUNCE_PATH

        if floor is None:
            req = (Path(requirements_path) if requirements_path
                   else _default_core_requirements())
            floor = _read_requirement_floors([pkg], req).get(pkg.lower())
        if not floor:
            _save_parity_streak(sp, 0)
            return None  # no reviewed floor → indeterminate, never alarm

        if installs is None:
            if service_user is None:
                try:
                    from utils.rns_tree_perms import _read_rnsd_user
                    service_user = _read_rnsd_user()
                except Exception:
                    service_user = None
            installs = _enumerate_pkg_installs(pkg, service_user)

        if not installs or len(installs) < 2:
            _save_parity_streak(sp, 0)
            return None  # 0/1 location → no fragmentation possible
        versions = set(installs.values())
        if len(versions) < 2:
            _save_parity_streak(sp, 0)
            return None  # every location agrees — not fragmented

        below = {label: v for label, v in installs.items()
                 if _version_below(v, floor)}
        if not below:
            _save_parity_streak(sp, 0)
            return None  # divergence but nothing below floor (pipx CLI ahead) — benign

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            return None  # fragmentation seen, not yet confirmed across ticks

        consumer = ("venv" if "venv" in installs
                    else ("user-site" if "user-site" in installs else None))
        consumer_ver = installs.get(consumer) if consumer else None
        loc_str = ", ".join(
            f"{label}={ver}{' (<floor)' if label in below else ''}"
            for label, ver in sorted(installs.items())
        )
        below_str = ", ".join(f"{label}={ver}" for label, ver in sorted(below.items()))
        consumer_note = (f"consumer-of-record {consumer}={consumer_ver}"
                         if consumer else "consumer-of-record unknown")
        detail = (
            f"meshtastic install fragmentation — {len(installs)} locations at "
            f"{len(versions)} versions ({loc_str}); below the fleet floor "
            f">={floor}: {below_str}. {consumer_note}. The TUI-as-root and any "
            f"root import path read a stray, so 'update available' phantoms appear "
            f"though the service consumer is fine (feedback_version_env_rigor, "
            f"2026-06-17). Reconcile every copy to >= the floor: "
            f"python3 scripts/meshtastic_install_audit.py (see its --fix hint), "
            f"then re-check."
        )
        return Signal(
            cls="dep_install_fragmented",
            subject="meshtastic",
            severity="degraded",
            detail=detail,
            extra={
                "installs": dict(sorted(installs.items())),
                "below_floor": below_str,
                "floor": floor,
                "consumer": consumer,
                "streak": streak,
            },
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: declared-role drift (fleet_roles.yaml + overrides vs live units)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_ROLE_DRIFT_DEBOUNCE_PATH = "/var/lib/meshforge/role_drift_debounce.json"

# plan() verbs that mean "the box would change under converge" = real drift.
_ROLE_DRIFT_VERBS = ("enable", "disable", "mask")


def _plan_role_actions(role: str, overrides: dict, meshforge_root: str):
    """Default plan_fn: importlib-load ``scripts/provision_role.py`` (the
    converge SSOT) and return its ``plan()`` actions for this box's effective
    declaration — base role flattened through ``inherits`` plus the box's
    documented ``service_overrides``.

    Returns ``None`` when the tooling/catalog can't be loaded (indeterminate);
    raises ``KeyError`` for a role missing from the catalog (a REAL mismatch
    the caller counts as drift — e.g. deployment.json names a role the box's
    fleet_roles.yaml doesn't carry yet).
    """
    try:
        import importlib.util
        script = os.path.join(meshforge_root, "scripts", "provision_role.py")
        spec = importlib.util.spec_from_file_location("provision_role", script)
        import sys
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # register before exec (py3.12+ @dataclass eval; see probe_parity_drift)
        spec.loader.exec_module(mod)
        catalog = mod.load_roles(mod.DEFAULT_ROLES_FILE)
    except Exception:
        return None  # tool/catalog unavailable → indeterminate, don't alarm
    role_def = mod.resolve_role(catalog, role)  # KeyError → unknown role
    return mod.plan(role_def, overrides)


def probe_role_drift(
    *,
    meshforge_root: str = "/opt/meshforge",
    deployment: Optional[Tuple[Optional[str], dict]] = None,
    plan_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Surface a box whose live systemd unit state diverges from its declared role.

    The fleet's role model (``docs/fleet_roles.yaml`` + per-box
    ``deployment.json`` ``role``/``service_overrides``) is converged only when an
    operator runs ``provision_role.py --apply`` — between runs, nothing alerted on
    divergence. The 2026-06-03 architecture audit hit exactly this legibility gap
    (moc2 read as "full-gateway" while deliberately not bridging) — see
    ``.claude/research/fleet_architecture_2026_06_03.md`` §7-B. This probe makes
    the converge SSOT's own dry-run plan a continuously-monitored signal.

    Drift = any plan action whose verb is enable/disable/mask (the box would
    change under converge) or a BLOCKING warning (required unit not installed; a
    waiver missing its required ``reason``). **Documented overrides are honored**
    — ``plan()`` reports them as non-blocking advisories, which do NOT fire (the
    moc2 lesson: a declared, reasoned exception is not drift). A role missing
    from the catalog counts as drift (mismatched declaration).

    Returns ``None`` when the box declares no role (not applicable), when the
    tool/catalog can't be loaded (indeterminate — never false-alarm), or while a
    divergence hasn't yet persisted ``debounce_ticks`` consecutive ticks — role
    catalog (git) and unit state (converge/restarts) deploy independently, so a
    single tick can catch a fleet-roll window (same rationale as
    ``probe_parity_drift``). Severity ``degraded``: latent legibility debt, not
    an active failure.
    """
    if state_path is None:
        state_path = DEFAULT_ROLE_DRIFT_DEBOUNCE_PATH
    if deployment is None:
        try:
            from utils.rns_tree_perms import _read_rnsd_user
            service_user = _read_rnsd_user()
        except Exception:
            service_user = None
        deployment = _read_deployment_declaration(service_user)
    role, overrides = deployment
    if not role:
        _save_parity_streak(state_path, 0)
        return None  # box not role-declared (or unreadable) → not applicable

    if plan_fn is None:
        def plan_fn(r, ov):
            return _plan_role_actions(r, ov, meshforge_root)

    unknown_role = False
    try:
        actions = plan_fn(role, overrides)
    except KeyError:
        unknown_role = True
        actions = []
    except Exception:
        _save_parity_streak(state_path, 0)
        return None  # tool error → indeterminate, don't count toward streak
    if actions is None and not unknown_role:
        _save_parity_streak(state_path, 0)
        return None

    if unknown_role:
        items = [f"role '{role}' not in the fleet_roles.yaml catalog"]
    else:
        items = []
        for a in actions:
            verb = getattr(a, "verb", "")
            if verb in _ROLE_DRIFT_VERBS or (
                verb == "warn" and getattr(a, "required", False)
            ):
                items.append(
                    f"{getattr(a, 'item', '?')}: "
                    f"{getattr(a, 'current', '?')} -> {getattr(a, 'desired', '?')}"
                )
    if not items:
        _save_parity_streak(state_path, 0)
        return None

    streak = _load_parity_streak(state_path) + 1
    _save_parity_streak(state_path, streak)
    if streak < debounce_ticks:
        return None  # divergence seen, not yet confirmed across consecutive ticks

    shown = "; ".join(items[:4]) + (f" (+{len(items) - 4} more)" if len(items) > 4 else "")
    detail = (
        f"live unit state diverges from declared role '{role}' "
        f"({len(items)} item(s)): {shown} | confirmed over {streak} consecutive "
        f"ticks | documented service_overrides are honored (not drift). Review: "
        f"python3 scripts/provision_role.py (dry-run); converge with sudo "
        f"python3 scripts/provision_role.py --apply, or correct the declared role."
    )
    return Signal(
        cls="role_drift",
        subject=role,
        severity="degraded",
        detail=detail,
        extra={"items": items, "debounce_streak": streak},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: MQTT root-topic drift (Issue #77 — the msh/US split guard)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_MQTT_ROOT_DEBOUNCE_PATH = "/var/lib/meshforge/mqtt_root_debounce.json"

# Extract the effective root prefix from a meshtasticd json-uplink journal
# line: "JSON publish message to msh/2/json/LongFast/!32962f10, 163 bytes:..."
# → "msh". A region-form root (mqtt.root="msh/US") yields "msh/US" — exactly
# the pre-unification moc2 drift shape this probe exists to catch.
_MQTT_PUBLISH_TOPIC_RE = re.compile(
    r"JSON publish message to (\S+?)/2/json/"
)

# The GatewayConfig dataclass default (gateway/config.py MQTTBridgeConfig).
# Used when gateway.json omits the key — that IS the consumer's effective
# value, so comparing against it is honest, not a guess.
_GATEWAY_DEFAULT_ROOT_TOPIC = "msh"


def _read_declared_root_topic(service_user) -> Optional[str]:
    """Read ``mqtt_bridge.root_topic`` from the service user's gateway.json.

    The watchdog runs as sandboxed root — home is derived from the service
    user and READ directly, never escalate (the rns_version_drift lesson).
    Returns the configured value, the GatewayConfig default when the key is
    absent (that is the consumer's effective root), or None when the file is
    unreadable/unparseable (indeterminate → caller stays silent).
    """
    if not service_user:
        return None
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
        path = os.path.join(home, ".config", "meshforge", "gateway.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        mqtt = data.get("mqtt_bridge")
        if not isinstance(mqtt, dict):
            return _GATEWAY_DEFAULT_ROOT_TOPIC
        root = mqtt.get("root_topic", _GATEWAY_DEFAULT_ROOT_TOPIC)
        if isinstance(root, str) and root:
            return root.strip().strip("/")
        return _GATEWAY_DEFAULT_ROOT_TOPIC
    except (KeyError, OSError, ValueError, TypeError):
        return None


def probe_mqtt_root_drift(
    *,
    unit: str = "meshtasticd.service",
    lookback: str = "6h",
    journalctl_path: str = "journalctl",
    systemctl_path: str = "systemctl",
    main_pid: Optional[int] = None,
    newest_line_fn=None,
    declared_root: Optional[str] = None,
    service_user_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when the radio's MQTT publish root drifts from the declared root.

    Issue #77 (2026-06-06 fleet unification): the fleet standardized on an
    explicit ``mqtt.root msh`` after the msh/US topic-root split silently
    fractured the uplink namespace (moc2 flipped 06-06 23:41). The remaining
    hole: a zero-config radio join (or a factory-reset radio) reintroduces a
    divergent root and every consumer pinned to the declared root partially
    or fully misses its feed — the dark-feed class again, but with the CAUSE
    visible hours before ``channel_feed_dark`` can prove the symptom.

    Local invariant, no fleet consensus needed (brokers are per-box islands):
    the root prefix the radio actually publishes under — observable in
    meshtasticd's json-uplink journal lines, ``JSON publish message to
    <root>[/<region>]/2/json/<channel>/!<id>`` — must equal the box's own
    declared consumer root (``gateway.json mqtt_bridge.root_topic``; an
    absent key means the GatewayConfig default ``msh``, which is the
    effective value, so the comparison stays honest).

    Observation is journal-only: never queries the radio (a per-tick CLI
    ``--get mqtt.root`` would open a TCP connection against the PhoneAPI —
    the #17 contention class this probe family exists to police).

    Self-guards (returns None):
    - meshtasticd inactive (``service_inactive`` owns that)
    - no json publish lines in the lookback (unobservable ≠ drift — the
      RX-only collector case, same gate as ``channel_feed_dark``)
    - gateway.json unreadable / service user unresolvable (indeterminate)
    - drift seen but not yet ``debounce_ticks`` consecutive ticks (rides out
      an operator mid-rotation: radio flipped before config, or vice versa)

    Recovery: ``meshtastic --host localhost --set mqtt.root <declared>``
    (or fix root_topic in gateway.json if the radio is the intended truth),
    then verify the next journal publish line carries the declared root.
    """
    pid = main_pid if main_pid is not None else _resolve_main_pid(
        unit, systemctl_path=systemctl_path
    )
    if pid is None:
        return None

    if newest_line_fn is None:
        def newest_line_fn(pattern: str) -> Optional[str]:
            return _journal_newest_match(
                unit, pattern, lookback, journalctl_path=journalctl_path
            )

    line = newest_line_fn("JSON publish message to ")
    if line is None:
        return None  # no json uplink at all — unobservable, not drift
    m = _MQTT_PUBLISH_TOPIC_RE.search(line)
    if m is None:
        return None  # publish line shape changed — indeterminate, not drift
    observed = m.group(1).strip("/")

    if declared_root is not None:
        declared = declared_root.strip().strip("/")
    else:
        user_fn = service_user_fn
        if user_fn is None:
            from utils.rns_tree_perms import _read_rnsd_user
            user_fn = _read_rnsd_user
        declared = _read_declared_root_topic(user_fn())
    if not declared:
        return None  # declared side indeterminate — never alarm on a guess

    sp = state_path or DEFAULT_MQTT_ROOT_DEBOUNCE_PATH
    if observed == declared:
        _save_parity_streak(sp, 0)
        return None

    streak = _load_parity_streak(sp) + 1
    _save_parity_streak(sp, streak)
    if streak < debounce_ticks:
        return None  # drift seen, not yet confirmed across consecutive ticks

    detail = (
        f"Radio publishes MQTT under root '{observed}' but this box's "
        f"declared consumer root is '{declared}' "
        f"(gateway.json mqtt_bridge.root_topic) — confirmed over {streak} "
        f"consecutive ticks. Consumers subscribed under '{declared}' are "
        f"partially or fully deaf to this radio's uplink (the msh/US split, "
        f"Issue #77; a zero-config radio join is the usual cause). Fix: "
        f"meshtastic --host localhost --set mqtt.root '{declared}' (or "
        f"correct root_topic in gateway.json if the radio is the intended "
        f"truth), then verify the next 'JSON publish message to' journal "
        f"line carries '{declared}'."
    )
    return Signal(
        cls="mqtt_root_drift",
        subject="meshtasticd",
        severity="degraded",
        detail=detail,
        issue_ref=77,
        extra={
            "observed_root": observed,
            "declared_root": declared,
            "streak": streak,
            "lookback": lookback,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Cron-verdict coverage (Issue #78) — a cron WIRED to cron_verdict.sh that
# reported FAIL/CONCERN or went silent past its schedule cadence. Cross-
# references the crontab so a stale ORPHAN verdict (a one-off verdict for a
# cron that no longer exists, e.g. the diag24h_watchdog line) never fires.
# Inert until crons are wired — the regime is opt-in.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_CRON_VERDICT_DEBOUNCE_PATH = "/var/lib/meshforge/cron_verdict_debounce.json"
CRON_VERDICT_STALE_FLOOR_S = 2 * 3600.0      # don't flag faster than this (anti-flap)
CRON_VERDICT_CADENCE_MULT = 3.0              # stale if age > MULT × expected interval
_CRON_VERDICT_FALLBACK_MAX_S = 26 * 3600.0   # unparseable schedule → panel's 26h
# Wired-cron extraction is owned by fleet_snapshot._verdict_names_in_command
# (one regex, one extractor — honest_failure_modes #5; imported in the probe
# below so this probe and the fleet-snapshot orphan filter can never drift).


def _cron_max_interval(schedule: str) -> float:
    """Coarse expected-max gap (seconds) for a 5-field cron schedule or
    ``@keyword``. Intentionally approximate — catch gross silence, not exact
    scheduling. Unparseable → the panel's 26h fallback. ``@reboot`` → inf
    (only runs at boot, never stale-checkable)."""
    if not isinstance(schedule, str):
        return _CRON_VERDICT_FALLBACK_MAX_S
    s = schedule.strip()
    kw = {
        "@hourly": 3600.0, "@daily": 86400.0, "@midnight": 86400.0,
        "@weekly": 604800.0, "@monthly": 2592000.0,
        "@yearly": 31536000.0, "@annually": 31536000.0,
        "@reboot": float("inf"),
    }
    if s in kw:
        return kw[s]
    fields = s.split()
    if len(fields) < 5:
        return _CRON_VERDICT_FALLBACK_MAX_S
    minute, hour, dom, mon, dow = fields[:5]
    mm = re.match(r'^\*/(\d+)$', minute)
    if mm:
        try:
            return max(60.0, int(mm.group(1)) * 60.0)
        except ValueError:
            return _CRON_VERDICT_FALLBACK_MAX_S
    if minute == "*":
        return 60.0
    # specific minute from here → at most hourly granularity
    if hour == "*":
        return 3600.0
    hm = re.match(r'^\*/(\d+)$', hour)
    if hm:
        try:
            return max(3600.0, int(hm.group(1)) * 3600.0)
        except ValueError:
            return _CRON_VERDICT_FALLBACK_MAX_S
    # specific minute + specific hour
    if dow != "*":
        return 604800.0    # weekly
    if dom != "*" or mon != "*":
        return 2592000.0   # monthly-ish
    return 86400.0         # daily


def _read_operator_crontab_spool(name: Optional[str]) -> Optional[str]:
    """Read the operator's crontab from the spool — as root, in-process, no
    sudo (the watchdog's NoNewPrivileges sandbox forbids privilege change).
    Debian path first, then RHEL-style. None on missing/unreadable."""
    if not name or name == "root":
        return None
    for path in (f"/var/spool/cron/crontabs/{name}", f"/var/spool/cron/{name}"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except (FileNotFoundError, IsADirectoryError):
            continue
        except OSError:
            return None
    return None


def _read_operator_verdicts_log(home: Optional[str]) -> Optional[str]:
    """Read ``~/cron_verdicts.log`` as root, in-process. None on absent/unreadable."""
    if not home:
        return None
    try:
        with open(os.path.join(str(home), "cron_verdicts.log"),
                  "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (FileNotFoundError, IsADirectoryError):
        return None
    except OSError:
        return None


def probe_cron_verdict_stale(
    *,
    operator: Optional[Tuple[int, str]] = None,
    crontab_text: Optional[str] = None,
    verdicts_text: Optional[str] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when a cron WIRED to cron_verdict.sh reported FAIL/CONCERN or went
    silent past its schedule cadence — "silence is the failure mode" for the
    cron-verdict regime (Issue #78).

    Reads the operator's crontab (spool) + ``~/cron_verdicts.log`` directly as
    root (no sudo — watchdog sandbox). Only WIRED crons (a ``cron_verdict.sh
    <name>`` in the crontab line) are judged, so a stale ORPHAN verdict for a
    cron that no longer exists never false-alarms. INERT (None) on any box with
    no wired crons — the regime is opt-in. 2-tick debounce rides a mid-run
    window where a fresh run hasn't recorded yet. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_CRON_VERDICT_DEBOUNCE_PATH

        # 1. Resolve operator (root-safe) — only when nothing is injected.
        if operator is None and crontab_text is None and verdicts_text is None:
            try:
                from utils.fleet_test_runner import _find_operator_user
                operator = _find_operator_user()
            except Exception:
                operator = None
        op_name = operator[1] if operator else None

        # 2. Wired crontab → {name: schedule}. No wired cron → inert.
        if crontab_text is None:
            crontab_text = _read_operator_crontab_spool(op_name)
        wired: Dict[str, str] = {}
        if crontab_text:
            try:
                from utils.fleet_snapshot import (
                    _parse_crontab, _verdict_names_in_command)
                for job in _parse_crontab(crontab_text):
                    for name in _verdict_names_in_command(
                            job.get("command", "")):
                        wired[name] = job.get("schedule", "")
            except Exception:
                wired = {}
        if not wired:
            _save_parity_streak(sp, 0)   # nothing to watch — clear + inert
            return None

        # 3. Verdict log → latest verdict per name.
        if verdicts_text is None and operator is not None:
            home = None
            try:
                import pwd
                home = pwd.getpwuid(operator[0]).pw_dir
            except (KeyError, OSError):
                home = None
            verdicts_text = _read_operator_verdicts_log(home)
        latest: Dict[str, dict] = {}
        if verdicts_text:
            try:
                from utils.fleet_snapshot import _parse_cron_verdicts
                for v in _parse_cron_verdicts(verdicts_text, now):
                    latest[v["name"]] = v
            except Exception:
                latest = {}

        # 4. Cross-reference — judge ONLY wired crons (orphans ignored).
        failed: List[str] = []
        stale: List[str] = []
        for name, schedule in sorted(wired.items()):
            v = latest.get(name)
            if v is not None and v.get("status", "").upper().startswith(
                    ("FAIL", "CONCERN")):
                failed.append(f"{name}({v.get('status')})")
                continue
            max_age = _cron_max_interval(schedule)
            if max_age == float("inf"):
                continue   # @reboot — not stale-checkable
            threshold = max(CRON_VERDICT_STALE_FLOOR_S,
                            CRON_VERDICT_CADENCE_MULT * max_age)
            if v is None:
                stale.append(f"{name}(never)")
            elif float(v.get("age_s", 0.0)) > threshold:
                stale.append(f"{name}({int(float(v['age_s']) // 3600)}h)")

        if not failed and not stale:
            _save_parity_streak(sp, 0)
            return None

        # 5. Debounce — first sighting silent, fire on the 2nd consecutive tick.
        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            return None

        bits = []
        if failed:
            bits.append(f"{len(failed)} failing: " + ", ".join(failed[:5]))
        if stale:
            bits.append(f"{len(stale)} silent: " + ", ".join(stale[:5]))
        return Signal(
            cls="cron_verdict_stale",
            subject="cron",
            severity="degraded",
            detail=("Wired cron(s) unhealthy — " + "; ".join(bits)
                    + " (fix the job or re-run + re-verify; silence is the "
                    "failure mode)"),
            issue_ref=78,
            extra={"failed": failed, "stale": stale, "streak": streak,
                   "wired_count": len(wired)},
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: fleet box unreachable (2026-06-17, Leg D) — surface a fleet box
# the offline-monitor (fleet_offline_check.sh on the manager box) has confirmed
# DOWN into mini's brief + /fleet, so a dark box can't sit silent in a
# side-channel logfile (the .32 33h-dark lesson). The monitor's OWN death
# is covered by cron_verdict_stale (fleet_offline_check is verdict-wired).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_FLEET_UNREACHABLE_DEBOUNCE_PATH = (
    "/var/lib/meshforge/fleet_unreachable_debounce.json")
FLEET_STATE_STALE_S = 1800          # state file older than this → not current
FLEET_UNREACHABLE_WEDGE_S = 1800    # a box down longer than this → wedge severity


def _read_operator_fleet_state(home) -> Tuple[Optional[str], Optional[float]]:
    """Read ``~/fleet_offline_state.tsv`` + its mtime as root, in-process (no
    sudo — watchdog sandbox). Returns ``(text, mtime)`` or ``(None, None)`` on
    absent/unreadable (→ INERT: the monitor is manager-box-only)."""
    if not home:
        return None, None
    path = os.path.join(str(home), "fleet_offline_state.tsv")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return text, os.path.getmtime(path)
    except (FileNotFoundError, IsADirectoryError):
        return None, None
    except OSError:
        return None, None


def probe_fleet_box_unreachable(
    *,
    operator: Optional[Tuple[int, str]] = None,
    state_text: Optional[str] = None,
    state_mtime: Optional[float] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    stale_after_s: float = FLEET_STATE_STALE_S,
    wedge_after_s: float = FLEET_UNREACHABLE_WEDGE_S,
) -> Optional[Signal]:
    """Surface fleet boxes the offline-monitor has confirmed DOWN, into the spine
    the operator actually watches (mini warm brief + /fleet) — Leg D, 2026-06-17.

    Reads the manager box's ``~/fleet_offline_state.tsv`` (written by the hardened
    ``fleet_offline_check.sh``) directly as root. A row with ``alerted==1`` is a
    box unreachable past the monitor's 3-fail (~15 min) threshold that is already
    being re-paged; this probe makes it VISIBLE in the brief/panel so it can't sit
    silent in a side-channel logfile (the ".32 dark 33h, found by manually poking
    it" gap). The monitor owns the ntfy page; this probe is visibility, not a
    second page — its seed rule is ``propose_escalation`` (no duplicate ntfy).

    Self-guards None: no state file (not the manager box → INERT — the monitor is
    manager-box-only), or the file is STALE past ``stale_after_s`` (the monitor
    itself stopped — reporting frozen down-rows as current would be the
    absence-of-evidence trap; ``cron_verdict_stale`` owns the dead-cron alert,
    since ``fleet_offline_check`` is verdict-wired). 2-tick debounce. Back-compat
    with the pre-Leg-D 3-field state rows. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_FLEET_UNREACHABLE_DEBOUNCE_PATH

        if state_text is None:
            if operator is None:
                try:
                    from utils.fleet_test_runner import _find_operator_user
                    operator = _find_operator_user()
                except Exception:
                    operator = None
            home = None
            if operator is not None:
                try:
                    import pwd
                    home = pwd.getpwuid(operator[0]).pw_dir
                except (KeyError, OSError):
                    home = None
            state_text, state_mtime = _read_operator_fleet_state(home)

        if not state_text:
            _save_parity_streak(sp, 0)      # no monitor here → INERT
            return None

        # Stale file = the monitor stopped updating; do NOT read frozen rows as
        # current (cron_verdict_stale owns the dead-monitor alert).
        if state_mtime is not None and (now - state_mtime) > stale_after_s:
            _save_parity_streak(sp, 0)
            return None

        down: List[Tuple[str, float, int]] = []
        for line in state_text.splitlines():
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[0].strip():
                continue
            try:
                alerted = int(parts[2])
            except ValueError:
                continue
            if alerted != 1:
                continue
            try:
                down_since = float(parts[3]) if len(parts) > 3 and parts[3] else 0.0
            except ValueError:
                down_since = 0.0
            try:
                alert_count = int(parts[5]) if len(parts) > 5 and parts[5] else 0
            except ValueError:
                alert_count = 0
            down.append((parts[0].strip(), down_since, alert_count))

        if not down:
            _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            return None

        descs: List[str] = []
        max_down_min = 0
        sustained = False
        for name, ds, ac in sorted(down):
            if ds and now >= ds:
                mins = int((now - ds) // 60)
                max_down_min = max(max_down_min, mins)
                if (now - ds) > wedge_after_s:
                    sustained = True
                descs.append(f"{name} (~{mins}m, page #{ac})" if ac
                             else f"{name} (~{mins}m)")
            else:
                descs.append(name)
        return Signal(
            cls="fleet_box_unreachable",
            subject="fleet",
            severity="wedge" if sustained else "degraded",
            detail=("Fleet box(es) the offline-monitor confirms DOWN: "
                    + ", ".join(descs)
                    + " — surfaced here so a dark box can't sit silent (Leg D); "
                    "ntfy is re-paging on a cadence. Check the box."),
            issue_ref=None,
            extra={"down": [d[0] for d in sorted(down)],
                   "max_down_min": max_down_min, "streak": streak},
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: host frozen (2026-06-17 Leg C — the dude-claw out-of-band witness)
#
# An ESP32 (dude-claw) on the watched box's OWN subnet runs a host_probe tool
# over NATS; an out-of-band collector cron on the claw's brain box polls it and
# writes a verdict file. This probe READS that file (no NATS in the sandboxed
# watchdog) and surfaces HOST_FROZEN / UNREACHABLE / (sustained) UNKNOWN into
# mini's brief + /fleet — exactly the swap-thrash freeze class the box's own
# self-petted HW watchdog can't catch. Mirrors fleet_box_unreachable's
# file-read pattern + 2-tick debounce. Alert-only (propose_escalation).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_HOST_FROZEN_DEBOUNCE_PATH = "/var/lib/meshforge/host_frozen_debounce.json"
HOST_PROBE_STATE_STALE_S = 900   # verdict file older than this → the collector
                                 # stopped; cron_verdict_stale owns the dead-cron
                                 # alert (host_probe_check is verdict-wired)

# verdicts that mean "the target is in trouble" (→ wedge) vs degraded visibility
_HOST_FROZEN_WEDGE_VERDICTS = ("HOST_FROZEN", "UNREACHABLE")


def _read_host_probe_verdict(home) -> Tuple[Optional[str], Optional[float]]:
    """Read ``~/host_probe_state.json`` + its mtime as root, in-process (no sudo
    — watchdog sandbox). Returns ``(text, mtime)`` or ``(None, None)`` on
    absent/unreadable (→ INERT: the collector runs only on the claw's brain box)."""
    if not home:
        return None, None
    path = os.path.join(str(home), "host_probe_state.json")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return text, os.path.getmtime(path)
    except (FileNotFoundError, IsADirectoryError):
        return None, None
    except OSError:
        return None, None


def probe_host_frozen(
    *,
    operator: Optional[Tuple[int, str]] = None,
    state_text: Optional[str] = None,
    state_mtime: Optional[float] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    stale_after_s: float = HOST_PROBE_STATE_STALE_S,
) -> Optional[Signal]:
    """Surface a dude-claw out-of-band witness verdict (Leg C, 2026-06-17).

    Reads the brain box's ``~/host_probe_state.json`` (written by the
    out-of-band ``host_probe_check`` collector that polls the claw's
    ``host_probe`` tool over NATS). The claw sits on the watched box's own
    subnet, so it tells HOST_FROZEN (the IP stack answers but the app port
    serves no banner = kernel alive / userspace swap-wedged — the .32 class the
    box's self-petted HW watchdog can't catch) from UNREACHABLE (no TCP answer
    = host/path/SoC down). A sustained UNKNOWN (the claw witness itself couldn't
    be reached) surfaces as *degraded* — lost visibility is NOT "healthy"
    (honest_failure_modes #2), not silently swallowed.

    Self-guards None: no verdict file (not the brain box → INERT), STALE file
    (the collector stopped — cron_verdict_stale owns the dead-cron alert,
    host_probe_check is verdict-wired; reading a frozen verdict as current would
    be the absence-of-evidence trap), unparseable JSON (don't false-fire), or
    every target OK. 2-tick debounce. Alert-only (seed rule is
    propose_escalation — no ntfy). Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_HOST_FROZEN_DEBOUNCE_PATH

        if state_text is None:
            if operator is None:
                try:
                    from utils.fleet_test_runner import _find_operator_user
                    operator = _find_operator_user()
                except Exception:
                    operator = None
            home = None
            if operator is not None:
                try:
                    import pwd
                    home = pwd.getpwuid(operator[0]).pw_dir
                except (KeyError, OSError):
                    home = None
            state_text, state_mtime = _read_host_probe_verdict(home)

        if not state_text:
            _save_parity_streak(sp, 0)      # no collector here → INERT
            return None

        # Stale file = the collector stopped; do NOT read a frozen verdict as
        # current (cron_verdict_stale owns the dead-collector alert).
        if state_mtime is not None and (now - state_mtime) > stale_after_s:
            _save_parity_streak(sp, 0)
            return None

        try:
            doc = json.loads(state_text)
            targets = doc.get("targets") or []
        except (ValueError, TypeError, AttributeError):
            _save_parity_streak(sp, 0)      # garbage → don't false-fire
            return None

        bad: List[Tuple[str, str, str]] = []   # (name, verdict, raw)
        for t in targets:
            if not isinstance(t, dict):
                continue
            verdict = str(t.get("verdict") or "").upper()
            if not verdict or verdict == "OK":
                continue
            name = str(t.get("name") or t.get("host") or "?")
            raw = str(t.get("raw") or "")
            bad.append((name, verdict, raw))

        if not bad:
            _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            return None

        wedge = any(v in _HOST_FROZEN_WEDGE_VERDICTS for _, v, _ in bad)
        descs = [f"{n}: {v}" + (f" [{r}]" if r else "") for n, v, r in sorted(bad)]
        names = sorted({n for n, _, _ in bad})
        return Signal(
            cls="host_frozen",
            subject=names[0] if len(names) == 1 else "claw-witness",
            severity="wedge" if wedge else "degraded",
            detail=("dude-claw out-of-band witness: "
                    + "; ".join(descs)
                    + " — HOST_FROZEN = kernel alive but userspace wedged "
                    "(the self-petted HW watchdog can't catch this); UNREACHABLE "
                    "= host/path down; UNKNOWN = claw witness itself unreachable "
                    "(lost visibility). Alert-only; check the box."),
            issue_ref=None,
            extra={"targets": [{"name": n, "verdict": v} for n, v, _ in sorted(bad)],
                   "streak": streak},
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: router scout degraded (2026-07-11 OpenWrt-router arc)
#
# A router-class fleet member (OpenWrt One, future MikroTik/AREDN boxes)
# runs the meshforge-scout agent (templates/openwrt/); the manager box's
# router_scout_pull.sh cron mirrors each tick to
# ~/.local/share/meshforge/router_scout/<device>_tick.json. This probe
# READS those mirrors (no ssh in the sandboxed watchdog). It is
# DEFENSE-IN-DEPTH behind the pull's own eval (which also FAILs
# cron_verdict on a stale/ok=false tick): its added value is surfacing
# the per-device verdict into the watchdog spine (/fleet + mini brief,
# device as subject), and covering ticks that reach the mirror by any
# path other than the verdict-wired pull. Fires on: fresh mirror + stale
# captured_at (agent cron dark on the router while the mirror keeps
# being re-copied), tick ok=false, or an unparseable mirror (the pull
# validates before writing, so garbage = writer/shape drift). Mirrors
# host_frozen's file-read pattern + 2-tick debounce. degraded only —
# every observed condition is REMOTE (tracer_peer_unreachable lesson).
# Alert-only (propose_escalation).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_ROUTER_SCOUT_DEBOUNCE_PATH = "/var/lib/meshforge/router_scout_debounce.json"
ROUTER_SCOUT_MIRROR_SUBDIR = ".local/share/meshforge/router_scout"
ROUTER_SCOUT_MIRROR_STALE_S = 5400   # 3 × the pull cron's 30-min cadence —
                                     # older = the pull stopped; skip the file
                                     # (cron_verdict_stale owns the dead cron)
ROUTER_SCOUT_TICK_STALE_S = 2700     # 3 × the agent's */15 router cadence —
                                     # a fresh mirror whose captured_at is
                                     # older than this = agent dark on-router


def _read_router_scout_ticks(home) -> Optional[List[Tuple[str, str, float]]]:
    """Every mirrored tick as ``(filename, text, mtime)``, read as root
    in-process (no sudo — watchdog sandbox). None when the mirror dir is
    absent/unreadable (→ INERT: the pull runs only on the manager box)."""
    if not home:
        return None
    d = os.path.join(str(home), ROUTER_SCOUT_MIRROR_SUBDIR)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith("_tick.json"))
    except OSError:
        return None
    out: List[Tuple[str, str, float]] = []
    for n in names:
        p = os.path.join(d, n)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            out.append((n, text, os.path.getmtime(p)))
        except OSError:
            continue   # a vanished/unreadable single file is skipped, not a page
    return out


def probe_router_scout_degraded(
    *,
    operator: Optional[Tuple[int, str]] = None,
    ticks: Optional[List[Tuple[str, str, float]]] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    mirror_stale_s: float = ROUTER_SCOUT_MIRROR_STALE_S,
    tick_stale_s: float = ROUTER_SCOUT_TICK_STALE_S,
) -> Optional[Signal]:
    """Surface a degraded router-side meshforge-scout agent (2026-07-11).

    Reads the manager box's mirrored scout ticks (written by the
    verdict-wired ``router_scout_pull.sh``). Defense-in-depth: the pull's
    own eval also FAILs cron_verdict on these conditions — this probe's
    added value is the watchdog-spine surface (per-device subject into
    /fleet + mini) and coverage of mirrors landed by any path other than
    the pull. Fires ``degraded`` (never wedge — remote conditions) when,
    for any FRESH mirror:

      * the tick's ``captured_at`` is older than ``tick_stale_s`` — the
        agent cron died on the router while the pull keeps re-copying the
        same old tick (a fresh mtime hides the corpse from mtime-only
        consumers);
      * the tick self-reports ``ok=false`` (its ``errors[]`` are the
        agent's own tri-state witnesses: tmpfs data_dir, unreadable
        /proc, dead radio TCP, ...);
      * the tick is unparseable (the pull validates JSON before its
        atomic write, so garbage here is writer/shape drift, not a torn
        read).

    Self-guards None: no mirror dir (not the manager box → INERT), and a
    STALE mirror file (mtime past ``mirror_stale_s``) is *skipped* — the
    pull cron stopped and ``cron_verdict_stale`` owns that alert; reading
    a frozen mirror as current would be the absence-of-evidence trap.
    2-tick debounce; observed-clean resets. Alert-only. Never raises into
    the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_ROUTER_SCOUT_DEBOUNCE_PATH

        if ticks is None:
            if operator is None:
                try:
                    from utils.fleet_test_runner import _find_operator_user
                    operator = _find_operator_user()
                except Exception:
                    operator = None
            home = None
            if operator is not None:
                try:
                    import pwd
                    home = pwd.getpwuid(operator[0]).pw_dir
                except (KeyError, OSError):
                    home = None
            ticks = _read_router_scout_ticks(home)

        if not ticks:
            _save_parity_streak(sp, 0)      # no mirrors here → INERT
            return None

        bad: List[Tuple[str, str]] = []     # (device-or-filename, why)
        for name, text, mtime in ticks:
            if mtime is not None and (now - mtime) > mirror_stale_s:
                continue    # dead pull cron — cron_verdict_stale's beat
            try:
                tick = json.loads(text)
            except (ValueError, TypeError):
                bad.append((name, "unparseable mirrored tick — "
                                  "writer/shape drift"))
                continue
            if not isinstance(tick, dict):
                bad.append((name, "mirrored tick is not an object"))
                continue
            device = str(tick.get("device") or name)
            cap = tick.get("captured_at")
            if isinstance(cap, bool) or not isinstance(cap, (int, float)):
                bad.append((device, "tick has no captured_at"))
                continue
            age = now - float(cap)
            if age > tick_stale_s:
                bad.append((device,
                            f"agent dark on the router — tick captured "
                            f"{int(age)}s ago but the mirror is fresh "
                            f"(pull keeps copying a corpse)"))
                continue
            if tick.get("ok") is False:
                errs = tick.get("errors") or []
                first = str(errs[0]) if errs else "unspecified"
                bad.append((device,
                            f"agent self-report ok=false "
                            f"({len(errs)} witness(es): {first})"))

        if not bad:
            _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            return None

        devices = sorted({d for d, _ in bad})
        descs = [f"{d}: {why}" for d, why in sorted(bad)]
        return Signal(
            cls="router_scout_degraded",
            subject=devices[0] if len(devices) == 1 else "router-scout",
            severity="degraded",
            detail=("router scout degraded: " + "; ".join(descs)
                    + " — check the router's scout cron/conf "
                    "(/etc/meshforge/, templates/openwrt/README.md); the "
                    "pull channel itself is healthy or this would be "
                    "cron_verdict_stale instead."),
            issue_ref=None,
            extra={"routers": [{"device": d, "why": w}
                               for d, w in sorted(bad)],
                   "streak": streak},
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: ntfy loopback (2026-06-18 — ntfy receipt-heartbeat Phase 2)
#
# The alerting spine's OWN liveness. A manager-box collector cron
# (scripts/fleet_ntfy_loopback.sh) publishes a nonce'd, MIN-priority heartbeat
# to the FLEET topic, then polls ntfy.sh's poll API to confirm the nonce LOOPS
# BACK within a window; on a miss it escalates via the Phase-1 EMAIL backbone
# (ntfy is the suspect channel, so it does NOT page back through ntfy) and
# writes a verdict file. This probe READS that file (read-only: probes never
# send network traffic) and surfaces a miss into mini's brief + /fleet — the
# "send ≠ receipt" lesson aimed at the spine itself (the 2026-06-14→17 dark
# incident). Catches ntfy.sh-down / fleet-topic-publish-broken / sender-no-op;
# the operator-phone-on-wrong-topic case is Phase 3's tap-to-ack job. Mirrors
# host_frozen's file-read pattern + 2-tick debounce. Alert-only
# (propose_escalation — the collector owns the email page).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_NTFY_LOOPBACK_DEBOUNCE_PATH = "/var/lib/meshforge/ntfy_loopback_debounce.json"
NTFY_LOOPBACK_STATE_STALE_S = 5400   # FLOOR for the stale window. The EFFECTIVE
                                     # window scales to 3× the collector's OWN
                                     # recorded cadence (interval_s in the verdict
                                     # file), so a cron cadence change (e.g.
                                     # */30 → q2hr) needs NO second edit here —
                                     # the cron is the single source of truth
                                     # (honest_failure_modes #5: one cadence, not
                                     # two drifting copies). This floor applies
                                     # only when interval_s is absent/insane.
                                     # Past the window the collector stopped →
                                     # cron_verdict_stale owns the dead-cron alert
                                     # (fleet_ntfy_loopback is verdict-wired).
NTFY_LOOPBACK_WEDGE_MISSES = 3       # this many consecutive misses → wedge


def _read_ntfy_loopback_state(home) -> Tuple[Optional[str], Optional[float]]:
    """Read ``~/ntfy_loopback_state.json`` + its mtime as root, in-process (no
    sudo — watchdog sandbox). Returns ``(text, mtime)`` or ``(None, None)`` on
    absent/unreadable (→ INERT: the loopback monitor is manager-box-only)."""
    if not home:
        return None, None
    path = os.path.join(str(home), "ntfy_loopback_state.json")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return text, os.path.getmtime(path)
    except (FileNotFoundError, IsADirectoryError):
        return None, None
    except OSError:
        return None, None


def probe_ntfy_loopback(
    *,
    operator: Optional[Tuple[int, str]] = None,
    state_text: Optional[str] = None,
    state_mtime: Optional[float] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    stale_after_s: float = NTFY_LOOPBACK_STATE_STALE_S,
    wedge_after_misses: int = NTFY_LOOPBACK_WEDGE_MISSES,
) -> Optional[Signal]:
    """Surface a failure of the ntfy alerting channel's OWN loopback — Phase 2 of
    the ntfy receipt-heartbeat arc (2026-06-18).

    Reads the manager box's ``~/ntfy_loopback_state.json`` (written by
    ``scripts/fleet_ntfy_loopback.sh``, which publishes a nonce'd min-priority
    heartbeat to the fleet topic and polls ntfy.sh to confirm it loops back).
    ``received == false`` means the channel is dark from this box's vantage —
    either the publish failed (sender-no-op / network) or it published but the
    heartbeat never came back (ntfy.sh down / the fleet topic's delivery
    broken). The collector OWNS the email page (it escalates via the Phase-1
    EMAIL backbone — ntfy is the suspect channel, so it does NOT page back
    through ntfy); this probe is VISIBILITY into mini's brief + /fleet
    (propose_escalation, no duplicate page — the fleet_box_unreachable model).

    Self-guards None: no verdict file (not the manager box → INERT — the monitor
    is manager-box-only), STALE file past ``stale_after_s`` (the collector
    stopped — reading a frozen verdict as current is the absence-of-evidence
    trap; ``cron_verdict_stale`` owns the dead-cron alert, fleet_ntfy_loopback is
    verdict-wired), unparseable JSON, a ``received`` that is not an explicit bool
    (torn/partial write — indeterminate, never read as a miss; honest_failure_
    modes #1/#2), or ``received == true``. 2-tick debounce rides a one-off
    transient miss. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_NTFY_LOOPBACK_DEBOUNCE_PATH

        if state_text is None:
            if operator is None:
                try:
                    from utils.fleet_test_runner import _find_operator_user
                    operator = _find_operator_user()
                except Exception:
                    operator = None
            home = None
            if operator is not None:
                try:
                    import pwd
                    home = pwd.getpwuid(operator[0]).pw_dir
                except (KeyError, OSError):
                    home = None
            state_text, state_mtime = _read_ntfy_loopback_state(home)

        if not state_text:
            _save_parity_streak(sp, 0)      # no monitor here → INERT
            return None

        try:
            doc = json.loads(state_text)
        except (ValueError, TypeError):
            _save_parity_streak(sp, 0)      # garbage → don't false-fire
            return None
        if not isinstance(doc, dict):
            _save_parity_streak(sp, 0)
            return None

        # The stale window scales with the collector's OWN recorded cadence
        # (interval_s) so changing the cron cadence (e.g. */30 → q2hr) needs no
        # second edit here — the cron is the single source of truth
        # (honest_failure_modes #5). stale_after_s is the FLOOR, applied when
        # interval_s is absent/insane; an absurd recorded value is clamped (#6).
        interval = doc.get("interval_s")
        if (isinstance(interval, (int, float)) and not isinstance(interval, bool)
                and 60 <= interval <= 21600):
            effective_stale = max(float(interval) * 3.0, float(stale_after_s))
        else:
            effective_stale = float(stale_after_s)

        # Stale file = the collector stopped; cron_verdict_stale owns that alert.
        if state_mtime is not None and (now - state_mtime) > effective_stale:
            _save_parity_streak(sp, 0)
            return None

        received = doc.get("received")
        # Indeterminate (key missing / torn write / not a bool) → HOLD, never
        # read as a miss — absence of evidence is not a failure (#80 class).
        if not isinstance(received, bool):
            _save_parity_streak(sp, 0)
            return None
        if received:
            _save_parity_streak(sp, 0)      # looped back → healthy
            return None

        # received is False → a real miss.
        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            return None

        published_ok = doc.get("published_ok")
        try:
            misses = int(doc.get("consecutive_misses", 0))
        except (ValueError, TypeError):
            misses = 0

        if published_ok is False:
            cause = ("could NOT publish to the fleet ntfy topic from this box "
                     "(sender no-op / network) — ntfy pages from here will not send")
        else:
            cause = ("published but the heartbeat did NOT loop back via ntfy.sh "
                     "(server or fleet-topic delivery broken) — pages may be lost")
        return Signal(
            cls="ntfy_loopback",
            subject="ntfy",
            severity="wedge" if misses >= wedge_after_misses else "degraded",
            detail=("ntfy alerting channel loopback FAILED: " + cause
                    + f" ({misses} consecutive miss(es)). The collector escalates "
                    "via the Phase-1 EMAIL backbone; this is visibility. "
                    "'send ≠ receipt' — the spine watching itself."),
            issue_ref=None,
            extra={"published_ok": (published_ok if isinstance(published_ok, bool)
                                    else None),
                   "consecutive_misses": misses, "streak": streak},
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: ntfy ack stale (2026-06-18 — ntfy receipt-heartbeat Phase 3)
#
# The ONLY rung that confirms the operator's DEVICE. A manager-box cron
# (scripts/fleet_ntfy_ack.sh) sends a WEEKLY tap-to-ack page to the fleet topic
# with an ntfy action button; the tap makes the PHONE POST to a dedicated
# ack-topic (<fleet>-ack), which the cron polls. consecutive_unacked_pings grows
# each unacked week (reset on ack); the cron escalates via the Phase-1 EMAIL
# backbone at >=2 unacked, and this READ-ONLY probe surfaces it into mini's brief
# + /fleet. Catches exactly the 2026-06-14→17 incident (phone on a wrong/dead
# topic, app killed, notifications off) — what loopback (Phase 2, a different
# subscriber) structurally cannot. Mirrors the host_frozen file-read pattern +
# 2-tick debounce. Alert-only (propose_escalation — the cron owns the email).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_NTFY_ACK_DEBOUNCE_PATH = "/var/lib/meshforge/ntfy_ack_debounce.json"
NTFY_ACK_STATE_STALE_S = 14400   # state file older than this → the cron stopped;
                                 # cron_verdict_stale owns the dead-cron alert
                                 # (fleet_ntfy_ack is verdict-wired). ~4× hourly.
NTFY_ACK_WEDGE_PINGS = 2         # this many consecutive unacked weeks → wedge


def _read_ntfy_ack_state(home) -> Tuple[Optional[str], Optional[float]]:
    """Read ``~/ntfy_ack_state.json`` + its mtime as root, in-process (no sudo —
    watchdog sandbox). Returns ``(text, mtime)`` or ``(None, None)`` on
    absent/unreadable (→ INERT: the ack monitor is manager-box-only)."""
    if not home:
        return None, None
    path = os.path.join(str(home), "ntfy_ack_state.json")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return text, os.path.getmtime(path)
    except (FileNotFoundError, IsADirectoryError):
        return None, None
    except OSError:
        return None, None


def probe_ntfy_ack_stale(
    *,
    operator: Optional[Tuple[int, str]] = None,
    state_text: Optional[str] = None,
    state_mtime: Optional[float] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    stale_after_s: float = NTFY_ACK_STATE_STALE_S,
    wedge_after_pings: int = NTFY_ACK_WEDGE_PINGS,
) -> Optional[Signal]:
    """Surface an UNCONFIRMED operator device — Phase 3 of the ntfy receipt-
    heartbeat arc (2026-06-18).

    Reads the manager box's ``~/ntfy_ack_state.json`` (written by
    ``scripts/fleet_ntfy_ack.sh``, which sends a weekly tap-to-ack page and polls
    the ack-topic the phone POSTs to on tap). ``consecutive_unacked_pings >= 1``
    means a weekly page went out with no tap-to-ack — the operator's phone may
    not be receiving fleet alerts (wrong/dead topic, app killed, notifications
    off), the exact 2026-06-14→17 failure that loopback (a different subscriber)
    cannot catch. The cron OWNS the email page (escalates at >=2 unacked weeks);
    this probe is VISIBILITY into mini's brief + /fleet (propose_escalation, no
    duplicate page — the fleet_box_unreachable model).

    Self-guards None: no state file (not the manager box → INERT), STALE file
    past ``stale_after_s`` (the cron stopped — cron_verdict_stale owns the
    dead-cron alert, fleet_ntfy_ack is verdict-wired), unparseable JSON, a
    ``consecutive_unacked_pings`` that is not an int (indeterminate → held, never
    invented; a JSON bool is rejected too), or ``<= 0`` (acked → healthy; also
    covers never-pinged, which keeps the counter at 0 so the first-week grace
    never false-alarms). 2-tick debounce. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_NTFY_ACK_DEBOUNCE_PATH

        if state_text is None:
            if operator is None:
                try:
                    from utils.fleet_test_runner import _find_operator_user
                    operator = _find_operator_user()
                except Exception:
                    operator = None
            home = None
            if operator is not None:
                try:
                    import pwd
                    home = pwd.getpwuid(operator[0]).pw_dir
                except (KeyError, OSError):
                    home = None
            state_text, state_mtime = _read_ntfy_ack_state(home)

        if not state_text:
            _save_parity_streak(sp, 0)      # no monitor here → INERT
            return None

        if state_mtime is not None and (now - state_mtime) > stale_after_s:
            _save_parity_streak(sp, 0)      # cron stopped → cron_verdict_stale owns it
            return None

        try:
            doc = json.loads(state_text)
        except (ValueError, TypeError):
            _save_parity_streak(sp, 0)
            return None
        if not isinstance(doc, dict):
            _save_parity_streak(sp, 0)
            return None

        unacked = doc.get("consecutive_unacked_pings")
        # Indeterminate (missing / not an int / a JSON bool) → HOLD, never invent.
        if not isinstance(unacked, int) or isinstance(unacked, bool):
            _save_parity_streak(sp, 0)
            return None
        if unacked <= 0:
            _save_parity_streak(sp, 0)      # acked (or never pinged) → healthy
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            return None

        return Signal(
            cls="ntfy_ack_stale",
            subject="ntfy",
            severity="wedge" if unacked >= wedge_after_pings else "degraded",
            detail=(f"weekly fleet-alert ack UNCONFIRMED — {unacked} consecutive "
                    "weekly page(s) with no tap-to-ack. Your phone may not be "
                    "receiving fleet pages (wrong/dead topic, app killed, "
                    "notifications off — the exact 06-14→17 failure). Tap "
                    "'Got it' on the next weekly page, or check your ntfy "
                    "subscription. The cron escalates via the Phase-1 EMAIL "
                    "backbone; this is visibility."),
            issue_ref=None,
            extra={"consecutive_unacked_pings": unacked, "streak": streak},
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: kernel reboot pending (2026-06-09 version-updates arc — the
# 6.12.75-straggler guard: moc/moc1/moc3/meshanchor-server silently ran
# an old kernel for days while a newer one sat installed; nothing paged)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_KERNEL_REBOOT_DEBOUNCE_PATH = "/var/lib/meshforge/kernel_reboot_debounce.json"

# "6.12.75+rpt-rpi-2712" → ("6.12.75", "+rpt-rpi-2712")
# "6.8.0-1057-raspi"     → ("6.8.0", "-1057-raspi")
_KERNEL_RELEASE_RE = re.compile(r"^(\d+(?:\.\d+)+)(.*)$")
# Ubuntu/Debian build segment immediately after the dotted core: "-1057-raspi"
# → build 1057, flavor remainder "-raspi". Trailing "(?:[-+.].*)?$" ensures the
# digits form a whole segment ("-1057raspi" does NOT split).
_KERNEL_BUILD_RE = re.compile(r"^-(\d+)((?:[-+.].*)?)$")


def _parse_kernel_release(release) -> Optional[Tuple[Tuple[int, ...], str]]:
    """Split a kernel release string into (comparable numeric tuple, flavor).

    The numeric tuple is the dotted core plus any Ubuntu-style ``-NNN`` build
    segments that follow it (``6.8.0-1057-raspi`` → ``(6, 8, 0, 1057)``); the
    flavor is everything left over (``-raspi`` / ``+rpt-rpi-2712``). RPi boxes
    install MULTIPLE flavors side by side (rpi-v8 AND rpi-2712), so ordering is
    only meaningful between identical flavors — callers must never compare
    across flavors. Returns None on anything unparseable (a module dir that
    fails to parse is SKIPPED by the caller, never treated as newer or older).
    """
    if not isinstance(release, str):
        return None
    m = _KERNEL_RELEASE_RE.match(release.strip())
    if m is None:
        return None
    try:
        nums = [int(x) for x in m.group(1).split(".")]
    except ValueError:
        return None
    rest = m.group(2)
    while True:
        bm = _KERNEL_BUILD_RE.match(rest)
        if bm is None:
            break
        nums.append(int(bm.group(1)))
        rest = bm.group(2)
    return tuple(nums), rest


def probe_kernel_reboot_pending(
    *,
    modules_dir: str = "/lib/modules",
    reboot_required_path: str = "/var/run/reboot-required",
    running_release: Optional[str] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when this box is running an older kernel than it has installed.

    The 2026-06-09 version-updates arc found moc/moc1/moc3/meshanchor-server
    silently running kernel 6.12.75 while 6.18.x was installed or available —
    moc1 had 6.18.33 INSTALLED but not running for days, and nothing paged.
    Reboot pending = EITHER ``/var/run/reboot-required`` exists (Ubuntu boxes)
    OR a NEWER **same-flavor** kernel sits under ``/lib/modules`` than the one
    in ``os.uname().release``. Flavor discipline is load-bearing: RPi boxes
    install rpi-v8 AND rpi-2712 simultaneously, so the comparison runs ONLY
    against same-flavor entries (see ``_parse_kernel_release``).

    Read-only, sandbox-safe (no sudo — the watchdog's NoNewPrivileges sandbox
    forbids it; ``/lib/modules`` and the flag file are world-readable anyway).

    Honest failure modes: ``/lib/modules`` unreadable/empty, the running
    release unparseable, or no same-flavor sibling entries → that leg is
    indeterminate (never false-alarm) while the reboot-required-file leg still
    works independently; unparseable module dirs are skipped. Observed-healthy
    AND indeterminate paths both reset the debounce streak (mirrors
    ``probe_role_drift``). 2-tick debounce rides out a tick that lands mid-
    upgrade (dpkg unpacking the new modules tree). Severity ``degraded``: the
    box serves fine — it is running known-stale code until a reboot.
    """
    sp = state_path or DEFAULT_KERNEL_REBOOT_DEBOUNCE_PATH

    # Leg A — distro reboot-required flag (independent of kernel parsing).
    try:
        reboot_required = os.path.exists(reboot_required_path)
    except OSError:
        reboot_required = False

    # Leg B — newer same-flavor kernel installed under /lib/modules.
    if running_release is None:
        running_release = os.uname().release
    running_parsed = _parse_kernel_release(running_release)

    newest_installed: Optional[str] = None
    newer_found = False
    if running_parsed is not None:
        run_nums, run_flavor = running_parsed
        try:
            entries = [e.name for e in os.scandir(modules_dir) if e.is_dir()]
        except OSError:
            entries = []  # unreadable → leg indeterminate, never an alarm
        best: Optional[Tuple[Tuple[int, ...], str]] = None
        for name in entries:
            parsed = _parse_kernel_release(name)
            if parsed is None:
                continue  # unparseable dir is skipped, not newer/older
            nums, flavor = parsed
            if flavor != run_flavor:
                continue  # different flavor (rpi-v8 vs rpi-2712) — never compare
            if best is None or nums > best[0]:
                best = (nums, name)
        if best is not None:
            newest_installed = best[1]
            newer_found = best[0] > run_nums

    if not reboot_required and not newer_found:
        # Observed-healthy (running == newest same-flavor, no flag) and the
        # indeterminate shapes (unreadable/empty modules dir, unparseable
        # release, no same-flavor sibling) all land here: reset, stay silent.
        _save_parity_streak(sp, 0)
        return None

    streak = _load_parity_streak(sp) + 1
    _save_parity_streak(sp, streak)
    if streak < debounce_ticks:
        return None  # pending seen, not yet confirmed across consecutive ticks

    reasons = []
    if newer_found and newest_installed:
        reasons.append(
            f"running {running_release} but {newest_installed} is installed "
            f"under {modules_dir}"
        )
    if reboot_required:
        reasons.append(f"{reboot_required_path} flag present")
    detail = (
        f"Kernel reboot pending — {'; '.join(reasons)} | confirmed over "
        f"{streak} consecutive ticks | the 2026-06-09 version-updates arc "
        f"found boxes silently running a stale kernel for days. Fix: schedule "
        f"a clean reboot (planned reboots through clean shutdown record "
        f"boot_health clean-exit)."
    )
    return Signal(
        cls="kernel_reboot_pending",
        subject=running_release,
        severity="degraded",
        detail=detail,
        extra={
            "running": running_release,
            "newest_installed": newest_installed,
            "reboot_required_file": reboot_required,
            "debounce_streak": streak,
        },
    )




# ─────────────────────────────────────────────────────────────────────
# Probe: AREDN local-source dark (Phase 0 AREDN organ, 2026-06-12)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_AREDN_SOURCE_DEBOUNCE_PATH = "/var/lib/meshforge/aredn_source_debounce.json"

# Diagnostics reasons that mean "configured, but the organ sees nothing".
# "unreachable" = AREDN node / LAN path dark; "not_configured" = the RUNNING
# service predates the config file (settings load at startup — restart loads).
_AREDN_DARK_REASONS = ("unreachable", "not_configured")


def _read_configured_aredn_ips(service_user) -> Optional[List[str]]:
    """Read ``aredn_node_ips`` from the service user's map_settings.json.

    Returns the configured list (possibly empty = organ deliberately off) or
    None when the file is unreadable/unparseable — indeterminate, the caller
    must stay silent. Sandboxed-root direct read, never escalate (the
    rns_version_drift lesson).
    """
    if not service_user:
        return None
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
        path = os.path.join(home, ".config", "meshforge", "map_settings.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        raw = data.get("aredn_node_ips", [])
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        return [ip.strip() for ip in raw if isinstance(ip, str) and ip.strip()]
    except (KeyError, OSError, ValueError, TypeError):
        return None


def _fetch_local_status_json(status_url: str, timeout_s: float) -> Optional[dict]:
    """GET the local map status JSON. None on ANY failure (indeterminate)."""
    import urllib.request
    try:
        req = urllib.request.Request(
            status_url, headers={"User-Agent": "meshforge-watchdog"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def probe_aredn_source_dark(
    *,
    status_url: str = "http://127.0.0.1:5000/api/status",
    timeout_s: float = 3.0,
    configured_ips: Optional[List[str]] = None,
    service_user_fn=None,
    status_fetch_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when a CONFIGURED local AREDN sysinfo source has gone dark.

    Phase 0 of the AREDN arc (2026-06-12): the box at the AREDN site was
    found with its local AREDN organ silently dormant — ``aredn_node_ips``
    never set, every "AREDN" node on the map coming from the worldmap
    fallback. Once the organ IS configured, two dark shapes must page
    instead of rotting silently (silence is the failure mode):

    - ``unreachable`` — none of the configured node IPs answered sysinfo
      (node down, LAN path broken, or the node's :8080 service dead).
    - ``not_configured`` reported by a RUNNING service while the settings
      file carries IPs — the service started before the config landed;
      a restart loads it. Without this leg, "I configured it" and "it
      runs" disagree forever and nobody is told.

    Reads intent from the service user's map_settings.json (sandboxed-root
    direct read) and observation from the local map's ``/api/status``
    ``source_diagnostics.aredn`` block (written fresh on every collect).

    Honest failure modes: unreadable settings → None (intent unknown, never
    alarm); empty/absent ``aredn_node_ips`` → None + streak reset (organ
    deliberately off — INERT by design on the 95% of boxes); status endpoint
    unreachable/malformed or diagnostics block absent → None with the streak
    HELD (http_local owns the wedge; a one-tick status hiccup must not erase
    confirmed-dark progress); ``no_positions``, ``slow_sysinfo`` or
    ``yielded>0`` → healthy, streak reset (reachable-but-no-GPS, or
    reachable-but-slow on a constrained mips_24kc router, is an alive organ —
    slow ≠ dark, the 2026-06-16 moc5 lesson); unknown reason strings →
    indeterminate, held. 2-tick debounce rides out a single failed collect
    (node reboot, transient LAN blip). Severity ``degraded`` — the map keeps
    serving, the AREDN leg is blind.
    """
    ips = configured_ips
    if ips is None:
        user_fn = service_user_fn
        if user_fn is None:
            from utils.rns_tree_perms import _read_rnsd_user
            user_fn = _read_rnsd_user
        ips = _read_configured_aredn_ips(user_fn())
    if ips is None:
        return None  # intent unreadable — indeterminate, never alarm
    sp = state_path or DEFAULT_AREDN_SOURCE_DEBOUNCE_PATH
    if not ips:
        _save_parity_streak(sp, 0)
        return None  # organ deliberately not configured — inert by design

    fetch = status_fetch_fn or (
        lambda: _fetch_local_status_json(status_url, timeout_s)
    )
    status = fetch()
    if not isinstance(status, dict):
        return None  # status unreadable — hold streak; http_local owns the wedge
    diags = status.get("source_diagnostics")
    if not isinstance(diags, dict):
        return None  # diagnostics surface absent — indeterminate, hold
    diag = diags.get("aredn")
    if not isinstance(diag, dict):
        return None  # no aredn entry yet (no collect since start) — hold

    yielded = diag.get("yielded")
    reason = diag.get("reason_if_zero")
    if isinstance(yielded, int) and yielded > 0:
        _save_parity_streak(sp, 0)
        return None  # organ alive
    if reason in ("no_positions", "slow_sysinfo"):
        _save_parity_streak(sp, 0)
        return None  # reachable (no GPS, or sysinfo slow) — alive, NOT dark
    if reason not in _AREDN_DARK_REASONS:
        return None  # unknown/absent reason — indeterminate, hold

    streak = _load_parity_streak(sp) + 1
    _save_parity_streak(sp, streak)
    if streak < debounce_ticks:
        return None  # dark seen, not yet confirmed across consecutive ticks

    if reason == "not_configured":
        hint = (
            "the RUNNING service reports not_configured while map_settings.json "
            "carries aredn_node_ips — it started before the config landed; fix: "
            "sudo systemctl restart meshforge-map.service"
        )
    else:
        hint = (
            "none of the configured AREDN node IPs answered sysinfo — check the "
            "node (web UI :8080), its power/PoE, and the LAN path from this box"
        )
    detail = (
        f"AREDN local source dark — configured IPs {ips} but the collector "
        f"reports '{reason}' (yielded 0), confirmed over {streak} consecutive "
        f"ticks. {hint}."
    )
    return Signal(
        cls="aredn_source_dark",
        subject=ips[0],
        severity="degraded",
        detail=detail,
        extra={
            "configured_ips": list(ips),
            "reason": reason,
            "attempted": diag.get("attempted"),
            "yielded": yielded,
            "debounce_streak": streak,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: inherited-app drift (Action 5, 2026-06-21 upstream-app ownership)
# An INHERITED upstream app checkout (origin NOT ours) carrying an
# unversioned tracked-file code patch — a hand-edit that lives in no repo
# we control and is one `git pull` from silent deletion (the rescued
# .32 + dev-box bot patches were exactly this). Enforces policy §4.2.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_INHERITED_DRIFT_DEBOUNCE_PATH = (
    "/var/lib/meshforge/inherited_app_drift_debounce.json")

# A git checkout is OWNED (skip it — already version-controlled) when its
# origin URL names our GitHub org. Everything else with a remote is INHERITED
# upstream — the risk class this probe polices. Matched case-insensitively
# against the whole URL (covers https://github.com/Nursedude/… and
# git@github.com:Nursedude/…).
_OWNED_ORIGIN_MARKER = "nursedude"

# Top-level basenames never treated as an inherited fleet app even with a
# non-owned origin (PINS.md "Excluded from pinning"): standard tool installs
# and separately-governed forks. wireclaw-dudeclaw is the dude-claw FIRMWARE
# fork (its own FORK invariant — never commit on the dudeclaw branch, so it is
# intentionally dirty); nvm is a tool, not a fleet app. (wireclaw also lives
# nested under ~/src so the top-level scan misses it anyway — this is the
# belt-and-suspenders guard.)
_INHERITED_SCAN_EXCLUDE = frozenset({".nvm", "nvm", "wireclaw-dudeclaw"})

# Tracked-file modifications that are MACHINE-GENERATED dependency metadata,
# NOT a hand-edited "code patch" (policy §4.3 config≠code). npm/yarn/pip/cargo
# regenerate these on install — the MeshSense package*.json churn is exactly
# this and must NOT read as an unversioned source patch. Matched by exact
# basename or a ``*.lock`` suffix. Deliberately small + generic: ONE constant,
# no per-app allowlist that would drift (honest_failure_modes #5). A real
# hand-edited source file is never on this list, so it still fires.
_BENIGN_TRACKED_BASENAMES = frozenset({
    "package.json", "package-lock.json", "npm-shrinkwrap.json",
    "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    "Cargo.lock", "composer.lock", "Gemfile.lock",
})


def _read_git_origin_url(repo_path: str) -> Optional[str]:
    """Origin remote URL from ``<repo>/.git/config`` — read DIRECTLY, no git
    subprocess.

    Why not ``git remote get-url``? Root running git on a user-owned checkout
    hits the 'dubious ownership' refusal, and spawning a process for the
    owned-repo majority (every box has /opt/meshforge etc.) is wasteful. A flat
    read of the config + a tiny INI walk classifies owned-vs-inherited with no
    spawn at all. Returns the URL, or None when the file/section/key is absent
    or unreadable — the caller then SKIPS the repo (can't classify → never
    guess owned-vs-inherited; indeterminate is not 'inherited')."""
    cfg = os.path.join(repo_path, ".git", "config")
    try:
        with open(cfg, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    in_origin = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            # [remote "origin"] — tolerate whitespace variants.
            in_origin = (line.replace(" ", "").lower() == '[remote"origin"]')
            continue
        if in_origin:
            m = re.match(r"url\s*=\s*(\S+)", line)
            if m:
                return m.group(1)
    return None


def _git_tracked_modifications(
    repo_path: str, git_path: str = "git", timeout_s: float = 10.0,
) -> Optional[List[str]]:
    """Tracked-file paths with uncommitted modifications in ``repo_path``
    (``git status --porcelain --untracked-files=no --ignore-submodules=all``).

    UNTRACKED files are excluded by the flag (Raven's raven.conf, ucode's
    build/ artifacts → not a patch, the policy's config-not-code line).
    SUBMODULES are excluded too (``--ignore-submodules=all``): a vendored
    submodule's own working-tree churn is a dependency state, not a hand-edited
    source patch in the PARENT app — MeshSense's ``api/webbluetooth`` /
    ``api/meshtastic-js`` gitlinks show ``S.M.`` from npm/build activity and
    must NOT read as a MeshSense source edit (verified against moc5's real tree
    2026-06-21; the #78 synthetic-vs-real lesson — a clean-package.json fixture
    missed this). Returns ``None`` on ANY git error/timeout — a git failure must
    NEVER read as a clean tree (honest_failure_modes #1/#2). ``-c safe.directory``
    lets sandboxed root status a user-owned checkout without the dubious-
    ownership refusal; ``-c core.fileMode=false`` keeps a mere perms-bit diff
    from masquerading as a content edit."""
    try:
        proc = subprocess.run(
            [git_path,
             "-c", f"safe.directory={repo_path}",
             "-c", "core.fileMode=false",
             "-C", repo_path,
             "status", "--porcelain", "--untracked-files=no",
             "--ignore-submodules=all"],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None  # git refused/errored → indeterminate, not "clean"
    files: List[str] = []
    for line in proc.stdout.splitlines():
        # porcelain v1: "XY <path>" (or "XY <old> -> <new>" for renames/copies).
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        path = path.strip('"')
        if path:
            files.append(path)
    return files


def _real_code_patches(files: List[str]) -> List[str]:
    """From a tracked-modification list, drop machine-generated dependency
    manifests/lockfiles (benign churn) and keep the real code/config patches —
    the unversioned-source-edit defect (R1). A path is benign iff its basename
    is a known manifest or ends in ``.lock``."""
    out: List[str] = []
    for p in files:
        base = os.path.basename(p)
        if base in _BENIGN_TRACKED_BASENAMES or base.endswith(".lock"):
            continue
        out.append(p)
    return out


def _iter_inherited_checkouts(
    scan_roots,
    *,
    exclude=_INHERITED_SCAN_EXCLUDE,
    origin_fn=_read_git_origin_url,
    max_repos: int = 40,
):
    """Yield ``(repo_path, origin_url)`` for each INHERITED (non-owned-origin)
    git checkout at the TOP LEVEL of any ``scan_roots`` dir.

    Top-level only (bounded; deep-nested checkouts like ~/src/wireclaw-dudeclaw
    are separately governed — PINS.md). Owned (Nursedude-origin) repos and the
    explicit excludes are skipped; a repo whose origin can't be read is skipped
    (can't classify → don't guess). Caps at ``max_repos`` (runaway guard)."""
    seen = 0
    for root in scan_roots:
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if seen >= max_repos:
                return
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            if entry.name in exclude:
                continue
            if not os.path.isdir(os.path.join(entry.path, ".git")):
                continue
            url = origin_fn(entry.path)
            if not url:
                continue  # no readable origin → can't classify, skip
            if _OWNED_ORIGIN_MARKER in url.lower():
                continue  # owned fork — version-controlled already
            seen += 1
            yield entry.path, url


def _resolve_operator_home(service_user_fn=None) -> Optional[str]:
    """Resolve the operator's home dir (where inherited app checkouts live),
    root-context safe. Tries the live --user-bus operator first (the
    cron_verdict/synth_soak pattern — linger is on for the mini units), then the
    rnsd service user (the dominant drift-probe pattern). None if neither
    resolves to a non-root home."""
    # Primary: an operator uid with a live systemd --user bus.
    if service_user_fn is None:
        try:
            from utils.fleet_test_runner import _find_operator_user
            op = _find_operator_user()
            if op:
                import pwd
                return pwd.getpwuid(op[0]).pw_dir
        except Exception:
            pass
    # Fallback (or injected): the rnsd/service user.
    try:
        fn = service_user_fn
        if fn is None:
            from utils.rns_tree_perms import _read_rnsd_user
            fn = _read_rnsd_user
        user = fn()
        if user and user != "root":
            import pwd
            return pwd.getpwnam(user).pw_dir
    except Exception:
        pass
    return None


def probe_inherited_app_drift(
    *,
    scan_roots=None,
    service_user_fn=None,
    repos=None,
    git_path: str = "git",
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when an INHERITED (non-owned) upstream app checkout carries an
    unversioned tracked-file code patch — the R1 defect the upstream-app
    ownership policy (Action 5, 2026-06-21) exists to police.

    The operator's core concern: *"a local patch on an inherited checkout gets
    CLOBBERED on git pull + isn't version-controlled."* A hand-edited source
    file on a checkout we don't own (origin not Nursedude) exists in no repo we
    control and is one ``git pull`` from silent deletion (the rescued
    .32 + dev-box bot patches were exactly this). This makes that hygiene defect
    a continuously-monitored signal in /fleet + the mini rollup instead of a
    once-a-quarter manual git survey.

    Scope is deliberately **LOCAL problem-class detection**, NOT a comparison
    against ``fleet-overlays/PINS.md`` (the human pin ledger): the probe
    enforces the §4.2 invariant directly, PINS.md stays the record, and the two
    never have to agree on a shared constant (honest_failure_modes #5 —
    two-consumers-of-one-constant drift). PINS.md isn't even present on moc5
    (the box that has inherited apps), so reading it would couple to an artifact
    that isn't there.

    The floating-``main`` / pin-drift leg is intentionally **NOT** a local fire
    here. The fleet's chosen enforcement is *"record the pin, never auto-pull"*
    (PINS.md), NOT detached HEAD — so firing on "on a branch" would contradict
    the policy and false-page every intentionally-pinned moc5 app. Detecting
    "drifted off the recorded pin" needs the ledger SHA, which belongs to a
    future ledger/cross-box check, not this single-box local probe.

    What it inspects per inherited checkout (top level of the operator home +
    ``/opt``): ``git status --porcelain --untracked-files=no
    --ignore-submodules=all``. Untracked config/build artifacts (Raven's
    raven.conf, ucode's build/) and vendored-submodule churn (MeshSense's
    ``api/webbluetooth`` gitlink) are excluded by the flags, and machine-
    generated dependency manifests/lockfiles (the MeshSense npm package*.json
    churn) are filtered out — so only a real hand-edited code/config patch in
    the parent app fires.

    Honest failure modes — every error path stays SILENT and never reads as a
    clean tree:

    - no inherited checkouts on this box → None (INERT — moc1/2/3, the common
      case; absence of inherited apps is not a failure to report).
    - operator home / scan root unresolvable or unreadable → that root is
      skipped (indeterminate).
    - a repo whose ``.git/config`` origin can't be read → skipped (can't
      classify owned-vs-inherited; never guess).
    - ``git status`` errors/times out for a repo → that repo contributes
      nothing (a git failure must not read as clean) — others still evaluated.
    - 2-tick debounce rides out an operator mid-edit / a fleet-roll window where
      a patch is briefly uncommitted before being committed or reverted (the
      patches this targets are long-lived, so debounce only suppresses blips).

    Severity ``degraded`` — latent version-control hygiene debt, not an active
    outage; the seed routes it to a side-effect-free escalation (no ntfy page).
    Expected true-positives on today's fleet: the gated ``.32 ~/meshing-around``
    bot patch (rescued to fleet-overlays, fork-built, deploy gated post-06-24)
    and ``.32 ~/raphael-kit`` example edit — both real unversioned patches the
    policy wants surfaced. ``repos`` may be injected for tests as a list of
    ``(path, origin_url, modified_tracked_files)`` (the benign filter is applied
    to the injected files too)."""
    sp = state_path or DEFAULT_INHERITED_DRIFT_DEBOUNCE_PATH
    try:
        # 1. Build the {inherited checkout → real code patches} list.
        dirty: List[Tuple[str, str, List[str]]] = []
        if repos is None:
            if scan_roots is None:
                home = _resolve_operator_home(service_user_fn)
                scan_roots = [r for r in (home, "/opt") if r]
            for path, url in _iter_inherited_checkouts(scan_roots):
                mods = _git_tracked_modifications(path, git_path)
                if mods is None:
                    continue  # git indeterminate for this repo — skip
                patches = _real_code_patches(mods)
                if patches:
                    dirty.append((path, url, patches))
        else:
            for path, url, files in repos:
                patches = _real_code_patches(list(files))
                if patches:
                    dirty.append((path, url, patches))

        if not dirty:
            _save_parity_streak(sp, 0)
            return None  # no inherited patch anywhere → INERT / clean

        # 2. Debounce — ride out a transient mid-edit / mid-roll window.
        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            return None

        # 3. Build the signal (one aggregate; subject stable for edge-tracking).
        dirty.sort(key=lambda r: r[0])
        names = [os.path.basename(p.rstrip("/")) for p, _u, _f in dirty]
        breakdown = {
            os.path.basename(p.rstrip("/")): files for p, _u, files in dirty
        }
        shown = "; ".join(
            f"{os.path.basename(p.rstrip('/'))} "
            f"({', '.join(files[:3])}"
            f"{' +%d more' % (len(files) - 3) if len(files) > 3 else ''})"
            for p, _u, files in dirty[:4]
        )
        if len(dirty) > 4:
            shown += f" (+{len(dirty) - 4} more app(s))"
        detail = (
            f"Unversioned code patch on {len(dirty)} INHERITED upstream "
            f"checkout(s): {shown} | confirmed over {streak} consecutive ticks "
            f"| these hand-edits exist in no repo we control and are one `git "
            f"pull` from silent deletion (upstream-app ownership policy §4.2). "
            f"Fix: rescue the patch into an owned fork or the tracked overlay "
            f"(Nursedude/fleet-overlays), or upstream it as a PR, then converge "
            f"the checkout clean and record the chosen pin in "
            f"fleet-overlays/PINS.md."
        )
        return Signal(
            cls="inherited_app_drift",
            subject="inherited-apps",
            severity="degraded",
            detail=detail,
            extra={
                "apps": names,
                "patches": breakdown,
                "debounce_streak": streak,
            },
        )
    except Exception:
        return None

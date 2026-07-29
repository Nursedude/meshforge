"""Watchdog probes — declared-state vs live-state drift failure shapes.

Foundation perms drift, MeshForge<->MeshAnchor parity drift, RNS fork-pin
version drift, pip dependency version-floor + install-fragmentation drift,
role drift, MQTT root drift (#77).
Part of the ``watchdog_probes`` split (2026-06-09) — import via the
``utils.watchdog_probes`` hub, not from here. The cron/fleet/host liveness
probes (#78) and the environment probes (router/ntfy/kernel/aredn/inherited)
were split into ``watchdog_probes_liveness`` + ``watchdog_probes_env``
(2026-07-14, MF025 size cap); this module re-exports them for back-compat.
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
    _load_parity_streak,
    _read_deployment_declaration,
    _resolve_main_pid,
    _save_parity_streak,
    note_disposition,
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
        from utils.rns_tree_perms import (
            _USERNAME_RE,
            logfile_perms_drift,
            probe_rns_tree_perms,
        )
    except Exception:
        note_disposition("foundation_perms_drift", "indeterminate",
                         reason="foundation tooling unimportable")
        return None  # foundation tooling absent — indeterminate, don't false-alarm
    if perms is None:
        try:
            perms = probe_rns_tree_perms()
        except Exception:
            note_disposition("foundation_perms_drift", "indeterminate",
                             reason="RNS tree perms probe raised")
            return None
    reason = logfile_perms_drift(perms)
    if not reason:
        # Disambiguate the helper's three None meanings (mirrors its guards).
        user = perms.rnsd_user
        if not user or user == "root":
            note_disposition("foundation_perms_drift", "inert",
                             reason="rnsd runs as root (no perms constraint)")
        elif not _USERNAME_RE.match(user):
            note_disposition("foundation_perms_drift", "indeterminate",
                             reason="rnsd user has unexpected shape")
        elif perms.configdir_owner is None:
            note_disposition("foundation_perms_drift", "indeterminate",
                             reason="RNS tree perms facts not probed")
        else:
            note_disposition("foundation_perms_drift", "clean")
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
        note_disposition("parity_drift", "inert",
                         reason="no sister repo on this box")
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
            note_disposition("parity_drift", "indeterminate",
                             reason="parity tool unavailable")
            return None  # parity tool unavailable → indeterminate, don't alarm
    try:
        findings, overall = check_fn(meshforge_root, meshanchor_root)
    except Exception:
        # Indeterminate — don't let a tool error count toward the streak.
        _save_parity_streak(state_path, 0)
        note_disposition("parity_drift", "indeterminate",
                         reason="parity check raised")
        return None
    if overall != "drift":
        _save_parity_streak(state_path, 0)  # in_sync / missing → streak broken
        if overall == "in_sync":
            note_disposition("parity_drift", "clean")
        else:
            note_disposition("parity_drift", "indeterminate",
                             reason=f"parity result '{overall}' — can't compare")
        return None

    streak = _load_parity_streak(state_path) + 1
    _save_parity_streak(state_path, streak)
    if streak < debounce_ticks:
        note_disposition("parity_drift", "indeterminate",
                         reason="drift candidate under debounce")
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
            note_disposition("rns_version_drift", "indeterminate",
                             reason="fork-pin reader failed")
            return None
    if not pins:
        note_disposition("rns_version_drift", "indeterminate",
                         reason="no fork pin parseable")
        return None  # no pin parseable (sub-arc A not applied) → indeterminate

    if installed is None:
        installed = _read_pkg_versions_for_user(rnsd_user, set(pins))
    if not installed:
        note_disposition("rns_version_drift", "indeterminate",
                         reason="service-user env unreadable")
        return None  # couldn't read the service env → indeterminate, no false alarm

    drift = []
    for pkg, want in pins.items():
        have = installed.get(pkg)
        if have is None:
            # Worst-wins: this note keeps a partial read from rendering clean.
            note_disposition("rns_version_drift", "indeterminate",
                             reason="pinned pkg not visible in user site")
            continue  # not visible in the user site (venv elsewhere?) — don't guess
        if have != want:
            drift.append(f"{pkg} installed={have} pinned={want}")
    if not drift:
        note_disposition("rns_version_drift", "clean")
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
#
# SCOPE IS DELIBERATE AND ACCEPTED-PERMANENT (structural-dark row 3, decided
# 2026-07-19 on a fleet-wide survey — see
# .claude/research/dep_stray_watch_scope_2026_07_19.md). Stray-copy risk is a
# property of deps installed by COMPETING TOOLS (pip vs pipx vs apt vs fork
# pin) into competing consumer positions — meshtastic and rns/lxmf. For deps
# that ship with the OS, a venv/user-site copy shadowing system-dist is the
# DESIGNED state, not drift: watching them would either page on benign
# divergence (moc4's system requests 2.28.1 sits below the core.txt floor while
# the actual consumer runs a compliant venv copy) or never fire at all against
# floors reality has long outgrown — noise or false assurance, both worse than
# an honestly-named blind spot.
#
# ⚠️ IF YOU EXTEND THIS TUPLE: two call sites below index it as
# ``_DEP_VERSION_WATCHED[0]`` (the fragmentation probe watches exactly one
# package). Adding a second entry WITHOUT fixing them silently watches only the
# first — the closed-enum/open-consumer class. TestDepWatchedTupleClosedConsumer
# in tests/test_watchdog_coverage.py fails the moment this grows, and names the
# sites (honest_failure_modes #7: coverage gates, not memory).
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
        note_disposition("dep_version_drift", "indeterminate",
                         reason="no requirements floor parseable")
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
        note_disposition("dep_version_drift", "indeterminate",
                         reason="consumer-of-record env unreadable")
        return None  # couldn't read the consumer env → indeterminate

    stale = []
    for pkg, floor in floors.items():
        have = installed.get(pkg)
        if have is None:
            # Worst-wins: this note keeps a partial read from rendering clean.
            note_disposition("dep_version_drift", "indeterminate",
                             reason="pkg not visible in consumer env")
            continue  # not visible in the consumer env — don't guess
        if _version_below(have, floor):
            stale.append(f"{pkg} installed={have} floor>={floor}")
    if not stale:
        note_disposition("dep_version_drift", "clean")
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
            note_disposition("dep_install_fragmented", "indeterminate",
                             reason="no reviewed floor parseable")
            return None  # no reviewed floor → indeterminate, never alarm

        user_scope_dark = False
        if installs is None:
            if service_user is None:
                try:
                    from utils.rns_tree_perms import _read_rnsd_user
                    service_user = _read_rnsd_user()
                except Exception:
                    service_user = None
            # _enumerate_pkg_installs SKIPS the user-site/user-pipx globs
            # whenever there is no resolvable non-root service user (no rnsd
            # unit, or rnsd runs as root) — the import-priority user-scope
            # locations were then never observed, so a would-be-clean exit
            # ("one location" / "all agree") cannot honestly claim
            # fragmentation impossible (honest_failure_modes #1).
            user_scope_dark = not service_user or service_user == "root"
            installs = _enumerate_pkg_installs(pkg, service_user)

        def _note_clean_unless_user_scope_dark():
            if user_scope_dark:
                note_disposition(
                    "dep_install_fragmented", "indeterminate",
                    reason=("user-scope install locations unobservable "
                            "(no non-root rnsd user)"))
            else:
                note_disposition("dep_install_fragmented", "clean")

        if not installs or len(installs) < 2:
            _save_parity_streak(sp, 0)
            if installs:
                # One location positively observed → fragmentation impossible
                # — IF every location was actually observable.
                _note_clean_unless_user_scope_dark()
            else:
                note_disposition("dep_install_fragmented", "indeterminate",
                                 reason="no readable install location found")
            return None  # 0/1 location → no fragmentation possible
        versions = set(installs.values())
        if len(versions) < 2:
            _save_parity_streak(sp, 0)
            _note_clean_unless_user_scope_dark()
            return None  # every (observed) location agrees — not fragmented

        below = {label: v for label, v in installs.items()
                 if _version_below(v, floor)}
        if not below:
            _save_parity_streak(sp, 0)
            _note_clean_unless_user_scope_dark()
            return None  # divergence but nothing below floor (pipx CLI ahead) — benign

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("dep_install_fragmented", "indeterminate",
                             reason="fragmentation candidate under debounce")
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
        note_disposition("dep_install_fragmented", "indeterminate",
                         reason="probe raised unexpectedly")
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS/LXMF stray-env coherence (the missed-venv roll hazard,
# 2026-07-19 — closes the rns/lxmf half of dep_version_drift_strays_blind)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_RNS_STRAY_DEBOUNCE_PATH = "/var/lib/meshforge/rns_stray_debounce.json"

# Operator-declared waivers: {"waived": {"<location-label>": "<reason>"}} —
# for the ONE legitimate exception to intra-box coherence: an app running an
# ISOLATED own-Reticulum instance (its RNS never speaks the shared rnsd's
# RPC, so version agreement buys nothing and a forced downgrade can break
# the app). A waiver is deliberate and VISIBLE: the clean disposition names
# every waived label (honest_failure_modes #9 — every swallow leaves a
# witness). A malformed/unreadable waiver file applies NO waivers — a broken
# waiver must never suppress a real signal.
DEFAULT_RNS_STRAY_WAIVERS_PATH = "/etc/meshforge/rns_stray_waivers.json"


def _load_stray_waivers(path):
    """Return the waived-label dict, or {} on absent/malformed (never raise)."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        waived = doc.get("waived")
        if isinstance(waived, dict):
            return {str(k): str(v) for k, v in waived.items()}
    except (OSError, ValueError, AttributeError):
        pass
    return {}

# Every root-readable location an rns/lxmf library copy can live. Unlike
# _DEP_INSTALL_SITE_GLOBS, the pipx globs are WILDCARDED across venv names:
# a LIBRARY rides along inside every app venv that depends on it, not just a
# venv named after it — the stray that proved this class lived inside the
# NOMADNET pipx venv on moc3 (silently stock 1.1.4 while the box's consumer
# ran the fork pin; invisible to every existing drift probe).
_LIB_STRAY_SITE_GLOBS = {
    "venv":        ["{root}/venv/lib/python3*/site-packages"],
    "system-dist": [
        "/usr/local/lib/python3*/dist-packages",
        "/usr/lib/python3*/dist-packages",
        "/usr/lib/python3/dist-packages",
    ],
    "root-pipx":   [
        "/root/.local/share/pipx/venvs/*/lib/python3*/site-packages",
        "/opt/pipx/venvs/*/lib/python3*/site-packages",
    ],
    "user-site":   ["{home}/.local/lib/python3*/site-packages"],
    "user-pipx":   ["{home}/.local/share/pipx/venvs/*/lib/python3*/site-packages"],
}


def _enumerate_lib_installs(pkg, service_user, *, meshforge_root="/opt/meshforge",
                            user_home=None):
    """Return ``{location_label: version}`` for every root-readable copy of
    ``pkg``, INCLUDING copies riding inside other apps' pipx venvs (labeled
    per venv, e.g. ``user-pipx:nomadnet``).

    Non-pipx label groups keep the sibling helper's semantics (first found in
    path-priority order — the copy an interpreter would actually import).
    Same sandbox constraints as ``_enumerate_pkg_installs``: root READS the
    trees directly; user-scoped globs are skipped without a resolvable home."""
    import glob
    if user_home is None and service_user and service_user != "root":
        try:
            import pwd
            user_home = pwd.getpwnam(service_user).pw_dir
        except (KeyError, OSError):
            user_home = f"/home/{service_user}"
    found = {}
    for label, patterns in _LIB_STRAY_SITE_GLOBS.items():
        if ("{home}" in "".join(patterns)) and not user_home:
            continue  # user-scoped location but no resolvable home — skip
        for pat in patterns:
            try:
                expanded = pat.format(root=meshforge_root, home=user_home or "")
            except (KeyError, IndexError):
                continue
            for d in sorted(glob.glob(expanded)):
                if "pipx/venvs/" in d:
                    venv_name = d.split("pipx/venvs/", 1)[1].split("/", 1)[0]
                    key = f"{label}:{venv_name}"
                else:
                    key = label
                if key in found:
                    continue  # first found per label wins (path priority)
                ver = _read_pkg_version_at_dirs([d], pkg)
                if ver is not None:
                    found[key] = ver
    return found


def probe_rns_env_coherence(
    *,
    service_user=None,
    installs=None,
    pkgs=("rns", "lxmf"),
    state_path=None,
    waivers_path=None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when rns/lxmf copies across this box's root-readable envs DISAGREE.

    Intra-box COHERENCE, deliberately NOT pin compliance:
    ``probe_rns_version_drift`` owns the +mf.N fork pin (and moc3's deliberate
    canary drift page rides there); this probe pages the MISSED-ENV half of
    the class. The fleet runs ONE rnsd per box and every app connects to it,
    so every env must carry the identical RNS substrate — the 1.3.8 roll gate
    is "flip rnsd + ALL clients + every pipx venv TOGETHER" (pickle→msgpack
    RPC framing: a stray env speaks the wrong RPC dialect at 8s-timeout cost).
    A box mid-roll that missed one venv pages here within 2 ticks instead of
    being operator-discovered. A deliberately-flipped canary box whose envs
    were all moved together stays CLEAN (they agree with each other).

    Waivers (2026-07-19): an app running an ISOLATED own-Reticulum instance
    (e.g. meshchatx-isolated.service — its RNS never speaks the shared
    rnsd's RPC) is the one legitimate coherence exception. The operator
    declares it in DEFAULT_RNS_STRAY_WAIVERS_PATH; waived labels are
    excluded BUT the clean disposition names them every tick, and a
    malformed waiver file waives nothing.

    Honest failure modes: 0/1 observed locations per pkg → not incoherent,
    never false-alarm; user scope unobservable (no non-root rnsd user) →
    indeterminate, never claims clean coverage it didn't have; 2-tick
    debounce rides a mid-roll window. Never raises into the tick.
    """
    try:
        sp = state_path or DEFAULT_RNS_STRAY_DEBOUNCE_PATH

        user_scope_dark = False
        if installs is None:
            if service_user is None:
                try:
                    from utils.rns_tree_perms import _read_rnsd_user
                    service_user = _read_rnsd_user()
                except Exception:
                    service_user = None
            user_scope_dark = not service_user or service_user == "root"
            installs = {p: _enumerate_lib_installs(p, service_user)
                        for p in pkgs}

        waivers = _load_stray_waivers(
            waivers_path or DEFAULT_RNS_STRAY_WAIVERS_PATH)
        waived_hit = sorted({lbl for locs in installs.values()
                             for lbl in locs if lbl in waivers})
        if waivers:
            installs = {p: {lbl: v for lbl, v in locs.items()
                            if lbl not in waivers}
                        for p, locs in installs.items()}

        observed_any = any(locs for locs in installs.values())
        incoherent = {p: locs for p, locs in installs.items()
                      if len(set(locs.values())) >= 2}

        if not incoherent:
            _save_parity_streak(sp, 0)
            if not observed_any:
                note_disposition("rns_stray_env_drift", "indeterminate",
                                 reason="no readable rns/lxmf install found")
            elif user_scope_dark:
                note_disposition(
                    "rns_stray_env_drift", "indeterminate",
                    reason=("user-scope install locations unobservable "
                            "(no non-root rnsd user)"))
            elif waived_hit:
                # Clean only BECAUSE of the waiver — say so, every tick.
                note_disposition(
                    "rns_stray_env_drift", "clean",
                    reason=("coherent with %d waived location(s): %s"
                            % (len(waived_hit), ", ".join(waived_hit))))
            else:
                note_disposition("rns_stray_env_drift", "clean")
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("rns_stray_env_drift", "indeterminate",
                             reason="incoherence candidate under debounce")
            return None

        parts = []
        for p, locs in sorted(incoherent.items()):
            loc_str = ", ".join(f"{k}={v}" for k, v in sorted(locs.items()))
            parts.append(f"{p} at {len(set(locs.values()))} versions "
                         f"({loc_str})")
        detail = (
            "rns/lxmf env incoherence — " + "; ".join(parts) + ". Every env "
            "on a box must carry the identical RNS substrate (one shared "
            "rnsd; RPC framing must match — a stray env costs 8s timeouts "
            "or worse). Usual cause: a roll/install that missed a pipx venv "
            "or user-site copy (the moc3 nomadnet-venv lesson, 2026-07-17). "
            "Fix: reinstall the odd env(s) to the box's consumer version — "
            "for pipx: pipx runpip <venv> install --force-reinstall "
            "-r /opt/meshforge/requirements/rns.txt — then re-check."
        )
        return Signal(
            cls="rns_stray_env_drift",
            subject="+".join(sorted(incoherent)),
            severity="degraded",
            detail=detail,
            extra={
                "installs": {p: dict(sorted(locs.items()))
                             for p, locs in sorted(incoherent.items())},
                "streak": streak,
            },
        )
    except Exception:
        note_disposition("rns_stray_env_drift", "indeterminate",
                         reason="probe raised unexpectedly")
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
        # _read_deployment_declaration returns (None, {}) for BOTH a
        # genuinely-undeclared box AND an unreadable/corrupt declaration —
        # per its own docstring the latter is indeterminate, and the two
        # cannot be told apart here, so the merged note must be the worse.
        note_disposition("role_drift", "indeterminate",
                         reason="no declared role, or declaration unreadable")
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
        note_disposition("role_drift", "indeterminate",
                         reason="role plan tool raised")
        return None  # tool error → indeterminate, don't count toward streak
    if actions is None and not unknown_role:
        _save_parity_streak(state_path, 0)
        note_disposition("role_drift", "indeterminate",
                         reason="role tool/catalog unavailable")
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
        note_disposition("role_drift", "clean")
        return None

    streak = _load_parity_streak(state_path) + 1
    _save_parity_streak(state_path, streak)
    if streak < debounce_ticks:
        note_disposition("role_drift", "indeterminate",
                         reason="drift candidate under debounce")
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
        note_disposition("mqtt_root_drift", "indeterminate",
                         reason="meshtasticd inactive or MainPID unresolvable")
        return None

    if newest_line_fn is None:
        def newest_line_fn(pattern: str) -> Optional[str]:
            return _journal_newest_match(
                unit, pattern, lookback, journalctl_path=journalctl_path
            )

    line = newest_line_fn("JSON publish message to ")
    if line is None:
        # _journal_newest_match None conflates "no publish line in the
        # lookback" (RX-only box) with "journalctl unavailable/timeout" —
        # indistinguishable here, so the merged note is the worse one.
        note_disposition(
            "mqtt_root_drift", "indeterminate",
            reason="no json publish line observed (or journal unavailable)")
        return None  # no json uplink at all — unobservable, not drift
    m = _MQTT_PUBLISH_TOPIC_RE.search(line)
    if m is None:
        note_disposition("mqtt_root_drift", "indeterminate",
                         reason="publish line shape unrecognized")
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
        note_disposition("mqtt_root_drift", "indeterminate",
                         reason="declared consumer root unreadable")
        return None  # declared side indeterminate — never alarm on a guess

    sp = state_path or DEFAULT_MQTT_ROOT_DEBOUNCE_PATH
    if observed == declared:
        _save_parity_streak(sp, 0)
        note_disposition("mqtt_root_drift", "clean")
        return None

    streak = _load_parity_streak(sp) + 1
    _save_parity_streak(sp, streak)
    if streak < debounce_ticks:
        note_disposition("mqtt_root_drift", "indeterminate",
                         reason="drift candidate under debounce")
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


# ─────────────────────────────────────────────────────────────────────
# Back-compat re-exports. The cron/fleet/host liveness probes and the
# environment probes were split into sibling modules (2026-07-14, MF025
# size cap), but every consumer — the watchdog_probes hub, the test suite,
# the install-audit script — imports them from HERE. Re-export the full
# moved surface so `from utils.watchdog_probes_drift import <name>` keeps
# working; the split is API-preserving.
# ─────────────────────────────────────────────────────────────────────
from utils.watchdog_probes_liveness import (  # noqa: E402,F401 (back-compat re-export)
    DEFAULT_CRON_VERDICT_DEBOUNCE_PATH,
    CRON_VERDICT_STALE_FLOOR_S,
    CRON_VERDICT_CADENCE_MULT,
    _CRON_VERDICT_FALLBACK_MAX_S,
    _cron_max_interval,
    _read_operator_crontab_spool,
    _read_operator_verdicts_log,
    probe_cron_verdict_stale,
    DEFAULT_FLEET_UNREACHABLE_DEBOUNCE_PATH,
    FLEET_STATE_STALE_S,
    FLEET_UNREACHABLE_WEDGE_S,
    _read_operator_fleet_state,
    probe_fleet_box_unreachable,
    DEFAULT_HOST_FROZEN_DEBOUNCE_PATH,
    HOST_PROBE_STATE_STALE_S,
    _HOST_FROZEN_WEDGE_VERDICTS,
    _read_host_probe_verdict,
    probe_host_frozen,
    probe_claw_device_dark,
    probe_claw_battery_low,
    probe_claw_rf_silent,
)
from utils.watchdog_probes_claw_uplink import (  # noqa: E402,F401 (re-export)
    DEFAULT_CLAW_UPLINK_DEBOUNCE_PATH,
    DEFAULT_CLAW_UPLINK_CONFIG,
    _read_arp_locations,
    probe_claw_uplink_node_moved,
)
from utils.watchdog_probes_env import (  # noqa: E402,F401 (back-compat re-export)
    DEFAULT_ROUTER_SCOUT_DEBOUNCE_PATH,
    ROUTER_SCOUT_MIRROR_SUBDIR,
    ROUTER_SCOUT_MIRROR_STALE_S,
    ROUTER_SCOUT_TICK_STALE_S,
    _read_router_scout_ticks,
    probe_router_scout_degraded,
    DEFAULT_NTFY_LOOPBACK_DEBOUNCE_PATH,
    NTFY_LOOPBACK_STATE_STALE_S,
    NTFY_LOOPBACK_WEDGE_MISSES,
    _read_ntfy_loopback_state,
    probe_ntfy_loopback,
    DEFAULT_NTFY_ACK_DEBOUNCE_PATH,
    NTFY_ACK_STATE_STALE_S,
    NTFY_ACK_WEDGE_PINGS,
    _read_ntfy_ack_state,
    probe_ntfy_ack_stale,
    DEFAULT_KERNEL_REBOOT_DEBOUNCE_PATH,
    _KERNEL_RELEASE_RE,
    _KERNEL_BUILD_RE,
    _parse_kernel_release,
    probe_kernel_reboot_pending,
    DEFAULT_AREDN_SOURCE_DEBOUNCE_PATH,
    _AREDN_DARK_REASONS,
    _read_configured_aredn_ips,
    _read_configured_aredn_ips_state,
    _fetch_local_status_json,
    probe_aredn_source_dark,
    DEFAULT_AREDN_UNDECLARED_DEBOUNCE_PATH,
    AREDN_LOCALNODE_NAME,
    _resolve_aredn_localnode,
    _fetch_aredn_sysinfo,
    probe_aredn_organ_undeclared,
    DEFAULT_INHERITED_DRIFT_DEBOUNCE_PATH,
    _OWNED_ORIGIN_MARKER,
    _INHERITED_SCAN_EXCLUDE,
    _BENIGN_TRACKED_BASENAMES,
    _read_git_origin_url,
    _git_tracked_modifications,
    _real_code_patches,
    _iter_inherited_checkouts,
    _resolve_operator_home,
    probe_inherited_app_drift,
)

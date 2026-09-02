"""Watchdog probes — declared-state vs live-state drift failure shapes.

Foundation perms drift, RNS fork-pin version drift, pip dependency
version-floor + install-fragmentation drift, role drift, MQTT root drift
(#77). The MeshForge<->MeshAnchor parity probe was split into
``watchdog_probes_parity`` (2026-09-01, same cap) and is re-exported here.
Part of the ``watchdog_probes`` split (2026-06-09) — import via the
``utils.watchdog_probes`` hub, not from here. The cron/fleet/host liveness
probes (#78) and the environment probes (router/ntfy/kernel/aredn/inherited)
were split into ``watchdog_probes_liveness`` + ``watchdog_probes_env``
(2026-07-14, MF025 size cap), and the RNS stray-env coherence probe into
``watchdog_probes_rns_env`` (2026-08-09, same cap); this module re-exports
them for back-compat.
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
    _SYSTEM_DIST_GLOBS,
    _journal_newest_match_status,
    _load_parity_streak,
    _read_deployment_declaration,
    _read_deployment_declaration_status,
    _read_pkg_version_at_dirs,
    _resolve_main_pid_status,
    _save_parity_streak,
    note_disposition,
    note_unit_presence_gate,
)

# ─────────────────────────────────────────────────────────────────────
# Probe: permission-foundation drift (mf.4 / Issue #73 perms class)
# ─────────────────────────────────────────────────────────────────────


def _rnsd_unit_was_readable(injected) -> bool:
    if injected is not None:
        return bool(injected)
    try:
        from utils.rns_tree_perms import rnsd_unit_readable
        return rnsd_unit_readable()
    except Exception:
        return False


def probe_foundation_drift(
    *,
    perms=None,
    rnsd_unit_readable=None,
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
        if not user and not _rnsd_unit_was_readable(rnsd_unit_readable):
            # `_read_rnsd_user()` is None for BOTH "unit read, no User=" (root,
            # inert is right) and "no unit file readable" — and this branch
            # filed both under root. A non-root rnsd whose unit moved would be
            # blind here wearing `inert` (falsifiability audit phase 2,
            # 2026-09-02). Unobserved is indeterminate, never absent-by-design.
            note_disposition("foundation_perms_drift", "indeterminate",
                             reason="rnsd unit file unreadable — service user unresolved")
        elif not user or user == "root":
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
# Probe: RNS/LXMF fork-pin version drift (RNS T2-isolate arc)
# ─────────────────────────────────────────────────────────────────────


DEFAULT_CONSUMER_PATH_CACHE = "/var/lib/meshforge/rns_consumer_path.json"

# How long a resolved import path stays good. It changes only when the service's
# interpreter, venv, or home changes — a provisioning event, not a runtime one —
# so this is deliberately long. Package VERSIONS are re-read every tick from the
# cached dirs, so an install still surfaces immediately.
CONSUMER_PATH_TTL_S = 6 * 3600


def _glob_consumer_site_dirs(user):
    """FALLBACK path guess: user-site then system dist-packages.

    This is a RECONSTRUCTION of what CPython would do, and it is wrong wherever
    the service's real path is not one of these shapes — a venv, a PYTHONPATH
    from the unit's ``Environment=``, a ``.pth`` file. Kept only for when the
    interpreter cannot be asked; the caller MUST say it fell back, because a
    guess that silently stands in for a measurement is how this probe already
    lost 12.3 days on moc4.
    """
    import glob
    dirs = []
    if user and user != "root":
        try:
            import pwd
            home = pwd.getpwnam(user).pw_dir
        except (KeyError, OSError):
            home = f"/home/{user}"
        dirs += sorted(glob.glob(f"{home}/.local/lib/python3*/site-packages"))
    for pat in _SYSTEM_DIST_GLOBS:
        dirs += sorted(glob.glob(pat))
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


# The ENTIRE program handed to the service's interpreter. Deliberately a named
# constant so a test can assert on what the child actually runs rather than on
# prose about it: it prints sys.path and imports nothing of ours. Constructing
# RNS.Reticulum() here would make this probe the #69 namespace-collision class
# it exists to help watch.
_CONSUMER_PATH_SCRIPT = "import sys\nprint('\\n'.join(p for p in sys.path if p))\n"


def _service_interpreter(unit="rnsd"):
    """The interpreter the SERVICE actually runs, from its unit's ExecStart.

    ExecStart names a console script (``/usr/local/bin/rnsd``); its shebang
    names the interpreter. Returns None when the unit, the binary, or the
    shebang cannot be read — never a guess.
    """
    try:
        out = subprocess.run(
            ["systemctl", "show", unit, "-p", "ExecStart", "--value"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"path=(\S+)", out.stdout or "")
    if not m:
        return None
    binary = m.group(1)
    if os.path.basename(binary).startswith("python"):
        return binary
    try:
        with open(binary, "rb") as fh:
            first = fh.readline(256).decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    parts = first[2:].strip().split()
    if not parts:
        return None
    # "#!/usr/bin/env python3" → the interpreter is the ARGUMENT
    interp = parts[1] if parts[0].endswith("/env") and len(parts) > 1 else parts[0]
    return interp if os.path.isfile(interp) else None


def _ask_interpreter_site_dirs(user, *, unit="rnsd", timeout_s=20):
    """Ask the service's OWN interpreter which dirs it imports from.

    This is the cure for reconstructing a search path by hand. CPython computes
    it — so a venv, a ``PYTHONPATH``, a ``.pth`` file, and the user-site/dist-
    packages ordering are all handled by the authority instead of by my model of
    it (the 2026-08-09 lesson, and MeshAnchor's ``check_dep_version_floor``
    design applied to MeshForge's split-process reality: MA reads its own env
    because it RUNS in the consumer; the MF watchdog runs as root in a different
    process, so the closest honest thing is to ask the consumer's interpreter).

    ``HOME`` is set to the service user's home so the interpreter computes THAT
    user's site-packages — the watchdog sandbox (NoNewPrivileges +
    RestrictSUIDSGID) blocks sudo/runuser, so we cannot become the user, but we
    can hand its interpreter the input that decides user-site.

    IMPORT-ONLY by construction: the child prints ``sys.path`` and never
    constructs ``RNS.Reticulum()`` — a probe that claimed an ``@rns`` listener
    would be the #69 class wearing a stethoscope.

    Returns a list of existing dirs, or None if the interpreter can't be asked.
    Measured in-probe: 0.154 s on the 905 MB box, 0.07 s elsewhere; cached reads
    are 0.0002 s. (An earlier shell benchmark said 0.56 s — that number included
    sudo + ``env -i`` + heredoc overhead per iteration and overstated it ~4x. The
    caching below is still right: 0.15 s x 2,880 ticks/day is ~7 min/day of CPU
    for a value that changes only on provisioning.)
    """
    interp = _service_interpreter(unit)
    if not interp:
        return None
    home = None
    if user and user != "root":
        try:
            import pwd
            home = pwd.getpwnam(user).pw_dir
        except (KeyError, OSError):
            home = f"/home/{user}"
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    if home:
        env["HOME"] = home
    try:
        out = subprocess.run([interp, "-"], input=_CONSUMER_PATH_SCRIPT, env=env,
                             capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    dirs = [d for d in (out.stdout or "").splitlines() if d.strip()]
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)] or None


def _consumer_site_dirs(user, *, cache_path=None, now=None, unit="rnsd"):
    """Ordered dirs the rnsd service actually imports from, with the expensive
    resolution cached.

    Split by RATE OF CHANGE, which is the whole design: the PATH changes only on
    a provisioning event, so it is resolved by the interpreter at most once per
    ``CONSUMER_PATH_TTL_S`` and persisted; the VERSIONS in those dirs are re-read
    in-process every tick, so a pip install still surfaces on the next tick. That
    keeps per-tick cost where it was while removing the hand-built path guess
    (measured in-probe: 0.154 s cold on the 905 MB box, 0.0002 s cached — so ~4
    spawns/day instead of 2,880; [[feedback_my_footprint_is_the_constraint]]).

    Returns ``(dirs, source)`` where source is ``"interpreter"`` (measured),
    ``"interpreter-cached"``, or ``"glob-fallback"`` (a GUESS the caller must
    disclose). A cache stamped in the future or with a backward clock is treated
    as due — RTC-less Pis forge wall-clock (honest_failure_modes #6).
    """
    cp = cache_path or DEFAULT_CONSUMER_PATH_CACHE
    ts = time.time() if now is None else now

    cached = None
    try:
        with open(cp, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if (isinstance(doc, dict) and doc.get("user") == user
                and isinstance(doc.get("dirs"), list)
                and isinstance(doc.get("ts"), (int, float))):
            age = ts - doc["ts"]
            if 0 <= age <= CONSUMER_PATH_TTL_S:
                cached = [d for d in doc["dirs"] if os.path.isdir(d)]
    except (OSError, ValueError, TypeError):
        cached = None
    if cached:
        return cached, "interpreter-cached"

    dirs = _ask_interpreter_site_dirs(user, unit=unit)
    if dirs:
        try:
            # atomic_write_text, not a hand-rolled f"{cp}.tmp": a FIXED temp
            # name is a collision, not a convention (honest_failure_modes #8) —
            # the watchdog and any operator-run probe write this same path.
            from utils.paths import atomic_write_text
            atomic_write_text(
                Path(cp),
                json.dumps({"user": user, "dirs": dirs, "ts": ts}))
        except (OSError, ImportError):
            pass  # cache is an optimisation; a fresh answer still stands
        return dirs, "interpreter"

    return _glob_consumer_site_dirs(user), "glob-fallback"


def _read_pkg_versions_for_user(user, pkgs):
    """Read installed versions of ``pkgs`` as the rnsd service user's interpreter
    would resolve them — read-only, no privilege change.

    Returns ``(versions, path_source)``. ``versions`` is ``{pkg: version}`` for
    those found (possibly ``{}`` — searched fine, none of ``pkgs`` present), or
    ``None`` ONLY when there was no readable location to search at all: those
    are different claims and the caller reports them differently ({} is an
    observation, None is blindness). ``path_source`` says HOW the import path
    was resolved so the caller can disclose a guessed one — returned, never
    stashed on a function attribute (that side channel went stale across calls
    by construction and nothing tested it).

    Why not just ``importlib.metadata.version()``? That reads the *current*
    interpreter's env — the watchdog runs as root, whose env may carry a different
    rns (verified live: root had 1.1.1 while the wh6gxz service env had 1.2.5+mf.4).
    And we can't switch user: the watchdog unit sets NoNewPrivileges + RestrictSUIDSGID,
    which block sudo AND runuser (both need setuid). But ProtectHome=no, so root can
    READ those trees directly and point importlib.metadata at them.
    """
    try:
        site_dirs, source = _consumer_site_dirs(user)
        if not site_dirs:
            return None, None
        found = {}
        for pkg in pkgs:
            # One dir at a time so FIRST hit wins explicitly. Handing the whole
            # list to importlib.metadata.distributions() and assigning per dist
            # takes the LAST match instead, so a stale system-dist copy would
            # shadow the user-site one the interpreter actually imports —
            # caught by test_user_site_wins_over_system_dist, not by review.
            for d in site_dirs:
                ver = _read_pkg_version_at_dirs([d], pkg)
                if ver is not None:
                    found[pkg.lower()] = ver
                    break
        return found, source
    except Exception:
        return None, None


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
    INSTALLED versions are read in the rnsd **service user's** import order —
    user-site, then system dist-packages (see ``_consumer_site_dirs``; and
    ``_read_pkg_versions_for_user`` for why root's own env / sudo / runuser all
    fail here). Fires ``degraded`` only on a concrete mismatch (installed != pinned
    for a package we can actually see). Returns None when compliant, when the pin
    can't be read, when NO install location is readable (indeterminate — never
    false-alarm), or when a package isn't visible in any searched env (possible
    isolated venv — don't guess).
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

    path_source = None
    if installed is None:
        installed, path_source = _read_pkg_versions_for_user(
            rnsd_user, set(pins))
    if installed is None:
        # No readable location AT ALL — genuine blindness, not an observation.
        note_disposition("rns_version_drift", "indeterminate",
                         reason=f"no readable install location for user {rnsd_user!r}")
        return None  # couldn't read any env → indeterminate, no false alarm

    # NOTE: `installed == {}` is NOT this branch. An empty dict means the search
    # ran and found none of the pinned packages — that falls through to the loop
    # below, which notes it per-package. Collapsing the two (`if not installed`)
    # is what made moc4 report "service-user env unreadable" about an env that
    # read perfectly well (honest_failure_modes #1: empty != error).
    drift = []
    for pkg, want in pins.items():
        have = installed.get(pkg)
        if have is None:
            # Worst-wins: this note keeps a partial read from rendering clean.
            note_disposition("rns_version_drift", "indeterminate",
                             reason=f"pinned pkg {pkg!r} not visible in any searched env")
            continue  # not visible anywhere we can read (isolated venv?) — don't guess
        if have != want:
            drift.append(f"{pkg} installed={have} pinned={want}")
    if not drift:
        # A clean verdict built on a GUESSED path is not the same claim as one
        # built on the interpreter's own answer — say which, every tick, so a
        # silently-degraded reader can never look identical to a measured one
        # (honest_failure_modes #9: every fallback leaves a witness).
        if path_source == "glob-fallback":
            note_disposition(
                "rns_version_drift", "indeterminate",
                reason="on-pin per a GUESSED import path — the service "
                       "interpreter could not be asked, so a venv/PYTHONPATH "
                       "install would be invisible; this is not a measurement")
            return None
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

    user_scope_dark = False
    if installed is None:
        # D1 (ported from dep_install_fragmented 2026-08-12 → here 2026-09-02,
        # falsifiability audit phase 2): _enumerate_pkg_installs SKIPS the
        # user-site/user-pipx globs whenever there is no resolvable non-root
        # service user, so a would-be-clean verdict over the system-dist copy
        # never looked at the import-priority user scope.
        user_scope_dark = not service_user or service_user == "root"
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
        if user_scope_dark:
            note_disposition(
                "dep_version_drift", "indeterminate",
                reason=("user-scope install locations unobservable "
                        "(no non-root rnsd user)"))
        else:
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
    "system-dist": list(_SYSTEM_DIST_GLOBS),
    "root-pipx":   [
        "/root/.local/share/pipx/venvs/{pkg}/lib/python3*/site-packages",
        "/opt/pipx/venvs/{pkg}/lib/python3*/site-packages",
    ],
    "user-site":   ["{home}/.local/lib/python3*/site-packages"],
    "user-pipx":   ["{home}/.local/share/pipx/venvs/{pkg}/lib/python3*/site-packages"],
}


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
        decl_status, role, overrides = _read_deployment_declaration_status(
            service_user)
    else:
        # An INJECTED deployment is a seam that positively returned its
        # answer, so a None role there means "observed: no role", never
        # "could not look" (same convention as newest_line_fn elsewhere).
        role, overrides = deployment
        decl_status = "declared" if role else "undeclared"
    if not role:
        _save_parity_streak(state_path, 0)
        # These used to be ONE indeterminate, because the flat reader
        # returned (None, {}) for both and this code said so in a comment.
        # meshanchor-server legitimately has NO deployment.json — it is a
        # MeshAnchor box, not MeshForge role-managed — so it sat permanently
        # indeterminate on this class, and a genuinely corrupt declaration on
        # a REAL fleet box would have been invisible inside that noise
        # (2026-08-07; the channel_feed_dark / mqtt_root_drift family).
        if decl_status == "unreadable":
            note_disposition(
                "role_drift", "indeterminate",
                reason="role declaration unreadable — cannot judge drift")
        else:
            note_disposition(
                "role_drift", "inert",
                reason="box declares no MeshForge role — nothing to drift from")
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


def _declared_root_status(service_user) -> Tuple[str, Optional[str]]:
    """Tri-state read of ``mqtt_bridge.root_topic`` from gateway.json.

    Returns ``("ok", root)``, ``("absent", None)`` when there is no
    gateway.json at all, or ``("unreadable", None)`` when one should be
    readable and isn't.

    ⚠️ Why tri-state (2026-08-05): these three used to collapse to None,
    so "this box runs no MQTT bridge consumer" — the normal state of every
    non-gateway box — was indistinguishable from "I could not read the
    declaration". The federator box sat ``indeterminate`` on this class for
    over a week, and, worse, a genuinely unreadable gateway.json on a REAL
    gateway would have looked exactly like that benign noise. Absent is
    INERT (nothing here can be deaf); unreadable is INDETERMINATE.

    The watchdog runs as sandboxed root — home is derived from the service
    user and READ directly, never escalate (the rns_version_drift lesson).
    A missing ``root_topic`` key still yields the GatewayConfig default:
    that IS the consumer's effective root, which is knowledge, not absence.
    """
    if not service_user:
        return ("unreadable", None)  # can't resolve the user → can't observe
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
        path = os.path.join(home, ".config", "meshforge", "gateway.json")
    except (KeyError, OSError, TypeError):
        return ("unreadable", None)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return ("absent", None)  # no gateway config → no consumer on this box
    except (OSError, ValueError, TypeError):
        return ("unreadable", None)
    try:
        mqtt = data.get("mqtt_bridge")
        if not isinstance(mqtt, dict):
            return ("ok", _GATEWAY_DEFAULT_ROOT_TOPIC)
        root = mqtt.get("root_topic", _GATEWAY_DEFAULT_ROOT_TOPIC)
        if isinstance(root, str) and root:
            return ("ok", root.strip().strip("/"))
        return ("ok", _GATEWAY_DEFAULT_ROOT_TOPIC)
    except (AttributeError, TypeError):
        return ("unreadable", None)


def _read_declared_root_topic(service_user) -> Optional[str]:
    """Back-compat shim over ``_declared_root_status`` (ONE implementation —
    honest_failure_modes #5). Callers that must distinguish "no gateway on
    this box" from "could not read it" need the status form."""
    return _declared_root_status(service_user)[1]


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
    - meshtasticd inactive (``service_inactive`` owns that) — but a box with
      NO meshtasticd unit at all is ``inert``, not indeterminate: there is no
      radio here whose root could drift (2026-08-12, meshanchor-server)
    - no json publish lines in the lookback (unobservable ≠ drift — the
      RX-only collector case, same gate as ``channel_feed_dark``)
    - gateway.json unreadable / service user unresolvable (indeterminate)
    - drift seen but not yet ``debounce_ticks`` consecutive ticks (rides out
      an operator mid-rotation: radio flipped before config, or vice versa)

    Recovery: ``meshtastic --host localhost --set mqtt.root <declared>``
    (or fix root_topic in gateway.json if the radio is the intended truth),
    then verify the next journal publish line carries the declared root.
    """
    if main_pid is not None:
        pid_status, pid = "ok", main_pid
    else:
        pid_status, pid = _resolve_main_pid_status(
            unit, systemctl_path=systemctl_path
        )
    if pid is None:
        # No meshtasticd unit here at all (meshanchor-server) → inert: no
        # radio whose publish root COULD drift, and `service_inactive` cannot
        # own a unit that does not exist. Policy in ONE place (2026-08-12).
        note_unit_presence_gate(
            "mqtt_root_drift", pid_status,
            absent_reason=f"no {unit} unit on this box; no radio root to compare",
            unresolved_reason="meshtasticd inactive or MainPID unresolvable",
        )
        return None

    # The default reader records WHETHER the journal answered, alongside the
    # line itself — captured from the one call it already makes, never a
    # second subprocess per tick (a per-tick journalctl on a 905 MB box is
    # not a rounding error). An INJECTED newest_line_fn is a test seam that
    # positively returned its answer, so it defaults to "ok".
    read_status = {"status": "ok"}
    if newest_line_fn is None:
        def newest_line_fn(pattern: str) -> Optional[str]:
            st, ln = _journal_newest_match_status(
                unit, pattern, lookback, journalctl_path=journalctl_path
            )
            read_status["status"] = st
            return ln

    line = newest_line_fn("JSON publish message to ")
    if line is None:
        # These used to be one None. What the silence MEANS depends on
        # whether the channel worked (honest_failure_modes #2): a journalctl
        # that ran and found nothing is a positive observation that this
        # radio has no MQTT JSON uplink — the RX-only collector case, where
        # there is no consumer/radio pair to drift and INERT is the truth.
        # Only a journal we could not read is indeterminate. Before
        # 2026-08-05 both were the pessimistic note, so four fleet boxes sat
        # permanently indeterminate and a real journal outage would have
        # been invisible inside that noise.
        if read_status["status"] == "ok":
            note_disposition(
                "mqtt_root_drift", "inert",
                reason="no MQTT JSON uplink from this radio — nothing to compare")
        else:
            note_disposition(
                "mqtt_root_drift", "indeterminate",
                reason="journal unavailable — cannot observe the publish root")
        return None  # no json uplink at all — nothing to compare
    m = _MQTT_PUBLISH_TOPIC_RE.search(line)
    if m is None:
        note_disposition("mqtt_root_drift", "indeterminate",
                         reason="publish line shape unrecognized")
        return None  # publish line shape changed — indeterminate, not drift
    observed = m.group(1).strip("/")

    if declared_root is not None:
        declared, status = declared_root.strip().strip("/"), "ok"
    else:
        user_fn = service_user_fn
        if user_fn is None:
            from utils.rns_tree_perms import _read_rnsd_user
            user_fn = _read_rnsd_user
        status, declared = _declared_root_status(user_fn())
    if status == "absent":
        # No gateway.json → this box runs no MQTT bridge consumer, so there
        # is nothing here that CAN be deaf to the radio's root. That is the
        # organ being legitimately absent, not a failed observation.
        note_disposition(
            "mqtt_root_drift", "inert",
            reason="no gateway.json on this box — no MQTT bridge consumer")
        return None
    if status != "ok" or not declared:
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
from utils.watchdog_probes_parity import (  # noqa: E402,F401 (back-compat re-export)
    DEFAULT_PARITY_DEBOUNCE_PATH,
    DEFAULT_PARITY_DIRTY_STATE_PATH,
    PARITY_DIRTY_WINDOW_MAX_S,
    PARITY_UNCOMMITTED_PARK_S,
    _git_dirty_paths,
    _load_parity_dirty_window,
    _parity_label_to_relpath,
    _save_parity_dirty_window,
    probe_parity_drift,
)
from utils.watchdog_probes_rns_env import (  # noqa: E402,F401 (back-compat re-export)
    DEFAULT_RNS_STRAY_DEBOUNCE_PATH,
    DEFAULT_RNS_STRAY_WAIVERS_PATH,
    _LIB_STRAY_SITE_GLOBS,
    _enumerate_lib_installs,
    _load_stray_waivers,
    probe_rns_env_coherence,
)
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
from utils.watchdog_probes_claw_watch import (  # noqa: E402,F401 (re-export)
    DEFAULT_CLAW_WATCH_DEBOUNCE_PATH,
    _fold_watch_verdicts,
    probe_claw_watched_node_silent,
)
from utils.watchdog_probes_peer_rf import (  # noqa: E402,F401 (re-export)
    DEFAULT_PEER_RF_STATE_PATH,
    load_peer_config,
    probe_segment_peer_silent,
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


def role_expected_active() -> Optional[Tuple[str, ...]]:
    """Units this box's declared role says must be ACTIVE, or None.

    Reuses probe_role_drift's plan helper — the converge SSOT
    (scripts/provision_role.py + docs/fleet_roles.yaml + the box's documented
    service_overrides) — so the probe that PAGES and the probe that reports
    drift read one source instead of two hand-maintained lists.

    None means the role or the tooling could not be resolved. That is
    indeterminate, not "nothing to watch": the caller holds its previous
    default rather than widening or narrowing on a guess.
    """
    try:
        try:
            from utils.rns_tree_perms import _read_rnsd_user
            service_user = _read_rnsd_user()
        except Exception:
            service_user = None
        role, overrides = _read_deployment_declaration(service_user)
    except Exception:
        return None
    if not role:
        return None
    try:
        actions = _plan_role_actions(role, overrides or {}, "/opt/meshforge")
    except Exception:
        return None
    if not actions:
        return None

    # provision_role.Action: item / current / desired / verb. `desired` is
    # "enabled" for units the role wants running and "disabled" otherwise, so
    # a documented service_override (moc3's deliberately-off meshforge-map)
    # lands on "disabled" and is correctly NOT watched — the false positive
    # moc3's hand-written override existed to suppress, now handled by the
    # declaration itself instead of by deleting entries from a list.
    units = []
    for a in actions:
        item = getattr(a, "item", None)
        desired = getattr(a, "desired", None)
        if not item or not desired:
            continue
        if str(desired).strip() != "enabled":
            continue
        item = str(item)
        if ":" in item:
            continue          # delta:/foundation: pseudo-actions
        if item.endswith(".timer"):
            continue          # timers are not "expected active" units
        units.append(item if item.endswith(".service") else f"{item}.service")
    return tuple(dict.fromkeys(units)) or None

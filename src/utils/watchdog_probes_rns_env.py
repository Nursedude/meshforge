"""Watchdog probes — RNS/LXMF cross-environment coherence.

Split out of ``watchdog_probes_drift.py`` 2026-08-09 (that file crossed the
1,500-line MF025 cap again while fixing the moc4 ``rns_version_drift``
blindness). Holds the stray-env coherence probe — "do all of this box's RNS
copies AGREE with each other" — which is deliberately distinct from the fork-
PIN compliance probe (``probe_rns_version_drift``) that stays in
watchdog_probes_drift. Import via the ``utils.watchdog_probes`` hub, not from
here; watchdog_probes_drift also re-exports these for back-compat.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from utils.watchdog_probe_core import (
    Signal,
    _SYSTEM_DIST_GLOBS,
    _load_parity_streak,
    _read_pkg_version_at_dirs,
    _save_parity_streak,
    note_disposition,
)


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
    "system-dist": list(_SYSTEM_DIST_GLOBS),
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


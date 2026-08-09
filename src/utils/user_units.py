"""Root-safe reads of the operator's ``systemd --user`` unit enrollment.

**Why this module exists.** User units are structurally invisible to
system-level ``systemctl`` (Issue #82): the sandboxed-root watchdog cannot
reach the operator's user bus at all, and ``systemctl --user`` from root
answers about root's own (usually absent) session. The bus-free answer is the
filesystem — ``systemctl --user enable X.timer`` on a ``WantedBy=timers.target``
unit creates a symlink in ``~/.config/systemd/user/timers.target.wants/``, and
that symlink IS the declaration that the unit is enabled. Reading it needs no
privilege and no bus.

**Why it is shared.** Three call sites needed this answer and were on their way
to three implementations — the drift trap honest_failure_modes #5 names
explicitly ("two consumers of one artifact share ONE constant"). The
enumerator below was proven first in ``watchdog_probes_user`` (2026-07-19,
the kiai tracer-timer hole) and is promoted here verbatim rather than copied.
Callers: ``watchdog_probes_user`` (is an enabled timer's job failing?),
``watchdog_probes_gateway_flow`` (is the soak scheduled here at all?), and
``scripts/provision_role.py`` (does live enrollment match the declared role?).

**Tri-state, always.** Every function distinguishes *observed absent* from
*could not observe*. An unreadable directory is ``None``, never an empty map —
collapsing those is the defect class this whole tree exists to prevent.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

# The operator's user-unit root. Enablement is a symlink under one of its
# `*.target.wants/` subdirectories — readable without root and without a bus.
_USER_UNIT_ROOT = (".config", "systemd", "user")

# DELIBERATELY NOT CONSULTED: /etc/systemd/user/timers.target.wants, the
# site-wide preset equivalent. It was included for one commit on 2026-08-09
# and reverted the same day — it is a GLOBAL path, so a function parameterised
# by `user_home` stopped being determined by its own argument, and an existing
# test that fully pinned a temp home started failing on CI (whose runner image
# has that directory populated) while passing on every fleet box, which has
# none. No box on this fleet enables user timers by site preset, so the
# coverage was theoretical while the loss of hermeticity was real. If a box
# ever needs it, pass the dir explicitly to a caller that takes dirs (see
# watchdog_probes_gateway_flow._timer_enrolled) rather than reaching for a
# global here.


def resolve_operator_home() -> Optional[str]:
    """The operator's home dir, correct under a ROOT service.

    Derived from the operator UID, NOT from the *effective* user's home (the
    pathlib helper MF001 bans, or ``$HOME``) — under the root watchdog those
    give ``/root`` and the caller would read the wrong box's state (the
    2026-08-05 stale-``/root``-config incident, where a root service read a
    stale config and a probe blamed rnsd for 8.8 days).
    ``None`` when no operator is resolvable — the caller must treat that as
    unobservable, never as "nothing enabled".
    """
    try:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
    except Exception:
        op = None
    if not op:
        return None
    try:
        import pwd
        return pwd.getpwuid(op[0]).pw_dir
    except (KeyError, OSError):
        return None


def timer_wants_dirs(user_home: str) -> Optional[List[str]]:
    """EVERY ``*.target.wants`` dir under the operator's user-unit root.

    ⚠️ Not just ``timers.target.wants``. A unit is enabled if a symlink to it
    exists under ANY target's wants dir, which is why ``systemctl --user
    is-enabled`` can say ``enabled`` for a timer that is nowhere near
    ``timers.target.wants``. Found on meshanchor-server 2026-08-09:
    ``meshanchor-map-restart.timer`` declares ``WantedBy=timers.target`` but is
    linked from ``default.target.wants`` — enabled and firing for months, and
    invisible to a reader that opened only the one directory. Reading a single
    target would have reported that live timer as disabled, which for a
    declared-``enabled`` unit is FALSE drift.

    Tri-state: ``None`` when the root exists but cannot be listed
    (unobservable); ``[]`` when the root is simply absent (a real observation —
    this box has no user units, so nothing is enabled).
    """
    root = os.path.join(user_home, *_USER_UNIT_ROOT)
    if not os.path.isdir(root):
        return []
    try:
        return sorted(os.path.join(root, n) for n in os.listdir(root)
                      if n.endswith(".target.wants"))
    except OSError:
        return None


def enabled_user_timers(user_home: str) -> Optional[Dict[str, str]]:
    """Map ``timer unit name -> triggered service unit name`` for the timers
    the operator has enabled.

    The triggered service is ``Unit=`` from the ``[Timer]`` section when the
    author set one, else systemd's default of the same stem with a
    ``.service`` suffix.

    Returns ``None`` when a wants directory exists but cannot be read — that
    is *unobservable*, which the caller must not collapse into "no timers"
    (honest_failure_modes #1). Directories that are simply absent are a real,
    observed empty enrollment; all-absent returns ``{}``.
    """
    dirs = timer_wants_dirs(user_home)
    if dirs is None:
        return None  # root unreadable → unobservable, never "none enabled"
    out: Dict[str, str] = {}
    for wants in dirs:
        if not os.path.isdir(wants):
            continue  # absent is an observation, not a blind spot
        try:
            names = [n for n in os.listdir(wants) if n.endswith(".timer")]
        except OSError:
            return None  # unreadable → unobservable, never "none enabled"

        for timer in names:
            if timer in out:
                continue  # home wins over the site-wide preset
            service = timer[: -len(".timer")] + ".service"
            # Best-effort Unit= override. An unreadable timer file falls back
            # to the stem default rather than dropping the timer from the map
            # — the conservative choice is to keep WATCHING it under its
            # likely name.
            try:
                with open(os.path.join(wants, timer), "r", encoding="utf-8",
                          errors="replace") as fh:
                    body = fh.read()
                m = re.search(r"(?mi)^\s*Unit\s*=\s*(\S+)\s*$", body)
                if m and m.group(1).endswith(".service"):
                    service = m.group(1)
            except OSError:
                pass
            out[timer] = service
    return out


def user_timer_enrolled(unit: str,
                        user_home: Optional[str] = None) -> Optional[bool]:
    """Is ``unit`` (e.g. ``meshforge-synth-soak.timer``) enabled here?

    ``True`` / ``False`` / ``None`` (unobservable — operator unresolvable or a
    wants dir that exists but cannot be read).

    Callers use this to tell *absent by design* from *died quietly*, so the
    ``None`` leg matters as much as the other two: a probe that flattens it to
    ``False`` would silently retire itself on a box it cannot currently see.
    """
    if user_home is None:
        user_home = resolve_operator_home()
        if user_home is None:
            return None
    timers = enabled_user_timers(user_home)
    if timers is None:
        return None
    return unit in timers

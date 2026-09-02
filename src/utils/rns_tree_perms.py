"""RNS-tree permission foundation — the shared, app-agnostic SSOT.

A non-root ``rnsd`` must be able to create, rotate, and append to its logfile
under the canonical configdir, and own its ``storage/`` subtree — otherwise
``RNS.log()`` fails on every write (the mf.4 self-deadlock trigger / Issue #73
class) and path/identity/hashlist persistence silently breaks. This module is
the **single definition** of "make the ``/etc/reticulum`` tree writable by a
non-root rnsd user" — the chown/chmod layout proven on the federator, the drift
detector, and a focused read-only probe.

It deliberately contains **no app-specific paths and only stdlib imports**, so it
is carried **byte-identical** in both MeshForge and MeshAnchor and gated by
``scripts/parity_check.py``. The mf.4 / #73 hang was born from *two* definitions
of this layout (the installer's root:root vs the TUI's user-flip) that agreed
only by luck — having one definition, shared, is the whole point.

Consumers:
- ``utils.rns_alignment`` (MeshForge) delegates its logfile-perms guard +
  apply-path here (and keeps its MeshForge-fleet-specific rpc_key/client-config
  alignment, which is *not* shared).
- ``utils.fleet_foundation`` (both repos) delegates the RNS leg of the
  permission foundation here.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# The canonical RNS configdir the whole fleet shares (rnsd --config /etc/reticulum).
CANONICAL_CONFIGDIR = Path('/etc/reticulum')

# A safe POSIX username: no shell metacharacters, so it can never break out of
# the chown/chmod script even though it is only placed in already-safe positions.
_USERNAME_RE = re.compile(r'^[a-z_][a-z0-9_-]*$')


@dataclass
class RnsTreePerms:
    """The RNS-tree permission facts the foundation reasons about.

    A focused subset (the perms-relevant fields) so this module never depends on
    a heavier, app-specific alignment-state struct. ``configdir_owner`` /
    ``configdir_mode`` / ``logfile_owner`` are ``'user:group'`` / octal-mode-string
    / ``None`` (absent or not probed — never guessed).
    """
    rnsd_user: Optional[str] = None
    configdir_owner: Optional[str] = None
    configdir_mode: Optional[str] = None
    logfile_exists: bool = False
    logfile_owner: Optional[str] = None


def _group_writable(mode: Optional[str]) -> bool:
    """True if an octal mode string (e.g. '1775', '755') grants group-write."""
    if not mode:
        return False
    try:
        # The group permission digit is always second from the right.
        return bool(int(mode[-2], 8) & 0o2)
    except (ValueError, IndexError):
        return False


def logfile_perms_drift(perms: RnsTreePerms) -> Optional[str]:
    """mf.4 / Issue #73 guard. A non-root rnsd must be able to create, rotate,
    and append to its logfile under the canonical configdir, or RNS.log() fails
    on every write — which self-deadlocked the daemon pre-fork-mf.4 and loses
    all logs after. A re-provision that recreates /etc/reticulum as root:root is
    the recurrence path (moc1/moc2, 2026-06-01). Returns a drift reason or None.

    Canonical layout (proven on the federator): configdir root:<rnsd_user> mode
    1775 (group-writable + sticky so the user can create/rotate), logfile
    <rnsd_user>:<rnsd_user>. A root-run rnsd needs no fix (root writes anything).
    Returns None when the perms facts weren't probed (configdir_owner is None) —
    never guess from absent data.
    """
    user = perms.rnsd_user
    if not user or user == 'root' or not _USERNAME_RE.match(user):
        return None
    if perms.configdir_owner is None:
        return None  # not probed / inaccessible — don't flag on absent data
    cd_group = perms.configdir_owner.split(':')[-1]
    if cd_group != user or not _group_writable(perms.configdir_mode):
        return (
            f"{CANONICAL_CONFIGDIR} is {perms.configdir_owner} mode "
            f"{perms.configdir_mode or '?'} but rnsd runs as {user} — it cannot "
            f"create/rotate its logfile, so RNS.log() fails on every write "
            f"(mf.4 self-deadlock trigger / lost logs). "
            f"Fix: chown root:{user} + chmod 1775 {CANONICAL_CONFIGDIR}"
        )
    if perms.logfile_exists:
        owner_user = (perms.logfile_owner or '').split(':')[0]
        if owner_user != user:
            return (
                f"{CANONICAL_CONFIGDIR}/logfile is owned by "
                f"{perms.logfile_owner or '?'} but rnsd runs as {user} — appends "
                f"fail (mf.4 self-deadlock trigger / lost logs). "
                f"Fix: chown {user}:{user} {CANONICAL_CONFIGDIR}/logfile"
            )
    return None


def build_logfile_perms_script(user: str) -> str:
    """Bash that makes the canonical RNS tree writable by a non-root rnsd user,
    matching the proven federator layout: configdir root:<user> mode 1775
    (group-writable + sticky so the user can create/rotate), logfile <user>:<user>,
    and the storage/ subtree <user>-owned (rnsd persists path/identity/hashlist
    there — root-owned storage silently broke persistence on the root boxes, which
    only worked because storage happened to be 0777). The config FILE stays
    root:root (operator-managed). Idempotent.

    `user` is validated against _USERNAME_RE upstream, so it cannot carry shell
    metacharacters, but it is only ever placed in already-safe positions.
    """
    cd = str(CANONICAL_CONFIGDIR)
    return f"""
set -e
CD={cd}
chown root:{user} "$CD"
chmod 1775 "$CD"
if [ -e "$CD/logfile" ]; then chown {user}:{user} "$CD/logfile"; fi
if [ -d "$CD/storage" ]; then chown -R {user}:{user} "$CD/storage"; fi
"""


def apply_logfile_perms(operator_user: str) -> None:
    """Apply the canonical RNS-tree perms for a non-root rnsd (the foundation's
    RNS layer): configdir ``root:<user> 1775``, logfile ``<user>:<user>``, config
    stays root-owned. Idempotent; requires sudo. This is the single apply-path for
    the RNS half of the permission foundation — ``fleet_foundation`` and the TUI
    ``_fix_rnsd_user`` both call it instead of carrying their own copies (mf.4/#73).

    ``operator_user`` is validated as a safe username (no shell metacharacters).
    """
    if not _USERNAME_RE.match(operator_user):
        raise ValueError(f"unsafe operator_user {operator_user!r}")
    subprocess.run(
        ['sudo', 'bash', '-c', build_logfile_perms_script(operator_user)],
        check=True, timeout=30,
    )


# --- focused read-only probe (self-contained: no heavy alignment-state dep) ----


def _parse_user_from_unit_files(paths: list) -> Optional[str]:
    """Parse User= from a list of systemd unit/drop-in files (last wins, drop-in
    semantics). Pure over the path list — the testable core of _read_rnsd_user.
    """
    user = None
    for p in paths:
        try:
            with open(p) as f:
                for raw in f:
                    line = raw.strip()
                    if line.startswith('User='):
                        user = line[len('User='):].strip()
        except OSError:
            continue
    return user


def _rnsd_unit_paths() -> list:
    paths = [
        '/etc/systemd/system/rnsd.service',
        '/lib/systemd/system/rnsd.service',
    ]
    dropin_dir = Path('/etc/systemd/system/rnsd.service.d')
    if dropin_dir.is_dir():
        paths.extend(str(p) for p in sorted(dropin_dir.glob('*.conf')))
    return paths


def _read_rnsd_user() -> Optional[str]:
    """Parse User= from the rnsd unit + drop-ins (last wins, drop-in semantics).

    None means EITHER "a unit was read and declares no User= (root)" OR "no
    unit file could be read at all". Consumers that must tell those apart
    (an unreadable unit is unobserved, not root) pair this with
    ``rnsd_unit_readable()``.
    """
    return _parse_user_from_unit_files(_rnsd_unit_paths())


def rnsd_unit_readable() -> bool:
    """True when at least one rnsd unit/drop-in file could be opened."""
    for p in _rnsd_unit_paths():
        try:
            with open(p):
                return True
        except OSError:
            continue
    return False


def _stat_owner(path: Path, sudo: bool = False) -> Optional[str]:
    """Return 'user:group' for path, or None if not accessible.

    ``sudo=True`` means "escalate if needed" — when the caller is ALREADY root
    we stat directly: sudo adds nothing, and inside a NoNewPrivileges sandbox
    (the watchdog) every ``sudo -n`` FAILS, which silently mapped the whole
    probe to None/"never guess" and left ``foundation_perms_drift`` inert
    (the 2026-06-09 finding; same class as the #79 sandbox-sudo lesson).
    """
    try:
        if sudo and os.geteuid() != 0:
            res = subprocess.run(
                ['sudo', '-n', 'stat', '-c', '%U:%G', str(path)],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            return None
        st = path.stat()
        import grp
        import pwd
        try:
            return f"{pwd.getpwuid(st.st_uid).pw_name}:{grp.getgrgid(st.st_gid).gr_name}"
        except KeyError:
            return f"{st.st_uid}:{st.st_gid}"
    except (OSError, subprocess.SubprocessError):
        return None


def _stat_mode(path: Path, sudo: bool = False) -> Optional[str]:
    """Return the octal mode string (e.g. '1775') for path, or None.

    Same root-direct rule as ``_stat_owner``: escalate only when not root.
    """
    try:
        if sudo and os.geteuid() != 0:
            res = subprocess.run(
                ['sudo', '-n', 'stat', '-c', '%a', str(path)],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            return None
        return oct(path.stat().st_mode & 0o7777)[2:]
    except (OSError, subprocess.SubprocessError):
        return None


def _path_exists(path: Path, sudo: bool = False) -> bool:
    """True when path exists; escalates via sudo only when not already root."""
    if sudo and os.geteuid() != 0:
        try:
            return subprocess.run(
                ['sudo', '-n', 'test', '-e', str(path)],
                capture_output=True, timeout=5,
            ).returncode == 0
        except subprocess.SubprocessError:
            return False
    try:
        return path.exists()
    except OSError:
        return False


def probe_rns_tree_perms() -> RnsTreePerms:
    """Read-only probe of the RNS-tree perms facts (rnsd user + configdir owner/
    mode + logfile owner/exists). Stats directly when running as root (the
    watchdog context — sudo is both pointless and sandbox-blocked there) and
    falls back to sudo stat for a non-root caller on the root-owned configdir;
    leaves fields None when not probed/inaccessible so the drift detector never
    guesses. Cheap: no full RNS-config scan, no MeshForge-specific paths.
    """
    perms = RnsTreePerms(rnsd_user=_read_rnsd_user())
    cd = CANONICAL_CONFIGDIR
    perms.configdir_owner = _stat_owner(cd, sudo=True)
    perms.configdir_mode = _stat_mode(cd, sudo=True)
    logfile = cd / 'logfile'
    if _path_exists(logfile, sudo=True):
        perms.logfile_exists = True
        perms.logfile_owner = _stat_owner(logfile, sudo=True)
    return perms

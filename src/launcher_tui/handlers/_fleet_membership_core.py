"""Fleet-membership declaration core — the first-run "standalone or fleet?"
seam, pure and unit-testable (no TUI, no dialogs).

An installing user should declare fleet membership in the TUI, not
hand-author ``~/.config/meshforge/fleet_hosts``. This module owns the file
mechanics; ``fleet_provision.py`` owns the dialog flow. Reading reuses
``utils.fleet_hosts.read_fleet_hosts_file`` — THE reader
shared with the rollup and lab tooling — so the wizard can never write a
file those consumers read differently.

Honesty contract: absent/empty file = standalone (a valid mode, not an
error); a file that EXISTS but cannot be read or holds a corrupt entry is
mode "unreadable" — never standalone, and the dialog will not let Edit
overwrite a list it could not read (r11, review r10/Fable); declaring standalone RENAMES the host list (reversible) instead of
deleting it; reachability is reported per host and an unreachable host is
still writable — the operator may be pre-declaring a box that is not
racked yet, and the probe's job is to inform, not gate.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from utils.fleet_hosts import FleetHostsUnreadable, read_fleet_hosts_file
from utils.paths import get_real_user_home

#: standalone declaration renames to this, preserving the list for re-join
DISABLED_SUFFIX = ".disabled"

#: ssh aliases / hostnames / IPs only — anything else is likely a paste error
#: (and this string later rides an ssh argv, so reject metacharacters loudly)
_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_HEADER = (
    "# MeshForge fleet host list — one ssh alias per line, '#' comments.\n"
    "# Consumed by: mini rollup, fleet_pull, fleet dup collector, lab tooling.\n"
    "# Written by the TUI Fleet Membership wizard; hand-edits are fine too.\n"
)


def default_hosts_path() -> str:
    """The user-scope fleet_hosts location (sudo-safe via real-user home)."""
    return str(get_real_user_home() / ".config" / "meshforge" / "fleet_hosts")


def parse_host_input(text: str) -> List[str]:
    """Operator-typed host list (commas and/or whitespace) → clean list.

    Deduplicates preserving order; raises ValueError naming the first bad
    token so the dialog can show exactly what to fix.
    """
    hosts: List[str] = []
    for token in re.split(r"[,\s]+", text or ""):
        if not token:
            continue
        if not _HOST_RE.match(token):
            raise ValueError(f"not a hostname/alias: {token!r}")
        if token not in hosts:
            hosts.append(token)
    return hosts


def membership_state(hosts_path: Optional[str] = None) -> dict:
    """Read the declaration. Absent or hosts-free file = standalone; a file
    that exists but is refused = "unreadable" with the reason."""
    path = hosts_path or default_hosts_path()
    hosts: List[str] = []
    error = ""
    try:
        hosts = read_fleet_hosts_file(path, allow_empty=True)   # the wizard SHOWS contents
    except FileNotFoundError:
        pass
    except FleetHostsUnreadable as e:
        error = str(e)
    # The wizard manages ONE file; the fleet tools resolve several tiers
    # (r13, Fable r12 #6). Say so when they differ, never "this box only".
    resolved_file, resolved_error, resolved_absent_why = "", "", ""
    if hosts_path is None:
        from utils.fleet_hosts import ENV_OVERRIDE_KEYS, resolve_fleet_hosts
        from utils.fleet_hosts import resolve_fleet_hosts_file
        try:
            rf = resolve_fleet_hosts_file()
            resolved_file = str(rf) if rf is not None else ""
            if rf is not None:
                resolve_fleet_hosts()   # r17: an EMPTY list is refused too
            else:
                # r17 (Fable r16 F1): a SET override pointing nowhere makes
                # every tool see NO list — never indistinguishable from
                # "the tools agree with this file".
                for key in ENV_OVERRIDE_KEYS:
                    if os.environ.get(key):
                        resolved_absent_why = ("$%s=%s does not exist "
                                               "(authoritative)" % (key, os.environ[key]))
                        break
        except FleetHostsUnreadable as e:
            resolved_error = str(e)
    return {
        "mode": "unreadable" if error else ("fleet" if hosts else "standalone"),
        "error": error,
        "resolved_file": resolved_file,
        "resolved_error": resolved_error,
        "resolved_absent_why": resolved_absent_why,
        "hosts": hosts,
        "path": path,
        "exists": os.path.lexists(path),
        "disabled_exists": Path(path + DISABLED_SUFFIX).exists(),
    }


def tools_note(st: dict) -> str:
    """One honest line for the dialog when the fleet TOOLS do not read this
    wizard's file (r15, Fable r14 F2: in FLEET mode the head showed the
    wizard's list while every tool used another one). "" when they agree."""
    if st.get("resolved_error"):
        if st.get("mode") == "unreadable" and str(st.get("path")) in st["resolved_error"]:
            return ""   # r17 (Fable r16 O2): the head already said it, for this file
        return f"\n⚠ The fleet tools REFUSE their list: {st['resolved_error']}"
    if st.get("resolved_absent_why"):
        return f"\n⚠ The fleet tools see NO list: {st['resolved_absent_why']}"
    rf = st.get("resolved_file")
    if rf and rf != st.get("path"):
        return (f"\n⚠ The fleet tools read {rf} — NOT this file "
                f"({st.get('path')}). Editing here does not change them.")
    return ""


def write_fleet_hosts(hosts_path: str, hosts: List[str]) -> None:
    """Write the list with a provenance header (tmp+rename, parents made).

    Under sudo the file must land owned by the invoking user, or their own
    rollup/fleet tooling loses write access to their own config.
    """
    path = Path(hosts_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_HEADER + "".join(h + "\n" for h in hosts))
    tmp.replace(path)
    _chown_to_real_user(path)


def declare_standalone(hosts_path: str) -> Optional[str]:
    """Rename fleet_hosts aside (reversible). None = was already standalone.

    r19 (Fable r18 F4): an EXISTING file naming no hosts is refused by every
    fleet tool, so this is the in-app cure for it — but renaming it over a
    SAVED ``.disabled`` list would destroy the saved hosts. An empty current
    file is therefore removed (it names nobody) and the saved list kept."""
    path = Path(hosts_path)
    if not path.exists():
        return None
    target = Path(hosts_path + DISABLED_SUFFIX)
    if target.exists():
        try:
            empty = not read_fleet_hosts_file(path, allow_empty=True)
        except FleetHostsUnreadable:
            empty = False
        if empty:
            path.unlink()
            return str(target)
    path.replace(target)
    return str(target)


def probe_hosts(hosts: List[str],
                runner: Optional[Callable[[str, float], bool]] = None,
                timeout_s: float = 8.0) -> List[Tuple[str, str]]:
    """Per-host reachability: [(host, 'ok'|'unreachable')], order preserved.

    A runner exception counts as unreachable — a broken probe must never
    read as a healthy host (unobservable is not ok).
    """
    runner = runner or _ssh_runner
    out: List[Tuple[str, str]] = []
    for h in hosts:
        try:
            ok = bool(runner(h, timeout_s))
        except Exception:
            ok = False
        out.append((h, "ok" if ok else "unreachable"))
    return out


def _ssh_runner(host: str, timeout_s: float) -> bool:
    """BatchMode ssh true — the same transport every fleet consumer uses."""
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes",
             "-o", f"ConnectTimeout={int(timeout_s)}", host, "true"],
            capture_output=True, timeout=timeout_s + 4, check=False)
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _chown_to_real_user(path: Path) -> None:
    """Root-run TUI writes into the invoking user's config — hand it back."""
    sudo_user = os.environ.get("SUDO_USER")
    if os.geteuid() != 0 or not sudo_user:
        return
    try:
        import pwd
        pw = pwd.getpwnam(sudo_user)
        os.chown(path, pw.pw_uid, pw.pw_gid)
        os.chown(path.parent, pw.pw_uid, pw.pw_gid)
    except (KeyError, OSError):
        pass  # ownership best-effort; the write itself already succeeded

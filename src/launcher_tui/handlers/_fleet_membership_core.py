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
import stat
import subprocess
import tempfile
import time
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
    # What the fleet TOOLS see — asked of THE resolver, one source for the
    # head, the Edit prompt, the Go-standalone prompt and its result.
    view = tools_view() if hosts_path is None else {"kind": "na", "file": "",
                                                     "hosts": [], "why": "", "path": ""}
    resolved_file = view["file"] if view["kind"] == "list" else ""
    resolved_error = view["why"] if view["kind"] in ("refuse", "unknown") else ""
    resolved_error_path = view["path"] if view["kind"] == "refuse" else ""
    resolved_absent_why = view["why"] if view["kind"] == "none" else ""
    return {
        "mode": "unreadable" if error else ("fleet" if hosts else "standalone"),
        "error": error,
        "view": view,
        "resolved_file": resolved_file,
        "resolved_error": resolved_error,
        "resolved_error_path": resolved_error_path,
        "resolved_absent_why": resolved_absent_why,
        "hosts": hosts,
        "path": path,
        "exists": os.path.lexists(path),
        "disabled_exists": Path(path + DISABLED_SUFFIX).exists(),
        "disabled_restorable": _restorable(path + DISABLED_SUFFIX),
    }


def _restorable(p: str) -> bool:
    """A saved list Edit can actually offer back: readable, with hosts
    (Fable standalone r6 O2: a socket at .disabled read as 'Edit restores it')."""
    try:
        return bool(read_fleet_hosts_file(p))
    except OSError:
        return False


def tools_view(skip: "Optional[str]" = None) -> dict:
    """What the fleet TOOLS see (or WOULD see with ``skip`` gone), asked of
    THE resolver — never re-derived here (Fable standalone r3: three hand-
    assembled sentences each guessed the resolver's rules and were wrong:
    "the first readable one", a string path compare, a partial's after-note).
    kind: list | refuse | none | unknown."""
    from utils.fleet_hosts import ENV_OVERRIDE_KEYS, resolve_fleet_hosts_file
    v = {"kind": "none", "file": "", "hosts": [], "why": "", "path": ""}
    try:
        rf = resolve_fleet_hosts_file(skip=skip)
        if rf is None:
            for key in ENV_OVERRIDE_KEYS:
                if os.environ.get(key):
                    v["why"] = "$%s=%s does not exist (authoritative)" % (key, os.environ[key])
                    break
            return v
        v["file"] = str(rf)
        v["hosts"] = read_fleet_hosts_file(rf)
        v["kind"] = "list"
    except FleetHostsUnreadable as e:
        v.update(kind="refuse", path=str(e.path), file=str(e.path), why=str(e))
    except FileNotFoundError as e:
        name = e.filename or "(unknown)"
        v.update(kind="refuse", path=str(e.filename or ""),
                 why="fleet host list %s vanished while being read" % name)
    except Exception as e:   # advisory: say it could not be checked
        v.update(kind="unknown", why=str(e))
    return v


def _same_file(a: str, b: str) -> bool:
    return bool(a) and bool(b) and os.path.realpath(a) == os.path.realpath(b)


def describe_view(v: dict, wizard_path: str, *, after: bool = False) -> str:
    """ONE sentence for a tools view — the only place that words it."""
    lead = "After this, the fleet tools" if after else "The fleet tools"
    if v["kind"] == "list":
        n = len(v["hosts"])
        if _same_file(v["file"], wizard_path):
            return f"{lead} read THIS file ({n} host(s))."
        return (f"{lead} read {v['file']} ({n} host(s)) — NOT this file "
                f"({wizard_path})"
                # the box-verdict belongs to the AFTER prompt only (r4 #3);
                # elsewhere the actionable truth is what editing changes
                + ("; this box STAYS in a fleet." if after
                   else ". Editing here does not change them."))
    if v["kind"] == "refuse":
        return f"{lead} REFUSE their list: {v['why']}"
    if v["kind"] == "none":
        return (f"{lead} see NO list" + (f": {v['why']}" if v["why"] else "")
                + (" — this box is standalone." if after else "."))
    return f"Could not check what the fleet tools see ({v['why']})."


def tools_note(st: dict) -> str:
    """The head/Edit note: "" when the tools agree with this file (they read
    it), or when the head already refused THIS file; else one ⚠ line."""
    v = st.get("view")
    if not v or v["kind"] == "na":
        return ""
    path = str(st.get("path"))
    if v["kind"] == "list" and _same_file(v["file"], path):
        return ""
    if v["kind"] == "refuse" and st.get("mode") == "unreadable" and _same_file(v["path"], path):
        return ""
    if v["kind"] == "none" and not v["why"]:
        return ""   # no list anywhere and none named: the head says STANDALONE
    return "\n⚠ " + describe_view(v, path)


def write_fleet_hosts(hosts_path: str, hosts: List[str]) -> None:
    """Write the list with a provenance header (tmp+rename, parents made).

    Under sudo the file must land owned by the invoking user, or their own
    rollup/fleet tooling loses write access to their own config.
    """
    path = Path(hosts_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A UNIQUE temp name, created exclusively (Fable standalone r6 F1): the
    # fixed "fleet_hosts.tmp" FOLLOWED a symlink/hardlink planted there and
    # wrote through into the saved list; two wizard sessions also shared it
    # (honest_failure_modes #8). mkstemp refuses an existing name.
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        mode = 0o644
    fd, tmpname = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
                                   suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_HEADER + "".join(h + "\n" for h in hosts))
        os.chmod(tmpname, mode)
        os.replace(tmpname, str(path))
    except BaseException:
        try:
            os.unlink(tmpname)
        except OSError:
            pass
        raise
    _chown_to_real_user(path)


def _utc_stamp() -> str:
    """The backup suffix clock — one seam, so tests pin it without patching
    the global ``time`` module (a global patch recursed and hung pytest)."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def plan_standalone(hosts_path: str, *, with_after: bool = True) -> dict:
    """What "Go standalone" WILL do, decided without touching anything — the
    dialog shows exactly this, and declare_standalone re-plans before acting
    (Fable r20, own arc). Input table (C = current file, D = saved
    ``.disabled``):

    =================  ========================================================
    C absent           noop (already standalone)
    C unusable         refuse — dangling link, unreadable, corrupt: never
                       promoted over a saved list, never "already standalone"
    C empty            remove C (it names nobody); D, if any, untouched
    C has hosts        D absent -> C becomes D
                       D a file -> D kept as D.<UTC stamp>, then C becomes D
                       D anything else (a directory...) -> refuse
    =================  ========================================================
    """
    path = Path(hosts_path)
    target = Path(hosts_path + DISABLED_SUFFIX)
    plan = {"action": "noop", "path": str(path), "saved": str(target),
            "backup": "", "why": "", "after_view": None}
    if not os.path.lexists(path):
        return plan
    if path.is_symlink():
        # #2: moving a symlink made D a self-loop (C -> D) or a pointer into
        # another tier — a "preserved" list nobody can read.
        plan.update(action="refuse",
                    why="%s is a symlink (to %s) — change it by hand; nothing "
                        "was changed" % (path, os.readlink(path)))
        return plan
    try:
        hosts = read_fleet_hosts_file(path, allow_empty=True)
    except (FleetHostsUnreadable, FileNotFoundError) as e:
        plan.update(action="refuse", why=str(e))
        return plan
    if not hosts:
        plan.update(action="remove_empty")
        if with_after:
            _plan_others(plan, path)
        return plan
    if os.path.lexists(target):
        if target.is_symlink() or not target.is_file():
            plan.update(action="refuse",
                        why="%s exists and is not a regular file — move it "
                            "aside by hand; nothing was changed" % target)
            return plan
        stamp = _utc_stamp()
        backup, n = "%s.%s" % (target, stamp), 1
        while os.path.lexists(backup):          # #3: never reuse a name
            n += 1
            backup = "%s.%s.%d" % (target, stamp, n)
        plan.update(backup=backup)
    plan.update(action="move")
    if with_after:
        _plan_others(plan, path)
    return plan


def _plan_others(plan: dict, path: Path) -> None:
    """What the tools WILL see once this file is gone — the resolver's own
    answer with ``skip`` (Fable standalone r3 #2). A move ALSO changes the
    saved list and its backup: if the tools would read either of those, the
    preview cannot be predicted from "C is gone" alone, so it says so
    instead of guessing (Fable standalone r5 F1 — it said "standalone" for a
    box that ends up in a fleet)."""
    if plan.get("action") == "move":
        from utils.fleet_hosts import DEFAULT_ETC_DIR, ENV_OVERRIDE_KEYS, _tier_candidates
        moved = {os.path.realpath(x) for x in (plan["saved"], plan["backup"]) if x}
        names = [os.environ[k] for k in ENV_OVERRIDE_KEYS if os.environ.get(k)][:1]
        if not names:
            try:
                names = [str(t) for t in _tier_candidates(None, None, DEFAULT_ETC_DIR)]
            except Exception:
                names = []
        for n in names:
            if os.path.realpath(n) in moved:
                plan["after_view"] = {"kind": "unknown", "file": "", "hosts": [], "path": "",
                                      "why": "%s names where this move puts a list" % n}
                return
    plan["after_view"] = tools_view(skip=str(path))


def declare_standalone(hosts_path: str, expect: Optional[dict] = None) -> dict:
    """Carry out plan_standalone. If ``expect`` (the plan the operator saw)
    no longer matches what the files say, nothing is touched and the result
    is a refusal — the operator confirmed a different action.

    Order matters for crash-safety: the saved list is moved to its backup
    name BEFORE the current list takes its place, so a crash between the
    two steps loses nothing."""
    plan = plan_standalone(hosts_path, with_after=False)   # E1: preview unused here
    if expect is not None:
        if (plan["action"], bool(plan["backup"])) != (
                expect.get("action"), bool(expect.get("backup"))):
            return dict(plan, action="refuse",
                        why="the files changed since you confirmed — nothing was changed")
        if expect.get("backup"):
            plan["backup"] = expect["backup"]   # #4: the name the operator SAW
    if plan["backup"] and os.path.lexists(plan["backup"]):
        return dict(plan, action="refuse",
                    why="%s already exists — nothing was changed" % plan["backup"])
    try:
        if plan["action"] == "remove_empty":
            Path(hosts_path).unlink()
        elif plan["action"] == "move":
            if plan["backup"]:
                Path(plan["saved"]).rename(plan["backup"])
            try:
                Path(hosts_path).replace(plan["saved"])
            except OSError as e:
                if plan["backup"]:              # #6: put the saved list back
                    try:
                        Path(plan["backup"]).rename(plan["saved"])
                    except OSError as e2:
                        # round 2 #2: the files DID change — say where they are
                        return dict(plan, action="partial",
                                    why="%s; putting the saved list back also failed (%s). "
                                        "The saved list is now at %s (not %s); the current "
                                        "list is still at %s." % (e, e2, plan["backup"],
                                                                   plan["saved"], hosts_path))
                raise
    except OSError as e:
        return dict(plan, action="refuse", why="%s — nothing was changed" % e)
    return plan


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

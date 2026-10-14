"""NOC read grants on THIS box — the only writer of ``authorized_keys``.

A grant is one ``authorized_keys`` line, ``restrict,command="<export> --grant
SHA256:<fp> </dev/null" ssh-ed25519 ... noc:<center>`` (box_truth.build_grant_line).
This module plans and applies grant / revoke / dedupe, each MEASURING its
preconditions before writing (security pass S10) and re-reading after (B3).
The TUI (session 3) and ``scripts/box_truth_grant.py`` are thin over it; a
center's push calls that CLI ON THE TARGET (B6), so there is exactly one
validator and one writer.

Bindings (``.claude/audits/noc_read_grant_security_pass_2026_10_14.md``):
B3 one validator, read-after-write; B4 identity = fingerprint; B5 revoke by
key blob across BOTH AuthorizedKeysFile paths, refuse-loud on a matching line
without our marker, atomic write preserving 0600, lstat-refuse a symlink,
``~/.ssh`` 0700; S5 one grant per fingerprint across apps; S6 a real login
shell; S10 the export present and executable at the embedded path.
"""
from __future__ import annotations

import json
import os
import pwd
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from utils import box_truth as bt
from utils.box_truth_collect import last_reads, witness_path
from utils.paths import atomic_write_text_preserving

AK_FILES = ("authorized_keys", "authorized_keys2")   # sshd -T default, measured
DEFAULT_EXPORT = "/opt/meshforge/scripts/" + bt.EXPORT_BASENAME
_NOT_SHELLS = ("nologin", "false")


class GrantRefused(Exception):
    """A precondition failed — the message names it and the fix."""


@dataclass
class Line:
    path: Path
    index: int
    raw: str
    entry: Optional[bt.AuthorizedKey]


@dataclass
class Plan:
    action: str
    target_file: Path
    line: Optional[str] = None
    fingerprint: Optional[str] = None
    existing_grants: int = 0
    removes: List[Line] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    sshd: Dict[str, str] = field(default_factory=dict)

    @property
    def confirm_tail(self) -> Optional[str]:
        return self.fingerprint[-8:] if self.fingerprint else None

    def render(self) -> str:
        out = [f"PLAN {self.action} on {self.target_file.parent}"]
        if self.fingerprint:
            out.append(f"  fingerprint : {self.fingerprint}")
        if self.line:
            out.append(f"  line        : {self.line}")
        for ln in self.removes:
            out.append(f"  remove      : {ln.path.name}:{ln.index + 1}  {ln.raw[:100]}")
        out.append(f"  grants now  : {self.existing_grants}")
        for k in ("acceptenv", "permituserenvironment", "authorizedkeysfile"):
            out.append(f"  sshd {k:<21}: {self.sshd.get(k, 'UNKNOWN (sshd -T not readable as this user)')}")
        out += [f"  note        : {n}" for n in self.notes]
        out += [f"  REFUSED     : {p}" for p in self.problems]
        if not self.problems and self.confirm_tail:
            out.append(f"  confirm     : type {self.confirm_tail} (the fingerprint's last 8)")
        return "\n".join(out)


def ssh_dir(home: Path) -> Path:
    return home / ".ssh"


def read_lines(home: Path) -> List[Line]:
    out: List[Line] = []
    for name in AK_FILES:
        p = ssh_dir(home) / name
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        for i, raw in enumerate(text.splitlines()):
            out.append(Line(p, i, raw, bt.parse_authorized_keys_line(raw)))
    return out


def list_grants(home: Path, env: Optional[dict] = None) -> List[dict]:
    """Every grant line, with its last read (time + source) from the witness."""
    reads = last_reads(witness_path(home, env if env is not None else dict(os.environ)))
    rows = []
    for ln in read_lines(home):
        e = ln.entry
        if e is None or not e.is_grant:
            continue
        last = reads.get(e.grant_fp) or {}
        rows.append({"fingerprint": e.grant_fp, "comment": e.comment,
                     "file": ln.path.name, "line": ln.index + 1,
                     "blob_matches": e.fingerprint == e.grant_fp,
                     "from": _from_opt(e.options),
                     "export": _export_in(e.options),
                     "last_read_ts": last.get("ts"), "last_read_src": last.get("src")})
    return rows


def _from_opt(options: str) -> Optional[str]:
    i = options.find('from="')
    return options[i + 6:options.find('"', i + 6)] if i >= 0 else None


def _export_in(options: str) -> Optional[str]:
    i = options.find('command="')
    if i < 0:
        return None
    parts = options[i + 9:].split()
    return parts[2] if len(parts) > 2 else None


# ── measured preconditions (S10) ─────────────────────────────────────────

def read_sshd_t(runner: Callable = subprocess.run) -> Dict[str, str]:
    """Selected ``sshd -T`` keys, or {} when not readable as this user —
    the plan then says UNKNOWN, never assumes."""
    try:
        proc = runner(["/usr/sbin/sshd", "-T"], capture_output=True, text=True,
                      timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    keep = {}
    for line in proc.stdout.splitlines():
        k, _, v = line.partition(" ")
        if k in ("acceptenv", "permituserenvironment", "authorizedkeysfile"):
            keep[k] = (keep[k] + " " + v) if k in keep else v
    return keep


def _login_shell() -> str:
    return pwd.getpwuid(os.getuid()).pw_shell or ""


def preflight(home: Path, export_path: str, *, shell: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """(problems, notes) measured now. Any problem refuses the write."""
    problems: List[str] = []
    notes: List[str] = []
    d = ssh_dir(home)
    try:
        st = os.lstat(d)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            problems.append(f"{d} is not a real directory")
        elif st.st_mode & 0o077:
            problems.append(f"{d} is mode {oct(st.st_mode & 0o777)} — sshd StrictModes "
                            f"ignores keys under it; fix: chmod 700 {d}")
    except FileNotFoundError:
        notes.append(f"{d} absent — will be created 0700")
    for name in AK_FILES:
        p = d / name
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode):
            problems.append(f"{p} is a symlink — refusing to write through it")
        elif st.st_mode & 0o022:
            problems.append(f"{p} is group/world-writable ({oct(st.st_mode & 0o777)}) — "
                            "sshd StrictModes would ignore it")
    sh = _login_shell() if shell is None else shell
    if not sh or not os.path.isabs(sh) or os.path.basename(sh) in _NOT_SHELLS:
        problems.append(f"login shell {sh!r} is not a real shell — sshd runs the "
                        "forced command through it, so the grant could never answer")
    if not (os.path.isfile(export_path) and os.access(export_path, os.X_OK)):
        problems.append(f"export {export_path} missing or not executable")
    if not os.access(bt.PYTHON, os.X_OK):
        problems.append(f"{bt.PYTHON} not executable")
    return problems, notes


# ── writes ───────────────────────────────────────────────────────────────

def _write(path: Path, lines: List[str]) -> None:
    d = path.parent
    if not d.exists():
        d.mkdir(mode=0o700)
    if path.is_symlink():                       # re-checked at the moment of write
        raise GrantRefused(f"{path} became a symlink — refusing")
    atomic_write_text_preserving(path, "".join(ln + "\n" for ln in lines))
    if path.stat().st_mode & 0o077:
        os.chmod(path, 0o600)


def _log(home: Path, env: Optional[dict], row: dict) -> None:
    """Grant/revoke action log beside the read witness (Q8: logged)."""
    p = witness_path(home, env if env is not None else dict(os.environ)).with_name("grants.jsonl")
    try:
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), **row}, ensure_ascii=True) + "\n")
    except OSError:
        pass   # the CLI prints the same row; the authorized_keys change stands


def plan_add(home: Path, pubkey: str, *, center: str, export_path: str = DEFAULT_EXPORT,
             from_: Optional[str] = None, shell: Optional[str] = None,
             sshd: Optional[Dict[str, str]] = None) -> Plan:
    target = ssh_dir(home) / AK_FILES[0]
    plan = Plan("add", target)
    try:
        key = bt.parse_noc_pubkey(pubkey)
        plan.fingerprint = key.fingerprint
        plan.line = bt.build_grant_line(key, export_path, from_=from_)
    except bt.KeyRejected as e:
        plan.problems.append(str(e))
        return plan
    if key.comment != f"noc:{center}":
        plan.problems.append(f"key comment {key.comment!r} does not name center "
                             f"{center!r} — expected noc:{center}")
    lines = read_lines(home)
    plan.existing_grants = sum(1 for ln in lines if ln.entry and ln.entry.is_grant)
    for ln in lines:
        e = ln.entry
        if e is None or e.fingerprint != key.fingerprint:
            continue
        where = f"{ln.path.name}:{ln.index + 1}"
        if e.is_grant:
            plan.problems.append(f"{key.fingerprint} is already granted ({where}, "
                                 f"export {_export_in(e.options)}) — one grant per "
                                 "fingerprint across apps (S5)")
        else:
            plan.problems.append(f"this key already has INTERACTIVE access ({where}); "
                                 "sshd would use that line and ignore the grant — "
                                 "remove it first if it should become read-only")
    problems, notes = preflight(home, export_path, shell=shell)
    plan.problems += problems
    plan.notes += notes
    plan.sshd = read_sshd_t() if sshd is None else sshd
    if plan.sshd.get("permituserenvironment", "no") not in ("no", ""):
        plan.notes.append("sshd permituserenvironment is ON — an environment= option "
                          "on any line would take effect")
    return plan


def apply_add(home: Path, plan: Plan, confirm: str, *, env: Optional[dict] = None) -> None:
    if plan.problems:
        raise GrantRefused("; ".join(plan.problems))
    if confirm != plan.confirm_tail:
        raise GrantRefused(f"confirmation {confirm!r} != {plan.confirm_tail} "
                           "(the fingerprint's last 8 characters)")
    existing = [ln.raw for ln in read_lines(home) if ln.path == plan.target_file]
    _write(plan.target_file, existing + [plan.line])
    # read-after-write (B3): the file must now carry exactly this grant, once.
    hits = [ln for ln in read_lines(home)
            if ln.entry and ln.entry.grant_fp == plan.fingerprint]
    if len(hits) != 1 or hits[0].raw != plan.line or hits[0].entry.fingerprint != plan.fingerprint:
        raise GrantRefused(f"read-after-write failed: {len(hits)} matching line(s) — "
                           f"inspect {plan.target_file}")
    _log(home, env, {"action": "add", "fp": plan.fingerprint,
                     "comment": hits[0].entry.comment})


def plan_revoke(home: Path, fingerprint: str, *, force: bool = False) -> Plan:
    plan = Plan("revoke", ssh_dir(home) / AK_FILES[0], fingerprint=fingerprint)
    lines = read_lines(home)
    plan.existing_grants = sum(1 for ln in lines if ln.entry and ln.entry.is_grant)
    for ln in lines:
        if ln.entry is None or ln.entry.fingerprint != fingerprint:
            continue
        if not ln.entry.is_grant and not force:
            plan.problems.append(
                f"{ln.path.name}:{ln.index + 1} carries this key WITHOUT a grant "
                "marker — an interactive grant; the operator decides (--force)")
        plan.removes.append(ln)
    for name in AK_FILES:
        p = ssh_dir(home) / name
        if p.is_symlink():
            plan.problems.append(f"{p} is a symlink — refusing to write through it")
    if not plan.removes:
        plan.problems.append(f"no line carries {fingerprint} in "
                             f"{' or '.join(AK_FILES)}")
    return plan


def apply_revoke(home: Path, plan: Plan, confirm: str, *, env: Optional[dict] = None) -> int:
    if plan.problems:
        raise GrantRefused("; ".join(plan.problems))
    if confirm != plan.confirm_tail:
        raise GrantRefused(f"confirmation {confirm!r} != {plan.confirm_tail}")
    removed = 0
    for name in AK_FILES:
        p = ssh_dir(home) / name
        file_lines = [ln for ln in read_lines(home) if ln.path == p]
        keep = [ln.raw for ln in file_lines
                if not (ln.entry and ln.entry.fingerprint == plan.fingerprint)]
        if len(keep) != len(file_lines):
            removed += len(file_lines) - len(keep)
            _write(p, keep)
    left = [ln for ln in read_lines(home)
            if ln.entry and ln.entry.fingerprint == plan.fingerprint]
    if left:
        raise GrantRefused(f"read-after-write failed: {len(left)} line(s) still carry "
                           f"{plan.fingerprint}")
    _log(home, env, {"action": "revoke", "fp": plan.fingerprint, "removed": removed})
    return removed


def plan_dedupe(home: Path) -> Plan:
    """Exact duplicate lines (same options, same key) — later copies removed.
    Never touches a line that differs in any byte (S12: moc3's duplicate)."""
    plan = Plan("dedupe", ssh_dir(home) / AK_FILES[0])
    seen = set()
    for ln in read_lines(home):
        if ln.entry is None:
            continue
        k = ln.raw.strip()
        if k in seen:
            plan.removes.append(ln)
        seen.add(k)
    return plan


def apply_dedupe(home: Path, plan: Plan, *, env: Optional[dict] = None) -> int:
    fresh = plan_dedupe(home)        # indices from the plan may have shifted
    if [ln.raw for ln in fresh.removes] != [ln.raw for ln in plan.removes]:
        raise GrantRefused("authorized_keys changed since the plan — re-run")
    drop = {(ln.path, ln.index) for ln in fresh.removes}
    for name in AK_FILES:
        p = ssh_dir(home) / name
        file_lines = [ln for ln in read_lines(home) if ln.path == p]
        keep = [ln.raw for ln in file_lines if (ln.path, ln.index) not in drop]
        if len(keep) != len(file_lines):
            _write(p, keep)
    _log(home, env, {"action": "dedupe", "removed": len(drop)})
    return len(drop)

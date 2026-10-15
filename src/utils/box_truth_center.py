"""The CENTER side of the NOC read grant — key, targets, push, rotate.

A center reads other boxes' box_truth/v1 documents with ONE dedicated key
(``~/.ssh/meshforge_noc_ed25519``, Q5/B7a). Getting that key granted on a box
is either target-side (the box's own ``box_truth_grant.py add``) or this
module's ``push`` over the operator's EXISTING access — which never edits a
remote ``authorized_keys`` itself: it runs the target's grant CLI (B6), only
after the target proved it is the box the registry names.

The targets file (``~/.config/meshforge/truth_spool_targets``) is shared with
the spool writer and is written HERE (Q11: the grant registers the target
pinned; the revoke leaves it UNFLAGGED — amended 2026-10-14 — so /fleet reads
it DARK with the re-grant named on the next tick instead of serving its last
healthy snapshot until SPOOL_STALE_S). A line is ``<alias> [noc]  # comment``;
``noc`` means "read with the pinned grant key" (S3). A line without it is not
read: its spool row is ``refused_grant`` (the interactive-key legacy read was
deleted 2026-10-14). Delete the line by hand to stop watching a box at all.

Every function takes its ssh/subprocess seams as arguments so the tests can
drive each step and each refusal without a network.
"""
from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from utils import box_truth as bt
from utils import box_truth_app as app
from utils import box_truth_reader as br
from utils.paths import atomic_write_text_preserving, get_real_user_home

#: The TARGET's grant CLI, found under the first ``app.GRANT_CLI_SEARCH``
#: dir that has it — the target may run either app (or both).
GRANT_CLI_BASENAME = "box_truth_grant.py"
SPOOL_SCRIPT = app.INSTALL_DIR + "/scripts/fleet_truth_spool.py"
SPOOL_CRON_LINE = (
    f"*/2 * * * * /usr/bin/python3 {SPOOL_SCRIPT} "
    f'>"$HOME/.local/state/{app.CONFIG_DIRNAME}/cron_out/truth_spool.out" 2>&1; '
    f"{app.INSTALL_DIR}/scripts/cron_verdict.sh truth_spool $?")
PIN_FLAG = "noc"

#: The truth spool (writer: scripts/fleet_truth_spool.py; reader: each app's
#: fleet_truth_collector). ONE constant for both halves (hfm #5) — it lived
#: in MeshForge's collector until session 3 step 3 (2026-10-15).
SPOOL_SCHEMA = "truth_spool/v1"
SPOOL_STALE_S = 360.0  # 3x the */2min cron cadence


def truth_spool_dir() -> Path:
    """``$XDG_STATE_HOME/<app>/truth_spool`` (operator-owned)."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else get_real_user_home() / ".local" / "state"
    return base / app.CONFIG_DIRNAME / "truth_spool"


def read_spool(alias: str, *, now: Optional[float] = None,
               spool_dir: Optional[Path] = None) -> Optional[dict]:
    """Fresh spool doc for ``alias``, or None (absent / unreadable / stale /
    wrong schema / stamped in the future). Never raises. The ONE reader both
    apps' collectors use, so freshness means the same thing everywhere."""
    import json
    import time
    try:
        path = (spool_dir or truth_spool_dir()) / f"{alias}.json"
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict) or doc.get("schema") != SPOOL_SCHEMA:
            return None
        fetched_at = doc.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return None
        age = (now if now is not None else time.time()) - fetched_at
        # a stamp AHEAD of this reader's clock is a clock fault, never
        # evidence of freshness (honest_failure_modes #6, RTC-less fleet)
        if age < 0 or age > SPOOL_STALE_S:
            return None  # stale = unobservable, never last-known-healthy
        return doc
    except (OSError, ValueError):
        return None
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ── targets file (shared with scripts/fleet_truth_spool.py) ─────────────

@dataclass(frozen=True)
class Target:
    alias: str
    pinned: bool


def targets_path(home: Optional[Path] = None) -> Path:
    return (home or get_real_user_home()) / ".config" / app.CONFIG_DIRNAME / "truth_spool_targets"


def read_targets(path: Path) -> List[Target]:
    """One alias per line, optional ``noc`` flag, '#' comments. Absent = [].
    Invalid lines are skipped LOUDLY on stderr (they ride into ssh argv and
    a spool filename)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    out: List[Target] = []
    for line in text.splitlines():
        body = line.split("#", 1)[0].split()
        if not body:
            continue
        alias, flags = body[0], body[1:]
        if not _ALIAS_RE.match(alias) or any(f != PIN_FLAG for f in flags):
            print(f"truth_spool: SKIP invalid target line {line.strip()!r} "
                  f"(want '<alias> [{PIN_FLAG}]')", file=sys.stderr)
            continue
        out.append(Target(alias, PIN_FLAG in flags))
    return out


def set_target(path: Path, alias: str, *, pinned: Optional[bool]) -> None:
    """Add/flag (``pinned`` bool) or remove (``pinned=None``) one alias,
    preserving every other line and its comment."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    out, found = [], False
    for line in lines:
        body, sep, comment = line.partition("#")
        toks = body.split()
        if toks and toks[0] == alias:
            found = True
            if pinned is None:
                # Keep the operator's note visible, commented (review B 7).
                out.append(f"# {alias} — unregistered by box_truth_grant unpush"
                           + (f"; was: {comment.strip()}" if sep else ""))
                continue
            new = alias + (f" {PIN_FLAG}" if pinned else "")
            out.append(new + (f"   #{comment}" if sep else ""))
        else:
            out.append(line)
    if not found and pinned is not None:
        out.append(alias + (f" {PIN_FLAG}" if pinned else ""))
    atomic_write_text_preserving(path, "".join(ln + "\n" for ln in out))


# ── the center key (B7a) ─────────────────────────────────────────────────

def center_name() -> str:
    name = socket.gethostname().split(".")[0]
    if not re.match(r"^[A-Za-z0-9._-]{1,64}$", name):
        raise bt.KeyRejected(f"hostname {name!r} cannot form a noc: comment")
    return name


def load_center_key(home: Path) -> bt.NocKey:
    pub = Path(str(br.noc_key_path(home)) + ".pub")
    return bt.parse_noc_pubkey(pub.read_text(encoding="utf-8"))


def create_center_key(home: Path, *, runner: Callable = subprocess.run,
                      path: Optional[Path] = None) -> bt.NocKey:
    """ssh-keygen a new unencrypted ed25519 key (cron reads it), 0600."""
    path = path or br.noc_key_path(home)
    if path.exists():
        raise bt.KeyRejected(f"{path} exists — refusing to overwrite (use rotate)")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    runner(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
            f"noc:{center_name()}", "-f", str(path)],
           check=True, timeout=30, stdin=subprocess.DEVNULL)
    os.chmod(path, 0o600)
    if os.geteuid() == 0:
        # Under sudo a root-owned 0600 key is unreadable to the operator's
        # cron, and every box would read "grant refused" (review B 2).
        st = os.stat(home)
        for p in (path, Path(str(path) + ".pub")):
            os.chown(p, st.st_uid, st.st_gid)
    return bt.parse_noc_pubkey(Path(str(path) + ".pub").read_text(encoding="utf-8"))


# ── spool cron (Q12: installed by the tooling, never by hand) ───────────

def ensure_spool_cron(*, runner: Callable = subprocess.run) -> str:
    """Idempotent. Returns 'present' / 'installed'; raises on failure."""
    cur = runner(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    text = cur.stdout if cur.returncode == 0 else ""
    if any("fleet_truth_spool.py" in ln and not ln.lstrip().startswith("#")
           for ln in text.splitlines()):
        return "present"            # a commented-out line is NOT present (B 6)
    new = text + ("" if not text or text.endswith("\n") else "\n") + SPOOL_CRON_LINE + "\n"
    runner(["crontab", "-"], input=new, text=True, timeout=10, check=True)
    back = runner(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    if SPOOL_CRON_LINE not in back.stdout:
        raise RuntimeError("spool cron line not present after install (read-back failed)")
    return "installed"


# ── push / unpush / rotate / bulk-revoke ────────────────────────────────

@dataclass
class Report:
    ok: bool = True
    steps: List[str] = field(default_factory=list)

    def step(self, text: str, ok: bool = True) -> bool:
        self.steps.append(("  ok   " if ok else "  FAIL ") + text)
        if not ok:
            self.ok = False
        return ok


def _remote_grant_cli(args: str) -> str:
    """Run the TARGET's grant CLI with ``args`` (already quoted): the first
    ``app.GRANT_CLI_SEARCH`` dir that carries it, else exit 127 naming the
    dirs tried. The dirs are module constants; nothing caller-supplied is
    interpolated outside ``args``.

    Wrapped as ``sh -c '<loop>'`` because the string is parsed by the
    target account's LOGIN shell: a bare POSIX ``for…do…done`` parses in
    bash/dash but not in fish or csh (non-author pass on 3f4c9996, item 1).
    Every login shell sees one simple command with one quoted argument."""
    dirs = " ".join(shlex.quote(d) for d in app.GRANT_CLI_SEARCH)
    cli = f'"$d/scripts/{GRANT_CLI_BASENAME}"'
    loop = (f"for d in {dirs}; do if [ -f {cli} ]; then "
            f"exec {bt.PYTHON} -I {cli} {args}; fi; done; "
            f"echo \"{GRANT_CLI_BASENAME} not installed under {dirs}\" >&2; exit 127")
    return f"sh -c {shlex.quote(loop)}"


def _add_cmd(key: bt.NocKey, center: str) -> str:
    return _remote_grant_cli(f"add --pubkey {shlex.quote(key.line())} "
                             f"--center {shlex.quote(center)} --confirm {key.fingerprint[-8:]}")


_FP_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")


def _revoke_cmd(fp: str) -> str:
    if not _FP_RE.match(fp or ""):          # A N3: never interpolate an unchecked fp
        raise bt.KeyRejected(f"fingerprint {fp!r} malformed")
    return _remote_grant_cli(f"revoke --fp {shlex.quote(fp)} "
                             f"--confirm {shlex.quote(fp[-8:])}")


Exec = Callable[[str, Optional[str], str], br.ExecResult]
Read = Callable[..., br.ReadResult]


def _grant_on(alias: str, expect: Optional[str], key: bt.NocKey, center: str,
              key_path: Path, rep: Report, exec_: Exec, read: Read) -> bool:
    r = exec_(alias, expect, _add_cmd(key, center))
    if not rep.step(f"{alias}: identity {expect} + grant {key.fingerprint}",
                    r.outcome == "ok"):
        rep.steps.append(f"         {r.outcome} rc={r.rc}: {r.stderr_head or r.stdout[-300:]}")
        return False
    rr = read(alias, expect, key_path=key_path)
    if not rep.step(f"{alias}: pinned read with the granted key -> {rr.outcome}",
                    rr.outcome == "ok"):
        rep.steps.append(f"         {br.describe(rr)}")
        return False
    return True


def push(alias: str, confirm: str, *, home: Path, expect: Optional[str],
         exec_: Exec = br.exec_verified, read: Read = br.read_box,
         cron: Callable[[], str] = ensure_spool_cron) -> Report:
    """Grant this center's key on ONE target (Q18: target name typed), verify
    a pinned read, register the target pinned, ensure the spool cron."""
    rep = Report()
    if confirm != alias:
        rep.step(f"typed confirmation {confirm!r} != target {alias!r}", False)
        return rep
    if not expect:
        rep.step(f"{alias}: no pinned host key — {app.HOSTKEY_FIX}; "
                 "refusing (B6)", False)
        return rep
    try:
        key = load_center_key(home)
    except (OSError, bt.KeyRejected) as e:
        rep.step(f"center key unusable ({e}) — box_truth_grant.py center-key --create", False)
        return rep
    if not _grant_on(alias, expect, key, key.comment[4:], br.noc_key_path(home),
                     rep, exec_, read):
        return rep
    set_target(targets_path(home), alias, pinned=True)
    rep.step(f"{alias}: registered '{alias} {PIN_FLAG}' in {targets_path(home)}")
    try:
        rep.step(f"spool cron {cron()}")
    except Exception as e:  # the grant stands; the missing writer is the finding
        rep.step(f"spool cron: {e}", False)
    return rep


def unpush(alias: str, confirm: str, *, home: Path, expect: Optional[str],
           exec_: Exec = br.exec_verified) -> Report:
    rep = Report()
    if confirm != alias:
        rep.step(f"typed confirmation {confirm!r} != target {alias!r}", False)
        return rep
    try:
        key = load_center_key(home)
    except (OSError, bt.KeyRejected) as e:
        rep.step(f"center key unusable ({e})", False)
        return rep
    r = exec_(alias, expect, _revoke_cmd(key.fingerprint))
    if not rep.step(f"{alias}: revoke {key.fingerprint}", r.outcome == "ok"):
        rep.steps.append(f"         {r.outcome} rc={r.rc}: {r.stderr_head or r.stdout[-300:]}")
        return rep
    set_target(targets_path(home), alias, pinned=False)
    rep.step(f"{alias}: left unflagged in {targets_path(home)} — /fleet reads it "
             "DARK (refused_grant) next tick; delete the line to stop watching it")
    return rep


def pinned_targets(home: Path) -> List[str]:
    return [t.alias for t in read_targets(targets_path(home)) if t.pinned]


def bulk_revoke(fp: str, confirm: str, *, home: Path,
                expect_of: Callable[[str], Optional[str]],
                exec_: Exec = br.exec_verified) -> Report:
    """Revoke one fingerprint on every pinned target (a leaked key's cure)."""
    rep = Report()
    if not _FP_RE.match(fp or ""):
        rep.step(f"fingerprint {fp!r} malformed — want SHA256:<43 chars>", False)
        return rep
    if confirm != fp[-8:]:
        rep.step(f"confirmation {confirm!r} != {fp[-8:]}", False)
        return rep
    for alias in pinned_targets(home):
        r = exec_(alias, expect_of(alias), _revoke_cmd(fp))
        rep.step(f"{alias}: revoke {fp} -> {r.outcome} {r.stderr_head[:120]}",
                 r.outcome == "ok")
    return rep


def rotate(confirm: str, *, home: Path, expect_of: Callable[[str], Optional[str]],
           exec_: Exec = br.exec_verified, read: Read = br.read_box,
           keygen: Callable[..., bt.NocKey] = create_center_key) -> Report:
    """B7c: new key -> grant it everywhere -> verify a read with it ->
    revoke the old -> swap the key files. Stops at the first failure with
    the old key still granted and still in place (nothing goes dark)."""
    rep = Report()
    if confirm != "rotate":
        rep.step("type --confirm rotate", False)
        return rep
    key_path = br.noc_key_path(home)
    new_path = key_path.with_name(key_path.name + ".new")
    try:
        old = load_center_key(home)
    except (OSError, bt.KeyRejected) as e:
        rep.step(f"current center key unusable ({e})", False)
        return rep
    targets = pinned_targets(home)
    if not targets:
        rep.step("no pinned targets — nothing to rotate onto", False)
        return rep
    for p in (new_path, Path(str(new_path) + ".pub")):
        if p.exists():
            rep.step(f"{p} exists from an interrupted rotate — inspect, remove, re-run", False)
            return rep
    new = keygen(home, path=new_path)
    rep.step(f"new key {new.fingerprint} at {new_path}")
    for alias in targets:
        if not _grant_on(alias, expect_of(alias), new, new.comment[4:], new_path,
                         rep, exec_, read):
            rep.steps.append(f"  STOP: old key {old.fingerprint} still granted and in place; "
                             f"new grants so far: revoke with bulk-revoke --fp {new.fingerprint}")
            return rep
    still_old = []
    for alias in targets:
        r = exec_(alias, expect_of(alias), _revoke_cmd(old.fingerprint))
        if not rep.step(f"{alias}: revoke old {old.fingerprint} -> {r.outcome}",
                        r.outcome == "ok"):
            still_old.append(alias)
    # Swap anyway: the new key reads every target (verified above), and the
    # old one may be the leaked one — but SAY where it still opens a box (A N4).
    os.replace(new_path, key_path)
    os.replace(str(new_path) + ".pub", str(key_path) + ".pub")
    rep.step(f"key files swapped: {key_path} is now {new.fingerprint}")
    for alias in still_old:
        rep.steps.append(f"  ⚠ OLD KEY STILL GRANTED on {alias} — retry: "
                         f"box_truth_grant.py bulk-revoke --fp {old.fingerprint} "
                         f"--confirm {old.fingerprint[-8:]}")
    rep.steps.append(f"  note: only targets in {targets_path(home)} were rotated; boxes "
                     "granted from their own side keep the old key — run "
                     "`box_truth_grant.py list` there")
    return rep

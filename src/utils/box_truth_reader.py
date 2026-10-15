"""box_truth/v1 reader — how a center pulls one box's document over ssh.

The ONE ssh read path for the NOC read grant (spool writer, grant push
verification, rotation). Security-pass bindings
(``.claude/audits/noc_read_grant_security_pass_2026_10_14.md``):

* S1  every read ends in exactly one ``box_truth.TRANSPORT_OUTCOMES`` member,
      with ``rc`` and the head of stderr, so the NOC can say WHICH failure —
      never fold "grant refused" into "no response".
* S2  host identity is checked against the registry's ``expect_hostkey`` on
      EVERY read: ssh connects with a fresh empty known_hosts (accept-new,
      pinned to ed25519, ``HostKeyAlias=<alias>``) and the key it recorded is
      compared to the registry AFTER the read. A mismatch discards the data —
      a box answering with the wrong identity is never rendered as the box it
      was addressed as (the 09-11 reassigned-front class). This works through
      ProxyJump/ProxyCommand stanzas, where ssh-keyscan from here cannot.
      No ``expect_hostkey`` at all → ``identity_unpinned``, no connection.
* S3  a grant read is PINNED: ``-i <noc key> -o IdentitiesOnly=yes
      -o IdentityAgent=none`` — otherwise an agent holding an interactive key
      satisfies the target after a revoke, and the control cannot fail.
* B5c ``ControlMaster=no -o ControlPath=none``: a persisted master (kiai's
      stanza: ControlPersist 10m) would keep answering after a revoke.
* S8  stdout is read through a pipe with the HTTP leg's byte cap and a
      deadline; overflow kills the read → ``export_broken``.
"""
from __future__ import annotations

import os
import re
import selectors
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from utils import box_truth as bt
from utils import box_truth_app as app

MAX_BODY_BYTES = 8 * 1024 * 1024        # same cap as the HTTP leg (collector)
MAX_STDERR_BYTES = 64 * 1024
READ_DEADLINE_S = 30.0                  # whole round trip (tunnel + export)
CONNECT_TIMEOUT_S = 8
STDERR_HEAD = 200

NOC_KEY_BASENAME = app.NOC_KEY_BASENAME
REMOTE_EXPORT = app.INSTALL_DIR + "/scripts/" + bt.EXPORT_BASENAME
#: What the reader asks to run. Under a grant the forced command replaces it
#: (and records it as data); over an interactive key it runs the same export.
REMOTE_COMMAND = f"{bt.PYTHON} -I {REMOTE_EXPORT}"

_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def noc_key_path(home: Path) -> Path:
    """``~/.ssh/<app>_noc_ed25519`` (Q5, B7a) — never under ~/.claude/ssh."""
    return home / ".ssh" / NOC_KEY_BASENAME


@dataclass
class ReadResult:
    alias: str
    outcome: str
    rc: Optional[int] = None
    stderr_head: str = ""
    doc: Optional[dict] = None
    hostkey_fp: Optional[str] = None
    ms: int = 0
    pinned: bool = False

    def transport(self) -> dict:
        return {"outcome": self.outcome, "rc": self.rc,
                "stderr_head": self.stderr_head, "pinned": self.pinned,
                "hostkey_fp": self.hostkey_fp}


def ssh_argv(alias: str, known_hosts: Path, *,
             key_path: Optional[Path] = None,
             command: str = REMOTE_COMMAND,
             strict: bool = False, learn: bool = False) -> list:
    if not _ALIAS_RE.match(alias or ""):
        raise ValueError(f"invalid ssh alias {alias!r}")
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR",
            "-o", f"ConnectTimeout={CONNECT_TIMEOUT_S}",
            "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "StrictHostKeyChecking=" + ("yes" if strict else "accept-new"),
            "-o", f"HostKeyAlias={alias}",
            "-o", "HostKeyAlgorithms=ssh-ed25519",
            "-o", "UpdateHostKeys=no", "-o", "HashKnownHosts=no",
            "-o", "ControlMaster=no", "-o", "ControlPath=none",
            # never hand anything to a box: no agent, no X11, no forwards, no
            # local command — whatever a user ~/.ssh/config stanza says (A N1)
            "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
            "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no"]
    if learn:
        # Identity-learning connection to a box NOT YET VERIFIED: offer no
        # credential at all. ssh records the host key before user auth, so
        # the key is learned and the connection then fails auth (rc 255) —
        # verified against a real sshd by review A (N1).
        argv += ["-o", "PubkeyAuthentication=no", "-o", "PasswordAuthentication=no",
                 "-o", "KbdInteractiveAuthentication=no", "-o", "GSSAPIAuthentication=no",
                 "-o", "HostbasedAuthentication=no", "-o", "IdentityAgent=none"]
    if key_path is not None:
        argv += ["-i", str(key_path), "-o", "IdentitiesOnly=yes",
                 "-o", "IdentityAgent=none"]
    return argv + [alias, command]


def learned_hostkey_fp(known_hosts: Path, alias: str) -> Optional[str]:
    """The fingerprint of the key ssh recorded for ``alias``, or None.

    The file is a fresh per-read temp file, so its only entries are this
    read's. The host field is matched as the alias OR a hashed entry
    (``|1|salt|hash``): Debian ships ``HashKnownHosts yes``, and the first
    live drill (2026-10-14) read identity_mismatch on every box because this
    parser matched the plain alias only — argv also passes
    ``HashKnownHosts=no``, this is the belt to that brace."""
    try:
        text = known_hosts.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and (parts[0] == alias or parts[0].startswith("|1|")):
            entry = bt.parse_authorized_keys_line(" ".join(parts[1:]))
            if entry is not None:
                return entry.fingerprint
    return None


def _bounded_run(argv: list, deadline_s: float):
    """(rc, stdout, stderr, timed_out, overflow) — never more than the caps
    held in memory, never longer than the deadline."""
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = bytearray(), bytearray()
    timed_out = overflow = False
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, out)
    sel.register(proc.stderr, selectors.EVENT_READ, err)
    end = time.monotonic() + deadline_s
    try:
        while sel.get_map():
            left = end - time.monotonic()
            if left <= 0:
                timed_out = True
                break
            for key, _ev in sel.select(timeout=left):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                buf = key.data
                cap = MAX_BODY_BYTES if buf is out else MAX_STDERR_BYTES
                if len(buf) + len(chunk) > cap:
                    if buf is out:
                        overflow = True
                        break
                    chunk = chunk[:max(0, cap - len(buf))]
                buf.extend(chunk)
            if overflow:
                break
    finally:
        sel.close()
        if timed_out or overflow:
            proc.kill()
        try:
            rc = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = None
        proc.stdout.close()
        proc.stderr.close()
    return rc, bytes(out), bytes(err), timed_out, overflow


Runner = Callable[[list, float], tuple]


def read_box(alias: str, expect_hostkey: Optional[str], *,
             key_path: Optional[Path] = None,
             runner: Runner = _bounded_run,
             deadline_s: float = READ_DEADLINE_S,
             command: str = REMOTE_COMMAND) -> ReadResult:
    """One read of ``alias``. ``key_path`` set = a pinned grant read (S3)."""
    t0 = time.monotonic()
    res = ReadResult(alias=alias, outcome="unreachable", pinned=key_path is not None)
    if not expect_hostkey:
        res.outcome = "identity_unpinned"
        res.stderr_head = (f"no pinned host key for {alias} — {app.HOSTKEY_FIX} "
                           "before reading it")
        return res
    with tempfile.TemporaryDirectory(prefix="box_truth_kh.") as td:
        kh = Path(td) / "known_hosts"
        try:
            rc, out, err, timed_out, overflow = runner(
                ssh_argv(alias, kh, key_path=key_path, command=command), deadline_s)
        except (OSError, ValueError) as e:
            res.stderr_head = f"{type(e).__name__}: {e}"[:STDERR_HEAD]
            res.ms = int((time.monotonic() - t0) * 1000)
            return res
        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")
        res.rc = rc
        res.stderr_head = " ".join(stderr.split())[:STDERR_HEAD]
        res.hostkey_fp = learned_hostkey_fp(kh, alias)
    res.outcome = bt.classify_transport(rc, stdout, stderr,
                                        timed_out=timed_out, overflow=overflow)
    # Judge identity before any byte of the answer is believed. A key that
    # was observed and is wrong outranks every other outcome — a wrong box
    # refusing us is a wrong box, not a revoked grant.
    if res.hostkey_fp is not None and res.hostkey_fp != expect_hostkey:
        res.outcome = "identity_mismatch"
        res.stderr_head = (f"{alias} presented {res.hostkey_fp}, registry "
                           f"expects {expect_hostkey}")
    elif res.hostkey_fp is None and res.outcome in ("ok", "rate_limited", "export_broken"):
        res.outcome = "identity_mismatch"
        res.stderr_head = "host key not observed on this read — cannot prove identity"
    if res.outcome == "ok":
        res.doc = bt.parse_framed(stdout)
    elif res.outcome in ("rate_limited", "export_broken") and rc == 0:
        # The export refused and SAID WHY — carry its reason, not ssh's stderr.
        try:
            why = bt.parse_framed(stdout).get("reason")
        except ValueError:
            why = None
        if isinstance(why, str) and why:
            res.stderr_head = " ".join(why.split())[:STDERR_HEAD]
    res.ms = int((time.monotonic() - t0) * 1000)
    return res


@dataclass
class ExecResult:
    alias: str
    outcome: str            # "ok" | identity_* | "unreachable" | "failed"
    rc: Optional[int] = None
    stdout: str = ""
    stderr_head: str = ""


def exec_verified(alias: str, expect_hostkey: Optional[str], command: str, *,
                  runner: Runner = _bounded_run,
                  deadline_s: float = READ_DEADLINE_S) -> ExecResult:
    """Run ``command`` on ``alias`` over the operator's existing access, but
    ONLY on the box the registry says it is (B6: a push to a reassigned front
    must not grant the wrong box).

    Two connections, no gap between check and act: the first (``true``)
    learns the host key into a fresh known_hosts and is compared with the
    registry; the second runs the command with ``StrictHostKeyChecking=yes``
    against exactly that verified key, so a different box cannot answer it.
    """
    res = ExecResult(alias=alias, outcome="unreachable")
    if not expect_hostkey:
        res.outcome = "identity_unpinned"
        res.stderr_head = f"no expect_hostkey in the registry for {alias} — refusing"
        return res
    with tempfile.TemporaryDirectory(prefix="box_truth_kh.") as td:
        kh = Path(td) / "known_hosts"
        try:
            rc, _o, err, timed_out, _ov = runner(
                ssh_argv(alias, kh, command="true", learn=True), deadline_s)
        except (OSError, ValueError) as e:
            res.stderr_head = f"{type(e).__name__}: {e}"[:STDERR_HEAD]
            return res
        fp = learned_hostkey_fp(kh, alias)
        if fp is None:
            res.rc = rc
            res.stderr_head = " ".join(err.decode("utf-8", "replace").split())[:STDERR_HEAD] \
                or ("timeout" if timed_out else "host key not observed")
            return res
        if fp != expect_hostkey:
            res.outcome = "identity_mismatch"
            res.stderr_head = f"{alias} presented {fp}, registry expects {expect_hostkey}"
            return res
        rc, out, err, timed_out, overflow = runner(
            ssh_argv(alias, kh, command=command, strict=True), deadline_s)
    res.rc = rc
    res.stdout = out.decode("utf-8", "replace")
    res.stderr_head = " ".join(err.decode("utf-8", "replace").split())[:STDERR_HEAD]
    if rc == 0 and not (timed_out or overflow):
        res.outcome = "ok"
    elif rc == 255 and "Host key verification failed" in res.stderr_head:
        res.outcome = "identity_mismatch"
    else:
        res.outcome = "failed"
    return res


def describe(res: ReadResult) -> str:
    """The cell text for a failed read — names the fix (Q11, S1)."""
    a = res.alias
    if res.outcome == "refused_grant" and res.stderr_head.startswith("center key"):
        return res.stderr_head            # the CENTER's fault, not the grant's (B 2)
    if res.outcome == "refused_grant":
        return (f"grant refused on {a} (key or source) — on {a}: "
                "box_truth_grant.py list; from this center: "
                f"box_truth_grant.py push {a} --confirm {a}")
    if res.outcome == "identity_mismatch":
        return f"identity mismatch on {a}: {res.stderr_head} — data discarded"
    if res.outcome == "identity_unpinned":
        return res.stderr_head
    if res.outcome == "export_broken":
        if res.rc == 0 and res.stderr_head:      # the export answered and refused
            return f"export on {a} refused: {res.stderr_head}"
        return (f"export broken on {a} (rc={res.rc}): {res.stderr_head or 'no frame'} "
                f"— is {REMOTE_EXPORT} deployed there?")
    if res.outcome == "rate_limited":
        return f"{a} refused this read for now: {res.stderr_head or 'rate limit'}"
    if res.outcome == "unreachable":
        return f"no response from {a} (ssh): {res.stderr_head or 'timeout'}"
    return f"{a}: {res.outcome}"

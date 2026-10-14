#!/usr/bin/env python3
"""box_truth_grant_drill — prove the NOC read grant against a REAL sshd.

Unit tests pin the model of ssh; this pins ssh. It starts a private,
unprivileged sshd on 127.0.0.1 (scratch host key, scratch authorized_keys,
``SetEnv XDG_STATE_HOME`` so the read witness stays in scratch), writes the
grant with the REAL grant module, and drives the REAL forced command with
the client shapes the security pass measured
(``.claude/audits/noc_read_grant_security_pass_2026_10_14.md``):

  A5  exec ``bash`` / ``-t`` / ``-tt`` / ``-s sftp`` / a stdin payload / ``-A`` /
      ``LC_ALL=C`` → the same box_truth JSON, nothing executed (canary)
  A5  ``-L`` forward → the target listener never sees a connection
  B8  the 7th read in a window → rate_limited
  A3  revoke → the pinned reader reads ``refused_grant``
  A9  a wrong registry fingerprint → ``identity_mismatch``, data discarded
  B4  the witness records fingerprint, source and the client's command as data

It touches NOTHING outside its own temp dir: not ~/.ssh, not the system sshd.
Exit 0 = every leg passed; 1 = a leg failed (the table says which).

    python3 scripts/box_truth_grant_drill.py
"""
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

_ROOT = Path(os.path.realpath(__file__)).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from utils import box_truth as bt  # noqa: E402
from utils import box_truth_collect as bc  # noqa: E402
from utils import box_truth_grants as bg  # noqa: E402
from utils import box_truth_reader as br  # noqa: E402

EXPORT = str(_ROOT / "scripts" / bt.EXPORT_BASENAME)
RESULTS = []


def leg(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def keygen(path, comment):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment,
                    "-f", str(path)], check=True, timeout=30, stdin=subprocess.DEVNULL)
    return bt.parse_noc_pubkey(Path(str(path) + ".pub").read_text()) \
        if comment.startswith("noc:") else None


def main():
    if not os.access("/usr/sbin/sshd", os.X_OK):
        print("UNKNOWN: no /usr/sbin/sshd here — cannot drill")
        return 2
    with tempfile.TemporaryDirectory(prefix="box_truth_drill.") as td:
        T = Path(td)
        home = T / "home"
        (home / ".ssh").mkdir(parents=True, mode=0o700)
        state = T / "state"
        env = {"XDG_STATE_HOME": str(state)}
        keygen(T / "hostkey", "drill-host")
        host_fp = bt.parse_authorized_keys_line(
            Path(str(T / "hostkey") + ".pub").read_text()).fingerprint
        key_a = keygen(T / "center_a", "noc:drill")
        key_b = keygen(T / "center_b", "noc:drill")
        port = free_port()
        (T / "sshd_config").write_text("\n".join([
            f"Port {port}", "ListenAddress 127.0.0.1", f"HostKey {T / 'hostkey'}",
            f"AuthorizedKeysFile {home / '.ssh' / 'authorized_keys'}",
            f"PidFile {T / 'sshd.pid'}", "UsePAM no", "StrictModes no",
            "PasswordAuthentication no", "KbdInteractiveAuthentication no",
            "AcceptEnv LANG LC_*", f"SetEnv XDG_STATE_HOME={state}",
            "Subsystem sftp /usr/lib/openssh/sftp-server", ""]))

        for k in (key_a, key_b):
            plan = bg.plan_add(home, k.line(), center="drill", export_path=EXPORT, sshd={})
            if plan.problems:
                print("REFUSED grant plan:", plan.problems)
                return 1
            bg.apply_add(home, plan, plan.confirm_tail, env=env)

        sshd = subprocess.Popen(["/usr/sbin/sshd", "-D", "-f", str(T / "sshd_config"),
                                 "-E", str(T / "sshd.log")])
        try:
            for _ in range(50):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            run_legs(T, port, host_fp, key_a, key_b, home, env)
        finally:
            sshd.terminate()
            try:
                sshd.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sshd.kill()

    width = max(len(n) for n, _o, _d in RESULTS)
    for name, ok, detail in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = [n for n, ok, _d in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} legs passed")
    return 1 if failed else 0


def run_legs(T, port, host_fp, key_a, key_b, home, env):
    kh = T / "known_hosts"

    def ssh(key, *args, stdin=b"", extra_env=None):
        argv = ["ssh", "-p", str(port), "-i", str(T / key), "-o", "IdentitiesOnly=yes",
                "-o", "IdentityAgent=none", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR",
                "-o", f"UserKnownHostsFile={kh}", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ControlMaster=no", "-o", "ControlPath=none", *args]
        e = dict(os.environ, **(extra_env or {}))
        return subprocess.run(argv, input=stdin, capture_output=True, timeout=60, env=e)

    def is_doc(proc, key):
        try:
            d = bt.parse_framed(proc.stdout.decode("ascii"))
        except (ValueError, UnicodeDecodeError) as e:
            return False, f"rc={proc.returncode} {type(e).__name__}: {proc.stderr[:120]!r}"
        return d.get("grant") == key.fingerprint and d.get("sections") is not None, \
            f"grant …{(d.get('grant') or '')[-8:]}, {sum(v is not None for v in (d.get('sections') or {}).values())} sections"

    def reader(argv, deadline):
        argv = [argv[0], "-p", str(port)] + argv[1:]
        return br._bounded_run(argv, deadline)

    canary = T / "PWNED"
    # 1. the pinned reader, end to end (served read 1 for key A)
    r = br.read_box("127.0.0.1", host_fp, key_path=T / "center_a", runner=reader)
    leg("A5 pinned reader -> ok", r.outcome == "ok", f"{r.outcome} {r.stderr_head[:80]}")
    # 2-6. client shapes (reads 2..6)
    leg("A5 exec bash", *is_doc(ssh("center_a", "127.0.0.1", f"touch {canary}; bash"), key_a))
    leg("A5 -t (stdin not a tty)", *is_doc(ssh("center_a", "-t", "127.0.0.1", "true"), key_a))
    leg("A5 -s sftp", *is_doc(ssh("center_a", "-s", "127.0.0.1", "sftp"), key_a))
    leg("A5 stdin payload", *is_doc(ssh("center_a", "127.0.0.1", "cat",
                                         stdin=f"touch {canary}\n".encode()), key_a))
    leg("A5 -A agent", *is_doc(ssh("center_a", "-A", "127.0.0.1", "x"), key_a))
    # 7. B8: the 7th read in the window is refused
    p = ssh("center_a", "127.0.0.1", "x")
    leg("B8 7th read -> rate_limited",
        bt.classify_transport(p.returncode, p.stdout.decode("ascii", "replace"), "") == "rate_limited")
    # key B: a separate grant, its own bucket
    leg("A5 LC_ALL=C client", *is_doc(ssh("center_b", "-o", "SendEnv=LC_ALL", "127.0.0.1", "x",
                                           extra_env={"LC_ALL": "C"}), key_b))
    # -tt FORCES a pty request: restrict refuses it. Pass = the JSON or a
    # refused pty — never a shell (measured: OpenSSH 10.0 aborts, rc 255).
    p = ssh("center_b", "-tt", "127.0.0.1", "true")
    ok_doc, detail = is_doc(p, key_b)
    refused = p.returncode == 255 and b"PTY allocation request failed" in p.stderr
    leg("A5 -tt forced pty -> refused or JSON", ok_doc or refused,
        detail if ok_doc else f"rc={p.returncode} pty refused")
    # -L: the forward must never reach the listener
    hits = []
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)
    lsock.settimeout(4)
    target = lsock.getsockname()[1]

    def accept():
        try:
            c, _ = lsock.accept()
            hits.append(1)
            c.close()
        except OSError:
            pass
    th = threading.Thread(target=accept, daemon=True)
    th.start()
    fwd_port = free_port()
    fwd = subprocess.Popen(["ssh", "-p", str(port), "-i", str(T / "center_b"),
                            "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
                            "-o", "BatchMode=yes", "-o", f"UserKnownHostsFile={kh}",
                            "-o", "StrictHostKeyChecking=accept-new", "-N",
                            "-L", f"{fwd_port}:127.0.0.1:{target}", "127.0.0.1"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        c = socket.create_connection(("127.0.0.1", fwd_port), timeout=2)
        c.settimeout(2)
        try:
            c.recv(1)
        except OSError:
            pass
        c.close()
    except OSError:
        pass
    th.join(timeout=5)
    fwd.terminate()
    lsock.close()
    leg("A5 -L forward never reaches the target", not hits, f"target connections: {len(hits)}")
    leg("A5 nothing executed (canary)", not canary.exists())

    # B4 witness
    rows = bc.read_witness(Path(env["XDG_STATE_HOME"]) / "box_truth" / "reads.jsonl")
    cmds = {r.get("orig_cmd") for r in rows}
    leg("B4 witness: fp + src + orig_cmd as data",
        any(r.get("fp") == key_a.fingerprint and r.get("src") == "127.0.0.1" for r in rows)
        and f"touch {canary}; bash" in cmds, f"{len(rows)} rows")

    # A9 identity: a wrong registry fingerprint discards the data
    r = br.read_box("127.0.0.1", "SHA256:" + "Z" * 43, key_path=T / "center_b", runner=reader)
    leg("A9 wrong host key -> identity_mismatch", r.outcome == "identity_mismatch" and r.doc is None,
        r.outcome)

    # F1 (review A): a key smuggled after U+2028 inside another line's comment
    # must stay inert across a rewrite (the A3 revoke below rewrites the file).
    keygen(T / "smuggled", "smuggled")
    smug = Path(str(T / "smuggled") + ".pub").read_text().strip()
    ak = home / ".ssh" / "authorized_keys"
    ak.write_bytes(ak.read_bytes() + ("ssh-ed25519 " + key_a.blob_b64 + " op\u2028" + smug + "\n").encode())
    pre = ssh("smuggled", "127.0.0.1", "true")

    # A3 revoke -> refused_grant through the pinned reader
    rp = bg.plan_revoke(home, key_b.fingerprint)
    n = bg.apply_revoke(home, rp, rp.confirm_tail, env=env)
    r = br.read_box("127.0.0.1", host_fp, key_path=T / "center_b", runner=reader)
    leg("A3 revoke -> refused_grant", n == 1 and r.outcome == "refused_grant",
        f"removed {n}, then {r.outcome}: {br.describe(r)[:60]}")
    post = ssh("smuggled", "127.0.0.1", "true")
    leg("F1 smuggled key stays inert across a rewrite",
        pre.returncode == 255 and post.returncode == 255,
        f"before rc={pre.returncode}, after rc={post.returncode}")

    # F2 (review A): a backslash decoy must not hide a live key from revoke
    keygen(T / "decoyd", "decoyd")
    keygen(T / "realk", "realk")
    decoy = Path(str(T / "decoyd") + ".pub").read_text().strip()
    real = Path(str(T / "realk") + ".pub").read_text().strip()
    ak.write_bytes(ak.read_bytes() + f'command="echo \\\\" {decoy}" {real}\n'.encode())
    live = ssh("realk", "127.0.0.1", "x")
    real_fp = bt.parse_authorized_keys_line(real).fingerprint
    rp = bg.plan_revoke(home, real_fp, force=True)
    removed = bg.apply_revoke(home, rp, rp.confirm_tail, env=env) if not rp.problems else 0
    dead = ssh("realk", "127.0.0.1", "x")
    leg("F2 backslash decoy cannot hide a key from revoke",
        live.returncode == 0 and removed == 1 and dead.returncode == 255,
        f"live rc={live.returncode}, removed {removed}, after rc={dead.returncode}"
        + (f" problems={rp.problems}" if rp.problems else ""))


if __name__ == "__main__":
    sys.exit(main())

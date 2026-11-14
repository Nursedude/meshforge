#!/usr/bin/env python3
"""eval_pf_key_install.py — grant (or revoke) the EVAL-PF lab eval key.

EVAL-PF slice 1, step 3 (plans/eval_pf_grilling_2026_11_13.md Q14/Q18/Q29).
This script writes NOTHING on the box itself: the box's key file has ONE
writer, `box_truth_grant.py` (S11, operator 11-13 "one writer"). It runs that
CLI on the lab box over the operator's normal admin ssh, as `add --kind
eval-pf`, with the same plan / typed-confirm / read-back every NOC grant gets.

    python3 scripts/eval_pf_key_install.py                 # dry run: print the plan
    python3 scripts/eval_pf_key_install.py --apply         # grant, verify, print the touch line
    python3 scripts/eval_pf_key_install.py --revoke --apply

What --apply does, in order (each step verified before the next):
  1. make the dedicated key ~/.ssh/eval_pf_ed25519 if absent (no passphrase;
     comment `eval-pf:<this host>`);
  2. on --box, refuse unless the box's checkout has the eval wrapper
     (deploy first);
  3. `box_truth_grant.py add --kind eval-pf --center <this host> --confirm
     <fp last 8>` on the box; then `list` must show this fingerprint once,
     kind eval-pf;
  4. write the eval ssh config (~/.config/meshforge/eval_pf/ssh_config):
     host/port/user/HostKeyAlias copied from `ssh -G <box>`, IdentityFile =
     the eval key, IdentitiesOnly yes, IdentityAgent none — so the shim
     (scripts/eval_pf_ssh.sh) never offers any other key;
  5. functional check through the shim: `uptime` must exit 0 and `id` must
     exit 126 (refused) — the proof the forced command is in force.
Exit: 0 done · 1 refused/failed (message names the fix) · 2 usage.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import socket
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from utils import box_truth as bt  # noqa: E402
from utils.paths import get_real_user_home  # noqa: E402

BOX_REPO = "/opt/meshforge"
BOX_WRAPPER = f"{BOX_REPO}/scripts/{bt.EVAL_BASENAME}"
BOX_GRANT_CLI = f"{BOX_REPO}/scripts/box_truth_grant.py"
SHIM = os.path.join(_HERE, "eval_pf_ssh.sh")
TIMEOUT_S = 60


def key_path(home: str) -> str:
    return os.path.join(home, ".ssh", "eval_pf_ed25519")


def config_path(home: str) -> str:
    return os.path.join(home, ".config", "meshforge", "eval_pf", "ssh_config")


def center_name(hostname: str) -> str:
    """This host as a grant center name ([A-Za-z0-9._-], ≤64)."""
    name = re.sub(r"[^A-Za-z0-9._-]", "-", hostname.lower())[:64]
    if not name:
        raise ValueError("hostname gives an empty center name")
    return name


def parse_ssh_g(text: str) -> dict:
    """`ssh -G host` output → the fields the eval config copies."""
    want = {"hostname", "port", "user", "hostkeyalias"}
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(" ")
        if k in want and v:
            out[k] = v.strip()
    missing = {"hostname", "port", "user"} - set(out)
    if missing:
        raise ValueError(f"`ssh -G` did not give {sorted(missing)}")
    return out


def render_ssh_config(g: dict, key: str, known_hosts: str) -> str:
    lines = [
        "# Written by scripts/eval_pf_key_install.py — EVAL-PF lab access. Do not edit.",
        "Host eval-lab",
        f"  HostName {g['hostname']}",
        f"  Port {g['port']}",
        f"  User {g['user']}",
    ]
    if g.get("hostkeyalias"):
        lines.append(f"  HostKeyAlias {g['hostkeyalias']}")
    lines += [
        f"  IdentityFile {key}",
        "  IdentitiesOnly yes",
        "  IdentityAgent none",
        "  ForwardAgent no",
        "  BatchMode yes",
        "  StrictHostKeyChecking yes",
        f"  UserKnownHostsFile {known_hosts}",
        "  ControlMaster no",
        "  ControlPath none",
        "  RequestTTY no",
        "",
    ]
    return "\n".join(lines)


def grant_add_cmd(pub: str, center: str, confirm: str) -> str:
    """The remote command line: the ONE grant writer, kind eval-pf."""
    return " ".join(shlex.quote(w) for w in (
        "python3", BOX_GRANT_CLI, "add", "--kind", bt.KIND_EVAL,
        "--pubkey", pub, "--center", center, "--confirm", confirm))


def grant_revoke_cmd(fp: str) -> str:
    return " ".join(shlex.quote(w) for w in (
        "python3", BOX_GRANT_CLI, "revoke", "--fp", fp, "--confirm", fp[-8:]))


def grant_list_cmd() -> str:
    return " ".join(shlex.quote(w) for w in ("python3", BOX_GRANT_CLI, "list"))


def listed(list_out: str, fp: str) -> list:
    """`list` rows carrying this fingerprint, as (kind, rest)."""
    rows = []
    for line in list_out.splitlines():
        parts = line.split()
        if parts and parts[0] == fp:
            rows.append((parts[1] if len(parts) > 1 else "", line))
    return rows


def run(argv, *, env=None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S,
                          env=env, stdin=subprocess.DEVNULL)


def admin_ssh(box: str, remote: str):
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", box, remote])


def fail(msg: str) -> int:
    print(f"eval_pf_key_install: REFUSED — {msg}", file=sys.stderr)
    return 1


def load_key(key: str):
    with open(key + ".pub") as fh:
        return bt.parse_noc_pubkey(fh.read(), kind=bt.KIND_EVAL)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--box", default="meshforge-lab",
                    help="the lab box's admin ssh alias (default meshforge-lab)")
    ap.add_argument("--apply", action="store_true", help="make changes (default: dry run)")
    ap.add_argument("--revoke", action="store_true", help="revoke the eval grant instead")
    a = ap.parse_args(argv)

    home = str(get_real_user_home())
    key, cfg = key_path(home), config_path(home)
    known = os.path.join(home, ".ssh", "known_hosts")
    center = center_name(socket.gethostname())

    if not a.apply:
        verb = "revoke the eval grant on" if a.revoke else "grant the eval key on"
        print(f"DRY RUN — would {verb} {a.box}:")
        if not a.revoke:
            print(f"  key      {key} ({'exists' if os.path.exists(key) else 'to create'}),"
                  f" comment {bt.KIND_EVAL}:{center}")
            print(f"  box      require {BOX_WRAPPER}; run the ONE grant writer there:")
            print(f"           {BOX_GRANT_CLI} add --kind {bt.KIND_EVAL} --center {center} --confirm <fp last 8>")
            print(f"  config   {cfg} (IdentitiesOnly yes, IdentityAgent none)")
            print(f"  check    {SHIM} uptime → 0 ; {SHIM} id → 126")
        print("Re-run with --apply to do it.")
        return 0

    if a.revoke:
        try:
            k = load_key(key)
        except (OSError, bt.KeyRejected) as e:
            return fail(f"cannot read the eval public key ({e})")
        r = admin_ssh(a.box, grant_revoke_cmd(k.fingerprint))
        if r.returncode != 0:
            return fail(f"revoke on {a.box} rc={r.returncode}: {(r.stdout + r.stderr).strip()[-300:]}")
        lst = admin_ssh(a.box, grant_list_cmd())
        if listed(lst.stdout, k.fingerprint):
            return fail(f"read-back: {k.fingerprint} still listed on {a.box}")
        print(r.stdout.strip())
        print(f"TOUCH: {time.strftime('%Y-%m-%d %H:%M %Z')} · {a.box} · EVAL-PF grant REVOKED "
              f"{k.fingerprint} via box_truth_grant.py (eval_pf_key_install.py --revoke)")
        return 0

    # 1. key
    if not os.path.exists(key):
        os.makedirs(os.path.dirname(key), mode=0o700, exist_ok=True)
        r = run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                 "-C", f"{bt.KIND_EVAL}:{center}", "-f", key])
        if r.returncode != 0:
            return fail(f"ssh-keygen failed: {r.stderr.strip()[:200]}")
    try:
        k = load_key(key)
    except (OSError, bt.KeyRejected) as e:
        return fail(f"eval public key unusable ({e}) — delete {key}* and re-run")

    # 2. the wrapper must already be on the box
    r = admin_ssh(a.box, f"test -x {shlex.quote(BOX_WRAPPER)} && echo ok")
    if r.stdout.strip() != "ok":
        return fail(f"{BOX_WRAPPER} is not on {a.box} (deploy first: fleet_sync then fleet_pull)")

    # 3. the one writer, then read back through its own list
    r = admin_ssh(a.box, grant_add_cmd(k.line(), center, k.fingerprint[-8:]))
    if r.returncode != 0:
        return fail(f"grant on {a.box} rc={r.returncode}: {(r.stdout + r.stderr).strip()[-400:]}")
    lst = admin_ssh(a.box, grant_list_cmd())
    rows = listed(lst.stdout, k.fingerprint)
    if len(rows) != 1 or rows[0][0] != bt.KIND_EVAL or "FOREIGN" in rows[0][1]:
        return fail(f"read-back on {a.box}: want exactly 1 {bt.KIND_EVAL} grant for "
                    f"{k.fingerprint}, got {rows!r}")

    # 4. eval ssh config
    g = run(["ssh", "-G", a.box])
    if g.returncode != 0:
        return fail(f"`ssh -G {a.box}` failed: {g.stderr.strip()[:200]}")
    os.makedirs(os.path.dirname(cfg), mode=0o700, exist_ok=True)
    with open(cfg + ".tmp", "w") as fh:
        fh.write(render_ssh_config(parse_ssh_g(g.stdout), key, known))
    os.chmod(cfg + ".tmp", 0o600)
    os.replace(cfg + ".tmp", cfg)

    # 5. functional check through the shim
    env = {k_: v for k_, v in os.environ.items() if k_ != "SSH_AUTH_SOCK"}
    up = run(["bash", SHIM, "uptime"], env=env)
    no = run(["bash", SHIM, "id"], env=env)
    if up.returncode != 0:
        return fail(f"shim `uptime` rc={up.returncode}: {up.stderr.strip()[:200]}")
    if no.returncode != 126:
        return fail(f"shim `id` rc={no.returncode} — the forced command is NOT in force; "
                    "revoke now (--revoke --apply)")
    print(f"granted {k.fingerprint} on {a.box} (kind {bt.KIND_EVAL}); uptime rc 0, id rc 126")
    print(f"TOUCH: {time.strftime('%Y-%m-%d %H:%M %Z')} · {a.box} · EVAL-PF grant {k.fingerprint} "
          f"(kind {bt.KIND_EVAL}, center {center}) via box_truth_grant.py; shim uptime 0 / id 126")
    return 0


if __name__ == "__main__":
    sys.exit(main())

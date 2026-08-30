#!/usr/bin/env python3
"""Copy a channel key from a RADIO to a dude-CLAW — no keyfile, no transcript.

WHY THIS EXISTS (2026-08-30)
----------------------------
`mesh_psk_safe.py copypsk` already does this safely **radio -> radio**: it reads
the source key in-process and writes it to the destination without the bytes
ever reaching a file or the transcript. But both its endpoints are meshtasticd
hosts, and a dude-claw is not one — it is an ESP32 that takes the key through
the NATS `mesh_set_channel` tool. So the safe pattern stopped one device class
short, and the only way to set a claw's channel was
`claw_set_fleet_channel.py`, which needs the key from `--psk-stdin` or a
`getpass` prompt.

That gap is why an agent session cannot restore a claw's fleet channel at all:
the guard that keeps PSKs out of transcripts (born after keys leaked TWICE) is
working exactly as intended, and this script is the missing bridge rather than
a hole in it. The key is read in-process and passed straight to the tool call;
it is never printed, never written to disk, never returned.

WHAT IT VERIFIES, AND WHAT THAT IS WORTH
----------------------------------------
The claw echoes back the resulting Meshtastic channel hash, and this compares
it to `--expect-hash` when given. Be honest about the strength: that hash is
ONE BYTE. It confirms *a* key landed and changed the channel identity; it does
NOT confirm *the right* key landed (1-in-256 collision). It is the strongest
read-back the device offers, and it is deliberately not dressed up as more.

The DEFINITIVE proof is a decode test, which terminates at the domain's end
rather than at a proxy: transmit from a radio on that channel and confirm the
claw decodes it. The command is printed after a successful set.

⚠️ `--persist` writes the key to the claw's FLASH. Default is RAM-only, which
is why a claw loses its fleet channel on every reboot — annoying, but it is
also the reason a stolen claw does not hand over the fleet key. Opt in
deliberately, per claw, knowing the trade.

    scripts/claw_copy_fleet_channel.py --source localhost --name meshforge \
        --device dudeclaw-01 --dry-run
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys

sys.path.insert(0, "/opt/meshforge/src")
from mini_dudeai.nats_client import request  # noqa: E402

# Import the psk-safe helpers rather than re-deriving them: two implementations
# of "read this channel's key" WILL drift, and the copy here would be the one
# nobody audits (honest_failure_modes #5 — two consumers, one constant).
_spec = importlib.util.spec_from_file_location(
    "mesh_psk_safe", "/opt/meshforge/scripts/mesh_psk_safe.py")
_mps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mps)

_RE_HASH = re.compile(r"hash 0x([0-9a-fA-F]{2})")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True,
                    help="meshtasticd host holding the channel (e.g. localhost)")
    ap.add_argument("--name", required=True, help="channel name, e.g. meshforge")
    ap.add_argument("--device", required=True, help="claw device name on the bus")
    ap.add_argument("--server", default="localhost:4222",
                    help="NATS server holding the claw bus. ⚠️ With --via-ssh "
                         "this is resolved ON THE REMOTE HOST, so the default "
                         "'localhost:4222' means the bus local to that box — "
                         "which is usually what you want.")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--expect-hash",
                    help="assert the claw reports this channel hash, e.g. 0xa2")
    ap.add_argument("--persist", action="store_true",
                    help="ALSO write the key to the claw's flash so it survives "
                         "reboots. Default RAM-only. This puts the fleet key on "
                         "the device permanently — opt in knowingly.")
    ap.add_argument("--via-ssh", metavar="HOST",
                    help="run the WRITE leg on HOST over ssh, feeding the key on "
                         "that process's STDIN. Use when no single box has both "
                         "the meshtastic CLI (to read) and reach to the claw's "
                         "NATS bus (to write) — which is the fleet's actual "
                         "shape. The key crosses an encrypted ssh channel on "
                         "stdin: never in argv, never on disk, never echoed.")
    ap.add_argument("--dry-run", action="store_true",
                    help="read the source key and report its hash; write nothing")
    a = ap.parse_args()

    status, psk = _mps._channel_psk(a.source, a.name)
    if status != _mps.CH_OK:
        return _mps._report_lookup_failure(a.name, status)
    if not _mps.B64_32.match(psk):
        # Same refusal copypsk makes: default/simple keys are public knowledge
        # and a 32-byte key is what a fleet channel means here.
        print(f"refusing: source '{a.name}' key is not a 32-byte base64 key "
              f"(sha256:{_mps._hash16(psk)})", file=sys.stderr)
        return 2

    print(f"source {a.source} '{a.name}': sha256:{_mps._hash16(psk)}")
    if a.dry_run:
        where = f"via ssh {a.via_ssh}" if a.via_ssh else f"direct to {a.server}"
        print(f"DRY-RUN: would set {a.device} channel '{a.name}' {where} "
              f"({'persisted to flash' if a.persist else 'RAM-only'})")
        return 0

    if a.via_ssh:
        # Hand the key to claw_set_fleet_channel.py --psk-stdin on the far side.
        # ⚠️ persist is passed EXPLICITLY in both directions: that tool defaults
        # --persist ON while this one defaults OFF, and letting the two defaults
        # meet in the middle is how one tool silently changes the other's
        # security posture (honest_failure_modes #5 — two consumers of one
        # setting must not each carry their own default).
        cmd = [
            "ssh", "-o", "BatchMode=yes", a.via_ssh,
            # PYTHONPATH belt-and-braces: the remote script self-inserts its
            # sys.path since 2026-08-30, but a box on an older checkout would
            # otherwise fail with ModuleNotFoundError AFTER the key was already
            # on its stdin. Cheap here, and this leg must not depend on how
            # recently a given box pulled.
            "env", "PYTHONPATH=/opt/meshforge/src",
            "python3", "/opt/meshforge/scripts/claw_set_fleet_channel.py",
            "--device", a.device, "--name", a.name,
            "--server", a.server, "--psk-stdin",
            "--persist" if a.persist else "--no-persist",
        ]
        try:
            r = subprocess.run(cmd, input=psk + "\n", capture_output=True,
                               text=True, timeout=max(a.timeout * 6, 90))
        finally:
            del psk
        # NEVER print r.stderr raw on the failure path without thinking: the
        # remote tool is written not to echo the key, but this is the boundary
        # where a future change there would leak through here.
        if r.returncode != 0:
            print(f"{a.device}: remote set FAILED on {a.via_ssh} "
                  f"(rc={r.returncode})", file=sys.stderr)
            for line in (r.stdout or "").splitlines()[-4:]:
                print(f"  {line}", file=sys.stderr)
            return 1
        text = r.stdout or ""
        print(f"(write ran on {a.via_ssh}; key sent on stdin, never argv)")
    else:
        payload = {"tool": "mesh_set_channel", "name": a.name, "psk": psk}
        if a.persist:
            payload["persist"] = 1
        try:
            resp = request(a.server, f"{a.device}.tool_exec",
                           json.dumps(payload), timeout_s=a.timeout)
        finally:
            # Drop the key from this process's reachable state as soon as the
            # call returns, success or not.
            del psk, payload
        if resp is None:
            print(f"no reply from {a.device} (claw dark, or wrong bus?) — "
                  f"channel state UNKNOWN", file=sys.stderr)
            return 2
        text = str(resp)
    if "Channel set" not in text:
        # The firmware reports key-length / format problems WITHOUT echoing the
        # key; surface its reason verbatim, it is safe and it is the finding.
        print(f"{a.device}: set FAILED — {text[:200]}", file=sys.stderr)
        return 1

    m = _RE_HASH.search(text)
    got = f"0x{m.group(1).lower()}" if m else None
    persisted = "persisted" in text.lower()
    print(f"{a.device}: channel '{a.name}' set, hash {got or 'UNREPORTED'}"
          f" ({'persisted to flash' if persisted else 'RAM-only'})")

    if a.expect_hash:
        want = a.expect_hash.lower()
        if not want.startswith("0x"):
            want = "0x" + want
        if got is None:
            print("expect-hash given but the claw reported no hash — UNKNOWN",
                  file=sys.stderr)
            return 2
        if got != want:
            print(f"HASH MISMATCH: claw reports {got}, expected {want} — the "
                  f"channel identity is not what you asked for", file=sys.stderr)
            return 1
        print(f"hash matches {want}")

    if not a.persist:
        print("⚠️ RAM-only: this is lost on the claw's next reboot "
              "(re-run, or use --persist knowing the key lands in flash)")
    print(f"NOT YET PROVEN: a 1-byte hash cannot confirm WHICH key landed. "
          f"Prove it by decode — transmit on '{a.name}' from a radio and check "
          f"the claw sees it:\n"
          f"  ssh <bus-host> \"PYTHONPATH=/opt/meshforge/src python3 -c \\\n"
          f"    'from mini_dudeai.nats_client import request; ...'\"  # lora_stats")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

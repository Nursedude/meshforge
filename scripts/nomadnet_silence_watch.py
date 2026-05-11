#!/usr/bin/env python3
"""Watch fleet NomadNets for silence-mode failures.

Polls each box's `~/.nomadnetwork/logfile` mtime over ssh. Emits one line
per state transition (ok ↔ quiet ↔ ssh-fail) so a single stdout stream
makes a clean event feed (suitable for Claude Code's Monitor tool, or
just `tee` to a log).

Default threshold is 60 minutes of silence — well below NomadNet's
typical idle-then-die failure mode (we caught 8+ day silence on the
fleet on 2026-05-11) so an operator notices long before the daemon is
effectively dead.

Origin: extracted from the 2026-05-11 session's ad-hoc watcher after
restarting NomadNet on all five fleet boxes. Codifies the silence
detector we built manually that day so the fleet doesn't have to
re-discover the failure mode the hard way.

Usage — default reads peers from your fleet.json
(``~/.config/meshforge/fleet.json`` by default; see
``scripts/fleet_sync.sh`` docs for the schema):

    python3 scripts/nomadnet_silence_watch.py
    python3 scripts/nomadnet_silence_watch.py --boxes host-a,host-b --quiet-min 30
    python3 scripts/nomadnet_silence_watch.py --boxes host-a --poll-sec 60

Stop with Ctrl+C.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def _real_home() -> Path:
    """sudo-safe home lookup — mirrors selftest.py's fallback pattern."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        return Path(f"/home/{sudo_user}")
    return Path.home()


DEFAULT_FLEET_HOSTS = _real_home() / ".config" / "meshforge" / "fleet_hosts"


def _boxes_from_fleet_hosts(path: Path, include_self: bool = True) -> list[str]:
    """Read peer ssh aliases from fleet_hosts (the file fleet_sync.sh reads).

    Format: one host per line, '#' comments allowed. By convention the
    file excludes self (so fleet_sync doesn't push to its own host); for
    a watcher we usually do want to monitor self too, so we prepend the
    box's own hostname unless include_self=False.
    """
    aliases: list[str] = []
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            aliases.append(line)
    except (FileNotFoundError, OSError):
        return []
    if include_self:
        # Lowercased to match the convention in fleet_hosts (moc, moc1, ...).
        self_name = socket.gethostname().lower()
        if self_name and self_name not in aliases:
            aliases.insert(0, self_name)
    return aliases


def probe(host: str, ssh_timeout: int = 15) -> Optional[int]:
    """Return seconds since the host's NomadNet logfile mtime, or None on error.

    `StrictHostKeyChecking=accept-new` is used so IP-addressed boxes from
    fleet.json work on first contact without an operator running ssh-keyscan
    by hand. Subsequent connections require the host key to match (MITM
    protection still applies).
    """
    try:
        r = subprocess.run(
            ["ssh",
             "-o", "ConnectTimeout=10",
             "-o", "StrictHostKeyChecking=accept-new",
             "-o", "BatchMode=yes",
             host,
             "expr $(date +%s) - $(stat -c %Y ~/.nomadnetwork/logfile "
             "2>/dev/null || echo 0)"],
            capture_output=True, text=True, timeout=ssh_timeout, check=False,
        )
        return int(r.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--boxes", default="",
                    help="comma-separated host list (ssh-resolvable). "
                         "If unset, reads peers from --fleet-json.")
    ap.add_argument("--fleet-hosts", default=str(DEFAULT_FLEET_HOSTS),
                    help=f"fleet host list (default: {DEFAULT_FLEET_HOSTS})")
    ap.add_argument("--poll-sec", type=int, default=300,
                    help="seconds between poll rounds (default 300)")
    ap.add_argument("--quiet-min", type=int, default=60,
                    help="minutes of logfile silence that counts as 'quiet' "
                         "(default 60)")
    ap.add_argument("--heartbeat-min", type=int, default=60,
                    help="minutes between status-quo 'all ok' heartbeats "
                         "(default 60)")
    args = ap.parse_args()

    if args.boxes:
        boxes = [b.strip() for b in args.boxes.split(",") if b.strip()]
    else:
        boxes = _boxes_from_fleet_hosts(Path(args.fleet_hosts))
    if not boxes:
        print(f"error: no boxes to watch. Pass --boxes or create "
              f"{args.fleet_hosts} (see scripts/fleet_sync.sh docs).",
              file=sys.stderr)
        return 2
    quiet_s = args.quiet_min * 60
    heartbeat_s = args.heartbeat_min * 60

    state = {b: "unknown" for b in boxes}
    last_heartbeat = 0.0

    print(f"watcher started: {len(boxes)} boxes, "
          f"quiet_threshold={args.quiet_min} min, "
          f"poll={args.poll_sec}s", flush=True)

    while True:
        now = time.time()
        quiet_now = []
        for box in boxes:
            age = probe(box)
            if age is None:
                new = "ssh-fail"
            elif age > quiet_s:
                new = "quiet"
                quiet_now.append(f"{box} ({age // 60} min)")
            else:
                new = "ok"
            if new != state[box]:
                ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                age_label = (
                    f" (last activity {age // 60} min ago)"
                    if age is not None else ""
                )
                print(f"{ts}  TRANSITION  {box}: {state[box]} -> {new}{age_label}",
                      flush=True)
                state[box] = new

        if now - last_heartbeat > heartbeat_s:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            ok_count = sum(1 for v in state.values() if v == "ok")
            extra = (
                ", quiet: " + ", ".join(quiet_now) if quiet_now else ""
            )
            print(f"{ts}  heartbeat: {ok_count}/{len(boxes)} ok{extra}",
                  flush=True)
            last_heartbeat = now

        time.sleep(args.poll_sec)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

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
from typing import Union


def _real_home() -> Path:
    """sudo-safe home lookup — mirrors selftest.py's fallback pattern."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        return Path(f"/home/{sudo_user}")
    return Path.home()


_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


def _default_fleet_hosts() -> Path:
    """The resolver's answer (env override, per-repo tier, /etc fallback) —
    this was a hardcoded user-home path, one of ~13 independent copies of
    the resolution chain (converged onto utils.fleet_hosts 2026-07-29). The
    hardcoded path stays only as the display/help default when NO list
    resolves anywhere."""
    from utils.fleet_hosts import resolve_fleet_hosts_file
    p = resolve_fleet_hosts_file()
    return p if p else _real_home() / ".config" / "meshforge" / "fleet_hosts"


DEFAULT_FLEET_HOSTS = _default_fleet_hosts()


def _boxes_from_fleet_hosts(path: Path, include_self: bool = True) -> list[str]:
    """Read peer ssh aliases from fleet_hosts (the file fleet_sync.sh reads).

    Parsing delegates to the shared ``utils.fleet_hosts`` parser ('#'
    comments anywhere on a line, whitespace-split). By convention the
    file excludes self (so fleet_sync doesn't push to its own host); for
    a watcher we usually do want to monitor self too, so we prepend the
    box's own hostname unless include_self=False.
    """
    from utils.fleet_hosts import parse_fleet_hosts_text
    try:
        aliases = parse_fleet_hosts_text(path.read_text())
    except (FileNotFoundError, OSError):
        return []
    if include_self:
        # Lowercased to match the convention in fleet_hosts (moc, moc1, ...).
        self_name = socket.gethostname().lower()
        if self_name and self_name not in aliases:
            aliases.insert(0, self_name)
    return aliases


# probe() sentinel: the box answered and has no NomadNet logfile at all.
# Distinct from None (the probe itself failed) and from a large age (real
# silence) — see classify().
NO_LOGFILE = "no-logfile"

# What the remote shell prints when the logfile is absent. A non-numeric
# token on purpose: any numeric sentinel (0, -1) is a value a real mtime
# delta could legitimately take.
_NOLOG_TOKEN = "NOLOG"


def probe(host: str, ssh_timeout: int = 15) -> Union[int, str, None]:
    """Seconds since the host's NomadNet logfile mtime — as one of THREE answers.

    Collapsing these is the defect this detector exists to avoid:

      * ``int``        — a real age. May be NEGATIVE if the file's mtime is
                         ahead of the box's own clock; RTC-less Pis on this
                         fleet do that after a fake-hwclock restore.
      * ``NO_LOGFILE`` — the box answered, and there is no logfile. NomadNet
                         has never run here. That is INERT, not silence.
      * ``None``       — the probe itself failed (ssh/transport). Unobservable,
                         which is never "healthy" and never "quiet".

    Until 2026-09-02 the remote command ended ``|| echo 0``, so an absent
    logfile became mtime 0 and the age became seconds-since-epoch. Four boxes
    reported ~29,806,174 minutes (56.7 years) of silence and latched into the
    "quiet" alarm state permanently — which also meant a REAL silence on those
    boxes could never fire a transition again, because the state never changed.

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
             'f="$HOME/.nomadnetwork/logfile"; '
             'if [ -e "$f" ]; then '
             'expr $(date +%s) - $(stat -c %Y "$f"); '
             f'else echo {_NOLOG_TOKEN}; fi'],
            capture_output=True, text=True, timeout=ssh_timeout, check=False,
        )
        out = r.stdout.strip()
        if out == _NOLOG_TOKEN:
            return NO_LOGFILE
        return int(out)
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        return None


def classify(age: Union[int, str, None], quiet_s: int) -> str:
    """Map a probe result to a watcher state.

    Absence, blindness, a skewed clock and real silence are four different
    claims. Only ``quiet`` means "NomadNet stopped talking here" — the one
    an operator should act on.
    """
    if age is None:
        return "ssh-fail"
    if age == NO_LOGFILE:
        return "no-logfile"
    if age < 0:
        return "clock-skew"
    if age > quiet_s:
        return "quiet"
    return "ok"


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
        blind_now = []
        for box in boxes:
            age = probe(box)
            new = classify(age, quiet_s)
            if new == "quiet":
                quiet_now.append(f"{box} ({age // 60} min)")
            elif new == "no-logfile":
                blind_now.append(f"{box} (no nomadnet logfile — never ran here)")
            elif new == "ssh-fail":
                blind_now.append(f"{box} (probe failed — UNOBSERVABLE)")
            elif new == "clock-skew":
                blind_now.append(
                    f"{box} (logfile mtime {abs(age) // 60} min AHEAD of its clock)")
            if new != state[box]:
                ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                # Only a real, non-negative age is an "age". A sentinel is not
                # a duration, and printing one as minutes is how this detector
                # claimed 56.7 years of silence.
                age_label = (
                    f" (last activity {age // 60} min ago)"
                    if isinstance(age, int) and age >= 0 else ""
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
            # Blindness gets its OWN clause, never folded into the quiet list:
            # "we cannot see this box" must not read as "this box is silent"
            # (honest_failure_modes #2 — surface the blind spot, don't average
            # it away).
            if blind_now:
                extra += ", not observed: " + ", ".join(blind_now)
            print(f"{ts}  heartbeat: {ok_count}/{len(boxes)} ok{extra}",
                  flush=True)
            last_heartbeat = now

        time.sleep(args.poll_sec)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

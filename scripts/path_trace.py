#!/usr/bin/env python3
"""path_trace — locate WAN packet loss, hop by hop, from the terminal.

Companion CLI to the TUI's Network Tools -> WAN Path Trace and to
``utils.path_trace`` (which carries the method and its three traps). A session
running alongside the operator uses the same entrypoint with ``--json``.

    python3 scripts/path_trace.py github.com
    python3 scripts/path_trace.py github.com pypi.org --json
    python3 scripts/path_trace.py my.vps --port 22 --probes 20
"""
import argparse
import json
import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from utils.path_trace import (  # noqa: E402
    DEFAULT_MAX_TTL, DEFAULT_PROBES, DEFAULT_TCP_PORT, DEFAULT_TCP_TRIALS,
    FAULT_STATUSES, compare, render, trace,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="locate WAN packet loss, hop by hop")
    ap.add_argument("targets", nargs="+", help="hosts or IPs to trace")
    ap.add_argument("--probes", type=int, default=DEFAULT_PROBES,
                    help="direct-echo probes per hop (default %d)" % DEFAULT_PROBES)
    ap.add_argument("--max-ttl", type=int, default=DEFAULT_MAX_TTL)
    ap.add_argument("--port", type=int, default=DEFAULT_TCP_PORT,
                    help="TCP port for the confirmation leg (0 disables it)")
    ap.add_argument("--tcp-trials", type=int, default=DEFAULT_TCP_TRIALS)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--quiet", action="store_true", help="no progress lines")
    args = ap.parse_args(argv)

    def progress(stage, detail):
        if not args.quiet and not args.json:
            print("  [%s] %s" % (stage, detail), file=sys.stderr)

    results = []
    for t in args.targets:
        results.append(trace(t, probes=args.probes, max_ttl=args.max_ttl,
                             tcp_port=(args.port or None), tcp_trials=args.tcp_trials,
                             progress=progress))

    if args.json:
        print(json.dumps({"results": [asdict(r) for r in results],
                          "comparison": compare(results) if len(results) > 1 else []},
                         indent=1))
    else:
        for r in results:
            print(render(r))
            print()
        if len(results) > 1:
            print("=== comparison ===")
            for line in compare(results):
                print(line)

    # 0 = every target clean (or provably just policing); 1 = a real fault.
    bad = [r for r in results if r.finding and r.finding.status in FAULT_STATUSES]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

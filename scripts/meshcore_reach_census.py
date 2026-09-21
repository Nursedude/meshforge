#!/usr/bin/env python3
"""MeshCore reach census — what the Hawaii community's reach ACTUALLY looks like.

Owed by the Public-bot decision (revisit >= 2026-09-25): *measure reach before
promising a Public bot*. The bar the operator set is "never make their channel
worse", and that is a quantitative claim — a reply that traverses 8 repeaters
costs 8 transmissions of airtime on a channel 70+ people share.

SOURCE. MeshCore RF lives on the MeshAnchor box (``meshcore.enabled: true``,
RAK4631 on /dev/ttyMeshCore). Since MA ``516ab8c5`` (2026-09-18) every inbound
channel message logs ``hops=<n|direct|?> snr=<dB|?>`` from the wire's own
``path_len`` / ``SNR`` fields. This reads that journal. Nothing new is
installed and no daemon is added — the measurement already exists, unread.

⚠️ FOUR honest limits, stated because a census that hides them is worse than none:

1. **Own traffic dominates and must be separated.** Most rx are the operator's
   own node in the same room (``hops=direct``, SNR ~11-12.75). Folding those in
   yields "median SNR 12, mostly direct" — a confident claim about the
   community derived almost entirely from our own test messages. Ask what the
   number is OF. ``--own`` names the local nodes; they are reported apart,
   never averaged in.
2. **The sender name is SELF-REPORTED, not authenticated.** MeshCore puts the
   sending node's name in the text header. Fine for descriptive statistics,
   never for access control (that is the standing MA oracle finding).
3. **Channel rx only.** DMs and advertisements do not pass this disclosure, so
   this undercounts reach and cannot see nodes that only advertise.
4. **Journal-bounded.** Retention caps the window; rotation silently truncates
   the past. A short window is reported as a short window, not smoothed.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import statistics
import subprocess
import sys
from collections import Counter, defaultdict

# `... MeshCore channel rx idx=<i> name=<n> hops=<h> snr=<s> [keys=[...]] text='...'`
_LINE = re.compile(
    r"MeshCore channel rx idx=(?P<idx>\S+)"
    r"(?:\s+name=(?P<name>\S+))?"
    r"(?:\s+hops=(?P<hops>\S+))?"
    r"(?:\s+snr=(?P<snr>\S+))?"
)
_TS = re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")
_SENDER = re.compile(r"text='(?P<sender>[^:']{1,40}):")


def _journal(host: str | None, unit: str, since: str) -> str:
    """Read the daemon journal locally or over ssh. Read-only; never writes."""
    cmd = ["sudo", "journalctl", "-u", unit, "--no-pager", "-o", "short-iso",
           "--since", since]
    if host:
        # shlex.quote each arg: a bare " ".join turns --since "30 days ago"
        # into three arguments and journalctl rejects it.
        cmd = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host,
               " ".join(shlex.quote(c) for c in cmd)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(f"UNKNOWN: journal read timed out ({host or 'local'})", file=sys.stderr)
        return ""
    if p.returncode != 0:
        print(f"UNKNOWN: journal read failed rc={p.returncode}: "
              f"{p.stderr.strip()[:200]}", file=sys.stderr)
        return ""
    return p.stdout


def _parse(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if "MeshCore channel rx" not in line:
            continue
        m = _LINE.search(line)
        if not m:
            continue
        ts = _TS.search(line)
        snd = _SENDER.search(line)
        hops_raw = m.group("hops")
        snr_raw = m.group("snr")
        # 'direct' == 0 repeaters. '?' / absent == the wire named nothing:
        # UNKNOWN, never silently 0 (absent is not zero).
        if hops_raw == "direct":
            hops = 0
        elif hops_raw and hops_raw.isdigit():
            hops = int(hops_raw)
        else:
            hops = None
        try:
            snr = float(snr_raw) if snr_raw not in (None, "?") else None
        except ValueError:
            snr = None
        rows.append({
            "ts": ts.group("ts") if ts else None,
            "channel_idx": m.group("idx"),
            "channel_name": m.group("name"),
            "hops": hops,
            "snr": snr,
            "sender": snd.group("sender").strip() if snd else None,
            "reach_disclosed": hops_raw is not None,
        })
    return rows


def _summarize(rows: list[dict], own: set[str]) -> dict:
    def _is_own(r):
        s = (r.get("sender") or "").lower()
        return any(o in s for o in own) if s else False

    disclosed = [r for r in rows if r["reach_disclosed"]]
    community = [r for r in disclosed if not _is_own(r)]
    ours = [r for r in disclosed if _is_own(r)]

    def _stats(rs):
        snrs = [r["snr"] for r in rs if r["snr"] is not None]
        hops = [r["hops"] for r in rs if r["hops"] is not None]
        return {
            "n": len(rs),
            "hop_histogram": dict(sorted(Counter(hops).items())),
            "snr_min": min(snrs) if snrs else None,
            "snr_median": round(statistics.median(snrs), 2) if snrs else None,
            "snr_max": max(snrs) if snrs else None,
            "senders": sorted({r["sender"] for r in rs if r["sender"]}),
        }

    by_hop = defaultdict(list)
    for r in community:
        if r["hops"] is not None and r["snr"] is not None:
            by_hop[r["hops"]].append(r["snr"])

    stamps = [r["ts"] for r in rows if r["ts"]]
    return {
        "window": {"first": min(stamps) if stamps else None,
                   "last": max(stamps) if stamps else None},
        "rx_total": len(rows),
        "rx_with_reach": len(disclosed),
        "rx_without_reach": len(rows) - len(disclosed),
        "community": _stats(community),
        "own": _stats(ours),
        "community_snr_by_hop": {
            h: {"n": len(v), "median": round(statistics.median(v), 2),
                "min": min(v), "max": max(v)}
            for h, v in sorted(by_hop.items())
        },
        "by_channel": dict(Counter(
            f"{r['channel_idx']}:{r['channel_name'] or '?'}" for r in rows)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="meshanchor-server",
                    help="box hosting the MeshCore radio ('-' for local)")
    ap.add_argument("--unit", default="meshanchor-daemon")
    ap.add_argument("--since", default="30 days ago")
    ap.add_argument("--own", default="meshanchor",
                    help="comma-separated sender-name fragments that are OURS")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    host = None if args.host == "-" else args.host
    own = {o.strip().lower() for o in args.own.split(",") if o.strip()}

    rows = _parse(_journal(host, args.unit, args.since))
    if not rows:
        print("UNKNOWN: no 'MeshCore channel rx' lines in window — "
              "unreadable journal, wrong unit, or the disclosure predates "
              "MA 516ab8c5 (2026-09-18). Not 'no traffic'.")
        return 2

    s = _summarize(rows, own)
    if args.json:
        print(json.dumps(s, indent=2))
        return 0

    c, o = s["community"], s["own"]
    print(f"# MeshCore reach census — {args.host}:{args.unit}")
    print(f"window {s['window']['first']} .. {s['window']['last']}")
    print(f"{s['rx_total']} channel rx · {s['rx_with_reach']} carry reach · "
          f"{s['rx_without_reach']} predate the disclosure\n")

    print(f"## COMMUNITY  n={c['n']}   <- the number the bot decision needs")
    if c["n"]:
        print(f"   hops      {c['hop_histogram']}")
        print(f"   SNR       min {c['snr_min']} / median {c['snr_median']} / max {c['snr_max']}")
        print(f"   senders   {', '.join(c['senders']) or '(none named)'}")
        for h, v in s["community_snr_by_hop"].items():
            label = "direct" if h == 0 else f"{h} hops"
            print(f"     {label:<9} n={v['n']:<3} SNR median {v['median']:>6} "
                  f"(min {v['min']}, max {v['max']})")
    else:
        print("   none — every reach-bearing rx in this window was our own node.")

    print(f"\n## OURS (excluded above)  n={o['n']}")
    if o["n"]:
        print(f"   hops      {o['hop_histogram']}")
        print(f"   SNR       min {o['snr_min']} / median {o['snr_median']} / max {o['snr_max']}")
    print(f"\n## by channel  {s['by_channel']}")
    print("\n⚠️ sender names are self-reported, not authenticated · channel rx "
          "only (no DMs/adverts) · journal-bounded window")
    return 0


if __name__ == "__main__":
    sys.exit(main())

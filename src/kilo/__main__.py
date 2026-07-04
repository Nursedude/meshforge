"""Kilo CLI — status / discover / collect.

    PYTHONPATH=src python3 -m kilo status
    PYTHONPATH=src python3 -m kilo discover
    PYTHONPATH=src python3 -m kilo collect --seconds 120

Exit codes (cron_verdict-wireable): 0 = every OBSERVABLE registered node
is OK; 1 = at least one observable node DARK/DEGRADED/NEVER-SEEN; 2 =
registry/DB/collection error (could not verify — never counted as pass).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import List, Optional

from kilo.registry import KiloNode, load_registry, registry_path
from kilo.store import UNITS, db_path, latest_by_kilo, open_db, prune, \
    seen_unregistered

# A node is OK while its newest expected reading is younger than this many
# declared cadences (3 missed beacons = dark, the fleet's usual debounce).
DARK_AFTER_CADENCES = 3.0


def build_status(nodes: List[KiloNode], latest, now: Optional[float] = None
                 ) -> List[dict]:
    """Pure tri-state join: registry expectation vs newest observations.

    UNKNOWN (no ingest adapter for the node's anchors yet) is its own
    state — an unobservable node is never OK and never DARK (#2)."""
    now = time.time() if now is None else now
    out: List[dict] = []
    for n in nodes:
        row = {"kilo_id": n.kilo_id, "role": n.role, "location": n.location,
               "metrics": {}, "missing": [], "state": "UNKNOWN",
               "detail": ""}
        if not n.observable():
            row["detail"] = (f"no ingest adapter yet for anchors "
                             f"{sorted(n.ids)} — unobservable ≠ dark")
            out.append(row)
            continue
        fresh = stale = 0
        newest = None
        for metric in n.expected_metrics:
            hit = latest.get((n.kilo_id, metric))
            if hit is None:
                row["missing"].append(metric)
                continue
            ts, value = hit
            age = now - ts
            newest = ts if newest is None else max(newest, ts)
            ok = age <= DARK_AFTER_CADENCES * n.cadence_s
            row["metrics"][metric] = {
                "value": value, "unit": UNITS.get(metric, ""),
                "age_s": round(age, 1), "ok": ok}
            fresh += 1 if ok else 0
            stale += 0 if ok else 1
        if not n.expected_metrics:
            row["state"] = "OK" if latest_any(latest, n.kilo_id, now,
                                              n.cadence_s) else "NEVER"
            row["detail"] = "no expected_metrics declared — presence only"
        elif fresh and not stale and not row["missing"]:
            row["state"] = "OK"
        elif fresh or (newest is not None):
            row["state"] = "DEGRADED"
            row["detail"] = (f"{stale} stale, {len(row['missing'])} "
                             f"never-seen metric(s)")
        else:
            row["state"] = "NEVER"
            row["detail"] = "registered but no reading has ever landed"
        out.append(row)
    return out


def latest_any(latest, kilo_id: str, now: float, cadence_s: float) -> bool:
    ages = [now - ts for (kid, _m), (ts, _v) in latest.items()
            if kid == kilo_id]
    return bool(ages) and min(ages) <= DARK_AFTER_CADENCES * cadence_s


def _cmd_status(args) -> int:
    nodes, errors = load_registry(args.registry)
    if nodes is None:
        for e in errors:
            print(f"kilo: registry error: {e}", file=sys.stderr)
        return 2
    conn = open_db(args.db)
    try:
        prune(conn)
        rows = build_status(nodes, latest_by_kilo(conn))
        unreg = seen_unregistered(conn)
    finally:
        conn.close()
    if args.json:
        print(json.dumps({"nodes": rows, "unregistered": len(unreg)},
                         indent=2))
    else:
        glyph = {"OK": "🟢", "DEGRADED": "🟡", "NEVER": "🔴",
                 "UNKNOWN": "⚪"}
        print(f"kilo status — {len(rows)} registered node(s), "
              f"{len(unreg)} unregistered sender(s) heard "
              f"(see `kilo discover`)")
        for r in rows:
            print(f"  {glyph[r['state']]} {r['state']:<8} {r['kilo_id']} "
                  f"[{r['role']}] {r['location']}")
            for m, d in sorted(r["metrics"].items()):
                mark = "" if d["ok"] else "  ← STALE"
                print(f"        {m}={d['value']}{d['unit']} "
                      f"({d['age_s']:.0f}s ago){mark}")
            if r["missing"]:
                print(f"        never seen: {', '.join(r['missing'])}")
            if r["detail"]:
                print(f"        {r['detail']}")
    observable = [r for r in rows if r["state"] != "UNKNOWN"]
    return 0 if all(r["state"] == "OK" for r in observable) else 1


def _cmd_discover(args) -> int:
    conn = open_db(args.db)
    try:
        unreg = seen_unregistered(conn)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(unreg, indent=2))
        return 0
    if not unreg:
        print("kilo: no unregistered senders in the readings window")
        return 0
    print(f"kilo discover — {len(unreg)} unregistered sender(s); add the "
          f"real lab nodes to {registry_path()}:")
    now = time.time()
    for u in unreg:
        age = int(now - (u["last_ts"] or now))
        print(f"  {u['node_key']}  ({u['transport']}, {u['readings']} "
              f"readings, last {age}s ago)  metrics: "
              f"{', '.join(sorted(m for m in u['metrics'] if m))}")
    return 0


def _cmd_collect(args) -> int:
    from kilo.ingest import collect_mqtt
    nodes, errors = load_registry(args.registry)
    if nodes is None:
        # Collection without a registry is still useful (pure discovery),
        # but say so — never silently pretend the join happened.
        for e in errors:
            print(f"kilo: WARN {e} — collecting for discovery only",
                  file=sys.stderr)
        nodes = []
    overrides = {}
    if args.broker:
        overrides.update({"broker": args.broker, "use_tls": False})
    if args.port:
        overrides["port"] = args.port
    if args.root_topic:
        overrides["root_topic"] = args.root_topic
    if args.channel:
        overrides["channel"] = args.channel
    conn = open_db(args.db)
    try:
        prune(conn)
        summary = collect_mqtt(conn, nodes, seconds=args.seconds,
                               sample_every=args.sample_every,
                               config_overrides=overrides or None)
    finally:
        conn.close()
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="kilo", description="Kilo K0 — lab-node registry, telemetry "
                                 "ingest spine, tri-state presence status.")
    ap.add_argument("--registry", default=None,
                    help=f"registry path (default {registry_path()})")
    ap.add_argument("--db", default=None,
                    help=f"readings DB path (default {db_path()})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="registry vs observed, tri-state")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_status)
    p = sub.add_parser("discover", help="unregistered senders heard")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_discover)
    p = sub.add_parser("collect", help="bounded MQTT collection window")
    p.add_argument("--seconds", type=float, default=120.0)
    p.add_argument("--sample-every", type=float, default=15.0)
    p.add_argument("--broker", help="override broker (implies no TLS)")
    p.add_argument("--port", type=int)
    p.add_argument("--root-topic")
    p.add_argument("--channel")
    p.set_defaults(fn=_cmd_collect)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

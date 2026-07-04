"""Kilo CLI — status / discover / collect / matrix.

    PYTHONPATH=src python3 -m kilo status
    PYTHONPATH=src python3 -m kilo discover
    PYTHONPATH=src python3 -m kilo collect --seconds 120
    PYTHONPATH=src python3 -m kilo matrix --window-hours 24

Exit codes (cron_verdict-wireable): 0 = every OBSERVABLE registered node
is OK; 1 = at least one observable node DARK/DEGRADED/NEVER-SEEN; 2 =
registry/DB/collection error (could not verify — never counted as pass).
``matrix`` mirrors the scheme: 0 = no edge SHIFTED, 1 = at least one
edge SHIFTED beyond its own baseline band, 2 = error (SPARSE/DRIFTING
never fail — sparse is unknown, and drifting is watch-not-page).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import List, Optional, Tuple

from kilo.registry import OBSERVABLE_ANCHORS, KiloNode, load_registry, \
    observable_anchor_map, registry_path
from kilo.store import UNITS, db_path, latest_by_key, open_db, prune, \
    prune_edges, seen_keys

# A node is OK while its newest expected reading is younger than this many
# declared cadences (3 missed beacons = dark, the fleet's usual debounce).
DARK_AFTER_CADENCES = 3.0


def build_status(nodes: List[KiloNode], latest, now: Optional[float] = None
                 ) -> List[dict]:
    """Pure tri-state join: registry expectation vs newest observations.

    ``latest`` is keyed by (node_key.lower(), metric) — the join runs
    against the CURRENT anchors at read time (re-derive, never trust the
    ingest-time stamp), so a node registered after its first readings
    landed still owns its history. UNKNOWN (no ingest adapter for the
    node's anchors yet) is its own state — an unobservable node is never
    OK and never DARK (#2)."""
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
        anchors = [n.ids[k].lower() for k in OBSERVABLE_ANCHORS
                   if n.ids.get(k)]
        fresh = stale = 0
        newest = None
        for metric in n.expected_metrics:
            # a node may carry several observable anchors (meshtastic +
            # claw); the newest reading across ALL of its keys wins
            hits = [latest[(a, metric)] for a in anchors
                    if (a, metric) in latest]
            hit = max(hits, key=lambda h: h[0]) if hits else None
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
            row["state"] = "OK" if latest_any(latest, anchors, now,
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


def latest_any(latest, anchors: List[str], now: float,
               cadence_s: float) -> bool:
    ages = [now - ts for (key, _m), (ts, _v) in latest.items()
            if key in anchors]
    return bool(ages) and min(ages) <= DARK_AFTER_CADENCES * cadence_s


def split_seen(seen: List[dict], nodes: List[KiloNode]
               ) -> Tuple[List[dict], List[dict]]:
    """(registered, unregistered) senders, judged against the CURRENT
    anchors of every observable kind."""
    anchors = observable_anchor_map(nodes)
    reg = [s for s in seen if s["node_key"].lower() in anchors]
    unreg = [s for s in seen if s["node_key"].lower() not in anchors]
    return reg, unreg


def _cmd_status(args) -> int:
    nodes, errors = load_registry(args.registry)
    if nodes is None:
        for e in errors:
            print(f"kilo: registry error: {e}", file=sys.stderr)
        return 2
    conn = open_db(args.db)
    try:
        prune(conn)
        prune_edges(conn)
        rows = build_status(nodes, latest_by_key(conn))
        _reg, unreg = split_seen(seen_keys(conn), nodes)
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
    nodes, errors = load_registry(args.registry)
    if nodes is None:
        # No readable registry: show EVERY sender, and say why — a broken
        # registry must not silently reshape the discovery list.
        print(f"kilo: WARN {errors[0]} — showing all senders",
              file=sys.stderr)
        nodes = []
    conn = open_db(args.db)
    try:
        _reg, unreg = split_seen(seen_keys(conn), nodes)
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
    from kilo.ingest import collect_claw, collect_claw_all, collect_mqtt
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
    summary = {"ok": True, "mqtt": None, "claw": None}
    conn = open_db(args.db)
    try:
        prune(conn)
        prune_edges(conn)
        if args.transport in ("all", "claw"):
            # instant (file reads) — runs before the mqtt window. Default
            # ingests EVERY tick on the box (multi-claw, W5.1); an explicit
            # --claw-tick keeps the single-file override.
            if args.claw_tick:
                summary["claw"] = collect_claw(conn, nodes,
                                               tick_path=args.claw_tick)
            else:
                summary["claw"] = collect_claw_all(conn, nodes)
            summary["ok"] = summary["ok"] and summary["claw"]["ok"]
        if args.transport in ("all", "mqtt"):
            summary["mqtt"] = collect_mqtt(
                conn, nodes, seconds=args.seconds,
                sample_every=args.sample_every,
                config_overrides=overrides or None, edges=args.edges)
            summary["ok"] = summary["ok"] and summary["mqtt"]["ok"]
    finally:
        conn.close()
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 2


def _cmd_matrix(args) -> int:
    from kilo.edges import DRIFT_GLYPHS, DRIFT_MIN_BASELINE, \
        DRIFT_MIN_RECENT, build_matrix
    nodes, errors = load_registry(args.registry)
    if nodes is None:
        # The matrix is a VIEW — a broken registry costs the labels, not
        # the data; warn like discover does, never silently reshape.
        print(f"kilo: WARN {errors[0]} — showing raw node keys",
              file=sys.stderr)
        nodes = []
    conn = open_db(args.db)
    try:
        prune(conn)
        prune_edges(conn)
        m = build_matrix(conn, nodes, window_s=args.window_hours * 3600.0,
                         direct_only=not args.all_hops)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(m, indent=2))
    else:
        t = m["totals"]
        purity = "direct-only" if m["direct_only"] else \
            "ALL HOPS (snr is last-hop — impure for relayed rows)"
        print(f"kilo matrix — {len(m['receivers'])} receiver(s) × "
              f"{len(m['senders'])} sender(s), window "
              f"{args.window_hours:g}h, {purity}")
        print(f"  7d edges: {t['edges_total']} total = "
              f"{t['edges_direct']} direct + {t['edges_relayed']} relayed "
              f"+ {t['edges_unknown_hops']} unknown-hop "
              f"({t['edges_no_snr']} without snr)")
        if not m["cells"]:
            print("  no edges in the store yet — run `kilo collect` on a "
                  "box whose gateway uplinks json")
        for receiver in m["receivers"]:
            cells = [c for c in m["cells"] if c["receiver"] == receiver]
            label = cells[0]["receiver_label"]
            shown = f"{label} ({receiver})" if label != receiver else receiver
            print(f"  receiver {shown}:")
            for c in cells:
                d = c["drift"]
                snr = (f"{c['median_snr']:+.2f} dB"
                       if c["median_snr"] is not None else "no snr")
                if d["state"] == "SPARSE":
                    detail = (f"sparse: baseline {d['baseline_n']}/"
                              f"{DRIFT_MIN_BASELINE} recent "
                              f"{d['recent_n']}/{DRIFT_MIN_RECENT} — "
                              f"unknown, not fine")
                else:
                    detail = (f"{d['state']} dev {d['deviation_db']:+.2f} "
                              f"dB vs band ±{d['band_db']:.2f}")
                print(f"    {DRIFT_GLYPHS[d['state']]} "
                      f"{c['sender_label']:<20} ×{c['n']:<5} {snr:<10} "
                      f"{detail}")
    shifted = [c for c in m["cells"] if c["drift"]["state"] == "SHIFTED"]
    return 1 if shifted else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="kilo", description="Kilo — lab-node registry, telemetry "
                                 "ingest spine, tri-state presence status, "
                                 "link matrix.")
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
    p = sub.add_parser("collect", help="bounded collection window "
                                       "(mqtt window + claw tick read)")
    p.add_argument("--transport", choices=("all", "mqtt", "claw"),
                   default="all")
    p.add_argument("--seconds", type=float, default=120.0)
    p.add_argument("--sample-every", type=float, default=15.0)
    p.add_argument("--broker", help="override broker (implies no TLS)")
    p.add_argument("--port", type=int)
    p.add_argument("--root-topic")
    p.add_argument("--channel")
    p.add_argument("--claw-tick", help="override claw_last_tick.json path")
    p.add_argument("--edges", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="capture per-packet (receiver ← sender) edges "
                        "during the mqtt window (K1; default on)")
    p.set_defaults(fn=_cmd_collect)
    p = sub.add_parser("matrix", help="receivers × senders link matrix "
                                      "with per-edge baseline drift (K1)")
    p.add_argument("--window-hours", type=float, default=24.0,
                   help="recent window; older edges form the baseline")
    p.add_argument("--all-hops", action="store_true",
                   help="include relayed/unknown-hop edges (snr is "
                        "last-hop — impure; default is direct-only)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_matrix)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

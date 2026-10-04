#!/usr/bin/env python3
"""Measure whether DMs from this box's radio ARRIVE — not just whether they left.

Sends N want_ack DMs to each --dest through meshtasticd and classifies each
by who acknowledged it (utils.dm_delivery): DELIVERED only on an ack from the
destination itself. Our own radio acks every DM within ~20 ms, so a check
that counts any ack reads 100% while proving nothing.

Transmits on air, so it refuses by default: without --send it prints the
plan and exits. Run it knowingly.

  scripts/dm_delivery_check.py --dest '!5f01371f' --count 10            # plan only
  scripts/dm_delivery_check.py --dest '!5f01371f' --count 10 --send     # transmit
  ... --out deliveries.jsonl   # one JSON line per DM (send time, outcome) for later binning
"""

import argparse
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from utils.dm_delivery import (  # noqa: E402
    MIN_N_FOR_RATE, RoutingReply, Send, classify, summarize,
)
from gateway.ack_tracker import _node_num  # noqa: E402
from utils.safe_import import safe_import  # noqa: E402

pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')

BROADCAST = 0xFFFFFFFF


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dest", action="append", required=True,
                    help="destination node id, '!hex' (repeatable)")
    ap.add_argument("--count", type=int, default=10, help="DMs per destination (default 10)")
    ap.add_argument("--gap", type=float, default=20.0, help="seconds between sends (default 20)")
    ap.add_argument("--wait", type=float, default=60.0,
                    help="seconds to keep listening after the last send (default 60)")
    ap.add_argument("--out", help="append one JSON line per DM to this file")
    ap.add_argument("--send", action="store_true", help="actually transmit (default: plan only)")
    a = ap.parse_args(argv)
    bad = [d for d in a.dest if _node_num(d) in (None, BROADCAST) or not d.startswith("!")]
    if bad:
        ap.error(f"--dest must be a '!hex' node id, not broadcast: {bad}")
    if not (1 <= a.count <= 100):
        ap.error("--count must be 1..100")
    if a.gap < 5:
        ap.error("--gap below 5 s floods the channel this tool is measuring")
    return a


def run(a) -> int:
    from utils.connection_manager import MeshtasticConnection
    if not _HAS_PUBSUB:
        print("pubsub (meshtastic dependency) is not importable — cannot hear acks", file=sys.stderr)
        return 2
    replies, lock = [], threading.Lock()

    def on_rx(packet, interface=None):
        d = packet.get("decoded") or {}
        if d.get("portnum") != "ROUTING_APP":
            return
        err = (d.get("routing") or {}).get("errorReason", "NONE")
        with lock:
            replies.append(RoutingReply(time.time(), int(packet.get("from", 0)),
                                        int(d.get("requestId", 0) or 0), str(err)))

    sends = []
    pub.subscribe(on_rx, "meshtastic.receive")
    try:
        with MeshtasticConnection(connect=True, caller="dm_delivery_check") as iface:
            if not iface:
                print("could not connect to meshtasticd (busy or down) — nothing sent", file=sys.stderr)
                return 2
            me = iface.myInfo.my_node_num
            stop = threading.Event()
            for k in range(a.count):
                for dest in a.dest:
                    t = time.time()
                    p = iface.sendText(f"dm-check {k}", destinationId=dest, wantAck=True)
                    sends.append(Send(t=t, packet_id=int(p.id), dest=_node_num(dest)))
                    stop.wait(a.gap)
            stop.wait(a.wait)
    finally:
        pub.unsubscribe(on_rx, "meshtastic.receive")

    with lock:
        outcomes = classify(sends, list(replies))
    print(f"from {me:#010x}: {len(sends)} DM(s), listened {a.wait:.0f}s after the last")
    for dest in a.dest:
        dn = _node_num(dest)
        mine = [o for o in outcomes if o.send.dest == dn]
        s = summarize(mine)
        rate = (f"{s.rate:.0%}" if s.rate is not None
                else f"too small to judge (n<{MIN_N_FOR_RATE})")
        lat = f"{s.median_latency_s:.1f}s" if s.median_latency_s is not None else "-"
        print(f"  {dest}: delivered {s.counts['DELIVERED']}/{s.sent} = {rate}; "
              f"failed {s.counts['FAILED']}, left-only {s.counts['LEFT_ONLY']}, "
              f"no witness {s.counts['NO_WITNESS']}; median ack {lat}")
    if a.out:
        with open(a.out, "a") as f:
            for o in outcomes:
                f.write(json.dumps({"t": o.send.t, "from": me, "dest": o.send.dest,
                                    "packet_id": o.send.packet_id, "status": o.status,
                                    "latency_s": o.latency_s, "reason": o.reason,
                                    "other_acks": o.other_acks}) + "\n")
    return 0


def main(argv=None) -> int:
    a = parse_args(argv)
    total = a.count * len(a.dest)
    print(f"plan: {total} want_ack DM(s) to {', '.join(a.dest)}, every {a.gap:.0f}s, "
          f"~{(total * a.gap + a.wait) / 60:.1f} min")
    if not a.send:
        print("plan only — add --send to transmit")
        return 0
    return run(a)


if __name__ == "__main__":
    sys.exit(main())

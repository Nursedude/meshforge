#!/usr/bin/env python3
"""BIC-2 leg-kill drill — the scripted RNS→Meshtastic sender + its reconcile.

Acceptance (review_provenance 2026-10-25, operator-OK'd): "On moc, a scripted
RNS→Meshtastic sender at one message per 2 min keeps its OWN send log. Stopping
meshtasticd for 30 min makes Dashboard → Delivery legs show, within N = 20 min
of the stop, an ``rns>meshtastic`` 15m window whose ``from_ts`` is AFTER the
stop with ``sent`` = 0 and ``queued`` or ``failed`` > 0; ``sent`` resumes
within 20 min of the restart. Every message in the sender's log ends sent or
failed in the gateway record — none unaccounted. A no-kill control run at the
same hour on another day, same sender, shows sent ≈ queued."

This script is the SENDER and the "none unaccounted" check. It does NOT stop
meshtasticd and does NOT read the pane — those stay the operator's acts and
the operator's eyes. Run it ON the gateway box (moc) as the operator:

    python3 scripts/bic2_leg_drill_sender.py hash          # print + allowlist
    python3 scripts/bic2_leg_drill_sender.py send --minutes 70
    python3 scripts/bic2_leg_drill_sender.py mark meshtasticd_stop   # 2nd shell
    python3 scripts/bic2_leg_drill_sender.py mark meshtasticd_start
    python3 scripts/bic2_leg_drill_sender.py reconcile <run.jsonl>

Identity: a dedicated drill identity at
``~/.config/meshforge/bic2_drill_identity`` (0600, created on first run). moc
ENFORCES its RNS→RF allowlist, so its LXMF source hash must be added to
``rns.bridge_source_identities`` in the gateway's gateway.json (+ a gateway
restart, + a name in the LXMF identity registry) BEFORE a run — that is an
operator act; ``send`` refuses while the hash is unlisted under enforce.

⚠️ Every message is bridged onto the real mesh (moc's RNS→Meshtastic channel).
At the default 1 per 2 min a 70-minute run puts ~35 short lines on air.

What ``sent`` is OF (BELIEVED until the drill): a queue row reaching
``delivered`` = meshtasticd ACCEPTED it over HTTP, never "it went out on RF".

Reconcile exit: 0 = every logged message terminal in the gateway queue
(sent or failed); 1 = something unaccounted, or the sender itself failed a
hand-off (a drill whose sender failed proves nothing); 2 = UNKNOWN (rows
still open, empty log, or the queue DB unreadable) — never a pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.paths import get_real_user_home  # noqa: E402

DEFAULT_INTERVAL_S = 120.0   # acceptance: one message per 2 min
PATH_WAIT_S = 10.0
SENT_STATUSES = frozenset({"delivered"})
FAILED_STATUSES = frozenset({"dead_letter", "failed"})
# pending / in_progress / anything unknown = open: never terminal.


def _cfg_dir() -> Path:
    return get_real_user_home() / ".config" / "meshforge"


def _log_dir() -> Path:
    return get_real_user_home() / ".local" / "share" / "meshforge" / "bic2_drill"


# ── pure parts (tested) ─────────────────────────────────────────────────────

def make_token(run_id: str, seq: int) -> str:
    return f"bic2-{run_id}-{seq:03d}"


def message_text(token: str) -> str:
    return f"BIC2 drill {token} (test, ignore)"


def _token_re(token: str) -> "re.Pattern[str]":
    # a trailing digit would make seq 100 match inside seq 1000
    return re.compile(re.escape(token) + r"(?![0-9])")


def allowlist_preflight(source_hex: str, identities: List[str],
                        policy: str) -> Tuple[bool, str]:
    """May the gateway bridge this sender onto RF? (ok, sentence)."""
    ids = {i.lower() for i in identities}
    if policy != "enforce":
        return True, f"RNS→RF policy={policy}: sender admitted"
    if source_hex.lower() in ids:
        return True, "RNS→RF policy=enforce: drill identity is listed"
    return False, (
        f"RNS→RF policy=enforce and the drill identity {source_hex} is NOT in "
        f"rns.bridge_source_identities — every message would be refused and "
        f"the drill would measure the allowlist, not the leg. Operator: add "
        f"{source_hex} to gateway.json rns.bridge_source_identities, name it "
        f"in the LXMF identity registry, restart meshforge-gateway, re-run.")


def classify(statuses: Iterable[str]) -> str:
    """One logged message's fate from its queue row status(es)."""
    st = list(statuses)
    if not st:
        return "unaccounted"
    if any(s not in SENT_STATUSES and s not in FAILED_STATUSES for s in st):
        return "open"
    if any(s in FAILED_STATUSES for s in st):
        return "failed"
    return "sent"


def append_log(path: Path, event: dict) -> None:
    """One JSON line, fsync'd — the sender's log must survive a kill."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_log(path: Path) -> List[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _queue_rows(db_path: Path) -> List[Tuple[str, str, str, str]]:
    """(destination, status, updated_at, payload) — read-only, never writes."""
    from utils.db_helpers import connect_tuned
    if not Path(db_path).is_file():
        raise FileNotFoundError(str(db_path))
    conn = connect_tuned(f"file:{db_path}?mode=ro", uri=True)
    try:
        return list(conn.execute(
            "SELECT destination, status, updated_at, payload FROM messages"))
    finally:
        conn.close()


def reconcile(log_path: Path, db_path: Path) -> dict:
    """Match every logged send to its rns>meshtastic queue row(s)."""
    counts = {"sent": 0, "failed": 0, "open": 0, "unaccounted": 0}
    rep = {"counts": counts, "sender_errors": 0, "messages": [],
           "marks": [], "error": None, "exit": 2}
    events = read_log(log_path)
    sends = [e for e in events if e.get("event") == "send"]
    rep["marks"] = [e for e in events if e.get("event") == "mark"]
    if not sends:
        rep["error"] = "no send events in the log"
        return rep
    try:
        rows = _queue_rows(db_path)
    except Exception as e:  # noqa: BLE001 — unreadable is UNKNOWN, not loss
        rep["error"] = f"queue DB unreadable ({db_path}): {e}"
        return rep

    for s in sends:
        entry = {"seq": s.get("seq"), "token": s.get("token"),
                 "ts": s.get("ts"), "handoff": s.get("handoff")}
        if s.get("handoff") != "handed":
            rep["sender_errors"] += 1
            entry["verdict"] = "sender_error"
            rep["messages"].append(entry)
            continue
        pat = _token_re(s["token"])
        src = (s.get("source") or "").lower()
        statuses, updated = [], []
        for dest, status, upd, payload in rows:
            if dest != "meshtastic":
                continue
            try:
                p = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if (str(p.get("source_id") or "").lower() == src
                    and pat.search(str(p.get("message") or ""))):
                statuses.append(status)
                updated.append(upd)
        entry["verdict"] = classify(statuses)
        entry["queue_status"] = statuses
        entry["updated_at"] = max(updated) if updated else None
        counts[entry["verdict"]] += 1
        rep["messages"].append(entry)

    if counts["unaccounted"] or rep["sender_errors"]:
        rep["exit"] = 1
    elif counts["open"]:
        rep["exit"] = 2
    else:
        rep["exit"] = 0
    return rep


# ── live parts (RNS / LXMF) ────────────────────────────────────────────────

def _load_identity():
    import RNS
    p = _cfg_dir() / "bic2_drill_identity"
    if p.exists():
        ident = RNS.Identity.from_file(str(p))
        if ident is None:
            raise SystemExit(f"FATAL: could not load {p}")
        return ident
    ident = RNS.Identity()
    p.parent.mkdir(parents=True, exist_ok=True)
    old = os.umask(0o077)   # a PRIVATE KEY: never world-readable, not even briefly
    try:
        ident.to_file(str(p))
    finally:
        os.umask(old)
    p.chmod(0o600)
    return ident


def _source_hex(ident) -> str:
    import RNS
    return RNS.Destination.hash(ident, "lxmf", "delivery").hex()


def _local_gateway_dest() -> Optional[str]:
    import RNS
    p = _cfg_dir() / "gateway_identity"
    gw = RNS.Identity.from_file(str(p)) if p.exists() else None
    return RNS.Destination.hash(gw, "lxmf", "delivery").hex() if gw else None


def _local_allowlist() -> Tuple[Optional[List[str]], Optional[str], str]:
    """(ids, policy, note) from THIS box's gateway config."""
    try:
        from gateway.config import GatewayConfig
        from gateway.rns_ingress_policy import effective_policy
        cfg = GatewayConfig.load()
        ids = list(cfg.rns.get_bridge_source_identities())
        declared = getattr(cfg.rns, "bridge_source_policy", None)
        return ids, effective_policy(
            ids, declared if isinstance(declared, str) else None), "local"
    except Exception as e:  # noqa: BLE001
        return None, None, f"gateway config unreadable: {e}"


def cmd_hash(_args) -> int:
    src = _source_hex(_load_identity())
    print(f"bic2 drill LXMF source hash: {src}")
    ids, pol, note = _local_allowlist()
    if ids is None:
        print(f"allowlist: UNKNOWN ({note})")
        return 2
    ok, why = allowlist_preflight(src, ids, pol)
    print(("allowlist: OK — " if ok else "allowlist: REFUSED — ") + why)
    return 0 if ok else 1


def cmd_mark(args) -> int:
    path = Path(args.log) if args.log else _latest_log()
    if path is None:
        print("no drill log found — pass --log")
        return 2
    append_log(path, {"event": "mark", "what": args.what, "ts": time.time()})
    print(f"marked {args.what} at {time.strftime('%H:%M:%S')} in {path}")
    return 0


def _latest_log() -> Optional[Path]:
    logs = sorted(_log_dir().glob("*.jsonl"))
    return logs[-1] if logs else None


def cmd_send(args) -> int:
    import RNS
    ident = _load_identity()
    src_hex = _source_hex(ident)
    dest_hex = args.to or _local_gateway_dest()
    if not dest_hex:
        print("FATAL: no --to and no local gateway identity")
        return 2
    ids, pol, note = _local_allowlist()
    if ids is None:
        print(f"REFUSED: allowlist UNKNOWN ({note}) — run on the gateway box")
        return 2
    ok, why = allowlist_preflight(src_hex, ids, pol)
    print(why)
    if not ok:
        return 1

    run_id = args.run_id or time.strftime("%Y%m%dT%H%M%S")
    log = _log_dir() / f"{run_id}.jsonl"
    total = args.count or max(1, int(args.minutes * 60 // args.interval) + 1)

    from utils.paths import ReticulumPaths
    from utils.rns_init import open_reticulum
    import LXMF
    if open_reticulum(ReticulumPaths.ensure_rns_client_configdir()) is None:
        print("FATAL: RNS unavailable/degraded (open_reticulum returned None)")
        return 2
    store = get_real_user_home() / ".local" / "share" / "meshforge" / "bic2_drill_lxmf"
    store.mkdir(parents=True, exist_ok=True)
    router = LXMF.LXMRouter(storagepath=str(store))
    src = router.register_delivery_identity(ident, display_name="MF BIC2 drill")

    dest_hash = bytes.fromhex(dest_hex)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    if not RNS.Transport.has_path(dest_hash):
        RNS.Transport.request_path(dest_hash)
        deadline = time.monotonic() + PATH_WAIT_S
        while not RNS.Transport.has_path(dest_hash) and time.monotonic() < deadline:
            stop.wait(0.1)
    dest_ident = RNS.Identity.recall(dest_hash)
    if dest_ident is None:
        print(f"FATAL: no path/identity for gateway {dest_hex}")
        return 2
    dest = RNS.Destination(dest_ident, RNS.Destination.OUT,
                           RNS.Destination.SINGLE, "lxmf", "delivery")

    append_log(log, {"event": "start", "run_id": run_id, "source": src_hex,
                     "dest": dest_hex, "interval_s": args.interval,
                     "planned": total, "host": socket.gethostname(),
                     "ts": time.time()})
    print(f"run {run_id}: {total} msg every {args.interval:.0f}s → {dest_hex}")
    print(f"log: {log}")

    def _lxmf_cb(seq, state):
        return lambda m: append_log(log, {"event": "lxmf", "seq": seq,
                                          "state": state, "ts": time.time()})

    seq = 0
    t0 = time.monotonic()
    while seq < total and not stop.is_set():
        seq += 1
        token = make_token(run_id, seq)
        ev = {"event": "send", "seq": seq, "token": token, "source": src_hex,
              "ts": time.time()}
        try:
            lxm = LXMF.LXMessage(dest, src, message_text(token).encode("utf-8"),
                                 desired_method=LXMF.LXMessage.DIRECT)
            lxm.register_delivery_callback(_lxmf_cb(seq, "delivered"))
            lxm.register_failed_callback(_lxmf_cb(seq, "failed"))
            router.handle_outbound(lxm)
            ev["handoff"] = "handed"
        except Exception as e:  # noqa: BLE001 — logged, the reconcile counts it
            ev["handoff"] = "error"
            ev["error"] = f"{type(e).__name__}: {e}"
        append_log(log, ev)
        print(f"[{seq}/{total}] {time.strftime('%H:%M:%S')} {token} {ev['handoff']}")
        # fixed cadence from t0, so a slow hand-off never stretches the run
        stop.wait(max(0.0, t0 + seq * args.interval - time.monotonic()))

    stop.wait(args.grace)   # let the last LXMF receipts land in the log
    append_log(log, {"event": "end", "sent": seq, "interrupted": stop.is_set(),
                     "ts": time.time()})
    print(f"done: {seq} logged. Reconcile once the queue settles:\n"
          f"  python3 {Path(__file__).name} reconcile {log}")
    return 0


def cmd_reconcile(args) -> int:
    db = Path(args.db) if args.db else _cfg_dir() / "message_queue.db"
    rep = reconcile(Path(args.log), db)
    if args.json:
        print(json.dumps(rep, indent=1, sort_keys=True))
        return rep["exit"]
    if rep["error"]:
        print(f"UNKNOWN: {rep['error']}")
    timeline = sorted(
        [(m["ts"], f"#{m['seq']:<3} {m['verdict']:<12} {m.get('queue_status')}")
         for m in rep["messages"]]
        + [(k["ts"], f"---- MARK {k['what']}") for k in rep["marks"]])
    for ts, line in timeline:
        print(f"{time.strftime('%H:%M:%S', time.localtime(ts))}  {line}")
    c = rep["counts"]
    verdict = {0: "PASS — none unaccounted", 1: "FAIL",
               2: "UNKNOWN — not a pass"}[rep["exit"]]
    print(f"\nsent={c['sent']} failed={c['failed']} open={c['open']} "
          f"unaccounted={c['unaccounted']} sender_errors={rep['sender_errors']}"
          f"  → {verdict}")
    print("('sent' = meshtasticd accepted it; RF transmission is not observed here)")
    return rep["exit"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hash", help="print the drill hash + allowlist verdict")
    s = sub.add_parser("send", help="run the paced sender")
    s.add_argument("--to", help="gateway LXMF hash (default: this box's)")
    s.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    s.add_argument("--minutes", type=float, default=70.0)
    s.add_argument("--count", type=int, help="overrides --minutes")
    s.add_argument("--run-id")
    s.add_argument("--grace", type=float, default=30.0)
    m = sub.add_parser("mark", help="stamp an operator act into the log")
    m.add_argument("what", help="e.g. meshtasticd_stop / meshtasticd_start")
    m.add_argument("--log")
    r = sub.add_parser("reconcile", help="sender log vs gateway queue")
    r.add_argument("log")
    r.add_argument("--db", help="queue DB (default: ~/.config/meshforge/message_queue.db)")
    r.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "send" and args.interval <= 0:
        ap.error("--interval must be > 0")
    return {"hash": cmd_hash, "send": cmd_send, "mark": cmd_mark,
            "reconcile": cmd_reconcile}[args.cmd](args)


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    # RNS/LXMF leave non-daemon threads behind; the log is fsync'd per line
    os._exit(rc)

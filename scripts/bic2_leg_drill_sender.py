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

⚠️ Every message is bridged onto the real mesh (moc's RNS→Meshtastic channel)
AND relayed to every peer gateway in ``rns.peer_gateway_destinations``: a
drill message is an LXMF ORIGINAL from a non-peer source, so the gateway's
``_maybe_relay_to_peers`` forwards it, and each peer bridges it onto ITS RF.
There is no legitimate sender-side opt-out (the only thing the gateway skips
on is the ``meshforge_relayed_by`` loop marker — setting it would be the
drill impersonating a peer). Airtime = messages × (1 + peers); ``hash`` and
``send`` print the true figure and name the peers (moc 2026-10-25: 2 peers →
a 70-minute run is ~36 messages × 3 ≈ 108 lines across three gateways).

What ``sent`` is OF (BELIEVED until the drill): a queue row reaching
``delivered`` = meshtasticd ACCEPTED it over HTTP, never "it went out on RF".

The queue is the gateway's record ONLY in ``bridge_mode=mqtt_bridge``: the
direct-send path leaves no row on success and requeues a failed chunk with
``source_id ''``. So ``send`` refuses any other mode, the ``start`` event
records the mode, and a missing row reads as LOSS only when that record says
``mqtt_bridge`` — otherwise (or once the queue's 1-day purge may have taken
the row) it is UNKNOWN. ``send`` also refuses when gateway.json changed after
the running meshforge-gateway started: the allowlist/mode on disk is not the
one the live gateway loaded. ``--to`` must name THIS box's gateway: preflight,
the start record and reconcile all read this box's config and queue.

A run is "in progress" (the one ``mark`` targets) while the pid + kernel start
time its ``start`` event recorded is still alive — a SIGKILLed sender never
writes ``end``, so a missing ``end`` alone means nothing.

Reconcile exit: 0 = every logged message terminal in THIS gateway's queue
(sent or failed); 1 = something unaccounted, or the sender itself failed a
hand-off (a drill whose sender failed proves nothing); 2 = UNKNOWN (rows
still open, rows unobservable, empty/damaged/missing log, a skipped torn
line, a run with no ``end`` event, or the queue DB unreadable) — never a pass. Peer gateways' relayed copies are NOT reconciled.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import socket
import subprocess
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
# The only bridge_mode in which _rns_bridge_xform enqueues RNS→Mesh WITH the
# sender's source_id (the direct path leaves no row on success).
QUEUE_RECORD_MODE = "mqtt_bridge"
# MessageQueue auto-cleanup runs purge_old(days=1) on delivered/dead_letter
# rows; past this age a missing row may be purged, not lost (wall-clock: an
# NTP step can skew it — it only ever turns FAIL into UNKNOWN).
PURGE_SAFE_S = 20 * 3600
GATEWAY_UNIT = "meshforge-gateway.service"
# Written by append_log when the file it appends to ends mid-line (a kill
# mid-write): the line before it is torn and read_log may skip it.
REPAIR_EVENT = "repair_torn_line"


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
    # no run-id character may follow: seq 100 must not match inside seq
    # 1000, nor run "r1" seq 2 inside run "r1-002x" seq 1 (a loss read as
    # sent); the charset is run_id_check's
    return re.compile(re.escape(token) + r"(?![A-Za-z0-9._-])")


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


def live_config_check(config_mtime: Optional[float], props: Optional[dict],
                      now_wall: float, now_mono: float) -> Tuple[str, str]:
    """Did the RUNNING gateway load the gateway.json we just read?

    ("ok" | "stale" | "unknown", sentence). The gateway reads gateway.json at
    start only, so a file changed after the unit's last activation is not
    what the live process enforces. Start time is systemd's monotonic stamp,
    mapped to wall time with THIS process's clocks; the file mtime is wall
    time, so an NTP step between the edit and now skews the comparison.
    """
    if props is None or config_mtime is None:
        return "unknown", (f"could not read {GATEWAY_UNIT}'s state or the "
                           f"config mtime — cannot tell what the running "
                           f"gateway loaded")
    state = props.get("ActiveState", "")
    if state != "active":
        return "stale", (f"{GATEWAY_UNIT} is {state or 'unknown'}, not active "
                         f"— there is no running gateway to drill")
    raw = props.get("ActiveEnterTimestampMonotonic", "")
    if not raw.isdigit() or raw == "0":
        return "unknown", f"{GATEWAY_UNIT} start time unreadable ({raw!r})"
    started = now_wall - (now_mono - int(raw) / 1e6)
    if config_mtime > started:
        return "stale", (
            f"gateway.json changed {config_mtime - started:.0f}s AFTER the "
            f"running {GATEWAY_UNIT} started — its allowlist/bridge_mode are "
            f"not what the live gateway enforces. Operator: restart "
            f"meshforge-gateway first, then re-run.")
    return "ok", (f"running {GATEWAY_UNIT} started after gateway.json's last "
                  f"change — it loaded this config")


def gateway_preflight(src_hex: str, gw: dict,
                      live: Tuple[str, str]) -> Tuple[int, List[str]]:
    """May a drill run against this gateway? (rc, lines): 0 go, 1 refused
    (a known-bad state), 2 UNKNOWN (could not observe)."""
    if gw.get("error"):
        return 2, [f"REFUSED: gateway config UNKNOWN ({gw['error']}) — run "
                   f"on the gateway box"]
    mode = gw.get("bridge_mode")
    if mode != QUEUE_RECORD_MODE:
        return 1, [f"REFUSED: bridge_mode={mode} — the gateway queues "
                   f"RNS→Mesh (the record reconcile reads) only in "
                   f"bridge_mode={QUEUE_RECORD_MODE}; the direct path leaves "
                   f"no row, so every message would read unaccounted"]
    ok, why = allowlist_preflight(src_hex, gw.get("ids") or [],
                                  gw.get("policy"))
    lines = [why]
    if not ok:
        return 1, lines
    verdict, live_why = live
    lines.append(live_why)
    if verdict == "unknown":
        return 2, lines
    if verdict != "ok":
        return 1, lines
    return 0, lines


def target_check(to: Optional[str], local: Optional[str]
                 ) -> Tuple[int, Optional[str], str]:
    """(rc, dest, sentence). The drill may only target THIS box's gateway:
    the preflight, the start record and reconcile all read this box's
    gateway.json and queue, so a remote ``--to`` would be judged and
    reconciled against the wrong gateway."""
    if not local:
        return 2, None, ("no local gateway identity "
                         "(~/.config/meshforge/gateway_identity) — run the "
                         "drill ON the gateway box")
    if to and to.lower() != local.lower():
        return 1, None, (f"--to {to} is not THIS box's gateway ({local}): "
                         f"preflight, the start record and reconcile all read "
                         f"this box's config and queue — run the drill on "
                         f"the target gateway box instead")
    return 0, local.lower(), ""


def _relay_targets(peers: Iterable, own_hex: Optional[str]) -> List[str]:
    """The entries ``_maybe_relay_to_peers`` would actually send to: 32-hex,
    not this gateway's own hash. Duplicates are kept — so does the loop."""
    own = (own_hex or "").lower()
    out = []
    for p in peers or []:
        if not isinstance(p, str) or len(p) != 32 or p.lower() == own:
            continue
        try:
            bytes.fromhex(p)
        except ValueError:
            continue
        out.append(p)
    return out


def airtime_estimate(n_msgs: int, peers: Iterable,
                     own_hex: Optional[str]) -> Tuple[int, List[str], str]:
    """(multiplier, relay targets, sentence): every message goes on this
    gateway's RF AND is relayed to each peer gateway, which bridges it onto
    its own RF (an upper bound: a peer may refuse or dedup it)."""
    used = _relay_targets(peers, own_hex)
    mult = 1 + len(used)
    names = ", ".join(h[:8] for h in used) or "none"
    if not own_hex and used:
        # the gateway relays NOTHING without its own LXMF hash, and an own
        # entry in the peer list cannot be excluded — so a range, not a point
        return mult, used, (
            f"airtime: own gateway identity unreadable — between {n_msgs} "
            f"and {n_msgs * mult} RF lines (this gateway alone, up to + "
            f"{len(used)} peer relay(s): {names})")
    return mult, used, (
        f"airtime: {n_msgs} message(s) × {mult} gateway(s) = up to "
        f"{n_msgs * mult} RF lines (this gateway + {len(used)} peer "
        f"relay(s): {names})")


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
    """One JSON line, fsync'd — the sender's log must survive a kill. If the
    file ends mid-line (a kill mid-write), terminate it and write a repair
    record first, so the torn line is not fused with this one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = json.dumps(event, sort_keys=True) + "\n"
    with open(path, "a+b") as f:
        # mark (another process) and the sender both append: the tail check
        # and the write must not interleave
        fcntl.flock(f, fcntl.LOCK_EX)
        if f.seek(0, os.SEEK_END):
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                out = "\n" + json.dumps({"event": REPAIR_EVENT,
                                         "ts": time.time()}) + "\n" + out
        f.write(out.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


class DrillLogError(Exception):
    """The log cannot be trusted as a whole — reconcile must say UNKNOWN."""


def _parse_line(raw: bytes) -> dict:
    """Strict: invalid UTF-8 is damage, never a replacement character that
    would turn a token into a non-matching one (a false 'unaccounted')."""
    ev = json.loads(raw.decode("utf-8"))
    if not isinstance(ev, dict):
        raise ValueError("not a JSON object")
    return ev


def read_log(path: Path) -> Tuple[List[dict], Optional[str]]:
    """(events, note). A damaged line is tolerated, and named in ``note``,
    only where a kill mid-write explains it: the FINAL line, or a line
    followed by append_log's repair record. A missing file or a damaged line
    anywhere else raises DrillLogError."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise DrillLogError(f"drill log unreadable ({path}): {e}") from e
    rows = [(n, ln.strip()) for n, ln in enumerate(data.split(b"\n"), 1)
            if ln.strip()]
    out, notes = [], []
    for k, (n, line) in enumerate(rows):
        try:
            ev = _parse_line(line)
        except ValueError as e:   # UnicodeDecodeError is a ValueError
            if k == len(rows) - 1:
                notes.append(f"final line {n} torn ({e}); ignored — if it "
                             f"was a send, that one message is not reconciled")
                break
            try:
                repaired = _parse_line(rows[k + 1][1]).get("event") == REPAIR_EVENT
            except ValueError:
                repaired = False
            if repaired:
                notes.append(f"line {n} torn by a kill mid-write and repaired "
                             f"by the next append; ignored — if it was a "
                             f"send, that one message is not reconciled")
                continue
            raise DrillLogError(
                f"drill log line {n} of {path} is corrupt ({e}) and is not "
                f"the last line — the log cannot be trusted") from e
        out.append(ev)
    return out, ("; ".join(notes) or None)


def _queue_rows(db_path: Path) -> List[Tuple[str, str, str, str]]:
    """(destination, status, updated_at, payload) — read-only, never writes."""
    from utils.db_helpers import connect_tuned
    if not Path(db_path).is_file():
        raise FileNotFoundError(str(db_path))
    # as_uri() quotes ? and # — unquoted, sqlite cuts the URI there, drops
    # mode=ro and CREATES the truncated path
    conn = connect_tuned(Path(db_path).resolve().as_uri() + "?mode=ro",
                         uri=True)
    try:
        return list(conn.execute(
            "SELECT destination, status, updated_at, payload FROM messages"))
    finally:
        conn.close()


def reconcile(log_path: Path, db_path: Path,
              now: Optional[float] = None) -> dict:
    """Match every logged send to its rns>meshtastic queue row(s) in THIS
    gateway's queue. A missing row is LOSS only when the run's ``start``
    event says the queue was the record (``bridge_mode=mqtt_bridge``) and
    the row is too young to have been purged; otherwise it is UNKNOWN."""
    now = time.time() if now is None else now
    counts = {"sent": 0, "failed": 0, "open": 0, "unaccounted": 0,
              "unobservable": 0}
    rep = {"counts": counts, "sender_errors": 0, "messages": [],
           "marks": [], "error": None, "log_note": None, "run_note": None,
           "unobservable_note": None, "peers": [],
           "exit": 2}
    try:
        events, rep["log_note"] = read_log(log_path)
    except DrillLogError as e:
        rep["error"] = str(e)
        return rep
    starts = [e for e in events if e.get("event") == "start"]
    if len(starts) > 1:
        # a reused --run-id: tokens repeat across runs and each run has its
        # own bridge_mode, so no send can be matched to one record
        rep["error"] = (f"log holds {len(starts)} start events (a reused "
                        f"--run-id) — tokens collide across runs; reconcile "
                        f"one run per log")
        return rep
    mode = starts[-1].get("bridge_mode") if starts else None
    rep["bridge_mode"] = mode
    rep["peers"] = list(starts[-1].get("peers") or []) if starts else []
    rep["run_note"] = (None if any(e.get("event") == "end" for e in events)
                       else "no end event: the sender is still running or "
                            "was killed — sends after its last log line are "
                            "not in this record")
    record_ok = mode == QUEUE_RECORD_MODE
    # LXMF's delivery proof comes from the GATEWAY's process: the only
    # evidence it took the message. ``failed`` = the sender's link never
    # established (no path, a wedged RNS client) — the gateway got nothing.
    lxmf = [e for e in events if e.get("event") == "lxmf"]
    delivered = {e.get("seq") for e in lxmf if e.get("state") == "delivered"}
    lxmf_failed = {e.get("seq") for e in lxmf if e.get("state") == "failed"}
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
        verdict = classify(statuses)
        if verdict == "unaccounted":
            why = _absence_unobservable(record_ok, mode, s.get("ts"), now)
            if not why and s.get("seq") not in delivered:
                why = ("LXMF reported this send FAILED — the sender's RNS "
                       "leg never handed it to the gateway"
                       if s.get("seq") in lxmf_failed else
                       "no LXMF delivery receipt — the gateway's receipt of "
                       "it is unobserved (sender killed, or --grace too "
                       "short for LXMF's retries)")
            if why:
                verdict = "unobservable"
                entry["why"] = why
        entry["verdict"] = verdict
        entry["queue_status"] = statuses
        entry["updated_at"] = max(updated) if updated else None
        counts[verdict] += 1
        rep["messages"].append(entry)

    if counts["unaccounted"] or rep["sender_errors"]:
        rep["exit"] = 1
    elif counts["open"] or counts["unobservable"]:
        rep["exit"] = 2
    elif rep["log_note"] or rep["run_note"]:
        # the record is incomplete: a skipped torn line may have been a
        # handed send, and a run with no end may have sent past its log
        rep["exit"] = 2
        rep["error"] = ("the sender's record is incomplete — "
                        + "; ".join(n for n in (rep["log_note"],
                                                rep["run_note"]) if n)
                        + " — every LOGGED message is terminal, but that is "
                          "not 'none unaccounted'")
    else:
        rep["exit"] = 0
    if counts["unobservable"]:
        whys = sorted({m["why"] for m in rep["messages"] if m.get("why")})
        rep["unobservable_note"] = (
            f"{counts['unobservable']} message(s) have no queue row and the "
            f"absence cannot be read as loss: " + "; ".join(whys))
    # ``error`` is the reason the verdict is UNKNOWN; a proven loss (FAIL)
    # outranks the open and unobservable sends, so they are not its reason
    if rep["exit"] == 2 and not rep["error"]:
        rep["error"] = "; ".join(r for r in (
            f"{counts['open']} message(s) still open in the queue "
            f"(pending/in_progress) — reconcile again once it settles"
            if counts["open"] else None,
            rep["unobservable_note"]) if r) or None
    return rep


def _absence_unobservable(record_ok: bool, mode, ts, now: float
                          ) -> Optional[str]:
    """Why a missing queue row is NOT evidence of loss, or None when it is.
    The purge window is wall-clock on an RTC-less Pi, so a send stamped in
    the future (clock stepped back) or with no stamp is UNKNOWN."""
    if not record_ok:
        return (f"bridge_mode={mode}: the queue is not the gateway's record "
                f"(only {QUEUE_RECORD_MODE})")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return "send has no timestamp — its age vs the purge window is unknown"
    age = now - float(ts)
    if age < 0:
        return (f"send stamped {-age:.0f}s in the future — the clock went "
                f"backward, its age vs the purge window is unknown")
    if age > PURGE_SAFE_S:
        return "older than the queue's 1-day purge window"
    return None


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


def _local_gateway() -> dict:
    """THIS box's gateway.json as the gateway would load it. A missing or
    unparsable file is an ``error`` — GatewayConfig.load() would hand back
    DEFAULTS (open policy, mqtt_bridge) and the preflight would pass on a
    config nobody wrote."""
    try:
        from gateway.config import GatewayConfig
        from gateway.rns_ingress_policy import effective_policy
        path = GatewayConfig.get_config_path()
        if not path.is_file():
            return {"error": f"{path} absent — not a configured gateway box"}
        mtime = path.stat().st_mtime
        cfg = GatewayConfig.load()
        if cfg.load_error:
            return {"error": f"{path} failed to load ({cfg.load_error})"}
        ids = list(cfg.rns.get_bridge_source_identities())
        declared = getattr(cfg.rns, "bridge_source_policy", None)
        return {"error": None, "ids": ids, "mtime": mtime,
                "policy": effective_policy(
                    ids, declared if isinstance(declared, str) else None),
                "bridge_mode": cfg.bridge_mode,
                "peers": list(cfg.rns.get_peer_gateway_destinations())}
    except Exception as e:  # noqa: BLE001 — reported as UNKNOWN by the caller
        return {"error": f"gateway config unreadable: {e}"}


def pick_unit_scope(system: Optional[dict], user: Optional[dict]
                    ) -> Tuple[Optional[str], str]:
    """Which systemd manager runs the gateway — ("system"|"user", note) or
    (None, why); a non-empty note names what the scope could not rule out. Scope is per UNIT and per box, so it is asked, not assumed:
    the wrong manager answers ``inactive`` for a unit that is running."""
    if system is None:
        return None, f"could not ask the system manager about {GATEWAY_UNIT}"
    if user is None:
        # plain root (no SUDO_USER, cron, systemd-run) has no user bus to
        # ask; a system unit that answered "loaded" is the gateway, and the
        # one thing left unseen is said, not hidden (5th read #1)
        if (system.get("LoadState") == "loaded"
                and system.get("ActiveState") == "active"):
            return "system", (f"the user manager could not be asked — a "
                              f"second, user-scope {GATEWAY_UNIT} is not "
                              f"ruled out")
        return None, f"could not ask the user manager about {GATEWAY_UNIT}"
    loaded = [name for name, p in (("system", system), ("user", user))
              if p.get("LoadState") == "loaded"]
    if len(loaded) == 1:
        return loaded[0], ""
    if len(loaded) == 2:
        return None, (f"{GATEWAY_UNIT} is installed in BOTH the system and "
                      f"the user manager — which one runs the gateway is "
                      f"ambiguous")
    return None, f"{GATEWAY_UNIT} is not installed in either manager"


def _unit_show(user: bool) -> Optional[dict]:
    """``systemctl [--user] show`` of the gateway unit via service_check's
    read-only argv builder, or None when that manager could not be asked."""
    from utils.service_check import _systemctl_query_argv
    try:
        out = subprocess.run(
            _systemctl_query_argv(
                ["show", "-p", "LoadState", "-p", "ActiveState",
                 "-p", "ActiveEnterTimestampMonotonic", GATEWAY_UNIT],
                user=user),
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    props = dict(ln.split("=", 1) for ln in out.stdout.splitlines()
                 if "=" in ln)
    return props or None


def _live(gw: dict) -> Tuple[str, str]:
    shows = {"system": _unit_show(False), "user": _unit_show(True)}
    scope, why = pick_unit_scope(shows["system"], shows["user"])
    if scope is None:
        return "unknown", why
    verdict, sentence = live_config_check(gw.get("mtime"), shows[scope],
                                          time.time(), time.monotonic())
    return verdict, (f"{sentence} ({why})" if why else sentence)


def _preflight(src_hex: str) -> Tuple[int, List[str], dict]:
    gw = _local_gateway()
    if gw.get("error"):
        return (*gateway_preflight(src_hex, gw, ("unknown", "")), gw)
    rc, lines = gateway_preflight(src_hex, gw, _live(gw))
    return rc, lines, gw


def cmd_hash(_args) -> int:
    src = _source_hex(_load_identity())
    print(f"bic2 drill LXMF source hash: {src}")
    rc, lines, gw = _preflight(src)
    tag = {0: "OK", 1: "REFUSED", 2: "UNKNOWN"}[rc]
    for ln in lines:
        print(f"preflight: {tag} — {ln}")
    if not gw.get("error"):
        default_n = int(70.0 * 60 // DEFAULT_INTERVAL_S) + 1
        print("default 70-min run " + airtime_estimate(
            default_n, gw.get("peers"), _local_gateway_dest())[2])
    return rc


def cmd_mark(args) -> int:
    if args.log:
        path, why = Path(args.log), ""
    else:
        path, why = pick_mark_log(_log_dir())
    if path is None:
        print(f"REFUSED: {why}")
        return 2
    append_log(path, {"event": "mark", "what": args.what, "ts": time.time()})
    print(f"marked {args.what} at {time.strftime('%H:%M:%S')} in {path}")
    return 0


def _proc_starttime(pid: int) -> Optional[str]:
    """Kernel start time (clock ticks since boot, /proc/<pid>/stat field 22)
    — with the pid, it names ONE process: a reused pid has another."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = stat.rsplit(")", 1)[-1].split()   # comm may hold spaces/parens
    return fields[19] if len(fields) > 19 else None


def _valid_run_ref(pid, starttime) -> bool:
    """The one rule for what a ``start`` event's process reference must be."""
    return (isinstance(pid, int) and not isinstance(pid, bool)
            and isinstance(starttime, str) and bool(starttime))


def _run_alive(pid, starttime) -> bool:
    return _valid_run_ref(pid, starttime) and _proc_starttime(pid) == starttime


def self_run_ref(starttime_of=_proc_starttime) -> Tuple[bool, str]:
    """(ok, this process's start time | why). Without it ``mark`` can never
    find the run, so send refuses rather than write an unfindable one."""
    st = starttime_of(os.getpid())
    if not st:
        return False, ("cannot read this process's start time from /proc — "
                       "`mark` could never find the run; refusing")
    return True, st


def _first_event(p: Path) -> Optional[dict]:
    try:
        with open(p, "rb") as f:
            return _parse_line(f.readline())
    except (OSError, ValueError):
        return None


def pick_mark_log(log_dir: Path, alive=_run_alive
                  ) -> Tuple[Optional[Path], str]:
    """The ONE run whose sender is still alive (the pid + kernel start time
    its ``start`` event recorded), never "the newest filename" (``--run-id``
    names sort anywhere) and never "no ``end``" (a SIGKILLed sender never
    writes one). Reads only each log's first line. 0 or >1 live runs:
    refuse and ask for --log."""
    open_runs, skipped = [], []
    for p in sorted(Path(log_dir).glob("*.jsonl")):
        first = _first_event(p)
        if first is None:
            skipped.append(p.name)
            continue
        pid, started = first.get("pid"), first.get("pid_start")
        if (first.get("event") == "start" and _valid_run_ref(pid, started)
                and alive(pid, started)):
            open_runs.append(p)
    note = f" (unreadable, not judged: {', '.join(skipped)})" if skipped else ""
    if len(open_runs) == 1:
        return open_runs[0], ""
    if not open_runs:
        return None, (f"no run in progress under {log_dir}{note} — pass "
                      f"--log <run.jsonl>")
    return None, (f"{len(open_runs)} runs in progress "
                  f"({', '.join(p.name for p in open_runs)}){note} — pass "
                  f"--log <run.jsonl>")


_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def run_id_check(run_id: str) -> Tuple[bool, str]:
    """A run id names ONE file in the log dir — no separator, no ``..``, no
    leading dot — or the run lands where ``mark`` never looks."""
    if _RUN_ID_RE.fullmatch(run_id or "") and ".." not in run_id:
        return True, ""
    return False, (f"--run-id {run_id!r} must be 1-64 of [A-Za-z0-9._-], "
                   f"start with a letter or digit, and hold no '..'")


def _run_id_arg(value: str) -> str:
    ok, why = run_id_check(value)
    if not ok:
        raise argparse.ArgumentTypeError(why)
    return value


def fresh_run_log(log: Path) -> Tuple[bool, str]:
    """Claim the run log EXCLUSIVELY (O_EXCL, at the check — two senders
    started in the same second must not share one). A run log holds ONE
    run: tokens are run-id + seq, so a reused run id collides in reconcile."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "x"):
            pass
    except FileExistsError:
        return False, (f"{log} already exists — a reused --run-id would mix "
                       f"two runs' tokens in one log; pick a new --run-id")
    return True, ""


def wait_grace(seconds: float) -> None:
    """Let the last LXMF receipts land. NOT the run's stop event: on Ctrl-C
    that is already set, and skipping the wait sends ``end`` before the
    final handed message has left — read later as a gateway loss."""
    threading.Event().wait(max(0.0, seconds))


def _release_unused(log: Path) -> None:
    """Drop a claimed run log that never got its start event."""
    try:
        if log.stat().st_size == 0:
            log.unlink()
    except OSError:
        pass


def cmd_send(args) -> int:
    ident = _load_identity()
    src_hex = _source_hex(ident)
    local_dest = _local_gateway_dest()
    rc, dest_hex, why = target_check(args.to, local_dest)
    if rc:
        print(f"REFUSED: {why}")
        return rc
    rc, lines, gw = _preflight(src_hex)
    for ln in lines:
        print(ln)
    if rc:
        return rc

    ok, pid_start = self_run_ref()
    if not ok:
        print(f"REFUSED: {pid_start}")
        return 2
    run_id = args.run_id or time.strftime("%Y%m%dT%H%M%S")
    ok, why = run_id_check(run_id)
    if not ok:
        print(f"REFUSED: {why}")
        return 2
    log = _log_dir() / f"{run_id}.jsonl"
    ok, why = fresh_run_log(log)
    if not ok:
        print(f"REFUSED: {why}")
        return 1
    try:
        return _run_send(args, log, run_id, ident, src_hex, local_dest,
                         dest_hex, gw, pid_start)
    finally:
        # any exit before the start event (exception, Ctrl-C during RNS
        # setup) must not leave an empty claim refusing this --run-id
        _release_unused(log)


def _run_send(args, log, run_id, ident, src_hex, local_dest, dest_hex, gw,
              pid_start) -> int:
    import RNS
    total = args.count or max(1, int(args.minutes * 60 // args.interval) + 1)
    _, relay_to, air = airtime_estimate(total, gw.get("peers"), local_dest)
    print(air)

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
                     "bridge_mode": gw.get("bridge_mode"),
                     "peers": relay_to, "pid": os.getpid(),
                     "pid_start": pid_start,
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

    print(f"waiting {args.grace:.0f}s for the last LXMF receipts")
    wait_grace(args.grace)
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
    c = rep["counts"]
    if rep["exit"] == 1:   # the operator reads the top line: lead with it
        rest = "".join(f", {k}={c[k]}" for k in ("open", "unobservable")
                       if c[k])
        print(f"FAIL: {c['unaccounted']} proven loss(es), "
              f"{rep['sender_errors']} sender error(s){rest}")
    if rep["error"]:
        print(f"UNKNOWN: {rep['error']}")
    if (rep.get("unobservable_note")
            and rep["unobservable_note"] not in (rep["error"] or "")):
        print(f"unobservable: {rep['unobservable_note']}")
    if rep.get("log_note"):
        print(f"log: {rep['log_note']}")
    if rep.get("run_note"):
        print(f"run: {rep['run_note']}")
    def _ts(v):   # a send may carry no stamp (R5): never crash, never invent
        return float(v) if isinstance(v, (int, float)) and not isinstance(
            v, bool) else None

    timeline = sorted(
        [(_ts(m.get("ts")),
          f"#{m['seq']!s:<3} {m['verdict']:<12} {m.get('queue_status')}")
         for m in rep["messages"]]
        + [(_ts(k.get("ts")), f"---- MARK {k.get('what')}")
           for k in rep["marks"]],
        key=lambda r: (r[0] is None, r[0] or 0.0))
    for ts, line in timeline:
        when = ("--:--:--" if ts is None
                else time.strftime('%H:%M:%S', time.localtime(ts)))
        print(f"{when}  {line}")
    verdict = {0: "PASS — none unaccounted", 1: "FAIL",
               2: "UNKNOWN — not a pass"}[rep["exit"]]
    print(f"\nsent={c['sent']} failed={c['failed']} open={c['open']} "
          f"unaccounted={c['unaccounted']} unobservable={c['unobservable']} "
          f"sender_errors={rep['sender_errors']}  → {verdict}")
    print("('sent' = meshtasticd accepted it; RF transmission is not observed here)")
    peers = rep.get("peers") or []
    print(f"scope: THIS gateway's queue only — the {len(peers)} peer relay(s)"
          f"{' (' + ', '.join(p[:8] for p in peers) + ')' if peers else ''} "
          f"each put these messages on another gateway's RF and are NOT "
          f"reconciled here")
    if c["unaccounted"]:
        print("unaccounted = LXMF proved the gateway took it, yet no row in "
              "this queue under mqtt_bridge: a drop "
              "(journal: 'dropped under queue pressure'), or a gateway that "
              "ran without its persistent queue — direct sends log "
              "'Bridge RNS→Mesh:' without '(queued'; or LXMF ingress "
              "dropped it after proving delivery (journal: 'ignored already "
              "received', an invalid stamp, or an exception in the delivery "
              "callback)")
    return rep["exit"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hash", help="print the drill hash + allowlist verdict")
    s = sub.add_parser("send", help="run the paced sender")
    s.add_argument("--to", help="gateway LXMF hash — must be THIS box's "
                   "(the default); a remote gateway is refused")
    s.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    s.add_argument("--minutes", type=float, default=70.0)
    s.add_argument("--count", type=int, help="overrides --minutes")
    s.add_argument("--run-id", type=_run_id_arg)
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

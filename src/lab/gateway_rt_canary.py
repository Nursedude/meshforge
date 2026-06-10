"""Gateway round-trip canary — one directed PING through the LIVE gateway.

The soak instrument for the reliable-communication-path arc (2026-06-09):
on a schedule, prove the gateway's whole confirmable pipeline end-to-end
instead of waiting for organic traffic ("silence is the failure mode").

One fire =
  leg 1 (hard assert)  enqueue ONE directed message into the running
      gateway's persistent queue (``destination="rns"``, a single
      ``destination_hash`` — never the broadcast fan-out, so no operator
      inboxes are touched). The gateway's own worker dispatches it through
      ``_queue_send_rns`` — circuit-breaker gate, bounded RPC, LXMF send —
      and the receiving peer's LXMF stack returns a real delivery receipt,
      which the gateway records as CONFIRMED in delivery_counters. We poll
      the counters DB for OUR msg_id reaching a terminal state.
  leg 2 (round-trip assert)  the body is a lab PING (``_lab_common``
      wire format), so the peer's ``lxmf_echo`` responder ACKs back to the
      gateway's LXMF source; the gateway bridges that inbound to mesh by
      enqueueing a ``destination="meshtastic"`` row whose text carries our
      unique ``ACK seq=<seq> orig=<sender>``. We poll the queue DB for it.
      Leg 2 failure with leg 1 confirmed = CONCERN (outbound pipeline
      healthy; echo peer or return path broken), not FAIL.

Cross-process safety: both DBs are WAL + busy_timeout (``connect_tuned``)
and the gateway's queue worker polls ``get_pending`` from the DB every
~1s, so an externally-inserted pending row is dispatched like any other
(verified against ``message_queue.py::process_once``).

Honest failure modes (.claude/rules/honest_failure_modes.md):
  - every error path yields a DISTINCT verdict reason — "counters DB
    unreadable" is never reported as "not confirmed";
  - unobservable is not OK: a read error on either DB fails the leg
    loudly instead of silently passing or silently timing out;
  - elapsed times use a monotonic clock;
  - the verdict line on stdout is the witness the cron wrapper records
    via cron_verdict.sh (no silent exits — every path prints VERDICT).

Run from ``src/``:  python3 -m lab.gateway_rt_canary --peer meshanchor-server
Wrapper + cron wiring: ``scripts/gateway_rt_canary.sh``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from lab._lab_common import make_ping_body, short_name

DEFAULT_PEER = "meshanchor-server"
DEFAULT_LEG1_TIMEOUT_S = 120.0
DEFAULT_LEG2_TIMEOUT_S = 120.0
POLL_INTERVAL_S = 2.0

# Terminal states in delivery_counters for one msg_id. Anything else
# ("queued", "sent") is in-flight and we keep waiting.
_TERMINAL_CONFIRMED = "confirmed"
_TERMINAL_DROPPED = "dropped"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CONCERN = 2


@dataclass
class CanaryResult:
    verdict: str          # "OK" | "CONCERN" | "FAIL"
    reason: str
    seq: Optional[int] = None
    msg_id: Optional[str] = None
    confirm_s: Optional[float] = None
    ack_s: Optional[float] = None

    @property
    def exit_code(self) -> int:
        return {"OK": EXIT_OK, "CONCERN": EXIT_CONCERN}.get(
            self.verdict, EXIT_FAIL)


# ---------------------------------------------------------------- helpers


def resolve_target(peer_name: str, peers) -> Optional[str]:
    """Find peer_name in the lab_peers rows; return its 32-hex hash."""
    for peer in peers:
        if peer.name == peer_name and len(peer.hash_hex) == 32:
            return peer.hash_hex
    return None


def ack_like_pattern(seq: int, sender: str) -> str:
    """SQL LIKE pattern matching the echo's ACK for OUR ping, as it
    appears inside the queue row's JSON payload (the bridged text may
    carry a leading ``[RNS:xxxx]`` tag; %...% absorbs it)."""
    return f"%ACK seq={seq} orig={sender}%"


def poll_counters_terminal(
    counters_db: Path, msg_id: str, timeout_s: float,
) -> Tuple[str, Optional[str], float]:
    """Poll delivery_counters events for msg_id reaching a terminal state.

    Returns (outcome, detail, elapsed_s) where outcome is one of
    "confirmed" | "dropped" | "timeout" | "error". Unreadable DB is
    "error" — NEVER folded into "timeout" (unobservable != not-delivered).
    """
    from utils.db_helpers import connect_tuned

    start = time.monotonic()
    deadline = start + timeout_s
    while True:
        try:
            with connect_tuned(counters_db) as conn:
                row = conn.execute(
                    "SELECT state, drop_reason FROM events"
                    " WHERE id = ? AND state IN (?, ?)"
                    " ORDER BY ts DESC LIMIT 1",
                    (msg_id, _TERMINAL_CONFIRMED, _TERMINAL_DROPPED),
                ).fetchone()
        except Exception as e:
            return "error", f"counters DB unreadable: {e}", time.monotonic() - start
        if row is not None:
            state, drop_reason = row[0], row[1]
            if state == _TERMINAL_CONFIRMED:
                return "confirmed", None, time.monotonic() - start
            return "dropped", drop_reason or "unknown", time.monotonic() - start
        if time.monotonic() >= deadline:
            return "timeout", None, time.monotonic() - start
        time.sleep(POLL_INTERVAL_S)


def poll_ack_row(
    queue_db: Path, seq: int, sender: str, timeout_s: float,
) -> Tuple[str, Optional[str], float]:
    """Poll the persistent queue for the bridged-back echo ACK row.

    Returns (outcome, detail, elapsed_s): "found" | "timeout" | "error".
    """
    from utils.db_helpers import connect_tuned

    pattern = ack_like_pattern(seq, sender)
    start = time.monotonic()
    deadline = start + timeout_s
    while True:
        try:
            with connect_tuned(queue_db) as conn:
                row = conn.execute(
                    "SELECT id FROM messages"
                    " WHERE destination = 'meshtastic' AND payload LIKE ?"
                    " LIMIT 1",
                    (pattern,),
                ).fetchone()
        except Exception as e:
            return "error", f"queue DB unreadable: {e}", time.monotonic() - start
        if row is not None:
            return "found", row[0], time.monotonic() - start
        if time.monotonic() >= deadline:
            return "timeout", None, time.monotonic() - start
        time.sleep(POLL_INTERVAL_S)


def classify(
    leg1: Tuple[str, Optional[str], float],
    leg2: Optional[Tuple[str, Optional[str], float]],
    seq: int,
    msg_id: str,
) -> CanaryResult:
    """Map the two leg outcomes onto one honest verdict.

    leg2 is None when leg 1 already failed (we never ran it)."""
    outcome1, detail1, elapsed1 = leg1
    if outcome1 == "dropped":
        return CanaryResult("FAIL", f"leg1 dropped: {detail1}",
                            seq=seq, msg_id=msg_id)
    if outcome1 == "timeout":
        return CanaryResult(
            "FAIL", f"leg1 not confirmed within {elapsed1:.0f}s",
            seq=seq, msg_id=msg_id)
    if outcome1 == "error":
        return CanaryResult("FAIL", f"leg1 {detail1}", seq=seq, msg_id=msg_id)

    # leg 1 confirmed
    if leg2 is None:  # defensive — caller always runs leg 2 after confirm
        return CanaryResult("CONCERN", "leg2 not attempted",
                            seq=seq, msg_id=msg_id, confirm_s=elapsed1)
    outcome2, detail2, elapsed2 = leg2
    if outcome2 == "found":
        return CanaryResult(
            "OK",
            f"seq={seq} confirmed={elapsed1:.1f}s ack_back={elapsed2:.1f}s",
            seq=seq, msg_id=msg_id, confirm_s=elapsed1, ack_s=elapsed2)
    if outcome2 == "error":
        return CanaryResult(
            "CONCERN", f"confirmed={elapsed1:.1f}s but leg2 {detail2}",
            seq=seq, msg_id=msg_id, confirm_s=elapsed1)
    return CanaryResult(
        "CONCERN",
        f"confirmed={elapsed1:.1f}s but no ACK bridged back within"
        f" {elapsed2:.0f}s (echo peer or return path)",
        seq=seq, msg_id=msg_id, confirm_s=elapsed1)


# ---------------------------------------------------------------- main


def run_canary(args) -> CanaryResult:
    from lab.lxmf_tracer import load_peers

    # Preflight: the gateway worker is the thing that dispatches our row.
    # Fail fast with the true reason instead of a vague 2-minute timeout.
    if not args.skip_service_check:
        from utils.service_check import check_service
        status = check_service("meshforge-gateway")
        if not status.available:
            return CanaryResult(
                "FAIL", f"meshforge-gateway not available: {status.message}")

    peers_path = Path(args.peers_file) if args.peers_file else None
    target = resolve_target(args.peer, load_peers(peers_path))
    if target is None:
        return CanaryResult(
            "FAIL", f"peer '{args.peer}' not found in lab_peers")

    from gateway.message_queue import PersistentMessageQueue
    from gateway.message_queue_models import MessagePriority

    seq = int(time.time())
    sender = f"canary-{short_name()}"
    queue = PersistentMessageQueue(db_path=args.queue_db)
    msg_id = queue.enqueue(
        payload={
            "message": make_ping_body(seq, sender),
            "destination_hash": target,
        },
        destination="rns",
        priority=MessagePriority.NORMAL,
    )
    if not msg_id:
        # enqueue's two falsy causes: dedup (impossible — seq is unique
        # per fire) or queue rejection. Either way the ping never entered
        # the pipeline; report it as its own failure, not a timeout.
        return CanaryResult(
            "FAIL", "enqueue refused (queue full or dedup)", seq=seq)

    counters_db = (Path(args.counters_db) if args.counters_db
                   else _default_counters_db())
    queue_db = Path(args.queue_db) if args.queue_db else Path(queue._db_path)

    leg1 = poll_counters_terminal(counters_db, msg_id, args.leg1_timeout)
    leg2 = None
    if leg1[0] == "confirmed":
        leg2 = poll_ack_row(queue_db, seq, sender, args.leg2_timeout)
    return classify(leg1, leg2, seq, msg_id)


def _default_counters_db() -> Path:
    from gateway.delivery_counters import default_db_path
    return default_db_path()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--peer", default=DEFAULT_PEER,
                        help="lab_peers name of the echo peer to ping")
    parser.add_argument("--peers-file", default=None,
                        help="override lab_peers path (test seam)")
    parser.add_argument("--queue-db", default=None,
                        help="override message_queue.db path (test seam)")
    parser.add_argument("--counters-db", default=None,
                        help="override delivery_counters.db path (test seam)")
    parser.add_argument("--leg1-timeout", type=float,
                        default=DEFAULT_LEG1_TIMEOUT_S)
    parser.add_argument("--leg2-timeout", type=float,
                        default=DEFAULT_LEG2_TIMEOUT_S)
    parser.add_argument("--skip-service-check", action="store_true",
                        help="skip the meshforge-gateway preflight (lab)")
    args = parser.parse_args(argv)

    result = run_canary(args)
    print(json.dumps({
        "seq": result.seq, "msg_id": result.msg_id,
        "confirm_s": result.confirm_s, "ack_s": result.ack_s,
    }))
    # The witness line the wrapper parses — keep the shape stable.
    print(f"VERDICT {result.verdict} {result.reason}")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())

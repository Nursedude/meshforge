"""Gateway RESOURCE round-trip canary — the gateway-reliability arc A1.

The OUTCOME-based source of truth (2026-06-20). The existing single-packet
``gateway_rt_canary`` proves a directed PING confirms and a small ACK bridges
back — but BOTH directions fit in one RNS packet, so it never forces an RNS
**Resource**. The 2026-06-20 wx-total-loss (``[Errno 30] EROFS`` writing an
assembled Resource under ``/etc/reticulum/storage/resources/`` — the #60
sandbox class) broke ONLY the multi-chunk Resource-assembly path: single-packet
replies kept working, the gateway read "active / RNS: connected", and every
liveness/shape probe stayed green. Enumerating failure shapes is a treadmill;
this proves the gateway DOES ITS JOB on the path that actually broke.

One fire sends TWO messages to the same echo peer, so a resource failure is
DIAGNOSTIC rather than ambiguous:

  control  ``PING seq=S from=rescanary-<host>``     (single packet, the proven
                                                     non-resource path)
  resource ``PINGBIG seq=S from=rescanary-<host> want=<bytes>`` — the echo
           replies with a RESOURCE-sized ``ACKBIG`` body, which LXMF delivers
           as an RNS Resource over the link, so the canary's own gateway must
           ASSEMBLE an inbound Resource (the EROFS step) to bridge it back.

Legs:
  leg 1 (FAIL on miss)  the PINGBIG outbound reaches CONFIRMED in
      ``delivery_counters`` — proves the peer RECEIVED the trigger (queue →
      circuit gate → bounded RPC → LXMF send → receipt). With leg 1 confirmed,
      any resource-return failure is on the RETURN/assembly side, not outbound.
  leg 2 (the discriminator)  poll the persistent queue for the two bridged-back
      rows: the control ``ACK`` and the resource ``ACKBIG`` (matched by their
      unique ``seq``/``orig`` marker — RNS Resources are all-or-nothing, so the
      marker bridged back PROVES the whole Resource assembled). Verdict:
        control back + resource back        → OK
        control back + resource MISSING     → FAIL  ← the 2026-06-20 EROFS
                                                       signature: single-packet
                                                       works, Resource doesn't
        resource back + control MISSING     → CONCERN (transient single-packet
                                                       loss; resource path fine)
        neither back                        → CONCERN (echo peer / return path
                                                       down — not resource-
                                                       specific; outbound was
                                                       confirmed)

Each fire writes ``<state_dir>/last.json`` (atomic) — the verdict envelope the
``probe_resource_canary_degraded`` watchdog probe consumes (FAIL/CONCERN, or a
stale file = the canary stopped → silence is the failure mode). It also prints
one ``VERDICT`` line, the witness shape the wrapper records.

Honest failure modes (.claude/rules/honest_failure_modes.md):
  - every degraded path yields a DISTINCT reason — a DB read error is never
    folded into "not arrived" (unobservable != not-delivered);
  - leg 2 DB unreadable after leg 1 confirmed is CONCERN, not a false FAIL;
  - elapsed times use a monotonic clock; the envelope ts is wall-clock (this is
    a normal process, not a resumable workflow);
  - no silent exit — every path writes the envelope AND prints VERDICT.

Run from ``src/``:
  python3 -m lab.gateway_resource_canary --peer meshanchor-server
Wrapper + timer wiring: ``scripts/gateway_resource_canary.sh`` +
``templates/systemd/meshforge-gateway-resource-canary-user.{service,timer}``.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Tuple

from lab._lab_common import make_bigping_body, make_ping_body, short_name
# Reuse the pure, test-pinned helpers from the single-packet canary rather than
# re-implementing (and risking divergence from) its counters/peers contracts.
from lab.gateway_rt_canary import poll_counters_terminal, resolve_target

DEFAULT_PEER = "meshanchor-server"
DEFAULT_LEG1_TIMEOUT_S = 120.0
DEFAULT_LEG2_TIMEOUT_S = 150.0
# Default reply size — KEEP AT 512; do NOT raise (see CEILING below).
# LXMF delivers a >319-byte body (LINK_PACKET_MAX_CONTENT) as an RNS Resource
# over the link, so 512 forces the gateway to ASSEMBLE an inbound Resource and
# WRITE it to its RNS resourcepath — the #60 EROFS step. VERIFIED 2026-06-27
# (strace, live moc gateway): a 512 ACKBIG IS written to
# ``<resourcepath>/<hash>`` (openat O_CREAT|O_APPEND), read back, then unlinked
# — exactly Resource.assemble(). So 512 already exercises the on-disk path,
# REFUTING the earlier "stays in RAM" belief. (The all-'.' body bz2-compresses
# to ~130 B → a SINGLE-part Resource; part count is irrelevant to the EROFS
# class — assemble() writes to disk for ANY Resource.)
#
# NOTE the resourcepath is the gateway's RUNTIME ``RNS.Reticulum.resourcepath``
# (whichever configdir wins the process-singleton init), verified 2026-06-27 to
# be ``/tmp/meshforge_rns_client/storage/resources`` (PrivateTmp), NOT
# ``/etc/reticulum/storage/resources``. Use ``scripts/gateway_resourcepath.sh``
# to discover the live path (the validation drill injects EROFS there).
#
# CEILING (VERIFIED 2026-06-27): do NOT raise above ~512. Larger replies still
# assemble fine, but the RNS→Mesh bridge drops them before the meshtastic queue
# the canary polls (1024/2048 reproduced a false ``resource_back=false`` FAIL
# that is a mesh-bridge/chunk artifact, NOT a resource failure). A bigger reply
# makes A1 cry wolf; the on-disk EROFS class is fully exercised at 512.
DEFAULT_REPLY_BYTES = 512
POLL_INTERVAL_S = 2.0

# Shared with probe_resource_canary_degraded (the reader) and the wrapper's
# STATE_DIR default — test-pinned together (honest_failure_modes #5: two
# consumers of one path WILL drift unless pinned). Keep these two literals in
# lockstep with utils.watchdog_probes_gateway and scripts/gateway_resource_canary.sh.
STATE_DIR_PARENT = "meshforge"
STATE_DIR_LEAF = "gateway_resource_canary"
ENVELOPE_NAME = "last.json"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CONCERN = 2


@dataclass
class ResourceCanaryResult:
    verdict: str          # "OK" | "CONCERN" | "FAIL"
    reason: str
    seq: Optional[int] = None
    msg_id: Optional[str] = None       # the PINGBIG outbound id (leg 1)
    confirm_s: Optional[float] = None
    control_back: Optional[bool] = None
    resource_back: Optional[bool] = None
    reply_bytes: Optional[int] = None
    rtt_s: Optional[float] = None       # leg-2 elapsed (returns)
    peer: Optional[str] = None
    host: Optional[str] = None
    ts: Optional[float] = None          # wall-clock of this fire

    @property
    def exit_code(self) -> int:
        return {"OK": EXIT_OK, "CONCERN": EXIT_CONCERN}.get(
            self.verdict, EXIT_FAIL)


# ---------------------------------------------------------------- helpers


def control_like_pattern(seq: int, sender: str) -> str:
    """SQL LIKE pattern for the bridged-back CONTROL ACK (single packet).

    ``%ACK seq=N orig=X%`` — the ``%`` absorbs any leading ``[RNS:xxxx]``
    bridge tag. Note "ACK seq=" is NOT a substring of "ACKBIG seq=" (the "BIG"
    separates them), so this never matches the resource reply.
    """
    return f"%ACK seq={seq} orig={sender}%"


def resource_like_pattern(seq: int, sender: str) -> str:
    """SQL LIKE pattern for the bridged-back RESOURCE ACKBIG."""
    return f"%ACKBIG seq={seq} orig={sender}%"


def default_state_dir() -> str:
    """``~/.local/state/meshforge/gateway_resource_canary`` (XDG-aware, sudo-safe).

    The canary runs as the operator, so ``get_real_user_home`` resolves the
    operator home even if invoked under sudo. The probe resolves the SAME
    directory from the operator UID (it runs as sandboxed root). Pinned by
    ``TestStateDirContract``.
    """
    from utils.paths import get_real_user_home

    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        str(get_real_user_home()), ".local", "state")
    return os.path.join(base, STATE_DIR_PARENT, STATE_DIR_LEAF)


def poll_both_rows(
    queue_db: Path, control_pat: str, resource_pat: str, timeout_s: float,
) -> Tuple[bool, bool, Optional[str], float]:
    """Poll the persistent queue for BOTH bridged-back rows in one loop.

    Returns ``(control_found, resource_found, db_error, elapsed_s)``. Returns
    as soon as both are found, or at the deadline. A DB read error returns the
    found-so-far booleans plus the error string — never silently read as
    "not arrived" (honest_failure_modes #1/#2); the caller maps a set
    ``db_error`` to CONCERN, not a false FAIL.
    """
    from utils.db_helpers import connect_tuned

    start = time.monotonic()
    deadline = start + timeout_s
    control = False
    resource = False
    while True:
        try:
            with connect_tuned(queue_db) as conn:
                if not control:
                    control = conn.execute(
                        "SELECT id FROM messages"
                        " WHERE destination = 'meshtastic' AND payload LIKE ?"
                        " LIMIT 1",
                        (control_pat,),
                    ).fetchone() is not None
                if not resource:
                    resource = conn.execute(
                        "SELECT id FROM messages"
                        " WHERE destination = 'meshtastic' AND payload LIKE ?"
                        " LIMIT 1",
                        (resource_pat,),
                    ).fetchone() is not None
        except Exception as e:
            return (control, resource, f"queue DB unreadable: {e}",
                    time.monotonic() - start)
        if control and resource:
            return control, resource, None, time.monotonic() - start
        if time.monotonic() >= deadline:
            return control, resource, None, time.monotonic() - start
        time.sleep(POLL_INTERVAL_S)


def classify(
    leg1: Tuple[str, Optional[str], float],
    leg2: Optional[Tuple[bool, bool, Optional[str], float]],
    *,
    seq: int,
    msg_id: str,
    reply_bytes: int,
) -> ResourceCanaryResult:
    """Map the leg outcomes onto one honest verdict.

    leg1 is the PINGBIG outbound confirm: ``(outcome, detail, elapsed)`` with
    outcome in {confirmed, dropped, timeout, error}. leg2 is the return poll
    ``(control_found, resource_found, db_error, elapsed)`` — None when leg 1
    already failed (we never ran it).
    """
    base = dict(seq=seq, msg_id=msg_id, reply_bytes=reply_bytes)
    outcome1, detail1, elapsed1 = leg1

    if outcome1 == "dropped":
        return ResourceCanaryResult(
            "FAIL", f"PINGBIG outbound dropped: {detail1}", **base)
    if outcome1 == "timeout":
        return ResourceCanaryResult(
            "FAIL",
            f"PINGBIG outbound not confirmed within {elapsed1:.0f}s "
            f"(gateway outbound pipeline or peer)",
            **base)
    if outcome1 == "error":
        return ResourceCanaryResult("FAIL", f"leg1 {detail1}", **base)

    # leg 1 confirmed — the peer RECEIVED the trigger.
    if leg2 is None:  # defensive — caller always runs leg 2 after confirm
        return ResourceCanaryResult(
            "CONCERN", "leg2 not attempted", confirm_s=elapsed1, **base)
    control, resource, db_error, elapsed2 = leg2

    if db_error is not None:
        return ResourceCanaryResult(
            "CONCERN",
            f"outbound confirmed={elapsed1:.1f}s but leg2 {db_error}",
            confirm_s=elapsed1, control_back=control, resource_back=resource,
            rtt_s=elapsed2, **base)

    if control and resource:
        return ResourceCanaryResult(
            "OK",
            f"seq={seq} confirmed={elapsed1:.1f}s resource_back={elapsed2:.1f}s "
            f"bytes={reply_bytes}",
            confirm_s=elapsed1, control_back=True, resource_back=True,
            rtt_s=elapsed2, **base)

    if control and not resource:
        # THE money signal: single-packet works, the Resource does not.
        # TWO distinct causes both land here — don't over-claim one (the old
        # reason hardcoded "/etc/reticulum/storage", which is MISLEADING: the
        # gateway's runtime resourcepath is /tmp/meshforge_rns_client under
        # PrivateTmp, verified 2026-06-27):
        #   (a) the inbound Resource could not be assembled+WRITTEN to the
        #       gateway's RNS resourcepath (the #60 EROFS class). The
        #       path-agnostic witness is A2's journal grep ("Error while
        #       assembling received resource" / EROFS) — that fires regardless
        #       of WHICH path. scripts/gateway_resourcepath.sh finds the live
        #       resourcepath to confirm it is writable.
        #   (b) the Resource assembled fine but the RNS→Mesh bridge dropped it
        #       before the meshtastic queue — only at reply_bytes >~512
        #       (verified 2026-06-27). At the 512 default this is not in play.
        return ResourceCanaryResult(
            "FAIL",
            f"RESOURCE round-trip BROKEN while single-packet works: control "
            f"PING returned but the {reply_bytes}-byte resource reply did NOT "
            f"within {elapsed2:.0f}s. Most likely the inbound Resource could "
            f"not be assembled+written to the gateway's RNS resourcepath (#60 "
            f"EROFS class) — run scripts/gateway_resourcepath.sh to find the "
            f"live resourcepath and verify it is writable, and grep the gateway "
            f"journal for 'Error while assembling received resource' / EROFS "
            f"(the path-agnostic witness).",
            confirm_s=elapsed1, control_back=True, resource_back=False,
            rtt_s=elapsed2, **base)

    if resource and not control:
        return ResourceCanaryResult(
            "CONCERN",
            f"resource reply returned ({elapsed2:.1f}s) but the control PING "
            f"did not — transient single-packet loss; the resource path is "
            f"healthy",
            confirm_s=elapsed1, control_back=False, resource_back=True,
            rtt_s=elapsed2, **base)

    # neither returned
    return ResourceCanaryResult(
        "CONCERN",
        f"neither control nor resource reply returned within {elapsed2:.0f}s — "
        f"echo peer down or return path broken (NOT resource-specific; the "
        f"PINGBIG outbound was confirmed, so the trigger reached the peer)",
        confirm_s=elapsed1, control_back=False, resource_back=False,
        rtt_s=elapsed2, **base)


# ---------------------------------------------------------------- main


def run_canary(args) -> ResourceCanaryResult:
    from lab.lxmf_tracer import load_peers

    if not args.skip_service_check:
        from utils.service_check import check_service
        status = check_service("meshforge-gateway")
        if not status.available:
            return ResourceCanaryResult(
                "FAIL", f"meshforge-gateway not available: {status.message}")

    peers_path = Path(args.peers_file) if args.peers_file else None
    target = resolve_target(args.peer, load_peers(peers_path))
    if target is None:
        return ResourceCanaryResult(
            "FAIL", f"peer '{args.peer}' not found in lab_peers")

    from gateway.message_queue import PersistentMessageQueue
    from gateway.message_queue_models import MessagePriority

    seq = int(time.time())
    sender = f"rescanary-{short_name()}"
    reply_bytes = int(args.reply_bytes)
    queue = PersistentMessageQueue(db_path=args.queue_db)

    # Enqueue the CONTROL ping first, then the PINGBIG. Both are single-packet
    # outbound to the SAME peer in the same fire; only the PINGBIG's REPLY is
    # resource-sized. We confirm the PINGBIG outbound (leg 1) because that is
    # the message whose resource reply we are testing.
    queue.enqueue(
        payload={"message": make_ping_body(seq, sender),
                 "destination_hash": target},
        destination="rns", priority=MessagePriority.NORMAL,
    )
    big_id = queue.enqueue(
        payload={"message": make_bigping_body(seq, sender, reply_bytes),
                 "destination_hash": target},
        destination="rns", priority=MessagePriority.NORMAL,
    )
    if not big_id:
        return ResourceCanaryResult(
            "FAIL", "PINGBIG enqueue refused (queue full or dedup)",
            seq=seq, reply_bytes=reply_bytes)

    counters_db = (Path(args.counters_db) if args.counters_db
                   else _default_counters_db())
    queue_db = Path(args.queue_db) if args.queue_db else Path(queue._db_path)

    leg1 = poll_counters_terminal(counters_db, big_id, args.leg1_timeout)
    leg2 = None
    if leg1[0] == "confirmed":
        leg2 = poll_both_rows(
            queue_db,
            control_like_pattern(seq, sender),
            resource_like_pattern(seq, sender),
            args.leg2_timeout,
        )
    result = classify(leg1, leg2, seq=seq, msg_id=big_id,
                      reply_bytes=reply_bytes)
    result.peer = args.peer
    result.host = short_name()
    return result


def _default_counters_db() -> Path:
    from gateway.delivery_counters import default_db_path
    return default_db_path()


def write_envelope(state_dir: str, result: ResourceCanaryResult) -> None:
    """Atomically publish the verdict envelope the probe consumes.

    Atomic rename means a reader (the probe) never sees a torn file, and the
    published file's mtime is the fire time the probe's SILENCE leg watches.
    Best-effort: a write failure must not crash the canary, but it leaves a
    stderr witness — a persistently unwritable state dir would make the canary
    look DARK (stale mtime) to the probe, which is the honest read.
    """
    try:
        os.makedirs(state_dir, exist_ok=True)
        path = os.path.join(state_dir, ENVELOPE_NAME)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(result), fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError as exc:
        print(f"resource-canary: could not write envelope to {state_dir}: "
              f"{exc}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--peer", default=DEFAULT_PEER,
                        help="lab_peers name of the resource-aware echo peer")
    parser.add_argument("--peers-file", default=None,
                        help="override lab_peers path (test seam)")
    parser.add_argument("--queue-db", default=None,
                        help="override message_queue.db path (test seam)")
    parser.add_argument("--counters-db", default=None,
                        help="override delivery_counters.db path (test seam)")
    parser.add_argument("--reply-bytes", type=int, default=DEFAULT_REPLY_BYTES,
                        help=f"requested resource reply size "
                             f"(default {DEFAULT_REPLY_BYTES}; forces a Resource"
                             f" above ~319B)")
    parser.add_argument("--leg1-timeout", type=float,
                        default=DEFAULT_LEG1_TIMEOUT_S)
    parser.add_argument("--leg2-timeout", type=float,
                        default=DEFAULT_LEG2_TIMEOUT_S)
    parser.add_argument("--state-dir", default=None,
                        help="verdict envelope dir (default "
                             "~/.local/state/meshforge/gateway_resource_canary)")
    parser.add_argument("--no-envelope", action="store_true",
                        help="skip writing the verdict envelope (lab/test)")
    parser.add_argument("--skip-service-check", action="store_true",
                        help="skip the meshforge-gateway preflight (lab)")
    args = parser.parse_args(argv)

    result = run_canary(args)
    result.ts = time.time()

    if not args.no_envelope:
        write_envelope(args.state_dir or default_state_dir(), result)

    print(json.dumps({
        "seq": result.seq, "msg_id": result.msg_id,
        "confirm_s": result.confirm_s, "rtt_s": result.rtt_s,
        "control_back": result.control_back,
        "resource_back": result.resource_back,
        "reply_bytes": result.reply_bytes,
    }))
    # The witness line — every code path produces exactly one.
    print(f"VERDICT {result.verdict} {result.reason}")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())

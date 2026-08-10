"""LXMF tracer — one-shot PING+ACK measurement to each peer.

Reads peers from ``~/.config/meshforge/lab_peers`` (``name=hash`` per
line; ``#`` comments OK). For each peer:

1. Resolves the LXMF path (requests if unknown).
2. Sends ``PING seq=<seq> from=<self_short>`` to the peer.
3. Waits up to ``--ack-timeout`` seconds for an inbound ACK that
   matches ``orig=<self_short>`` and ``seq=<seq>``.
4. Emits one journal line per peer:
   ``tracer: rtt seq=N peer=<name> result=ok|timeout|no-route ms=<int>``

Self-loopback path: list yourself in lab_peers and the same code path
proves standalone reliability (the "fleet can do it - so can standalone"
constraint from L1 plan question #3).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from utils.tx_guard import assert_rns_tx_allowed

logger = logging.getLogger("lab.tracer")


DEFAULT_PATH_TIMEOUT_S = 8.0
DEFAULT_ACK_TIMEOUT_S = 30.0
DEFAULT_PATH_RETRIES = 3
DEFAULT_PATH_RETRY_BACKOFF_S = 1.5

# Per-fire JSON state. Survives volatile journals on Pi boxes (every
# fleet box runs `Storage=volatile`, which rotates user-unit journal
# data away within an hour on small tmpfs partitions; the original
# 2026-05-12 design assumed durable journals and broke fleet rollups
# as a result — see project_lab_install_gap_moc3.md follow-up).
STATE_SCHEMA_VERSION = 1
STATE_TTL_DAYS = 7


@dataclass
class Peer:
    """One row from lab_peers."""
    name: str
    hash_hex: str

    @property
    def dest_hash(self) -> bytes:
        return bytes.fromhex(self.hash_hex)


@dataclass
class TraceResult:
    """One outcome row from a tracer run."""
    seq: int
    peer: str
    result: str  # "ok" | "timeout" | "no-route" | "send-error"
    rtt_ms: int  # 0 when result != "ok"
    # Path-resolve attempts used before this peer's PING was sent (or
    # before resolution gave up). 1 = first try succeeded; >1 = cold-
    # start retried successfully; equal to --path-retries = exhausted.
    # Phase-1 watchdog reads this from /fleet/tracer-fires to surface
    # cold-start vs persistent unreachable in /api/status.watchdog.
    path_retries_used: int = 1


@dataclass
class _PendingPing:
    """Tracks one outbound PING awaiting an ACK."""
    seq: int
    peer_name: str
    sent_at_monotonic: float
    ack_event: threading.Event = field(default_factory=threading.Event)
    rtt_ms: int = 0
    path_retries_used: int = 1


# --------------------------------------------------------------- peers file


def _peers_file_default() -> Path:
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".config" / "meshforge" / "lab_peers"


def parse_peers_file(text: str) -> List[Peer]:
    """Parse a ``lab_peers`` file body.

    Format: one ``name=32-hex`` per line. ``#`` starts a comment. Blank
    lines OK. Returns peers in file order so the operator controls the
    PING sequence (useful when one peer is the slow link).
    """
    peers: List[Peer] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            logger.warning("peers line %d: missing '=' — skipping: %r", lineno, raw)
            continue
        name, _, hash_hex = line.partition("=")
        name = name.strip()
        hash_hex = hash_hex.strip().lower()
        if not name:
            logger.warning("peers line %d: empty name — skipping", lineno)
            continue
        if len(hash_hex) != 32 or any(c not in "0123456789abcdef" for c in hash_hex):
            logger.warning(
                "peers line %d: hash must be 32 hex chars — skipping %r",
                lineno, raw,
            )
            continue
        peers.append(Peer(name=name, hash_hex=hash_hex))
    return peers


def load_peers(path: Optional[Path] = None) -> List[Peer]:
    p = path or _peers_file_default()
    if not p.exists():
        return []
    return parse_peers_file(p.read_text())


# ---------------------------------------------------------------- ACK match


def match_ack_to_pending(
    body: str, self_short: str, pending: Dict[int, _PendingPing],
) -> Optional[Tuple[int, _PendingPing]]:
    """Given an inbound LXMF body, find the matching pending PING.

    Returns (seq, _PendingPing) on match, None otherwise. Out-of-order
    delivery is handled — we key by seq, not arrival order.
    """
    from lab._lab_common import parse_ack

    ack = parse_ack(body)
    if not ack:
        return None
    if ack.orig != self_short:
        # Belongs to another tracer instance on this box (unlikely) or
        # someone else's traffic. Ignore.
        return None
    p = pending.get(ack.seq)
    if p is None:
        # Late ACK after timeout. Don't fail-loud; tracer already
        # recorded the timeout result.
        logger.debug("tracer: late ack seq=%d (already timed out)", ack.seq)
        return None
    return ack.seq, p


# ------------------------------------------------------------------ RNS bits


def _build_client_config(tmpdir: Path) -> Path:
    """Same shape as lxmf_echo._build_client_config — duplicated rather
    than imported to keep the two modules' import surfaces independent."""
    from utils.paths import ReticulumPaths

    instance_name = ReticulumPaths.get_configured_instance_name()
    lines = [
        "# MeshForge lab-tracer RNS client config (auto-generated)",
        "[reticulum]",
        "share_instance = Yes",
        f"instance_name = {instance_name}",
        "shared_instance_port = 37428",
        "instance_control_port = 37429",
    ]
    rpc_key = ReticulumPaths.get_shared_rpc_key()
    if rpc_key:
        lines.append(f"rpc_key = {rpc_key}")
    cfg = tmpdir / "config"
    cfg.write_text("\n".join(lines) + "\n")
    return cfg


def _resolve_path(
    RNS, dest_hash: bytes, timeout_s: float,
    *,
    retries: int = DEFAULT_PATH_RETRIES,
    retry_backoff_s: float = DEFAULT_PATH_RETRY_BACKOFF_S,
) -> Tuple[bool, int]:
    """Block until RNS has a path or all retries exhausted.

    Returns ``(resolved, attempts_used)``. ``attempts_used`` is 1 for
    a first-try success, 2..retries for cold-start successes, and
    equals ``retries`` when all attempts fail.

    Why retries: today's /fleet symptom showed every box reporting
    100% timeout against moc1 while moc1's echo responder was healthy
    and answering. A single 8s ``request_path`` window doesn't tolerate
    path_table cold-starts when the local RNS just initialized — by
    the time the second-fire fires, the cache has populated and the
    same probe succeeds. Re-issuing ``request_path`` between attempts
    pokes RNS to re-flood the announce request, mirroring Issue #65's
    two-tier transient/persistent backoff pattern: cold-start gets one
    cheap retry, a real outage exhausts all and reports honestly.
    """
    retries = max(1, retries)
    for attempt in range(1, retries + 1):
        if not RNS.Transport.has_path(dest_hash):
            try:
                RNS.Transport.request_path(dest_hash)
            except Exception as exc:
                logger.debug(
                    "tracer: request_path raised on attempt %d: %s",
                    attempt, exc,
                )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if RNS.Transport.has_path(dest_hash):
                return True, attempt
            time.sleep(0.1)
        # Backoff before next retry (except after the last attempt).
        if attempt < retries:
            time.sleep(retry_backoff_s)
    return RNS.Transport.has_path(dest_hash), retries


def parse_peer_timeout_overrides(
    raw: List[str],
) -> Dict[str, float]:
    """Parse ``--peer-timeout NAME=SECS`` repeated args.

    Per-peer override lets the operator dial up the budget for a peer
    that reliably needs longer (e.g. a moc box that's two RNS hops
    deep) without inflating the global timeout for fast peers. Mirrors
    Issue #65's per-peer backoff_extended pattern.

    Garbage entries log a warning and are skipped — the tracer is an
    observability tool; a typo in a CLI arg shouldn't fail the fire.
    """
    overrides: Dict[str, float] = {}
    for spec in raw or []:
        if "=" not in spec:
            logger.warning("tracer: --peer-timeout: missing '=' in %r", spec)
            continue
        name, _, secs = spec.partition("=")
        name = name.strip()
        if not name:
            logger.warning("tracer: --peer-timeout: empty peer name in %r", spec)
            continue
        try:
            secs_f = float(secs.strip())
        except (TypeError, ValueError):
            logger.warning(
                "tracer: --peer-timeout: bad seconds value in %r — skipping",
                spec,
            )
            continue
        if secs_f <= 0:
            logger.warning(
                "tracer: --peer-timeout: %s=%.1f is non-positive — skipping",
                name, secs_f,
            )
            continue
        overrides[name] = secs_f
    return overrides


# ---------------------------------------------------------------- main flow


def run_trace(
    peers: List[Peer],
    path_timeout_s: float = DEFAULT_PATH_TIMEOUT_S,
    ack_timeout_s: float = DEFAULT_ACK_TIMEOUT_S,
    seq_start: int = 1,
    *,
    path_retries: int = DEFAULT_PATH_RETRIES,
    path_retry_backoff_s: float = DEFAULT_PATH_RETRY_BACKOFF_S,
    peer_timeout_overrides: Optional[Dict[str, float]] = None,
) -> List[TraceResult]:
    """Send one PING per peer, wait for ACKs (concurrent), emit results.

    PINGs are fired sequentially (small inter-peer gap so we don't burst
    the channel), but ACKs are matched in parallel by a single inbound
    callback. With N peers, total time is bounded by:

        N * (path_timeout_s + small_send_time) + ack_timeout_s

    not N * full_timeout — because we wait for all ACKs concurrently
    after all PINGs are out.
    """
    try:
        import RNS
        import LXMF
    except ImportError as exc:
        logger.error("RNS/LXMF not installed: %s", exc)
        return []

    from lab._lab_common import (
        bounded_block,
        init_reticulum_with_watchdog,
        load_or_create_identity, make_ping_body, short_name,
    )

    self_short = short_name()
    pending: Dict[int, _PendingPing] = {}

    with tempfile.TemporaryDirectory(prefix="meshforge_lab_tracer_") as tmp:
        tmpdir = Path(tmp)
        _build_client_config(tmpdir)

        try:
            reticulum = init_reticulum_with_watchdog(str(tmpdir))
        except Exception as exc:
            logger.error("tracer: RNS init failed: %s", exc)
            return [
                TraceResult(seq=0, peer=p.name, result="send-error", rtt_ms=0)
                for p in peers
            ]

        # Post-init region — LXMRouter init, announce, path-resolve, and
        # handle_outbound all open Unix-socket connections to rnsd's RPC
        # and can hang in unix_wait_for_peer if the listener wedges (the
        # init-only watchdog above has already disarmed by this point).
        # Bound this whole region. Formula accounts for path_retries:
        # per-peer can take up to retries*(timeout+backoff), ACK wait is
        # concurrent, 20s buffer covers LXMRouter init + announce + send-
        # loop overhead.
        per_peer_max_s = path_retries * (path_timeout_s + path_retry_backoff_s)
        # Honor per-peer overrides — pick the worst-case peer's budget.
        overrides = peer_timeout_overrides or {}
        if overrides:
            override_max = max(
                path_retries * (t + path_retry_backoff_s)
                for t in overrides.values()
            )
            per_peer_max_s = max(per_peer_max_s, override_max)
        post_init_timeout_s = (
            len(peers) * per_peer_max_s + ack_timeout_s + 20.0
        )
        with bounded_block(post_init_timeout_s, label="tracer post-init"):
            identity, _ = load_or_create_identity("lab_tracer")
            lxmf_storage = tmpdir / "lxmf"
            lxmf_storage.mkdir(parents=True, exist_ok=True)
            router = LXMF.LXMRouter(storagepath=str(lxmf_storage))
            source = router.register_delivery_identity(
                identity, display_name=f"lab-tracer ({self_short})",
            )

            # Inbound ACK matcher.
            def _on_receive(message):
                body = message.content
                if isinstance(body, bytes):
                    try:
                        body = body.decode("utf-8")
                    except UnicodeDecodeError:
                        body = body.decode("utf-8", errors="replace")
                match = match_ack_to_pending(body, self_short, pending)
                if match is None:
                    return
                seq, p = match
                p.rtt_ms = int((time.monotonic() - p.sent_at_monotonic) * 1000)
                p.ack_event.set()
                logger.info(
                    "tracer: ack seq=%d peer=%s rtt_ms=%d",
                    seq, p.peer_name, p.rtt_ms,
                )

            router.register_delivery_callback(_on_receive)

            # Announce so peers can resolve us (for the ACK return path).
            assert_rns_tx_allowed(kind="rns_announce", detail="lab tracer announce")
            router.announce(source.hash)
            time.sleep(0.2)  # let the announce hit the wire

            # Send PINGs.
            results: List[TraceResult] = []
            seq = seq_start
            for peer in peers:
                effective_timeout = (peer_timeout_overrides or {}).get(
                    peer.name, path_timeout_s,
                )
                resolved, attempts_used = _resolve_path(
                    RNS, peer.dest_hash, effective_timeout,
                    retries=path_retries,
                    retry_backoff_s=path_retry_backoff_s,
                )
                if not resolved:
                    logger.info(
                        "tracer: rtt seq=%d peer=%s result=no-route ms=0 retries=%d",
                        seq, peer.name, attempts_used,
                    )
                    results.append(TraceResult(
                        seq=seq, peer=peer.name, result="no-route", rtt_ms=0,
                        path_retries_used=attempts_used,
                    ))
                    seq += 1
                    continue

                if attempts_used > 1:
                    logger.info(
                        "tracer: peer=%s path resolved on attempt %d (cold-start)",
                        peer.name, attempts_used,
                    )

                dest_identity = RNS.Identity.recall(peer.dest_hash)
                if dest_identity is None:
                    logger.warning(
                        "tracer: recall returned None for peer=%s — counting "
                        "as no-route", peer.name,
                    )
                    results.append(TraceResult(
                        seq=seq, peer=peer.name, result="no-route", rtt_ms=0,
                        path_retries_used=attempts_used,
                    ))
                    seq += 1
                    continue

                destination = RNS.Destination(
                    dest_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
                    "lxmf", "delivery",
                )
                body = make_ping_body(seq, self_short)
                lxm = LXMF.LXMessage(destination, source, body, "lab tracer PING")

                ping = _PendingPing(
                    seq=seq, peer_name=peer.name,
                    sent_at_monotonic=time.monotonic(),
                    path_retries_used=attempts_used,
                )
                pending[seq] = ping
                try:
                    assert_rns_tx_allowed(kind="lxmf_outbound", detail="lab tracer PING")
                    router.handle_outbound(lxm)
                except Exception as exc:
                    logger.warning(
                        "tracer: send failed seq=%d peer=%s: %s",
                        seq, peer.name, exc,
                    )
                    del pending[seq]
                    results.append(TraceResult(
                        seq=seq, peer=peer.name, result="send-error", rtt_ms=0,
                        path_retries_used=attempts_used,
                    ))
                    seq += 1
                    continue

                seq += 1
                # Small gap so we don't slam rnsd's outbound queue.
                time.sleep(0.05)

            # Wait for ACKs concurrently.
            deadline = time.monotonic() + ack_timeout_s
            for ping in list(pending.values()):
                remaining = max(0.0, deadline - time.monotonic())
                if ping.ack_event.wait(remaining):
                    results.append(TraceResult(
                        seq=ping.seq, peer=ping.peer_name,
                        result="ok", rtt_ms=ping.rtt_ms,
                        path_retries_used=ping.path_retries_used,
                    ))
                    logger.info(
                        "tracer: rtt seq=%d peer=%s result=ok ms=%d retries=%d",
                        ping.seq, ping.peer_name, ping.rtt_ms,
                        ping.path_retries_used,
                    )
                else:
                    results.append(TraceResult(
                        seq=ping.seq, peer=ping.peer_name,
                        result="timeout", rtt_ms=0,
                        path_retries_used=ping.path_retries_used,
                    ))
                    logger.info(
                        "tracer: rtt seq=%d peer=%s result=timeout ms=0 retries=%d",
                        ping.seq, ping.peer_name, ping.path_retries_used,
                    )

            # Sort results by seq so journal output is monotonic.
            results.sort(key=lambda r: r.seq)
            return results


# ---------------------------------------------------------------- state files


def _state_dir_default() -> Path:
    """``$XDG_STATE_HOME/meshforge/tracer`` (or ``~/.local/state/...``).

    Same shape `lab_synth_soak_*` use, so an operator only has to learn
    one path convention.
    """
    from utils.paths import get_real_user_home

    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else get_real_user_home() / ".local" / "state"
    return base / "meshforge" / "tracer"


def write_state_file(
    results: List[TraceResult],
    fire_at_unix: float,
    self_short: str,
    state_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Write one JSON file per fire: ``tracer-<unix-ts>.json``.

    One JSON object per file, terminated with a newline so ``cat *.json``
    produces clean JSONL for the aggregator. Returns the written path,
    or None on write failure (we never want state-file IO to fail the
    fire — observability, not gating).
    """
    sd = state_dir or _state_dir_default()
    try:
        sd.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("tracer: state dir create failed: %s", exc)
        return None

    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "fire_at_unix": float(fire_at_unix),
        "fire_at_iso": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(fire_at_unix),
        ),
        "self_short": self_short,
        "results": [
            {
                "seq": r.seq,
                "peer": r.peer,
                "result": r.result,
                "rtt_ms": r.rtt_ms,
                "path_retries_used": r.path_retries_used,
            }
            for r in results
        ],
    }

    target = sd / f"tracer-{int(fire_at_unix)}.json"
    tmp = sd / f".tracer-{int(fire_at_unix)}.json.tmp"
    try:
        tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning("tracer: state write failed: %s", exc)
        tmp.unlink(missing_ok=True)
        return None
    return target


def prune_state_dir(
    state_dir: Optional[Path] = None,
    ttl_days: int = STATE_TTL_DAYS,
    now_unix: Optional[float] = None,
) -> int:
    """Delete ``tracer-*.json`` files older than ``ttl_days``.

    Returns the count deleted. Best-effort: a single unlink failure
    logs and continues. Called from main() before the fire so a
    partial fire doesn't skip the prune cycle.
    """
    sd = state_dir or _state_dir_default()
    if not sd.is_dir():
        return 0
    cutoff = (now_unix if now_unix is not None else time.time()) - ttl_days * 86400
    deleted = 0
    for p in sd.glob("tracer-*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                deleted += 1
        except OSError as exc:
            logger.debug("tracer: prune skip %s: %s", p.name, exc)
    return deleted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--peers", type=Path, default=None,
        help="Override path to lab_peers file "
             "(default ~/.config/meshforge/lab_peers)",
    )
    parser.add_argument(
        "--path-retries", type=int, default=DEFAULT_PATH_RETRIES,
        help=(
            f"Path-resolve attempts per peer before giving up "
            f"(default {DEFAULT_PATH_RETRIES}). Re-issues request_path "
            f"between attempts to tolerate path_table cold-start."
        ),
    )
    parser.add_argument(
        "--path-retry-backoff", type=float,
        default=DEFAULT_PATH_RETRY_BACKOFF_S,
        help=(
            f"Seconds between path-resolve retries "
            f"(default {DEFAULT_PATH_RETRY_BACKOFF_S})."
        ),
    )
    parser.add_argument(
        "--peer-timeout", action="append", default=[],
        metavar="NAME=SECS",
        help=(
            "Per-peer path-resolve timeout override. Repeatable. "
            "Example: --peer-timeout moc1=12 --peer-timeout moc2=6. "
            "Useful when one peer is two RNS hops deep and needs longer "
            "than the global budget."
        ),
    )
    parser.add_argument(
        "--path-timeout", type=float, default=DEFAULT_PATH_TIMEOUT_S,
        help=f"Seconds to wait for path resolution (default {DEFAULT_PATH_TIMEOUT_S})",
    )
    parser.add_argument(
        "--ack-timeout", type=float, default=DEFAULT_ACK_TIMEOUT_S,
        help=f"Seconds to wait for ACKs (default {DEFAULT_ACK_TIMEOUT_S})",
    )
    parser.add_argument(
        "--loglevel", default="INFO",
        help="Python logging level (default INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.loglevel.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    peers = load_peers(args.peers)
    if not peers:
        logger.error(
            "tracer: no peers in %s — nothing to do",
            args.peers or _peers_file_default(),
        )
        return 2

    logger.info("tracer: starting trace against %d peer(s)", len(peers))

    # Prune old state files BEFORE the fire so a long run won't skip a
    # prune cycle (and so we don't accidentally evict the file we just
    # wrote). Best-effort — failures don't gate the fire.
    deleted = prune_state_dir()
    if deleted:
        logger.debug("tracer: pruned %d expired state files", deleted)

    fire_at_unix = time.time()
    overrides = parse_peer_timeout_overrides(args.peer_timeout)
    if overrides:
        logger.info(
            "tracer: per-peer timeout overrides: %s",
            ", ".join(f"{n}={s:.1f}s" for n, s in sorted(overrides.items())),
        )
    results = run_trace(
        peers,
        path_timeout_s=args.path_timeout,
        ack_timeout_s=args.ack_timeout,
        path_retries=args.path_retries,
        path_retry_backoff_s=args.path_retry_backoff,
        peer_timeout_overrides=overrides,
    )

    # Durable per-fire state — the journal is volatile on the small
    # fleet boxes and the rollup can't see >1h of history. Aggregator
    # (lab_traffic_rollup.sh) reads these JSON files first; the journal
    # remains as a secondary human-readable log only.
    from lab._lab_common import short_name
    state_path = write_state_file(
        results, fire_at_unix=fire_at_unix, self_short=short_name(),
    )
    if state_path is not None:
        logger.debug("tracer: wrote state file %s", state_path)

    # Always exit 0 — the tracer is an observability tool, not a gating
    # check. Returning non-zero on partial failures would flag
    # `meshforge-tracer.service` as failed in systemd every cycle that
    # has a single timeout/no-route, drowning real failures (like the
    # daemon crashing) in routine red.
    return 0


if __name__ == "__main__":
    sys.exit(main())

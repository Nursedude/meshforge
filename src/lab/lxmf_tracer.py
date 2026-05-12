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
import logging
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("lab.tracer")


DEFAULT_PATH_TIMEOUT_S = 8.0
DEFAULT_ACK_TIMEOUT_S = 30.0


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


@dataclass
class _PendingPing:
    """Tracks one outbound PING awaiting an ACK."""
    seq: int
    peer_name: str
    sent_at_monotonic: float
    ack_event: threading.Event = field(default_factory=threading.Event)
    rtt_ms: int = 0


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


def _resolve_path(RNS, dest_hash: bytes, timeout_s: float) -> bool:
    """Block until RNS has a path or timeout. Returns True if path known."""
    if not RNS.Transport.has_path(dest_hash):
        RNS.Transport.request_path(dest_hash)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if RNS.Transport.has_path(dest_hash):
            return True
        time.sleep(0.1)
    return RNS.Transport.has_path(dest_hash)


# ---------------------------------------------------------------- main flow


def run_trace(
    peers: List[Peer],
    path_timeout_s: float = DEFAULT_PATH_TIMEOUT_S,
    ack_timeout_s: float = DEFAULT_ACK_TIMEOUT_S,
    seq_start: int = 1,
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
        load_or_create_identity, make_ping_body, short_name,
    )

    self_short = short_name()
    pending: Dict[int, _PendingPing] = {}

    with tempfile.TemporaryDirectory(prefix="meshforge_lab_tracer_") as tmp:
        tmpdir = Path(tmp)
        _build_client_config(tmpdir)

        try:
            reticulum = RNS.Reticulum(configdir=str(tmpdir), loglevel=2)
        except Exception as exc:
            logger.error("tracer: RNS init failed: %s", exc)
            return [
                TraceResult(seq=0, peer=p.name, result="send-error", rtt_ms=0)
                for p in peers
            ]

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
        router.announce(source.hash)
        time.sleep(0.2)  # let the announce hit the wire

        # Send PINGs.
        results: List[TraceResult] = []
        seq = seq_start
        for peer in peers:
            if not _resolve_path(RNS, peer.dest_hash, path_timeout_s):
                logger.info(
                    "tracer: rtt seq=%d peer=%s result=no-route ms=0",
                    seq, peer.name,
                )
                results.append(TraceResult(
                    seq=seq, peer=peer.name, result="no-route", rtt_ms=0,
                ))
                seq += 1
                continue

            dest_identity = RNS.Identity.recall(peer.dest_hash)
            if dest_identity is None:
                logger.warning(
                    "tracer: recall returned None for peer=%s — counting "
                    "as no-route", peer.name,
                )
                results.append(TraceResult(
                    seq=seq, peer=peer.name, result="no-route", rtt_ms=0,
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
            )
            pending[seq] = ping
            try:
                router.handle_outbound(lxm)
            except Exception as exc:
                logger.warning(
                    "tracer: send failed seq=%d peer=%s: %s",
                    seq, peer.name, exc,
                )
                del pending[seq]
                results.append(TraceResult(
                    seq=seq, peer=peer.name, result="send-error", rtt_ms=0,
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
                ))
                logger.info(
                    "tracer: rtt seq=%d peer=%s result=ok ms=%d",
                    ping.seq, ping.peer_name, ping.rtt_ms,
                )
            else:
                results.append(TraceResult(
                    seq=ping.seq, peer=ping.peer_name,
                    result="timeout", rtt_ms=0,
                ))
                logger.info(
                    "tracer: rtt seq=%d peer=%s result=timeout ms=0",
                    ping.seq, ping.peer_name,
                )

        # Sort results by seq so journal output is monotonic.
        results.sort(key=lambda r: r.seq)
        return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--peers", type=Path, default=None,
        help="Override path to lab_peers file "
             "(default ~/.config/meshforge/lab_peers)",
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
    results = run_trace(
        peers,
        path_timeout_s=args.path_timeout,
        ack_timeout_s=args.ack_timeout,
    )

    # Exit code: 0 if every peer ACKed, 1 if any timeout/no-route.
    # Aggregator reads journal lines, not exit code — this is for ad-hoc
    # operator runs where exit status matters.
    failed = [r for r in results if r.result != "ok"]
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

"""LXMF multi-user load synthesizer — N concurrent virtual users.

Sister to ``lxmf_tracer`` (N=1). Spawns N virtual users in one process,
each with its own RNS identity + LXMF delivery destination, all sharing
one shared-instance rnsd. Each user fires PINGs at all configured peers
per a configurable pattern; ACKs are matched concurrently.

The point: stress fleet gateways under realistic multi-user load,
exercising per-source path discovery, LXMF dedup tables, message_queue
serialization, and concurrent inbound delivery — none of which the
single-identity tracer surfaces.

Patterns
--------
sustained : each user fires 1 PING / interval_s for duration_s. Users'
            start times staggered by ``interval_s / n_users`` for smooth
            global rate.
burst     : all users fire simultaneously every interval_s for
            duration_s. Stresses dedup + LXMF inbound serialization
            under spike.
ramp      : 1 user active at t=0, N active at t=duration_s. Surfaces
            "at what concurrency does it break" boundaries.

Identity model (v1)
-------------------
ONE RNS identity (``~/.config/meshforge/lab_synth_identity``), ONE LXMF
delivery destination. Virtual users exist only at the wire-format layer
— the ``from=synth-uN`` field carries the per-user identity in the
PING body, and ACKs echo it back in ``orig=synth-uN``. The shared
inbound callback dispatches by (orig, seq) into the per-user pending
dict.

Why not N identities? LXMRouter only supports one delivery identity per
router instance (LXMRouter.py:331 returns None + logs ERROR on the
second call). To get truly distinct LXMF source identities for each
virtual user, you'd need N LXMRouter instances in one process — ~50
threads on a Pi, worth it only if you specifically want to exercise
per-source-identity path discovery / identity cache on the gateway.
For 'how does the gateway hold up under 10x concurrent inbound', a
single source is equivalent because LXMF inbound serialization,
content-hash dedup, and message_queue contention all live downstream
of the source-identity check.

Future ``--per-user-identity`` flag could opt into N-router mode.

Pass criterion (default)
------------------------
N=10 sustained × interval=5s × duration=60s ≥ 95% ACK within 30s.
Configurable via ``--ok-ratio-threshold``.

Wire format
-----------
Identical to tracer (PING/ACK in ``_lab_common``). The single inbound
callback dispatches by ``orig=`` to the per-user pending dict, so 10
concurrent users share one router without contention beyond the dict
lock.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from utils.tx_guard import assert_rns_tx_allowed

logger = logging.getLogger("lab.synth")


DEFAULT_N_USERS = 10
DEFAULT_INTERVAL_S = 5.0
DEFAULT_DURATION_S = 60.0
DEFAULT_PATH_TIMEOUT_S = 8.0
DEFAULT_ACK_TIMEOUT_S = 30.0
DEFAULT_OK_RATIO_THRESHOLD = 0.95
PATTERNS = ("sustained", "burst", "ramp")


# ----------------------------------------------------------------- data types


@dataclass
class _PendingPing:
    """Tracks one outbound PING awaiting an ACK."""
    user_short: str
    seq: int
    peer_name: str
    sent_at_monotonic: float
    ack_event: threading.Event = field(default_factory=threading.Event)
    rtt_ms: int = 0


@dataclass
class PairResult:
    """Aggregated outcome for one (virtual user, peer) pair."""
    user_short: str
    peer_name: str
    samples: int
    ok: int
    timeout: int
    no_route: int
    send_error: int
    rtts_ms: List[int] = field(default_factory=list)

    @property
    def fail_pct(self) -> float:
        if self.samples == 0:
            return 0.0
        return 100.0 * (self.samples - self.ok) / self.samples

    @property
    def mean_ms(self) -> Optional[float]:
        return statistics.fmean(self.rtts_ms) if self.rtts_ms else None

    @property
    def p95_ms(self) -> Optional[float]:
        if not self.rtts_ms:
            return None
        sorted_rtts = sorted(self.rtts_ms)
        # p95 via nearest-rank — matches scripts/lab_traffic_rollup.sh
        idx = max(0, int(0.95 * len(sorted_rtts)) - 1)
        return float(sorted_rtts[idx])


@dataclass
class SynthReport:
    """Full run output. Serializable + table-renderable."""
    started_at_iso: str
    finished_at_iso: str
    pattern: str
    n_users: int
    interval_s: float
    duration_s: float
    ack_timeout_s: float
    pair_results: List[PairResult]
    total_samples: int
    total_ok: int
    ok_ratio_threshold: float
    pass_envelope: bool

    @property
    def ok_ratio(self) -> float:
        if self.total_samples == 0:
            return 0.0
        return self.total_ok / self.total_samples


# ------------------------------------------------------------------ RNS bits


def _build_client_config(tmpdir: Path) -> Path:
    """Same shape as lxmf_tracer / lxmf_echo. Deliberately duplicated so
    these three modules' import surfaces stay independent — extracting
    would force all three to share a helper that's 15 lines and changes
    rarely."""
    from utils.paths import ReticulumPaths

    instance_name = ReticulumPaths.get_configured_instance_name()
    lines = [
        "# MeshForge lab-synth RNS client config (auto-generated)",
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
    """Block until RNS has a path or timeout. Returns True if path known.

    Same shape as the tracer helper; path table is global to the
    Reticulum instance, so resolving once before any user fires is
    sufficient for all N users to use the same path."""
    if not RNS.Transport.has_path(dest_hash):
        RNS.Transport.request_path(dest_hash)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if RNS.Transport.has_path(dest_hash):
            return True
        time.sleep(0.1)
    return RNS.Transport.has_path(dest_hash)


# --------------------------------------------------------------- ack dispatch


def dispatch_ack_to_pending(
    body: str,
    pending: Dict[Tuple[str, int], _PendingPing],
    pending_lock: threading.Lock,
) -> Optional[_PendingPing]:
    """Parse inbound LXMF body as ACK, look up matching pending PING.

    Keyed by (orig, seq) so two virtual users firing seq=1 to the same
    peer at the same time can both have their ACKs routed correctly.
    Returns the matched _PendingPing on hit, None otherwise.
    """
    from lab._lab_common import parse_ack

    ack = parse_ack(body)
    if not ack:
        return None
    key = (ack.orig, ack.seq)
    with pending_lock:
        p = pending.get(key)
    if p is None:
        # Late ACK after timeout, or not addressed to any of our users.
        logger.debug(
            "synth: stray ack orig=%s seq=%d (no matching pending)",
            ack.orig, ack.seq,
        )
        return None
    return p


# --------------------------------------------------------------- virtual user


@dataclass
class _VirtualUser:
    """One synthetic user. All users share the same RNS identity and
    LXMF delivery destination (LXMRouter is single-identity); the only
    per-user state is the wire-format short_name and its seq counter.

    Concurrency: each user runs in its own sender thread. ``next_seq``
    is touched only by the owning thread, so no lock is needed; the
    shared pending dict has its own lock at insert/remove sites."""
    index: int
    short_name: str  # short identifier like "synth-u0"
    next_seq: int = 1


def _user_short_name(index: int) -> str:
    """Stable per-index short name for the wire format."""
    return f"synth-u{index}"


def _create_users(n_users: int) -> List[_VirtualUser]:
    """Build N _VirtualUser records. No RNS / LXMF state involved —
    every user shares the single source destination passed to the
    sender loop."""
    return [
        _VirtualUser(index=i, short_name=_user_short_name(i))
        for i in range(n_users)
    ]


def compute_pattern_offsets(
    pattern: str, n_users: int,
    interval_s: float, duration_s: float,
) -> List[float]:
    """Per-user start offsets in seconds, per pattern.

    Exposed as a module-level helper so tests can pin offset math
    without driving the full run_synth.

    - sustained: stagger across one interval window → smooth global rate
    - burst: every user fires simultaneously (offset 0)
    - ramp: stagger across the whole duration → grows from 1 active
      user at t=0 to N active at t=duration_s
    """
    if pattern not in PATTERNS:
        raise ValueError(f"unknown pattern {pattern!r}; expected one of {PATTERNS}")
    if n_users < 1:
        return []
    if pattern == "sustained":
        stagger = interval_s / n_users
        return [i * stagger for i in range(n_users)]
    if pattern == "burst":
        return [0.0] * n_users
    # ramp
    stagger = duration_s / n_users
    return [i * stagger for i in range(n_users)]


# ---------------------------------------------------------------- send paths


def _send_one_ping_with_router(
    RNS, LXMF, router, source, user: _VirtualUser,
    peer_name: str, peer_dest_hash: bytes,
    pending: Dict[Tuple[str, int], _PendingPing],
    pending_lock: threading.Lock,
) -> Optional[PairResult]:
    """The real send path. ``source`` is the shared LXMF delivery
    destination (one per process — see module docstring). Returns an
    early-fail PairResult on no-route / send-error; None on successful
    enqueue."""
    from lab._lab_common import make_ping_body

    seq = user.next_seq
    user.next_seq += 1

    dest_identity = RNS.Identity.recall(peer_dest_hash)
    if dest_identity is None:
        logger.info(
            "synth: send no-route user=%s peer=%s seq=%d",
            user.short_name, peer_name, seq,
        )
        return PairResult(
            user_short=user.short_name, peer_name=peer_name,
            samples=1, ok=0, timeout=0, no_route=1, send_error=0,
        )

    destination = RNS.Destination(
        dest_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
        "lxmf", "delivery",
    )
    body = make_ping_body(seq, user.short_name)
    lxm = LXMF.LXMessage(destination, source, body, "lab synth PING")

    ping = _PendingPing(
        user_short=user.short_name, seq=seq, peer_name=peer_name,
        sent_at_monotonic=time.monotonic(),
    )
    key = (user.short_name, seq)
    with pending_lock:
        pending[key] = ping

    try:
        assert_rns_tx_allowed(kind="lxmf_outbound", detail="lab multi-user synth")
        router.handle_outbound(lxm)
    except Exception as exc:
        logger.warning(
            "synth: send-error user=%s peer=%s seq=%d: %s: %s",
            user.short_name, peer_name, seq,
            type(exc).__name__, exc or "(no message)",
            exc_info=True,
        )
        with pending_lock:
            pending.pop(key, None)
        return PairResult(
            user_short=user.short_name, peer_name=peer_name,
            samples=1, ok=0, timeout=0, no_route=0, send_error=1,
        )

    logger.debug(
        "synth: tx user=%s peer=%s seq=%d", user.short_name, peer_name, seq,
    )
    return None


# ---------------------------------------------------------------- send loops


def _sender_loop(
    RNS, LXMF, router, source, user: _VirtualUser,
    peers: List["Peer"],
    pattern: str,
    interval_s: float,
    user_start_offset_s: float,
    deadline_monotonic: float,
    pending: Dict[Tuple[str, int], _PendingPing],
    pending_lock: threading.Lock,
    early_failures: List[PairResult],
    early_failures_lock: threading.Lock,
    stop_event: threading.Event,
) -> None:
    """Per-user send loop. Fires PINGs at all peers per pattern until
    deadline or stop. Early failures (no-route / send-error) are
    appended to the shared early_failures list; ACK successes are
    matched in the inbound callback."""
    # Respect pattern start offset before first fire.
    if user_start_offset_s > 0 and stop_event.wait(user_start_offset_s):
        return

    while time.monotonic() < deadline_monotonic and not stop_event.is_set():
        for peer in peers:
            if stop_event.is_set():
                return
            fail = _send_one_ping_with_router(
                RNS, LXMF, router, source, user,
                peer.name, peer.dest_hash,
                pending, pending_lock,
            )
            if fail is not None:
                with early_failures_lock:
                    early_failures.append(fail)
        # Sleep `interval_s` between cycles. For 'burst' pattern,
        # all users sleep the same interval and stay roughly synced
        # (drift bounded by single send latency, ~ms).
        if stop_event.wait(interval_s):
            return


# --------------------------------------------------------- exclusion matching


# Common per-host prefixes the operator's lab_peers names carry that
# users won't naturally type when passing --exclude. We strip these
# when matching so a short alias (--exclude <node>) matches its full
# fleet-prefixed name (<prefix>-<node>).
_PEER_NAME_STRIP_PREFIXES = ("meshforge-", "meshanchor-")


def _peer_matches_exclude(peer_name: str, excludes: set) -> bool:
    """Case-insensitive exclude match supporting common name aliases.

    Operators run --exclude with short names but lab_peers carries
    full names with fleet-specific prefixes. This bridges by matching:

    - exact lower-case
    - stripped lower-case (remove configured prefixes)

    Returns True if the peer should be excluded.
    """
    name_lower = peer_name.lower()
    if name_lower in excludes:
        return True
    for prefix in _PEER_NAME_STRIP_PREFIXES:
        if name_lower.startswith(prefix):
            stripped = name_lower[len(prefix):]
            if stripped in excludes:
                return True
    return False


# ----------------------------------------------------------------- peers I/O


@dataclass
class Peer:
    """Reuses the lab_peers row format. Same shape as lxmf_tracer.Peer
    but kept local to avoid coupling the two modules' types."""
    name: str
    hash_hex: str

    @property
    def dest_hash(self) -> bytes:
        return bytes.fromhex(self.hash_hex)


def load_peers(path: Optional[Path] = None) -> List[Peer]:
    """Load lab_peers — delegates to tracer's parser to keep behavior
    identical (comment handling, hash validation, etc.)."""
    from lab.lxmf_tracer import load_peers as tracer_load_peers, Peer as TracerPeer
    rows: List[TracerPeer] = tracer_load_peers(path)
    return [Peer(name=r.name, hash_hex=r.hash_hex) for r in rows]


# ------------------------------------------------------------ aggregation


def _aggregate(
    n_users: int,
    peers: List[Peer],
    pending: Dict[Tuple[str, int], _PendingPing],
    early_failures: List[PairResult],
) -> List[PairResult]:
    """Walk the final pending dict + early-failure list, produce one
    PairResult per (user, peer) seen. After deadline + ack_timeout,
    any pending with ack_event still unset is a timeout."""
    pair: Dict[Tuple[str, str], PairResult] = {}

    def _slot(user_short: str, peer_name: str) -> PairResult:
        k = (user_short, peer_name)
        if k not in pair:
            pair[k] = PairResult(
                user_short=user_short, peer_name=peer_name,
                samples=0, ok=0, timeout=0, no_route=0, send_error=0,
            )
        return pair[k]

    # Walk pending: every entry was a successful enqueue; outcome is
    # ok (ack_event set) or timeout (event not set).
    for (user_short, seq), p in pending.items():
        slot = _slot(user_short, p.peer_name)
        slot.samples += 1
        if p.ack_event.is_set():
            slot.ok += 1
            slot.rtts_ms.append(p.rtt_ms)
        else:
            slot.timeout += 1

    # Fold in early failures (no-route, send-error) — these never made
    # it to the pending dict.
    for fail in early_failures:
        slot = _slot(fail.user_short, fail.peer_name)
        slot.samples += fail.samples
        slot.no_route += fail.no_route
        slot.send_error += fail.send_error

    # Stable sort: by user index, then peer name (preserves user order
    # which is index-monotonic via short name 'synth-u0', 'synth-u1', ...).
    def _sort_key(r: PairResult) -> Tuple[int, str]:
        try:
            user_idx = int(r.user_short.removeprefix("synth-u"))
        except ValueError:
            user_idx = -1
        return (user_idx, r.peer_name)

    return sorted(pair.values(), key=_sort_key)


# ------------------------------------------------------------------ main run


def run_synth(
    peers: List[Peer],
    n_users: int = DEFAULT_N_USERS,
    pattern: str = "sustained",
    interval_s: float = DEFAULT_INTERVAL_S,
    duration_s: float = DEFAULT_DURATION_S,
    path_timeout_s: float = DEFAULT_PATH_TIMEOUT_S,
    ack_timeout_s: float = DEFAULT_ACK_TIMEOUT_S,
    ok_ratio_threshold: float = DEFAULT_OK_RATIO_THRESHOLD,
) -> SynthReport:
    """Drive the full synth run end-to-end. Returns SynthReport with
    pass/fail vs envelope."""
    if pattern not in PATTERNS:
        raise ValueError(f"unknown pattern {pattern!r}; expected one of {PATTERNS}")
    if n_users < 1:
        raise ValueError(f"n_users must be >= 1, got {n_users}")

    started_at = datetime.now(timezone.utc)

    try:
        import RNS
        import LXMF
    except ImportError as exc:
        logger.error("synth: RNS/LXMF not installed: %s", exc)
        return _empty_report(
            started_at, pattern, n_users, interval_s, duration_s,
            ack_timeout_s, ok_ratio_threshold,
        )

    pending: Dict[Tuple[str, int], _PendingPing] = {}
    pending_lock = threading.Lock()
    early_failures: List[PairResult] = []
    early_failures_lock = threading.Lock()

    with tempfile.TemporaryDirectory(prefix="meshforge_lab_synth_") as tmp:
        tmpdir = Path(tmp)
        _build_client_config(tmpdir)

        from lab._lab_common import init_reticulum_with_watchdog
        try:
            reticulum = init_reticulum_with_watchdog(str(tmpdir))
        except Exception as exc:
            logger.error("synth: RNS init failed: %s", exc)
            return _empty_report(
                started_at, pattern, n_users, interval_s, duration_s,
                ack_timeout_s, ok_ratio_threshold,
            )

        lxmf_storage = tmpdir / "lxmf"
        lxmf_storage.mkdir(parents=True, exist_ok=True)
        router = LXMF.LXMRouter(storagepath=str(lxmf_storage))

        # Single LXMF source identity for the whole synth (router is
        # single-identity). All virtual users share this destination on
        # the wire; differentiation is body-level (from=synth-uN).
        from lab._lab_common import load_or_create_identity, short_name as host_short
        identity, identity_path = load_or_create_identity("lab_synth")
        source = router.register_delivery_identity(
            identity, display_name=f"lab-synth ({host_short()})",
        )
        logger.info(
            "synth: identity %s, source destination %s",
            identity_path, source.hash.hex(),
        )

        users = _create_users(n_users)

        # Single shared inbound callback — dispatches by (orig, seq).
        def _on_receive(message):
            body = message.content
            if isinstance(body, bytes):
                try:
                    body = body.decode("utf-8")
                except UnicodeDecodeError:
                    body = body.decode("utf-8", errors="replace")
            ping = dispatch_ack_to_pending(body, pending, pending_lock)
            if ping is None:
                return
            ping.rtt_ms = int(
                (time.monotonic() - ping.sent_at_monotonic) * 1000
            )
            ping.ack_event.set()
            logger.info(
                "synth: ack user=%s peer=%s seq=%d rtt_ms=%d",
                ping.user_short, ping.peer_name, ping.seq, ping.rtt_ms,
            )

        router.register_delivery_callback(_on_receive)

        # Single announce — one source, one path back from peers.
        assert_rns_tx_allowed(kind="rns_announce", detail="lab multi-user synth announce")
        router.announce(source.hash)
        # Let announce propagate before any sends — RNS Transport
        # batches with a small jitter; 0.5s is plenty for LAN-scale.
        time.sleep(0.5)

        # Resolve all peer paths up-front. Path table is global, so
        # one resolve per peer suffices for all N users.
        resolvable_peers: List[Peer] = []
        for peer in peers:
            if _resolve_path(RNS, peer.dest_hash, path_timeout_s):
                resolvable_peers.append(peer)
                logger.info("synth: path ok peer=%s", peer.name)
            else:
                # All N users will record no-route for this peer.
                logger.info("synth: path timeout peer=%s", peer.name)
                for user in users:
                    early_failures.append(PairResult(
                        user_short=user.short_name, peer_name=peer.name,
                        samples=1, ok=0, timeout=0, no_route=1, send_error=0,
                    ))

        offsets = compute_pattern_offsets(pattern, n_users, interval_s, duration_s)

        # Spawn sender threads.
        deadline_send = time.monotonic() + duration_s
        stop_event = threading.Event()
        threads: List[threading.Thread] = []
        for user, offset in zip(users, offsets):
            t = threading.Thread(
                target=_sender_loop,
                name=f"synth-{user.short_name}",
                args=(
                    RNS, LXMF, router, source, user, resolvable_peers,
                    pattern, interval_s, offset, deadline_send,
                    pending, pending_lock,
                    early_failures, early_failures_lock,
                    stop_event,
                ),
                daemon=True,
            )
            threads.append(t)
            t.start()

        # Wait for sender phase to finish.
        for t in threads:
            t.join(timeout=duration_s + 5.0)
        # Belt-and-suspenders: if any thread hangs, signal stop.
        if any(t.is_alive() for t in threads):
            logger.warning("synth: sender thread(s) overran — signaling stop")
            stop_event.set()
            for t in threads:
                t.join(timeout=2.0)

        # ACK grace window — keep the inbound callback live for
        # ack_timeout_s after the last send.
        logger.info(
            "synth: sender phase done — waiting up to %.1fs for trailing ACKs",
            ack_timeout_s,
        )
        ack_deadline = time.monotonic() + ack_timeout_s
        with pending_lock:
            pending_snapshot = list(pending.values())
        for p in pending_snapshot:
            remaining = max(0.0, ack_deadline - time.monotonic())
            if remaining == 0.0:
                break
            p.ack_event.wait(remaining)

    # Aggregate.
    pair_results = _aggregate(n_users, peers, pending, early_failures)
    total_samples = sum(r.samples for r in pair_results)
    total_ok = sum(r.ok for r in pair_results)
    ratio = (total_ok / total_samples) if total_samples else 0.0

    finished_at = datetime.now(timezone.utc)
    return SynthReport(
        started_at_iso=started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        finished_at_iso=finished_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        pattern=pattern,
        n_users=n_users,
        interval_s=interval_s,
        duration_s=duration_s,
        ack_timeout_s=ack_timeout_s,
        pair_results=pair_results,
        total_samples=total_samples,
        total_ok=total_ok,
        ok_ratio_threshold=ok_ratio_threshold,
        pass_envelope=(ratio >= ok_ratio_threshold and total_samples > 0),
    )


def _empty_report(
    started_at: datetime, pattern: str, n_users: int,
    interval_s: float, duration_s: float, ack_timeout_s: float,
    ok_ratio_threshold: float,
) -> SynthReport:
    return SynthReport(
        started_at_iso=started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        finished_at_iso=started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        pattern=pattern, n_users=n_users, interval_s=interval_s,
        duration_s=duration_s, ack_timeout_s=ack_timeout_s,
        pair_results=[], total_samples=0, total_ok=0,
        ok_ratio_threshold=ok_ratio_threshold, pass_envelope=False,
    )


# -------------------------------------------------------------- output formats


def render_markdown(report: SynthReport) -> str:
    """Markdown table — same shape as scripts/lab_traffic_rollup.sh
    output so an operator can paste both into the same notebook."""
    lines: List[str] = []
    lines.append(
        f"# Multi-user synth — {report.pattern}, "
        f"N={report.n_users}, interval={report.interval_s}s, "
        f"duration={report.duration_s}s"
    )
    lines.append("")
    lines.append(
        f"- started:  `{report.started_at_iso}`"
    )
    lines.append(
        f"- finished: `{report.finished_at_iso}`"
    )
    lines.append(
        f"- ack timeout: {report.ack_timeout_s}s"
    )
    lines.append(
        f"- threshold: ≥{report.ok_ratio_threshold * 100:.0f}% ACK"
    )
    lines.append("")
    verdict = "PASS" if report.pass_envelope else "FAIL"
    lines.append(
        f"**{verdict}** — ok {report.total_ok}/{report.total_samples} "
        f"({report.ok_ratio * 100:.1f}%) vs threshold "
        f"{report.ok_ratio_threshold * 100:.0f}%"
    )
    lines.append("")
    lines.append("| user | peer | samples | ok | mean ms | p95 ms | fail % | breakdown |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---|")
    for r in report.pair_results:
        mean = f"{r.mean_ms:.0f}" if r.mean_ms is not None else "—"
        p95 = f"{r.p95_ms:.0f}" if r.p95_ms is not None else "—"
        breakdown_bits = []
        if r.timeout:
            breakdown_bits.append(f"timeout={r.timeout}")
        if r.no_route:
            breakdown_bits.append(f"no-route={r.no_route}")
        if r.send_error:
            breakdown_bits.append(f"send-error={r.send_error}")
        breakdown = " ".join(breakdown_bits) or "(none)"
        lines.append(
            f"| `{r.user_short}` | {r.peer_name} | {r.samples} | "
            f"{r.ok} | {mean} | {p95} | {r.fail_pct:.1f} | {breakdown} |"
        )
    return "\n".join(lines)


def render_json(report: SynthReport) -> str:
    """JSON output — for piping into a rollup aggregator or dashboard."""
    return json.dumps({
        "started_at": report.started_at_iso,
        "finished_at": report.finished_at_iso,
        "pattern": report.pattern,
        "n_users": report.n_users,
        "interval_s": report.interval_s,
        "duration_s": report.duration_s,
        "ack_timeout_s": report.ack_timeout_s,
        "ok_ratio_threshold": report.ok_ratio_threshold,
        "pass_envelope": report.pass_envelope,
        "total_samples": report.total_samples,
        "total_ok": report.total_ok,
        "ok_ratio": report.ok_ratio,
        "pair_results": [
            {
                "user": r.user_short,
                "peer": r.peer_name,
                "samples": r.samples,
                "ok": r.ok,
                "timeout": r.timeout,
                "no_route": r.no_route,
                "send_error": r.send_error,
                "mean_ms": r.mean_ms,
                "p95_ms": r.p95_ms,
                "fail_pct": r.fail_pct,
            }
            for r in report.pair_results
        ],
    }, indent=2)


# ------------------------------------------------------------------------ CLI


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--peers", type=Path, default=None,
        help="Override path to lab_peers file "
             "(default ~/.config/meshforge/lab_peers)",
    )
    parser.add_argument(
        "--exclude", default="",
        help="Comma-separated peer names to skip (e.g. 'moc3' if its "
             "echo is intentionally disabled). Case-insensitive. "
             "Self-loopback is always excluded.",
    )
    parser.add_argument(
        "--users", type=int, default=DEFAULT_N_USERS,
        help=f"Number of virtual users (default {DEFAULT_N_USERS})",
    )
    parser.add_argument(
        "--pattern", choices=PATTERNS, default="sustained",
        help="Send pattern (default sustained)",
    )
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S,
        help=f"Seconds between PINGs per user (default {DEFAULT_INTERVAL_S})",
    )
    parser.add_argument(
        "--duration", type=float, default=DEFAULT_DURATION_S,
        help=f"Send-phase duration in seconds (default {DEFAULT_DURATION_S})",
    )
    parser.add_argument(
        "--path-timeout", type=float, default=DEFAULT_PATH_TIMEOUT_S,
        help=f"Per-peer path resolve timeout (default {DEFAULT_PATH_TIMEOUT_S})",
    )
    parser.add_argument(
        "--ack-timeout", type=float, default=DEFAULT_ACK_TIMEOUT_S,
        help=f"Trailing ACK grace window (default {DEFAULT_ACK_TIMEOUT_S})",
    )
    parser.add_argument(
        "--ok-ratio-threshold", type=float, default=DEFAULT_OK_RATIO_THRESHOLD,
        help=f"Pass threshold for ok/total ratio (default {DEFAULT_OK_RATIO_THRESHOLD})",
    )
    parser.add_argument(
        "--output", choices=("markdown", "json"), default="markdown",
        help="Output format (default markdown)",
    )
    parser.add_argument(
        "--loglevel", default="INFO",
        help="Python logging level (default INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.loglevel.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    peers = load_peers(args.peers)
    if not peers:
        logger.error(
            "synth: no peers in %s — nothing to do",
            args.peers or "~/.config/meshforge/lab_peers",
        )
        return 2

    # Filter out self-loopback — synth users live on this same box, so
    # firing at our own echo would skew the numbers wildly. The tracer
    # keeps self-loopback as a feature (standalone validation); synth
    # is about fleet-wide stress.
    from lab._lab_common import short_name
    self_short = short_name()
    excludes = {
        e.strip().lower() for e in args.exclude.split(",") if e.strip()
    }
    excludes.add(self_short.lower())

    filtered = [p for p in peers if not _peer_matches_exclude(p.name, excludes)]
    if len(filtered) < len(peers):
        skipped = [p.name for p in peers if p not in filtered]
        logger.info("synth: skipping peer(s): %s", skipped)
    peers = filtered

    if not peers:
        logger.error(
            "synth: no peers left after exclusions — nothing to fire at",
        )
        return 2

    logger.info(
        "synth: starting %s N=%d interval=%.1fs duration=%.1fs against %d peer(s)",
        args.pattern, args.users, args.interval, args.duration, len(peers),
    )
    report = run_synth(
        peers,
        n_users=args.users,
        pattern=args.pattern,
        interval_s=args.interval,
        duration_s=args.duration,
        path_timeout_s=args.path_timeout,
        ack_timeout_s=args.ack_timeout,
        ok_ratio_threshold=args.ok_ratio_threshold,
    )

    if args.output == "json":
        print(render_json(report))
    else:
        print(render_markdown(report))

    # Observability tool — always exit 0. Pass/fail surfaces in the
    # report's verdict line, not the exit code. Same rationale as
    # tracer (see commit e27930e): a soak's job is to MEASURE, not
    # to gate. Gating would flag the systemd service red on every
    # routine fleet-state variance (moc3 always no-routes by design,
    # LongFast HI nightly degrades, etc.) and drown real crash signal.
    return 0


if __name__ == "__main__":
    sys.exit(main())

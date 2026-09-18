"""MeshCore dual-path channel state, policy and metrics — one owner.

Extracted from ``meshcore_handler.py`` on 2026-09-18. The handler kept NINE
attributes for the channel path (two hash maps, a lock, a window, a poll
cursor, a poll interval, a log interval, a metrics dict and the inbound
source-channel allowlist) while the code consuming them lived hundreds of
lines away in two async legs. That is a reader/writer pair split across a
boundary nothing enforces (honest_failure_modes #4), and it is the shape a
line-count cap notices without naming.

⚠️ Deliberately a COLLABORATOR, not a mixin. A mixin would have left all
nine attributes declared in the host's ``__init__`` and consumed here, so
composing it without the host's init would raise AttributeError — or worse,
half-wire the dedup path. Owning the state is the point; the flat namespace
was the problem, not the file length. (The operator's open question stands
and this is the answer for THIS cluster, not a verdict on mixins: MeshAnchor
composes five of them for good reasons.)

What did NOT move, and why: ``_on_channel_message`` and
``_poll_channel_messages`` stay in the handler. They are entangled with
handler concerns — the oracle, the bridge queue, the stats lock, the
message callback, the live ``MeshCore`` object and the connection
predicates — so relocating them would need an injected sink and source, a
larger change on a live gateway for benefit already obtained here: there is
exactly ONE place the inbound channel policy can live, so a guard cannot be
written on one leg and not its twin. Adjacency was a proxy for that; single
ownership is the thing itself.
"""

import hashlib
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# MeshCore's Public channel. Slot 0 is the network-wide open channel every
# MeshCore node carries; anything said there is readable by any stranger in
# radio range. ONE constant, because this gate and any future outbound rail
# must not hardcode it independently (honest_failure_modes #5 — two
# consumers of one concept WILL drift: 24,000 vs 24,576).
MESHCORE_PUBLIC_CHANNEL = 0

# Env override for the inbound source-channel allowlist.
BRIDGE_CHANNELS_ENV = "MESHFORGE_MESHCORE_BRIDGE_CHANNELS"


def resolve_bridge_channels(meshcore_config: Any) -> Optional[frozenset]:
    """Resolve the inbound source-channel allowlist.

    Returns a frozenset of permitted channel indices, or None meaning "all
    except Public". Precedence: env override > declared config > default.

    An explicit EMPTY list means "bridge nothing" and is honoured — refusing
    everything is a legitimate posture for a box that should only receive
    DMs, and silently reinterpreting it as "allow all" would be the
    degraded-value-looks-valid class this codebase exists to refuse.
    """
    raw = os.environ.get(BRIDGE_CHANNELS_ENV)
    if raw is None and meshcore_config is not None:
        declared = getattr(meshcore_config, 'bridge_source_channels', None)
        if declared is not None:
            raw = ",".join(str(t) for t in declared)
    if raw is None:
        return None  # default: everything but Public
    out = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.add(int(tok))
        except ValueError:
            # Loud, not absorbed: a typo'd channel must not silently narrow
            # OR widen the gate (honest_failure_modes #3).
            logger.warning(
                f"MeshCore bridge channel {tok!r} is not an int; ignored")
    return frozenset(out)


class ChannelPath:
    """Owns dedup state, poll pacing, metrics and the inbound channel policy."""

    def __init__(self, meshcore_config: Any = None, hash_window: int = 120,
                 metrics_log_interval: int = 50) -> None:
        self.poll_interval = (
            getattr(meshcore_config, 'channel_poll_interval_sec', 5)
            if meshcore_config else 5
        )
        self.metrics_log_interval = metrics_log_interval
        self._hash_window = hash_window
        self._last_poll = 0.0
        # Messages seen via event subscription / discovered via polling
        # (content_hash -> monotonic timestamp).
        self._event_hashes = {}
        self._poll_hashes = {}
        self._lock = threading.Lock()
        self._metrics = {
            'event_received': 0,      # Messages received via event subscription
            'poll_discovered': 0,     # Messages discovered via polling
            'event_missed': 0,        # Found by poll but not by event
            'duplicate_reconciled': 0,  # Same message seen from both paths
            'poll_cycles': 0,         # Total poll cycles run
            'last_event_time': None,  # Timestamp of last event-delivered msg
            'last_poll_time': None,   # Timestamp of last poll-delivered msg
            # Inbound channel messages refused by the source-channel policy.
            # A swallow with no witness never happened (hfm #9), so this is
            # counted here and printed by log_metrics() even when it is 0.
            'channel_suppressed': 0,
        }
        self.allowed_channels = resolve_bridge_channels(meshcore_config)

    # ---------------------------------------------------------------- policy

    def bridge_allowed(self, msg: Any) -> bool:
        """THE predicate both inbound channel legs derive from.

        ⚠️ ONE method, called from BOTH ``_on_channel_message`` and
        ``_poll_channel_messages``. Those are independent paths to the same
        bridge (event subscription + the #1232 polling fallback), and a
        filter on the event path alone would leak every message the event
        path happened to MISS — i.e. exactly the traffic the poll fallback
        exists to catch.

        Not called on the DM leg: a direct message has no channel (the
        oracle gates it with channel=None), so a channel policy cannot
        speak to it.
        """
        chan = (msg.metadata or {}).get('channel', MESHCORE_PUBLIC_CHANNEL)
        try:
            chan = int(chan)
        except (TypeError, ValueError):
            # Unparseable channel on a channel message: refuse. An unknown
            # source is not a known-safe source (unobservable != healthy).
            logger.warning(
                f"MeshCore channel message with unparseable channel "
                f"{chan!r}; refusing to bridge")
            return False
        if self.allowed_channels is None:
            return chan != MESHCORE_PUBLIC_CHANNEL
        return chan in self.allowed_channels

    def note_suppressed(self, msg: Any) -> None:
        """Record + disclose a refusal. Never silent."""
        with self._lock:
            self._metrics['channel_suppressed'] += 1
            n = self._metrics['channel_suppressed']
        chan = (msg.metadata or {}).get('channel', MESHCORE_PUBLIC_CHANNEL)
        # INFO for the first few so an operator sees it without DEBUG, then
        # back off — a busy Public channel must not flood the journal, but
        # the counter above keeps climbing and log_metrics() prints it.
        log = logger.info if n <= 3 or n % 100 == 0 else logger.debug
        log(f"MeshCore channel {chan} not in the inbound bridge allowlist; "
            f"message not bridged (suppressed={n}). Set "
            f"{BRIDGE_CHANNELS_ENV} to change this.")

    # ----------------------------------------------------------------- dedup

    @staticmethod
    def compute_hash(msg: Any) -> str:
        """Content hash for channel message dedup across paths."""
        key = f"{msg.source_address}:{msg.content}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def record_event(self, content_hash: str, now: float) -> bool:
        """Register an event-path sighting. Returns True if poll saw it first.

        ⚠️ The caller registers BEFORE the oracle may consume, so a consumed
        query is marked event-seen and the poll leg won't count it
        'event_missed' and re-bridge the very message the oracle took off
        the wire (the consume invariant — a consumed query must never reach
        the far mesh).
        """
        with self._lock:
            self._event_hashes[content_hash] = now
            self._metrics['event_received'] += 1
            self._metrics['last_event_time'] = datetime.now().isoformat()
            if content_hash in self._poll_hashes:
                self._metrics['duplicate_reconciled'] += 1
                return True
        return False

    def record_poll(self, content_hash: str, now: float) -> bool:
        """Register a poll-path sighting. Returns True if the event path had it."""
        with self._lock:
            self._poll_hashes[content_hash] = now
            self._metrics['last_poll_time'] = datetime.now().isoformat()
            if content_hash in self._event_hashes:
                self._metrics['duplicate_reconciled'] += 1
                return True
            self._metrics['poll_discovered'] += 1
            self._metrics['event_missed'] += 1
        return False

    def cleanup(self) -> None:
        """Remove expired entries from both hash maps."""
        cutoff = time.monotonic() - self._hash_window
        with self._lock:
            for m in (self._event_hashes, self._poll_hashes):
                for k in [k for k, v in m.items() if v < cutoff]:
                    del m[k]

    # ----------------------------------------------------------- poll pacing

    def poll_due(self, now: float) -> bool:
        return (now - self._last_poll) >= self.poll_interval

    def mark_polled(self, now: float) -> None:
        self._last_poll = now

    def begin_poll_cycle(self) -> int:
        with self._lock:
            self._metrics['poll_cycles'] += 1
            return self._metrics['poll_cycles']

    # --------------------------------------------------------------- metrics

    def snapshot(self) -> dict:
        with self._lock:
            return self._metrics.copy()

    def log_metrics(self) -> None:
        """Log periodic summary of dual-path metrics."""
        m = self.snapshot()
        total = m['event_received'] + m['poll_discovered']
        if total == 0:
            return
        event_pct = (m['event_received'] / total * 100) if total else 0
        miss_pct = (m['event_missed'] / total * 100) if total else 0
        logger.info(
            f"MeshCore channel metrics: "
            f"event={m['event_received']} ({event_pct:.0f}%), "
            f"poll_discovered={m['poll_discovered']}, "
            f"event_missed={m['event_missed']} ({miss_pct:.0f}%), "
            f"reconciled={m['duplicate_reconciled']}, "
            f"poll_cycles={m['poll_cycles']}, "
            f"channel_suppressed={m['channel_suppressed']}"
        )

"""Bounded ring buffer + pub/sub for RNS RPC wedge events.

When a `bounded_call` in the gateway hot path exceeds its timeout
budget, the default `on_wedge` hook publishes a `WedgeEvent` here.
Two consumers:

1. Operators reading live state via `bridge_cli wedge-events --recent N`
   (or the `/api/status.gateway.wedge_events` block) — forensic
   short-term trail of what RNS calls hung when.
2. The cascade recovery actor (`utils.cascade_recovery_actions`, Fork B)
   subscribes to events so a watchdog-triggered process abort leaves
   an audit row that recovery uses to correlate with the
   `rns_rpc_wedge` cascade fingerprint.

Sized for diagnostics, not durability. The ring caps at 200 events
(roughly a day of healthy traffic with the occasional outlier; a
sustained wedge will overflow and that's fine — the cascade detector
covers durable signaling). Process restart resets the buffer.

Module-level state with a `threading.Lock` so concurrent publishers
from gateway / map / lab threads don't race. Subscribers fire
synchronously inside `publish()` — callbacks must be fast and
non-blocking; slow subscribers will delay all publishers.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional


logger = logging.getLogger(__name__)


RING_BUFFER_CAP = 200
"""Max events retained in memory. Sized for ~1 day of healthy traffic
where wedges are exceptional events; tight enough that a long-running
process stays bounded; loose enough that a small burst doesn't
evict useful forensic context. Tune via the constructor argument
to `_Registry` if a deployment proves this wrong."""


@dataclass(frozen=True)
class WedgeEvent:
    """One RNS RPC wedge observation.

    `source` is the publisher subsystem ("gateway.rns",
    "node_tracker", etc.) — lets the cascade actor and operator UIs
    filter by origin without parsing the label.

    `label` and `target` mirror `boundary_timing.call_boundary` /
    `timed_boundary` naming, so cross-correlation with the p50/p95/p99
    metrics ring buffer is a string match.

    `note` is a free-text annotation (e.g. error context, the
    destination hash being addressed). Optional.
    """

    ts: float
    source: str
    label: str
    target: Optional[str]
    timeout_s: float
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


Subscriber = Callable[[WedgeEvent], None]


class _Registry:
    """Internal singleton state. Module-level singleton at the bottom."""

    def __init__(self, maxlen: int = RING_BUFFER_CAP) -> None:
        self._ring: Deque[WedgeEvent] = deque(maxlen=maxlen)
        self._subscribers: List[Subscriber] = []
        self._lock = threading.Lock()

    def publish(self, event: WedgeEvent) -> None:
        with self._lock:
            self._ring.append(event)
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub(event)
            except Exception:
                logger.exception(
                    "wedge_events subscriber raised — continuing dispatch"
                )

    def recent(self, limit: Optional[int] = None) -> List[WedgeEvent]:
        with self._lock:
            buf = list(self._ring)
        if limit is None or limit >= len(buf):
            return buf
        if limit <= 0:
            return []
        return buf[-limit:]

    def subscribe(self, cb: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(cb)

    def unsubscribe(self, cb: Subscriber) -> None:
        with self._lock:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass

    def clear(self) -> None:
        """Test-only reset. Not safe to call from production."""
        with self._lock:
            self._ring.clear()
            self._subscribers.clear()


_registry = _Registry()


def publish(event: WedgeEvent) -> None:
    """Append `event` to the ring and dispatch to every subscriber.

    Subscribers fire synchronously on the publishing thread; do not
    publish from inside a subscriber callback (recursive lock attempts
    would deadlock — keep subscribers fast and side-effect-free).
    """
    _registry.publish(event)


def recent(limit: Optional[int] = None) -> List[WedgeEvent]:
    """Return the most recent `limit` events (newest last), or all
    retained events when `limit` is None. Returns a copy; safe for
    callers to mutate."""
    return _registry.recent(limit)


def subscribe(cb: Subscriber) -> None:
    """Register a callback fired for every subsequent `publish()`.

    Callbacks run synchronously inside `publish()` on the publisher's
    thread — keep them fast. Exceptions from one subscriber don't stop
    dispatch to the others.
    """
    _registry.subscribe(cb)


def unsubscribe(cb: Subscriber) -> None:
    """Remove a previously registered subscriber. No-op if not present."""
    _registry.unsubscribe(cb)


def _reset_for_tests() -> None:
    """Internal helper: clear ring + subscribers between tests so state
    doesn't leak across cases. Production code must never call this."""
    _registry.clear()

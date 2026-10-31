"""Per-leg delivery counts in time windows — BIC-2 slice 1 (ROADMAP gate 3).

A **leg** is the direction a message crosses the bridge:
``<source transport>><destination transport>`` (``meshtastic>rns``). The
operator chose transport + direction (2026-10-23) because a leg-kill drill
kills a direction, and a Mesh→RNS outage must not hide behind healthy
RNS→Mesh traffic in one per-transport total.

Counts are kept in ``BUCKET_S`` buckets in the same SQLite file as
``delivery_counters`` (it owns the connection and the transaction; this
module only knows the table). On disk, so a gateway restart does not blind
the view the way the in-memory ``recent_terminal`` ring does (DET-DCS).

Observe before alarm: this module COUNTS. It derives no rate and raises no
alarm; that waits for a soak baseline.

Honesty rules the reader can rely on:

* A caller that did not say where a message came from is counted under
  ``?>dst`` — never guessed onto a real leg.
* A leg with no events in a window is ABSENT from that window, not zero:
  "nothing observed" and "observed nothing failing" are different claims.
* Bucket granularity is reported, not hidden: each window carries
  ``from_ts``, the start of the oldest bucket it summed, so a "15m" window
  that actually spans 15–30 min says so.

Known residual (hfm #6, wall clock): buckets key on the event's wall-clock
ts. A clock that jumps more than ``RETAIN_S`` FORWARD and then back would
let prune-on-insert delete real buckets. A stale clock stepping forward
(the RTC-less Pi case) is harmless — those buckets age out early.
"""
from __future__ import annotations

import itertools
import logging
import sqlite3
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

BUCKET_S = 300
"""Bucket width. A window sums every bucket that OVERLAPS it, so it can
reach back up to one bucket further than its label: "15m" covers 15–20 min.
That bounds N in the leg-kill drill ("leg shows down within N min") at
window + BUCKET_S. (Was 900 until review R9, 10-23: "15m" summed up to
30 min, so a killed leg kept showing sends for half an hour.)"""

WINDOWS = (("15m", 900), ("1h", 3600), ("24h", 86400))
"""(label, seconds) served by ``view``. The largest bounds retention."""

RETAIN_S = WINDOWS[-1][1] + 2 * BUCKET_S
"""Rows older than this are pruned on insert."""

UNKNOWN_SOURCE = "?"

_SYNTH_SEQ = itertools.count()


def synth_id(prefix: str) -> str:
    """A delivery id for a send that has no packet id. The process-wide
    sequence keeps two sends in one millisecond apart (review R5); the
    wall clock only orders them across restarts (hfm #6)."""
    return f"{prefix}-{int(time.time() * 1000)}-{next(_SYNTH_SEQ) & 0xFFFF:04x}"


LEG_SOURCE_KEY = "_leg_source"
"""Where a persistent-queue row carries its origin transport (BIC-2 slice
2). One constant for the writer (``enqueue``) and every reader (the queue's
lifecycle records, ``_queue_send_rns``), hfm #5."""


def payload_source(payload: Any) -> Optional[str]:
    """The origin a queue row's payload carries; None when it carries none."""
    if isinstance(payload, dict):
        v = payload.get(LEG_SOURCE_KEY)
        return v if isinstance(v, str) and v else None
    return None

TRANSPORTS = frozenset({"meshtastic", "meshcore", "rns", "mqtt", "local"})
"""Closed vocabulary for both ends of a leg (hfm #7). ``local`` = the
gateway authored the message (oracle replies, synthetic ACKs). Anything
else — a typo, a routing lane, a queue sender name like ``rns_xform`` —
counts as ``?``: visible, never a new leg nobody reads."""

OUTCOMES = ("queued", "sent", "confirmed", "failed", "dropped_other",
            "refused")
"""Closed vocabulary of what a bucket counts. ``failed`` = a delivery that
was attempted and failed; ``dropped_other`` = benign drops (dedup,
shedding, invalid payload) that are not delivery failures; ``refused`` =
the ingress policy declined a message the sender's transport had already
reported delivered (neither a failed attempt nor benign)."""


DISPATCH_DESTINATIONS = {"rns_xform": "rns"}
"""Queue senders that are NOT transports: a row bound for one is traffic
for the mapped transport, but its "sent" only hands it back to the
pipeline, whose real send records the real SENT. Counting the dispatch
too would double every spilled message; dropping the row's queued /
failed outcomes would hide a loss that never reached the transport."""


def leg_for(source: Optional[str], destination: Optional[str],
            outcome: str) -> Optional[str]:
    """The leg an outcome counts on; None when it counts on no leg."""
    if destination in DISPATCH_DESTINATIONS:
        if outcome == "sent":
            return None
        destination = DISPATCH_DESTINATIONS[destination]
    return leg_key(source, destination)


def leg_key(source: Optional[str], destination: Optional[str]) -> Optional[str]:
    """``src>dst``; None when there is no destination (nothing to count)."""
    if not destination:
        return None

    def _name(v):
        return v if v in TRANSPORTS else UNKNOWN_SOURCE
    return f"{_name(source)}>{_name(destination)}"


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS leg_buckets ("
        " bucket_ts INTEGER NOT NULL,"
        " leg TEXT NOT NULL,"
        " outcome TEXT NOT NULL,"
        " count INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY (bucket_ts, leg, outcome)"
        ")"
    )
    # When leg counting began in THIS file. Without it a 24h window on a
    # box that deployed an hour ago reads as 24 h of clean evidence.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS leg_meta ("
        " key TEXT PRIMARY KEY, value REAL NOT NULL)"
    )


def since_ts(conn: sqlite3.Connection) -> Optional[float]:
    row = conn.execute(
        "SELECT value FROM leg_meta WHERE key = 'since_ts'").fetchone()
    return float(row[0]) if row else None


def partial_windows(since: Optional[float], now: float) -> list:
    """Window labels that reach back before counting began. Every window
    is partial when the start is unknown."""
    return [label for label, seconds in WINDOWS
            if since is None or since > now - seconds]


def bump(conn: sqlite3.Connection, ts: float, leg: str, outcome: str) -> None:
    """Count one event. Runs inside the caller's transaction.

    The FIRST bump stamps ``since_ts`` with the WRITE time. Not schema
    creation: the map daemon (a reader) or an old gateway can create the
    tables hours before any leg is counted (review R5). Not the event's ts:
    a replayed or clock-stepped first event would pull the start back and
    make an uncovered span read whole (review F6)."""
    conn.execute(
        "INSERT INTO leg_meta(key, value) VALUES('since_ts', ?) "
        "ON CONFLICT(key) DO NOTHING",
        (time.time(),),
    )
    bucket = int(ts // BUCKET_S) * BUCKET_S
    conn.execute(
        "INSERT INTO leg_buckets(bucket_ts, leg, outcome, count) "
        "VALUES(?, ?, ?, 1) "
        "ON CONFLICT(bucket_ts, leg, outcome) DO UPDATE SET count = count + 1",
        (bucket, leg, outcome),
    )
    conn.execute("DELETE FROM leg_buckets WHERE bucket_ts < ?",
                 (bucket - RETAIN_S,))


def view(conn: sqlite3.Connection, now: float) -> Dict[str, Any]:
    """``{leg: {window: {outcome: n, ..., "from_ts": t}}}``.

    A window sums every bucket that overlaps ``(now - seconds, now]``.
    """
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for label, seconds in WINDOWS:
        cutoff = now - seconds
        rows = conn.execute(
            "SELECT leg, outcome, SUM(count), MIN(bucket_ts) FROM leg_buckets "
            "WHERE bucket_ts + ? > ? AND bucket_ts <= ? "
            "GROUP BY leg, outcome",
            (BUCKET_S, cutoff, now),
        ).fetchall()
        for leg, outcome, n, first in rows:
            win = out.setdefault(leg, {}).setdefault(label, {})
            win[outcome] = int(n)
            win["from_ts"] = min(win.get("from_ts", first), first)
    return out


def read(conn: sqlite3.Connection, now: float) -> Tuple:
    """``(legs, since_ts, partial_windows, write_errors, last_error_ts)``.

    Runs on the snapshot's connection (one read transaction, review F8) but
    fenced (review R6): a failure is legs-unobservable — None everywhere —
    never a blind core snapshot. The two write-error keys live in the
    ``counters`` table ``delivery_counters`` owns.
    """
    try:
        since = since_ts(conn)
        kv = dict(conn.execute(
            "SELECT key, value FROM counters WHERE key IN "
            "('legs.write_errors', 'legs.last_write_error_ts')").fetchall())
        last = kv.get("legs.last_write_error_ts")
        return (view(conn, now), since, partial_windows(since, now),
                int(kv.get("legs.write_errors", 0)),
                last / 1000.0 if last is not None else None)
    except sqlite3.Error as e:
        logger.warning("delivery_legs: leg view read failed: %s", e)
        return None, None, None, None, None

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

import sqlite3
from typing import Any, Dict, Optional

BUCKET_S = 900
"""Bucket width. 15 min = the smallest window, so N in the leg-kill drill
("leg shows down within N min") is bounded by one bucket."""

WINDOWS = (("15m", 900), ("1h", 3600), ("24h", 86400))
"""(label, seconds) served by ``view``. The largest bounds retention."""

RETAIN_S = WINDOWS[-1][1] + 2 * BUCKET_S
"""Rows older than this are pruned on insert."""

UNKNOWN_SOURCE = "?"

OUTCOMES = ("queued", "sent", "confirmed", "failed", "dropped_other")
"""Closed vocabulary of what a bucket counts. ``failed`` = a delivery that
was attempted and failed; ``dropped_other`` = benign drops (dedup,
shedding, invalid payload) that are not delivery failures."""


def leg_key(source: Optional[str], destination: Optional[str]) -> Optional[str]:
    """``src>dst``; None when there is no destination (nothing to count)."""
    if not destination:
        return None
    return f"{source or UNKNOWN_SOURCE}>{destination}"


def init_schema(conn: sqlite3.Connection, now: float) -> None:
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
    conn.execute(
        "INSERT INTO leg_meta(key, value) VALUES('since_ts', ?) "
        "ON CONFLICT(key) DO NOTHING",
        (now,),
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
    """Count one event. Runs inside the caller's transaction."""
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

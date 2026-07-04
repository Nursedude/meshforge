"""Kilo readings store — the durable telemetry time-series (SQLite, MF013).

One narrow table: a reading is (ts, transport, node_key, kilo_id, metric,
value). ``node_key`` is the raw transport identity as heard (e.g. the
Meshtastic ``!hex``); ``kilo_id`` is the registry join stamped at ingest
time (NULL for unregistered senders — those are DISCOVERY candidates, not
noise, and ``kilo discover`` lists them).

Growth honesty: UNIQUE(transport, node_key, metric, ts) makes re-observed
snapshots idempotent (INSERT OR IGNORE), and ``prune()`` enforces the
30-day retention declared in utils.db_inventory — the DBSpec and this
module are pinned together by test (two consumers, one constant).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.db_helpers import connect_tuned
from utils.paths import get_real_user_home

DB_BASENAME = "kilo_telemetry.db"
RETENTION_DAYS = 30  # pinned to the DBSpec entry by test

# metric name -> display unit (derivable, so not a schema column)
UNITS = {
    "temperature": "°C", "humidity": "%", "pressure": "hPa",
    "gas_resistance": "Ω", "co2": "ppm", "iaq": "idx",
    "pm25_standard": "µg/m³", "pm10_standard": "µg/m³",
    "voltage": "V", "battery_level": "%",
    "channel_utilization": "%", "air_util_tx": "%",
    "snr": "dB", "rssi": "dBm",
}


def db_path() -> Path:
    return (get_real_user_home() / ".local" / "share" / "meshforge"
            / DB_BASENAME)


def open_db(path: Optional[str] = None):
    """Open (creating schema if needed) with the tuned pragmas (MF013)."""
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_tuned(p)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            ts        REAL NOT NULL,
            transport TEXT NOT NULL,
            node_key  TEXT NOT NULL,
            kilo_id   TEXT,
            metric    TEXT NOT NULL,
            value     REAL NOT NULL,
            UNIQUE (transport, node_key, metric, ts)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_node "
                 "ON readings (node_key, metric, ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_ts "
                 "ON readings (ts)")
    conn.commit()
    return conn


def record_readings(conn, rows: List[Tuple[float, str, str, Optional[str],
                                           str, float]]) -> int:
    """INSERT OR IGNORE rows of (ts, transport, node_key, kilo_id, metric,
    value); returns how many actually landed (idempotent re-observation)."""
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO readings "
        "(ts, transport, node_key, kilo_id, metric, value) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return conn.total_changes - before


def prune(conn, retention_days: float = RETENTION_DAYS,
          now: Optional[float] = None) -> int:
    """Drop readings older than the retention window; returns rows removed."""
    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400.0
    before = conn.total_changes
    conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
    conn.commit()
    return conn.total_changes - before


def latest_by_kilo(conn) -> Dict[Tuple[str, str], Tuple[float, float]]:
    """{(kilo_id, metric): (ts, value)} — newest reading per registered
    node per metric (the status join's observation side)."""
    out: Dict[Tuple[str, str], Tuple[float, float]] = {}
    for kid, metric, ts, value in conn.execute(
            "SELECT kilo_id, metric, MAX(ts), value FROM readings "
            "WHERE kilo_id IS NOT NULL GROUP BY kilo_id, metric"):
        out[(kid, metric)] = (ts, value)
    return out


def seen_unregistered(conn) -> List[dict]:
    """Discovery candidates: senders heard on the air with NO registry
    match — each with count, last-heard, and the metrics they emit."""
    rows = conn.execute(
        "SELECT transport, node_key, COUNT(*), MAX(ts), "
        "GROUP_CONCAT(DISTINCT metric) FROM readings "
        "WHERE kilo_id IS NULL GROUP BY transport, node_key "
        "ORDER BY MAX(ts) DESC").fetchall()
    return [{"transport": t, "node_key": k, "readings": c,
             "last_ts": ts, "metrics": (m or "").split(",")}
            for t, k, c, ts, m in rows]

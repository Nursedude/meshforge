"""Kilo readings + edges store — the durable telemetry tables (SQLite, MF013).

Two narrow tables. A reading is (ts, transport, node_key, kilo_id, metric,
value). ``node_key`` is the raw transport identity as heard (e.g. the
Meshtastic ``!hex``); ``kilo_id`` is the registry join stamped at ingest
time (NULL for unregistered senders — those are DISCOVERY candidates, not
noise, and ``kilo discover`` lists them). An edge (K1) is one per-packet
(receiver ← sender) RF sounding; see the schema comment in open_db().

Growth honesty: UNIQUE guards make re-observation idempotent (INSERT OR
IGNORE), and ``prune()``/``prune_edges()`` enforce the retentions declared
in utils.db_inventory — the DBSpec and this module are pinned together by
test (two consumers, one constant).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.db_helpers import connect_tuned
from utils.paths import MeshForgePaths

DB_BASENAME = "kilo_telemetry.db"
RETENTION_DAYS = 30  # pinned to the DBSpec entry by test
# Edges are per-packet (high volume) — shorter retention; this is also the
# baseline-drift horizon for `kilo matrix`. Test-pinned ≤ RETENTION_DAYS.
EDGE_RETENTION_DAYS = 7

# metric name -> display unit (derivable, so not a schema column).
# Closed-consumers gate: tests pin that every ingest vocabulary entry
# (NODE_METRICS, CLAW_METRICS) has a unit here — a metric added on one
# side without the other fails a test, not a display.
UNITS = {
    "temperature": "°C", "humidity": "%", "pressure": "hPa",
    "gas_resistance": "Ω", "co2": "ppm", "iaq": "idx",
    "pm25_standard": "µg/m³", "pm10_standard": "µg/m³",
    "voltage": "V", "battery_level": "%",
    "channel_utilization": "%", "air_util_tx": "%",
    "snr": "dB", "rssi": "dBm",
    # claw (WireClaw last-tick) vocabulary, K0.1
    "heap_free_bytes": "B", "heap_total_bytes": "B", "uptime_s": "s",
    "wifi_rssi_dbm": "dBm", "ble_adv_age_s": "s", "ble_advs": "",
    "ble_last_rssi_dbm": "dBm",
}


def db_path() -> Path:
    return MeshForgePaths.get_data_dir() / DB_BASENAME


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
    # K1 edges: one row per packet a receiver heard — (receiver ← sender)
    # RF soundings. snr/rssi describe the LAST HOP into the receiver;
    # hops_away==0 marks a true direct edge, NULL means the packet didn't
    # say (unknown ≠ direct). relay_partial is the last byte of the
    # relayer's id when present. packet_id dedups re-observation of the
    # same packet — NULL packet ids never dedup (SQLite UNIQUE treats
    # NULLs as distinct), which records everything rather than guessing.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS edges (
            ts            REAL NOT NULL,
            receiver      TEXT NOT NULL,
            sender        TEXT NOT NULL,
            channel       TEXT,
            snr           REAL,
            rssi          REAL,
            hops_away     INTEGER,
            hop_start     INTEGER,
            relay_partial INTEGER,
            packet_id     TEXT,
            UNIQUE (receiver, sender, packet_id)
        )
    """)
    # idx_edges_pair had NO reader (dedup rides the UNIQUE auto-index;
    # every query filters on ts) — pure per-insert write amplification on
    # SD. Dropped 2026-07-05; the DROP cleans existing fleet DBs.
    conn.execute("DROP INDEX IF EXISTS idx_edges_pair")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_ts "
                 "ON edges (ts)")
    conn.commit()
    return conn


def _insert_ignore(conn, sql: str, rows: List[tuple],
                   commit: bool = True) -> int:
    """Shared INSERT OR IGNORE mechanics for both tables — returns how
    many rows actually landed. ``commit=False`` lets a bounded collect
    window batch its per-tick writes into one WAL commit (SD wear)."""
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(sql, rows)
    if commit:
        conn.commit()
    return conn.total_changes - before


def _delete_older_than(conn, table: str, retention_days: float,
                       now: Optional[float]) -> int:
    """Shared retention mechanics; returns rows removed. ``table`` is one
    of this module's two literals, never caller input."""
    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400.0
    before = conn.total_changes
    conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))  # nosec — literal
    conn.commit()
    return conn.total_changes - before


def record_readings(conn, rows: List[Tuple[float, str, str, Optional[str],
                                           str, float]],
                    commit: bool = True) -> int:
    """INSERT OR IGNORE rows of (ts, transport, node_key, kilo_id, metric,
    value); returns how many actually landed (idempotent re-observation)."""
    return _insert_ignore(
        conn,
        "INSERT OR IGNORE INTO readings "
        "(ts, transport, node_key, kilo_id, metric, value) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows, commit=commit)


def prune(conn, retention_days: float = RETENTION_DAYS,
          now: Optional[float] = None) -> int:
    """Drop readings older than the retention window; returns rows removed."""
    return _delete_older_than(conn, "readings", retention_days, now)


def record_edges(conn, rows: List[tuple], commit: bool = True) -> int:
    """INSERT OR IGNORE rows of (ts, receiver, sender, channel, snr, rssi,
    hops_away, hop_start, relay_partial, packet_id); returns how many
    landed. First-heard wins for a re-observed packet id."""
    return _insert_ignore(
        conn,
        "INSERT OR IGNORE INTO edges "
        "(ts, receiver, sender, channel, snr, rssi, hops_away, hop_start, "
        "relay_partial, packet_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows, commit=commit)


def prune_edges(conn, retention_days: float = EDGE_RETENTION_DAYS,
                now: Optional[float] = None) -> int:
    """Drop edges older than the edge retention window; returns rows
    removed. Runs on the write path (`kilo collect`) only — status/matrix
    /discover are pure reads."""
    return _delete_older_than(conn, "edges", retention_days, now)


def edges_since(conn, since_ts: float) -> List[tuple]:
    """(ts, receiver, sender, snr, hops_away) for every edge at or after
    ``since_ts`` — the matrix/baseline working set. Receiver/sender are
    stored lowercased at parse time, so no read-time folding here. No
    ORDER BY: the sole consumer (build_matrix) buckets and takes medians,
    which are order-independent."""
    return conn.execute(
        "SELECT ts, receiver, sender, snr, hops_away FROM edges "
        "WHERE ts >= ?", (since_ts,)).fetchall()


def latest_by_key(conn) -> Dict[Tuple[str, str], Tuple[float, float]]:
    """{(node_key.lower(), metric): (ts, value)} — newest reading per
    SENDER per metric. The registry join happens at READ time against the
    CURRENT anchors (re-derive, never trust the ingest-time stamp): a node
    registered after its first readings landed still owns its history.
    The stored kilo_id column remains as the historical witness of what
    the registry said at capture time."""
    out: Dict[Tuple[str, str], Tuple[float, float]] = {}
    for key, metric, ts, value in conn.execute(
            "SELECT node_key, metric, MAX(ts), value FROM readings "
            "GROUP BY node_key, metric"):
        # GROUP BY is case-sensitive but the fold key is lowercased — two
        # case-variant groups for one identity must resolve newest-wins,
        # never iteration-order-wins (an older group would fabricate a
        # stale age for a live node).
        k = (str(key).lower(), metric)
        cur = out.get(k)
        if cur is None or ts > cur[0]:
            out[k] = (ts, value)
    return out


def seen_keys(conn) -> List[dict]:
    """Every sender heard, grouped — the CLI splits registered vs
    discovery candidates against the CURRENT anchor map at read time."""
    rows = conn.execute(
        "SELECT transport, node_key, COUNT(*), MAX(ts), "
        "GROUP_CONCAT(DISTINCT metric) FROM readings "
        "GROUP BY transport, node_key "
        "ORDER BY MAX(ts) DESC").fetchall()
    return [{"transport": t, "node_key": k, "readings": c,
             "last_ts": ts, "metrics": (m or "").split(",")}
            for t, k, c, ts, m in rows]

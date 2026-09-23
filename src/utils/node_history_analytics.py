"""Read-only analytics over the LIVE node history store.

Why this exists (2026-09-23): Dashboard › Analytics read three tables in
``analytics.db`` that nothing has ever written — ``record_link_budget``,
``record_network_health`` and ``record_coverage`` have had no caller since
the module landed, and the tables were empty or absent on all 10 boxes. The
screens said "Data is collected when nodes exchange packets"; nothing was.

``node_history.db`` IS written, continuously, by the map collector
(``NodeHistoryDB.record_observations``): per node and per observation a
timestamp, online flag, position, SNR and battery where the source carries
them. On a box with a radio that is tens of thousands of rows per 48 h. These
functions answer the Analytics questions from it.

Contract, every function:
  * opens the DB READ-ONLY (``mode=ro`` via ``connect_tuned``) — a screen
    must never create or migrate a store (``NodeHistoryDB()`` would mkdir and
    create the schema);
  * returns a dict whose ``state`` says what was observed:
    ``absent`` (no DB on this box), ``unreadable`` (it exists but could not
    be read — ``error`` says why), ``empty`` (readable, nothing in the
    window) or ``ok``. Absent and unreadable are never folded into an empty
    result (honest_failure_modes #1/#2);
  * carries ``window_h`` and the sample counts it judged from, so a screen can
    say what the numbers are OF.

What a row IS (measured 2026-09-23, read before trusting any number here):
the collector writes a SNAPSHOT of every node in the map's node table each
cycle (throttled to one row per node per ``MIN_RECORD_INTERVAL``). ``snr``
and ``battery`` are the node's LAST-HEARD values, repeated across snapshots
until a new packet changes them; ``is_online`` is the node table's own
recently-heard flag. So: "nodes in an hour" is KNOWN nodes, not heard ones —
the heard count is ``online``; and a value repeated across snapshots is ONE
reading. Trend/slope code therefore uses online rows only and collapses
consecutive repeats per node before counting samples.
"""
from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.db_helpers import connect_tuned
from utils.paths import get_real_user_home

DEFAULT_WINDOW_H = 48          # node_observations retention default is 48 h
TREND_EDGE_H = 6               # compare the first vs last 6 h of the window
TREND_MIN_SAMPLES = 3          # per node, per edge window
PREDICT_MIN_SAMPLES = 6        # per node, for a slope
BATTERY_SLOPE_ALERT = -1.0     # %/h — flag a node losing a percent an hour or faster
BATTERY_FLOOR = 20.0           # % — ETA is to this level
SNR_SLOPE_ALERT = -0.5         # dB/h
BATTERY_MAX_REAL = 100         # Meshtastic reports 101 for "on external power"


def default_db_path() -> Path:
    """Where the map collector writes node history (same as NodeHistoryDB)."""
    return get_real_user_home() / ".local" / "share" / "meshforge" / "node_history.db"


def _open(db_path: Optional[Path]):
    """(state, conn_or_error). Never creates the file."""
    path = Path(db_path) if db_path is not None else default_db_path()
    if not path.exists():
        return "absent", str(path)
    conn = None
    try:
        conn = connect_tuned(f"file:{path}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM node_observations LIMIT 1").fetchall()
        return "ok", conn
    except sqlite3.Error as e:
        if conn is not None:
            conn.close()
        return "unreadable", f"{path}: {e}"


def _base(state: str, detail: Any, window_h: float) -> Dict[str, Any]:
    out: Dict[str, Any] = {"state": state, "window_h": window_h}
    if state == "absent":
        out["path"] = detail
    elif state == "unreadable":
        out["error"] = detail
    return out


def _slope(points: List[tuple]) -> Optional[float]:
    """Least-squares slope of (t_seconds, value), in value per HOUR."""
    n = len(points)
    if n < 2:
        return None
    mt = sum(p[0] for p in points) / n
    mv = sum(p[1] for p in points) / n
    den = sum((p[0] - mt) ** 2 for p in points)
    if den <= 0:
        return None
    return sum((p[0] - mt) * (p[1] - mv) for p in points) / den * 3600.0


def _readings(c, column: str, cut: float, valid) -> Dict[str, List[tuple]]:
    """{node_id: [(ts, value), ...]} from ONLINE rows, oldest first, with
    consecutive identical values collapsed to their first occurrence."""
    out: Dict[str, List[tuple]] = {}
    for node_id, ts, v in c.execute(
            f"SELECT node_id, timestamp, {column} FROM node_observations"
            f" WHERE timestamp > ? AND is_online = 1 AND {column} IS NOT NULL"
            " ORDER BY node_id, timestamp", (cut,)):
        if not valid(v):
            continue
        seq = out.setdefault(node_id, [])
        if seq and seq[-1][1] == v:
            continue
        seq.append((ts, float(v)))
    return out


def _count(c, cut: float) -> int:
    return c.execute("SELECT COUNT(*) FROM node_observations WHERE timestamp > ?",
                     (cut,)).fetchone()[0] or 0


def _names(c, cut: float) -> Dict[str, str]:
    return {nid: (nm or nid) for nid, nm in c.execute(
        "SELECT node_id, MAX(name) FROM node_observations WHERE timestamp > ?"
        " GROUP BY node_id", (cut,))}


def health_timeline(db_path: Optional[Path] = None, *, window_h: float = DEFAULT_WINDOW_H,
                    now: Optional[float] = None) -> Dict[str, Any]:
    """Per hour: distinct nodes heard, how many were online, mean SNR."""
    state, c = _open(db_path)
    out = _base(state, c, window_h)
    if state != "ok":
        return out
    now = time.time() if now is None else now
    cut = now - window_h * 3600
    try:
        rows = c.execute(
            "SELECT CAST(timestamp/3600 AS INTEGER) AS h, COUNT(DISTINCT node_id),"
            " COUNT(DISTINCT CASE WHEN is_online=1 THEN node_id END),"
            " AVG(CASE WHEN is_online=1 THEN snr END),"
            " SUM(is_online=1 AND snr IS NOT NULL), COUNT(*)"
            " FROM node_observations WHERE timestamp > ? GROUP BY h ORDER BY h",
            (cut,)).fetchall()
        mqtt = c.execute("SELECT SUM(via_mqtt=1), COUNT(*) FROM node_observations"
                         " WHERE timestamp > ?", (cut,)).fetchone()
    except sqlite3.Error as e:
        return _base("unreadable", str(e), window_h)
    finally:
        c.close()
    if not rows:
        out["state"] = "empty"
        return out
    this_hour = int(now // 3600)
    out["hours"] = [{"hour_epoch": h * 3600, "known": n, "online": on,
                     "avg_snr_online": s, "snr_samples": ns, "observations": obs,
                     "partial": h == this_hour}
                    for h, n, on, s, ns, obs in rows]
    out["observations"] = mqtt[1] or 0
    out["via_mqtt"] = mqtt[0] or 0
    return out


def link_trends(db_path: Optional[Path] = None, *, window_h: float = DEFAULT_WINDOW_H,
                edge_h: float = TREND_EDGE_H, min_samples: int = TREND_MIN_SAMPLES,
                top: int = 8, now: Optional[float] = None) -> Dict[str, Any]:
    """Per node: mean SNR over the FIRST edge_h hours of the window vs the
    LAST edge_h hours. Only nodes with >= min_samples SNR readings in both
    edges are judged; everything else is counted, not guessed."""
    state, c = _open(db_path)
    out = _base(state, c, window_h)
    if state != "ok":
        return out
    now = time.time() if now is None else now
    start = now - window_h * 3600
    first_end, last_start = start + edge_h * 3600, now - edge_h * 3600
    try:
        total = _count(c, start)
        series = _readings(c, "snr", start, lambda v: True)
        names = _names(c, start)
    except sqlite3.Error as e:
        return _base("unreadable", str(e), window_h)
    finally:
        c.close()
    # "empty" = no observations at all. Observations without an online SNR
    # reading (an MQTT-only box) are a different fact: ok, 0 nodes with SNR.
    if not total:
        out["state"] = "empty"
        return out
    out["nodes_with_snr"] = len(series)
    out.update(edge_h=edge_h, min_samples=min_samples)
    judged = []
    for node_id, pts in series.items():
        early = [v for t, v in pts if t <= first_end]
        late = [v for t, v in pts if t >= last_start]
        if len(early) >= min_samples and len(late) >= min_samples:
            e, l_ = sum(early) / len(early), sum(late) / len(late)
            judged.append({"node_id": node_id, "name": names.get(node_id, node_id),
                           "snr_first": e, "snr_last": l_, "delta_db": l_ - e,
                           "samples_first": len(early), "samples_last": len(late)})
    out["nodes_judged"] = len(judged)
    judged.sort(key=lambda r: r["delta_db"])
    out["declining"] = [r for r in judged if r["delta_db"] < 0][:top]
    out["improving"] = [r for r in reversed(judged) if r["delta_db"] > 0][:top]
    return out


def predictive(db_path: Optional[Path] = None, *, window_h: float = DEFAULT_WINDOW_H,
               min_samples: int = PREDICT_MIN_SAMPLES, now: Optional[float] = None) -> Dict[str, Any]:
    """Nodes whose battery or SNR is FALLING, by least-squares slope over the
    window. A slope is only computed from >= min_samples readings; battery
    values above 100 (Meshtastic's "external power") are not a charge level."""
    state, c = _open(db_path)
    out = _base(state, c, window_h)
    if state != "ok":
        return out
    now = time.time() if now is None else now
    cut = now - window_h * 3600
    try:
        # 0 is excluded with >100: many boards report 0 when they have no
        # battery monitor, and a slope into "0" would page on a mains node.
        total = _count(c, cut)
        bat = _readings(c, "battery", cut, lambda v: 0 < v <= BATTERY_MAX_REAL)
        snr = _readings(c, "snr", cut, lambda v: True)
        names = _names(c, cut)
    except sqlite3.Error as e:
        return _base("unreadable", str(e), window_h)
    finally:
        c.close()
    bat_eval = {k: v for k, v in bat.items() if len(v) >= min_samples}
    snr_eval = {k: v for k, v in snr.items() if len(v) >= min_samples}
    out.update(min_samples=min_samples, battery_nodes_judged=len(bat_eval),
               snr_nodes_judged=len(snr_eval),
               battery_slope_alert=BATTERY_SLOPE_ALERT, snr_slope_alert=SNR_SLOPE_ALERT)
    if not total:
        out["state"] = "empty"
        return out
    alerts = []
    for node_id, pts in bat_eval.items():
        slope = _slope(pts)
        if slope is not None and slope <= BATTERY_SLOPE_ALERT:
            last = pts[-1][1]
            eta = (last - BATTERY_FLOOR) / -slope if last > BATTERY_FLOOR else 0.0
            alerts.append({"kind": "battery", "node_id": node_id, "name": names.get(node_id, node_id),
                           "slope": slope, "last": last, "samples": len(pts),
                           "eta_h_to_floor": eta})
    for node_id, pts in snr_eval.items():
        slope = _slope(pts)
        if slope is not None and slope <= SNR_SLOPE_ALERT:
            alerts.append({"kind": "snr", "node_id": node_id, "name": names.get(node_id, node_id),
                           "slope": slope, "last": pts[-1][1], "samples": len(pts)})
    alerts.sort(key=lambda a: (a["kind"] != "battery", a["slope"]))
    out["alerts"] = alerts
    return out


def _km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def coverage(db_path: Optional[Path] = None, *, window_h: float = DEFAULT_WINDOW_H,
             now: Optional[float] = None) -> Dict[str, Any]:
    """Nodes with a position in the window: count by network, how many came
    over RF vs MQTT, and the extent (bounding box and its diagonal)."""
    state, c = _open(db_path)
    out = _base(state, c, window_h)
    if state != "ok":
        return out
    now = time.time() if now is None else now
    cut = now - window_h * 3600
    try:
        heard = c.execute("SELECT COUNT(DISTINCT node_id) FROM node_observations"
                          " WHERE timestamp > ?", (cut,)).fetchone()[0]
        # Each node's NEWEST positioned observation (an explicit join — a bare
        # column beside two aggregates is not guaranteed to come from the
        # max-timestamp row in SQLite).
        pos = ("timestamp > ? AND latitude IS NOT NULL AND longitude IS NOT NULL"
               " AND NOT (latitude = 0 AND longitude = 0)")
        rows = c.execute(
            "SELECT o.node_id, o.network, o.via_mqtt, o.latitude, o.longitude"
            " FROM node_observations o JOIN (SELECT node_id, MAX(timestamp) AS mt"
            f" FROM node_observations WHERE {pos} GROUP BY node_id) l"
            " ON o.node_id = l.node_id AND o.timestamp = l.mt", (cut,)).fetchall()
        rows = list({r[0]: r for r in rows}.values())  # one row per node
    except sqlite3.Error as e:
        return _base("unreadable", str(e), window_h)
    finally:
        c.close()
    # Nodes in the history this window — which records POSITIONED nodes only
    # (record_observations skips features without coordinates). Not "heard".
    out["nodes_known"] = heard or 0
    if not rows:
        out["state"] = "empty"
        return out
    by_net: Dict[str, int] = {}
    for _n, net, _m, _la, _lo in rows:
        by_net[net or "unknown"] = by_net.get(net or "unknown", 0) + 1
    # Extent from the CENTRAL 90% of positions: the node table carries junk
    # and far-away fixes (0.03°N and 48°N beside a Hawaii fleet — a 6,607 km
    # "diagonal" when measured 2026-09-23). The rest are counted, not drawn.
    lats = sorted(r[3] for r in rows)
    lons = sorted(r[4] for r in rows)
    lo_i, hi_i = int(0.05 * (len(rows) - 1)), int(round(0.95 * (len(rows) - 1)))
    box = {"south": lats[lo_i], "north": lats[hi_i], "west": lons[lo_i], "east": lons[hi_i]}
    outside = sum(1 for r in rows if not (box["south"] <= r[3] <= box["north"]
                                          and box["west"] <= r[4] <= box["east"]))
    out.update(
        positioned=len(rows), by_network=by_net,
        via_mqtt=sum(1 for r in rows if r[2] == 1),
        extent_90=box, outside_extent=outside,
        extent_diagonal_km=_km(box["south"], box["west"], box["north"], box["east"]),
    )
    return out

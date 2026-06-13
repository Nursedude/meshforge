"""Traffic Pulse — the heartbeat of real gateway traffic flow.

A read-only aggregator that buckets live gateway traffic into five axes —
**telemetry · RF · DUPs · diagnostics · QA** — for the TUI "Traffic
Heartbeat" view. It answers *what is actually moving through the gateway
right now*, where the topology graph (``gateway.network_topology``) only
shows *who exists*.

Scope: this module is **observability only** — like ``delivery_counters``,
it reads existing stores, creates no DB, never writes, and never raises out
of :func:`pulse_snapshot`. The gateway/map daemons own the writes; the TUI
is a reader. Delivery data comes over HTTP from the map daemon's
``/api/gateway/delivery`` (the same source the #74 watchdog probe reads).
RF/telemetry come from a strictly read-only (``mode=ro``) open of
``node_observations`` (``node_history.db``): the fleet runs mqtt_bridge mode,
so Meshtastic SNR/battery arrive over MQTT and are decoded into per-node
observations — there is no capturable local-radio packet stream. Per-box only
— fleet aggregation is a later phase that points the HTTP base at a peer.

Honest-failure-mode contract (``.claude/rules/honest_failure_modes.md``):

* **Read error / source down ≠ empty / healthy.** A block whose source
  cannot be read returns ``status="unobservable"`` with a ``reason`` — never
  a fabricated "0 packets / all confirmed / recovered". Absence of evidence
  is surfaced as its own state, not as good news (checklist 1, 2, 9).
* **QA confirmation is judged honestly.** The cumulative
  ``confirmation_rate`` from ``delivery_counters`` is the meaningless
  cross-population ratio ``RNS-confirmed ÷ all-sent`` (#74) — Meshtastic
  sends never confirm (no ACK consumption yet), so a naive panel reads
  ~0–50% as "delivery failure". The QA block instead judges **only
  confirmable protocols' real terminal outcomes** over the recent-events
  ring, sharing :data:`_DELIVERY_FAILURE_REASONS` with the watchdog probe so
  the two never drift (checklist 5).
* **Collector-stale ≠ mesh-quiet ≠ feed-dark.** The telemetry/RF blocks
  distinguish "node collector isn't recording" (unobservable — newest
  observation is stale) from "collector alive but the mesh is genuinely quiet"
  (quiet). ``channel_feed_dark`` (a watchdog signal surfaced in the diag
  block) owns true dark-feed detection.
"""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import urlopen

from utils.db_helpers import connect_tuned
from utils.db_inventory import INVENTORY
from utils.logging_config import get_logger

logger = get_logger(__name__)

# ── Block status vocabulary ──────────────────────────────────────────
# A closed set so the TUI renderer can switch on it. "unobservable" is the
# honest answer when the source can't be read — it is NEVER collapsed into
# "ok" or a zero count.
OK = "ok"                       # observed and active/healthy
QUIET = "quiet"                 # observed, collector alive, ~no activity in window
STALE = "stale"                 # data present but no recent activity
ALERT = "alert"                 # observed anomaly worth attention
UNOBSERVABLE = "unobservable"   # could not read / source down / stale — HELD

DEFAULT_BASE_URL = "http://127.0.0.1:5000"
DEFAULT_WINDOW_S = 3600         # RF/telemetry node-freshness window (60m). Node
                                # telemetry is periodic, so a 15m window is too
                                # sparse to read SNR/battery health honestly.
DEFAULT_TIMEOUT_S = 3.0
STALE_AFTER_S = 3600            # delivery counters older than this → STALE
NODE_STALE_AFTER_S = 7200       # newest node observation older → collector stopped
MIN_CONFIRMABLE_TERMINAL = 20   # mirrors the #74 probe's min_terminal
CONF_DEGRADED = 0.50
CONF_WEDGE = 0.10

# Drop reasons that mean a delivery was ATTEMPTED and FAILED — the
# denominator-mates of `confirmed`. Shared with the #74 probe so the two
# consumers can never drift (honest_failure_modes checklist item 5). Pinned
# mirror only if the import fails; `tests/test_traffic_pulse.py` asserts the
# effective set equals the watchdog module's.
try:
    from utils.watchdog_probes_gateway import _DELIVERY_FAILURE_REASONS
except Exception:  # pragma: no cover - exercised only on a broken import graph
    _DELIVERY_FAILURE_REASONS = frozenset({
        "rns_delivery_failed", "retries_exhausted", "destination_unreachable",
        "delivery_timeout", "non_retriable_error", "circuit_open", "wedged",
    })

# Benign drops — capacity/duplicate management, NOT delivery failures.
_BENIGN_DROP_REASONS = frozenset({
    "dedup", "queue_pressure", "queue_shed", "evicted_overflow",
})


# ─────────────────────────────────────────────────────────────────────
# Path + read-only connection helpers
# ─────────────────────────────────────────────────────────────────────


def _db_path(name: str) -> Optional[Path]:
    """Resolve a DB's canonical runtime path from the inventory SSOT."""
    for spec in INVENTORY:
        if spec.name == name:
            try:
                return spec.path_factory()
            except Exception as exc:  # path factory should never fail; be safe
                logger.debug("traffic_pulse: path_factory(%s) failed: %s", name, exc)
                return None
    return None


def _ro_connect(path: Path):
    """Open a strictly read-only connection (writes blocked) or None.

    Uses ``connect_tuned(mode=ro)`` — MF013-compliant and side-effect-free
    on an existing WAL DB held open by a daemon writer.
    """
    if not path or not path.exists():
        return None
    try:
        conn = connect_tuned(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = __import__("sqlite3").Row
        return conn
    except Exception as exc:
        logger.debug("traffic_pulse: ro-connect %s failed: %s", path, exc)
        return None


# ─────────────────────────────────────────────────────────────────────
# Source loaders (HTTP-first, read-only DB fallback)
# ─────────────────────────────────────────────────────────────────────


def _http_json(url: str, timeout_s: float) -> Optional[dict]:
    try:
        with urlopen(url, timeout=timeout_s) as resp:  # nosec - localhost map daemon
            payload = json.loads(resp.read())
        return payload if isinstance(payload, dict) else None
    except (URLError, socket.timeout, json.JSONDecodeError, OSError, ValueError):
        return None


def _load_delivery(base_url: str, timeout_s: float) -> Tuple[Optional[dict], str]:
    """Return (delivery_snapshot, source). source ∈ {http, db, none}.

    Prefer the served ``/api/gateway/delivery`` (zero side effects, identical
    to ``delivery_counters.snapshot()``). Fall back to a read-only DB parse
    when the map daemon is down so a single-box TUI still works.
    """
    payload = _http_json(f"{base_url}/api/gateway/delivery", timeout_s)
    if payload is not None:
        return payload, "http"
    db = _parse_delivery_db()
    if db is not None:
        return db, "db"
    return None, "none"


def _parse_delivery_db() -> Optional[dict]:
    """Reconstruct the delivery snapshot from a read-only DB read.

    Mirrors the key scheme written by ``gateway.delivery_counters`` —
    ``state.<state>``, ``state_proto.<state>.<proto>``, ``drop.<reason>``,
    ``meta.*`` — and the events ring. Pinned to that writer; if the scheme
    there changes, this fallback (and its test) updates with it.
    """
    path = _db_path("delivery_counters")
    conn = _ro_connect(path) if path else None
    if conn is None:
        return None
    try:
        state_totals: Dict[str, int] = {}
        state_by_protocol: Dict[str, Dict[str, int]] = {}
        drop_reasons: Dict[str, int] = {}
        last_event_ts = None
        preflight_ok = None
        write_errors = 0
        for row in conn.execute("SELECT key, value FROM counters"):
            key, value = row["key"], row["value"]
            if key.startswith("state_proto."):
                _, state_v, proto = key.split(".", 2)
                state_by_protocol.setdefault(state_v, {})[proto] = value
            elif key.startswith("state."):
                state_totals[key.split(".", 1)[1]] = value
            elif key.startswith("drop."):
                drop_reasons[key.split(".", 1)[1]] = value
            elif key == "meta.last_event_ts":
                last_event_ts = value / 1000.0
            elif key == "meta.preflight_ok":
                preflight_ok = bool(value)
            elif key == "meta.consecutive_write_errors":
                write_errors = int(value)
        recent: List[Dict[str, Any]] = []
        rows = conn.execute(
            "SELECT ts, id, state, protocol, drop_reason FROM events "
            "ORDER BY ts DESC LIMIT 50"
        ).fetchall()
        for r in reversed(rows):  # snapshot contract is newest-LAST
            recent.append({
                "ts": r["ts"], "id": r["id"], "state": r["state"],
                "protocol": r["protocol"], "drop_reason": r["drop_reason"],
            })
        sent = state_totals.get("sent", 0)
        confirmed = state_totals.get("confirmed", 0)
        return {
            "state_totals": state_totals,
            "state_by_protocol": state_by_protocol,
            "drop_reasons": drop_reasons,
            "confirmation_rate": (confirmed / sent if sent > 0 else None),
            "recent": recent,
            "last_event_ts": last_event_ts,
            "health": {
                "preflight_ok": preflight_ok,
                "consecutive_write_errors": write_errors,
                "last_successful_write_ts": last_event_ts,
            },
        }
    except Exception as exc:
        logger.debug("traffic_pulse: delivery DB parse failed: %s", exc)
        return None
    finally:
        conn.close()


@dataclass
class _NodeFacts:
    """Read-only RF/telemetry facts from node_observations over a window.

    The fleet runs mqtt_bridge mode: Meshtastic telemetry/RF arrives over MQTT
    and is decoded into per-node observations (snr, rssi, battery), NOT a
    capturable local-radio packet stream. So the RF/telemetry pulse is sourced
    from the node-observation time-series — all networks, read-only, windowed.
    """
    status: str
    reason: str = ""
    window_s: int = 0
    obs: int = 0
    nodes: int = 0
    latest_age_s: Optional[float] = None
    snr_values: List[float] = None          # type: ignore[assignment]
    snr_nodes: int = 0
    rssi_values: List[int] = None           # type: ignore[assignment]
    rssi_nodes: int = 0
    battery_values: List[int] = None        # type: ignore[assignment]
    battery_nodes: int = 0


def _node_facts(window_s: int, now: datetime) -> _NodeFacts:
    """Strictly read-only RF/telemetry facts from node_observations.

    Liveness is derived from data freshness: observations in the window prove
    the collector is live; a newest observation hours old means it is
    effectively stopped → unobservable, never a fabricated "quiet".
    """
    path = _db_path("node_history")
    if path is None or not path.exists():
        return _NodeFacts(status=UNOBSERVABLE, reason="db_absent", window_s=window_s)
    conn = _ro_connect(path)
    if conn is None:
        return _NodeFacts(status=UNOBSERVABLE, reason="db_unreadable", window_s=window_s)
    try:
        latest = conn.execute(
            "SELECT MAX(timestamp) FROM node_observations").fetchone()[0]
        latest_age = (now.timestamp() - float(latest)) if latest is not None else None
        cutoff = now.timestamp() - window_s
        snr: List[float] = []
        rssi: List[int] = []
        battery: List[int] = []
        node_ids = set()
        snr_node_ids = set()
        rssi_node_ids = set()
        bat_node_ids = set()
        obs = 0
        # rssi was added later (collector migration) — an un-migrated DB read
        # restart-free has no rssi column, so select it only when present
        # rather than crash the whole read.
        obs_cols = {r[1] for r in
                    conn.execute("PRAGMA table_info(node_observations)")}
        has_rssi = "rssi" in obs_cols
        sel = ("SELECT node_id, snr, battery"
               + (", rssi" if has_rssi else "")
               + " FROM node_observations WHERE timestamp > ?")
        for r in conn.execute(sel, (cutoff,)):
            obs += 1
            nid = r["node_id"]
            if nid:
                node_ids.add(nid)
            if r["snr"] is not None:
                snr.append(float(r["snr"]))
                snr_node_ids.add(nid)
            if has_rssi and r["rssi"] is not None:
                rssi.append(int(r["rssi"]))
                rssi_node_ids.add(nid)
            if r["battery"] is not None:
                battery.append(int(r["battery"]))
                bat_node_ids.add(nid)
        if obs > 0:
            status, reason = OK, ""
        elif latest is None:
            status, reason = UNOBSERVABLE, "no_observations"
        elif latest_age is not None and latest_age > NODE_STALE_AFTER_S:
            status, reason = UNOBSERVABLE, "collector_stale"
        else:
            status, reason = QUIET, ""
        return _NodeFacts(
            status=status, reason=reason, window_s=window_s, obs=obs,
            nodes=len(node_ids), latest_age_s=latest_age, snr_values=snr,
            snr_nodes=len(snr_node_ids), rssi_values=rssi,
            rssi_nodes=len(rssi_node_ids), battery_values=battery,
            battery_nodes=len(bat_node_ids),
        )
    except Exception as exc:
        logger.debug("traffic_pulse: node_observations read failed: %s", exc)
        return _NodeFacts(status=UNOBSERVABLE, reason="read_error", window_s=window_s)
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# Environment-sensor enrichment (temp/humidity/pressure)
# ─────────────────────────────────────────────────────────────────────
# node_observations carries only snr+battery, so the richer device telemetry
# (BME280/680 temp/humidity/pressure) comes from the MQTT subscriber's node
# cache. The cache is atomically written (no torn reads) and already pruned to
# current nodes, so we read the whole set rather than window it (its last_seen
# is a human age-string, not a timestamp). Env sensors are SPARSE on a mesh:
# "none reporting" is a benign info state, never an error, and a missing cache
# must NOT degrade the battery-based telemetry status.


def _mqtt_nodes_path() -> Optional[Path]:
    try:
        from utils.paths import get_real_user_home
        return (get_real_user_home() / ".local" / "share" / "meshforge"
                / "mqtt_nodes.json")
    except Exception as exc:
        logger.debug("traffic_pulse: mqtt_nodes path resolve failed: %s", exc)
        return None


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _avg1(vals: List[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 1) if vals else None


def _mqtt_node_facts() -> Dict[str, Any]:
    """Read-only environment (temp/humidity/pressure) aggregates from the MQTT
    subscriber's node cache — the device-telemetry fields node_observations
    lacks. (RSSI is NOT taken from here: on this mqtt_bridge fleet the cache's
    rssi is structurally empty — relayed nodes have no local RF leg — so RSSI
    is sourced from node_observations alongside SNR instead.)

    ``status`` reports only whether the cache was READABLE (ok / unobservable);
    whether a given metric is present is just its count (env sensors are sparse
    — zero is benign, not an error).
    """
    path = _mqtt_nodes_path()
    if path is None or not path.exists():
        return {"status": UNOBSERVABLE, "reason": "cache_absent"}
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug("traffic_pulse: mqtt_nodes.json read failed: %s", exc)
        return {"status": UNOBSERVABLE, "reason": "cache_unreadable"}
    feats = data.get("features") if isinstance(data, dict) else None
    if not isinstance(feats, list):
        return {"status": UNOBSERVABLE, "reason": "cache_shape"}
    temps: List[float] = []
    hums: List[float] = []
    press: List[float] = []
    for f in feats:
        if not isinstance(f, dict):
            continue
        p = f.get("properties") or {}
        t = _coerce_float(p.get("temperature"))
        if t is not None and -50.0 <= t <= 100.0:   # filter bogus sensors
            temps.append(t)
        h = _coerce_float(p.get("humidity"))
        if h is not None and 0.0 <= h <= 100.0:
            hums.append(h)
        pr = _coerce_float(p.get("pressure"))
        if pr is not None and 800.0 <= pr <= 1100.0:
            press.append(pr)
    return {
        "status": OK,
        "total_nodes": len(feats),
        "temp_nodes": len(temps), "avg_temp_c": _avg1(temps),
        "humidity_nodes": len(hums), "avg_humidity": _avg1(hums),
        "pressure_nodes": len(press), "avg_pressure_hpa": _avg1(press),
    }


# ─────────────────────────────────────────────────────────────────────
# Per-axis blocks
# ─────────────────────────────────────────────────────────────────────


def _node_unobservable_block(nf: _NodeFacts) -> Dict[str, Any]:
    """Shared unobservable shape for telemetry/RF when node data can't be read.

    Absent/stale node data is unobservable, NOT quiet — the panel must not
    imply the mesh is silent when the collector simply isn't recording.
    """
    reason = nf.reason or "no_observations"
    if reason == "collector_stale":
        age_m = int((nf.latest_age_s or 0) // 60)
        detail = (f"Newest node observation is ~{age_m}m old — the node "
                  "collector appears stopped; RF/telemetry not observable now.")
    elif reason == "no_observations":
        detail = ("No node observations recorded yet — RF/telemetry not "
                  "observable (not the same as a quiet mesh).")
    else:  # db_absent / db_unreadable / read_error
        detail = ("Node observation store unavailable — cannot observe "
                  "RF/telemetry on this box.")
    return {"status": UNOBSERVABLE, "reason": reason, "detail": detail}


def _attach_env(block: Dict[str, Any], mqtt: Optional[Dict[str, Any]]) -> None:
    """Fold MQTT environment metrics (temp/humidity/pressure) into a populated
    telemetry block.

    Always records the env sub-block (so the panel shows observability); only
    extends the headline detail when sensors actually reported — env sensors
    are sparse, so silence here is normal, not a gap to flag.
    """
    if not isinstance(mqtt, dict):
        return
    status = mqtt.get("status")
    block["env"] = {
        "status": status,
        "temp_nodes": mqtt.get("temp_nodes"), "avg_temp_c": mqtt.get("avg_temp_c"),
        "humidity_nodes": mqtt.get("humidity_nodes"),
        "avg_humidity": mqtt.get("avg_humidity"),
        "pressure_nodes": mqtt.get("pressure_nodes"),
        "avg_pressure_hpa": mqtt.get("avg_pressure_hpa"),
    }
    if status != OK:
        return
    bits = []
    if mqtt.get("avg_temp_c") is not None:
        bits.append(f"{mqtt['temp_nodes']} temp ~{mqtt['avg_temp_c']}°C")
    if mqtt.get("avg_humidity") is not None:
        bits.append(f"{mqtt['humidity_nodes']} RH ~{mqtt['avg_humidity']}%")
    if mqtt.get("avg_pressure_hpa") is not None:
        bits.append(f"{mqtt['pressure_nodes']} baro ~{mqtt['avg_pressure_hpa']}hPa")
    if bits:
        block["detail"] += " · env: " + ", ".join(bits)


def _attach_rssi(block: Dict[str, Any], nf: _NodeFacts) -> None:
    """Fold node_observations RSSI into a populated RF block. Only extends the
    detail when nodes actually carried RSSI (a node heard over relayed/MQTT
    paths has no local RF leg, so RSSI is sparse — silence here is normal)."""
    if not nf.rssi_values:
        return
    avg = round(sum(nf.rssi_values) / len(nf.rssi_values))
    block["rssi_nodes"] = nf.rssi_nodes
    block["avg_rssi_dbm"] = avg
    block["detail"] += f" · RSSI ~{avg} dBm ({nf.rssi_nodes} nodes)"


def _telemetry_block(nf: _NodeFacts,
                     mqtt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if nf.status == UNOBSERVABLE:
        return _node_unobservable_block(nf)
    win_m = nf.window_s // 60
    if nf.status == QUIET or nf.obs == 0:
        return {"status": QUIET, "telemetry_nodes": 0, "nodes_heard": nf.nodes,
                "detail": f"No node activity in the last {win_m}m — mesh quiet."}
    if nf.battery_nodes == 0:
        # Nodes are heard but none reported device telemetry this window —
        # normal (telemetry is periodic), not an alarm.
        block = {"status": OK, "telemetry_nodes": 0, "nodes_heard": nf.nodes,
                 "detail": f"{nf.nodes} nodes heard in {win_m}m, none reporting "
                           "device telemetry (battery) — telemetry is periodic."}
    else:
        avg_bat = round(sum(nf.battery_values) / len(nf.battery_values))
        block = {"status": OK, "telemetry_nodes": nf.battery_nodes,
                 "nodes_heard": nf.nodes, "avg_battery": avg_bat,
                 "detail": f"{nf.battery_nodes} of {nf.nodes} nodes reporting "
                           f"device telemetry in {win_m}m · avg battery {avg_bat}%."}
    _attach_env(block, mqtt)
    return block


def _rf_quality_band(avg_snr: Optional[float]) -> str:
    """Map mean SNR to the rf.py quality bands (best-effort import)."""
    if avg_snr is None:
        return "n/a"
    try:
        from utils import rf
        for name, thresh in (("excellent", getattr(rf, "SNR_EXCELLENT", -3.0)),
                             ("good", getattr(rf, "SNR_GOOD", -7.0)),
                             ("fair", getattr(rf, "SNR_FAIR", -15.0))):
            if avg_snr > thresh:
                return name
        return "bad"
    except Exception:
        # Pinned fallback mirrors rf.py SNR bands.
        if avg_snr > -3.0:
            return "excellent"
        if avg_snr > -7.0:
            return "good"
        if avg_snr > -15.0:
            return "fair"
        return "bad"


def _rf_block(nf: _NodeFacts) -> Dict[str, Any]:
    if nf.status == UNOBSERVABLE:
        return _node_unobservable_block(nf)
    win_m = nf.window_s // 60
    if nf.status == QUIET or not nf.snr_values:
        if nf.status == QUIET:
            return {"status": QUIET, "snr_samples": 0,
                    "detail": f"No node activity in the last {win_m}m."}
        # Nodes heard but none carried SNR — relayed/RNS nodes have no RF leg.
        return {"status": UNOBSERVABLE, "reason": "no_snr_samples", "snr_samples": 0,
                "detail": f"{nf.nodes} nodes heard in {win_m}m but none carried "
                          "SNR (relayed/RNS nodes have no RF leg)."}
    avg_snr = sum(nf.snr_values) / len(nf.snr_values)
    band = _rf_quality_band(avg_snr)
    status = ALERT if band == "bad" else OK
    block = {"status": status, "snr_samples": len(nf.snr_values),
             "snr_nodes": nf.snr_nodes, "avg_snr": round(avg_snr, 1),
             "min_snr": round(min(nf.snr_values), 1), "quality_band": band,
             "detail": f"{len(nf.snr_values)} SNR samples from {nf.snr_nodes} nodes "
                      f"in {win_m}m · avg {round(avg_snr, 1)} dB ({band})."}
    _attach_rssi(block, nf)
    return block


def _dups_block(delivery: Optional[dict]) -> Dict[str, Any]:
    if delivery is None:
        return {"status": UNOBSERVABLE, "reason": "delivery_source_down",
                "detail": "Delivery counters unreachable — dedup count "
                          "unobservable."}
    drops = delivery.get("drop_reasons") or {}
    dedup_total = int(drops.get("dedup", 0) or 0)
    state_totals = delivery.get("state_totals") or {}
    queued = int(state_totals.get("queued", 0) or 0)
    # Denominator = enqueue attempts that survived dedup + the dedup drops.
    ingress = queued + dedup_total
    dedup_pct = round(100.0 * dedup_total / ingress, 1) if ingress > 0 else 0.0
    # Windowed dedup from the recent ring (catches a recent dup storm the
    # lifetime total would dilute).
    recent = delivery.get("recent") or []
    window_dedup = sum(
        1 for e in recent if isinstance(e, dict)
        and e.get("state") == "dropped" and e.get("drop_reason") == "dedup"
    )
    # Dedup is BENIGN by design — the gateway correctly suppressing duplicates
    # from dual-path delivery. A high LIFETIME count/pct is the system WORKING,
    # not an alarm. Only a recent STORM (a big slice of the event ring is dedup
    # drops → possible routing loop / retransmit flood) is worth flagging.
    status = ALERT if window_dedup >= 20 else OK
    return {"status": status, "dedup_total": dedup_total, "dedup_pct": dedup_pct,
            "window_dedup": window_dedup, "ingress": ingress,
            "detail": f"{dedup_total} duplicates suppressed "
                      f"({dedup_pct}% of ingress, benign) · {window_dedup} in the "
                      f"recent window."}


def _traffic_signal_classes() -> frozenset:
    return frozenset({
        "channel_feed_dark", "delivery_confirmation_stall", "mqtt_root_drift",
        "queue_backlog", "delivery_write_canary", "aredn_source_dark",
        "phoneapi_tcp_leak",
    })


def _diag_block(status_blob: Optional[dict]) -> Dict[str, Any]:
    """Active watchdog signals (read-only) tagged for traffic relevance."""
    if not isinstance(status_blob, dict):
        return {"status": UNOBSERVABLE, "reason": "status_source_down",
                "signals": [],
                "detail": "/api/status unreachable — watchdog signals "
                          "unobservable."}
    wd = status_blob.get("watchdog") or {}
    if not wd.get("installed"):
        return {"status": UNOBSERVABLE, "reason": "watchdog_absent",
                "signals": [],
                "detail": "Watchdog not installed on this box."}
    raw = wd.get("signals") or []
    traffic_classes = _traffic_signal_classes()
    signals = []
    worst = OK
    for s in raw:
        if not isinstance(s, dict):
            continue
        # /api/status serializes the Signal.cls field as "class"; in-process
        # callers may pass "cls". Accept either.
        cls = s.get("class") or s.get("cls")
        sev = (s.get("severity") or "info").lower()
        relevant = cls in traffic_classes
        signals.append({"cls": cls, "subject": s.get("subject"),
                        "severity": sev, "issue_ref": s.get("issue_ref"),
                        "detail": s.get("detail"), "traffic_relevant": relevant})
        if relevant and sev in ("degraded", "wedge"):
            worst = ALERT
    n_rel = sum(1 for s in signals if s["traffic_relevant"])
    return {"status": worst, "signals": signals,
            "active_total": len(signals), "traffic_relevant": n_rel,
            "detail": (f"{n_rel} traffic-relevant of {len(signals)} active "
                       f"watchdog signal(s).") if signals else
                      "No active watchdog signals."}


def _honest_confirmation(delivery: dict) -> Dict[str, Any]:
    """Judge ONLY confirmable protocols' real terminal outcomes (#74).

    Returns a status + windowed rate, NEVER the cross-population
    confirmation_rate. Mirrors ``probe_delivery_confirmation_stall``.
    """
    confirmed_by_proto = (delivery.get("state_by_protocol") or {}).get("confirmed") or {}
    confirmable = {
        p for p, c in confirmed_by_proto.items()
        if isinstance(c, (int, float)) and not isinstance(c, bool) and c > 0
    }
    if not confirmable:
        return {"status": UNOBSERVABLE, "reason": "no_confirmable_protocol",
                "confirmable": [],
                "detail": "No protocol records delivery confirmations here "
                          "(Meshtastic has no ACK consumption yet — Thread-2 "
                          "step 4). Sends are real; confirmation is unobservable."}
    recent = delivery.get("recent")
    if not isinstance(recent, list):
        return {"status": UNOBSERVABLE, "reason": "no_recent_ring",
                "confirmable": sorted(confirmable), "detail": "No recent-event ring."}
    confirmed = failed = 0
    for e in recent:
        if not isinstance(e, dict) or e.get("protocol") not in confirmable:
            continue
        st = e.get("state")
        if st == "confirmed":
            confirmed += 1
        elif st == "dropped" and e.get("drop_reason") in _DELIVERY_FAILURE_REASONS:
            failed += 1
    terminal = confirmed + failed
    if terminal < MIN_CONFIRMABLE_TERMINAL:
        return {"status": UNOBSERVABLE, "reason": "insufficient_sample",
                "confirmable": sorted(confirmable), "terminal": terminal,
                "detail": f"Only {terminal} confirmable terminal events in the "
                          f"window (<{MIN_CONFIRMABLE_TERMINAL}) — too small to "
                          f"judge honestly."}
    rate = confirmed / terminal
    if rate <= CONF_WEDGE:
        status = ALERT
    elif rate <= CONF_DEGRADED:
        status = ALERT
    else:
        status = OK
    return {"status": status, "confirmable": sorted(confirmable),
            "confirmed": confirmed, "failed": failed, "terminal": terminal,
            "rate": round(rate, 3),
            "detail": f"{confirmed}/{terminal} {', '.join(sorted(confirmable))} "
                      f"confirmed in the window ({rate:.0%})."}


def _queue_facts() -> Dict[str, Any]:
    """Read-only message_queue depth + dead-letter count."""
    path = _db_path("message_queue")
    conn = _ro_connect(path) if path else None
    if conn is None:
        return {"status": UNOBSERVABLE, "reason": "queue_unreadable"}
    try:
        counts: Dict[str, int] = {}
        for r in conn.execute("SELECT status, COUNT(*) n FROM messages GROUP BY status"):
            counts[r["status"]] = r["n"]
        backlog = counts.get("pending", 0) + counts.get("in_progress", 0)
        dead = counts.get("dead_letter", 0)
        return {"status": OK, "by_status": counts, "backlog": backlog,
                "dead_letter": dead}
    except Exception as exc:
        logger.debug("traffic_pulse: queue read failed: %s", exc)
        return {"status": UNOBSERVABLE, "reason": "read_error"}
    finally:
        conn.close()


def _qa_block(delivery: Optional[dict], now: datetime) -> Dict[str, Any]:
    """The app diagnosing its own delivery quality & assured reliability.

    Confirmation honesty + failure-vs-benign drops + write canary +
    freshness + queue depth → a single reliability verdict.
    """
    if delivery is None:
        return {"status": UNOBSERVABLE, "reason": "delivery_source_down",
                "detail": "Delivery counters unreachable — gateway reliability "
                          "unobservable."}
    state_totals = delivery.get("state_totals") or {}
    drops = delivery.get("drop_reasons") or {}
    sent = int(state_totals.get("sent", 0) or 0)
    dropped = int(state_totals.get("dropped", 0) or 0)
    failed_drops = sum(int(drops.get(r, 0) or 0) for r in _DELIVERY_FAILURE_REASONS)
    benign_drops = sum(int(drops.get(r, 0) or 0) for r in _BENIGN_DROP_REASONS)
    circuit_open = int(drops.get("circuit_open", 0) or 0)

    confirmation = _honest_confirmation(delivery)
    queue = _queue_facts()

    # Freshness — counters are cumulative; if nothing has happened in a long
    # while, the gateway is idle/inactive (stale), not "100% healthy".
    last_event_ts = delivery.get("last_event_ts")
    if last_event_ts is None:
        last_event_ts = (delivery.get("health") or {}).get("last_successful_write_ts")
    age_s = None
    if isinstance(last_event_ts, (int, float)):
        age_s = max(0.0, now.timestamp() - float(last_event_ts))

    health = delivery.get("health") or {}
    write_errors = int(health.get("consecutive_write_errors", 0) or 0)
    preflight_ok = health.get("preflight_ok")

    # ── Synthesize verdict ──
    # Hard failures (live reliability breakage) flip ALERT and own the
    # headline. Staleness (idle/inactive gateway) is its own honest state, NOT
    # "100% healthy". Secondary concerns (dead-letters, backlog) are always
    # listed but never masquerade as a live outage on an idle box.
    hard_fail: List[str] = []
    if write_errors > 0 or preflight_ok is False:
        hard_fail.append(f"delivery write canary failing ({write_errors} errors)")
    if confirmation["status"] == ALERT:
        hard_fail.append(f"confirmation collapsed ({confirmation.get('rate', 0):.0%})")
    if circuit_open > 0:
        hard_fail.append(f"{circuit_open} circuit-open drops")

    concerns: List[str] = []
    dead = queue.get("dead_letter")
    if isinstance(dead, int) and dead > 0:
        concerns.append(f"{dead} dead-letter messages")
    backlog = queue.get("backlog")
    if isinstance(backlog, int) and backlog >= 50:
        concerns.append(f"{backlog} messages backlogged")

    stale = age_s is not None and age_s > STALE_AFTER_S

    if hard_fail:
        status = ALERT
        verdict = "Reliability concern: " + "; ".join(hard_fail)
        verdict += (" (also: " + "; ".join(concerns) + ")" if concerns else "") + "."
    elif stale:
        status = STALE
        verdict = (f"No delivery activity for {int(age_s // 60)}m — gateway "
                   "idle/inactive (counters healthy)")
        verdict += (" but " + "; ".join(concerns) if concerns else "") + "."
    else:
        status = OK
        # Don't claim reliability we can't verify: only say "reliably moving"
        # when confirmation is actually observed healthy. Otherwise we are
        # sending without confirmation — honest, not a failure, not a guarantee.
        if confirmation["status"] == OK:
            verdict = (f"Gateway is reliably moving traffic "
                       f"(confirmation {confirmation.get('rate', 0):.0%})")
        else:
            verdict = ("Sending traffic, no hard failures — delivery "
                       "confirmation not observable here")
        verdict += (" — note: " + "; ".join(concerns) if concerns else "") + "."

    return {"status": status, "verdict": verdict, "concerns": concerns,
            "confirmation": confirmation, "queue": queue,
            "sent": sent, "dropped": dropped, "failed_drops": failed_drops,
            "benign_drops": benign_drops, "circuit_open": circuit_open,
            "write_errors": write_errors, "preflight_ok": preflight_ok,
            "last_event_age_s": round(age_s) if age_s is not None else None,
            # Surfaced ONLY labelled — never as the headline reliability number.
            "cumulative_confirmation_rate_cross_population": delivery.get("confirmation_rate")}


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


def pulse_snapshot(*, base_url: str = DEFAULT_BASE_URL,
                   window_s: int = DEFAULT_WINDOW_S,
                   timeout_s: float = DEFAULT_TIMEOUT_S,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """Aggregate the five-axis traffic pulse. JSON-serializable; never raises.

    Read-only: HTTP to the map daemon for delivery/status, ``mode=ro`` DB reads
    for node-observation (RF/telemetry) and queue stats. Per-box only.
    """
    now = now or datetime.now()
    delivery, delivery_src = _load_delivery(base_url, timeout_s)
    status_blob = _http_json(f"{base_url}/api/status", timeout_s)
    nf = _node_facts(window_s, now)
    mqtt = _mqtt_node_facts()

    return {
        "meta": {
            "generated_ts": now.timestamp(),
            "window_s": window_s,
            "base_url": base_url,
            "scope": "local_box",
            "delivery_source": delivery_src,
            "status_source": "http" if status_blob is not None else "none",
            "nodes_heard": nf.nodes,
            "node_obs": nf.obs,
            "node_obs_latest_age_s": (round(nf.latest_age_s)
                                      if nf.latest_age_s is not None else None),
        },
        "telemetry": _telemetry_block(nf, mqtt),
        "rf": _rf_block(nf),
        "dups": _dups_block(delivery),
        "diag": _diag_block(status_blob),
        "qa": _qa_block(delivery, now),
    }

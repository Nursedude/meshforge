"""Kilo ingest — bounded MQTT collection window into the readings store.

Rides the EXISTING hardened pipeline end to end: radio → meshtasticd →
mosquitto → ``MQTTNodelessSubscriber`` (the #73-hardened client that
already decodes environment/power/RF-health telemetry into ``MQTTNode``)
→ this module snapshots those nodes on a sample cadence and persists
CHANGED readings. Zero new RF traffic, zero PhoneAPI reads (#17): LoRa
airtime stays the measured subject, never the transport.

A collection window is a bounded run (``kilo collect --seconds N``), not
a daemon — K0 proves the path; a resident collector unit is a later rung
and will arrive with its own systemd template + watchdog probe.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Tuple

from kilo.registry import KiloNode, anchor_map

# MQTTNode attribute -> canonical metric name. Environment + power + RF
# health; grows deliberately (closed consumers: UNITS in kilo.store and
# the example registry name the same vocabulary).
NODE_METRICS = {
    "temperature": "temperature",
    "humidity": "humidity",
    "pressure": "pressure",
    "gas_resistance": "gas_resistance",
    "co2": "co2",
    "iaq": "iaq",
    "pm25_standard": "pm25_standard",
    "pm10_standard": "pm10_standard",
    "voltage": "voltage",
    "battery_level": "battery_level",
    "channel_utilization": "channel_utilization",
    "air_util_tx": "air_util_tx",
    # snr/rssi are deliberately ABSENT: MQTTNode.snr/rssi are set keyed on
    # the payload's ``sender`` (the uplinking GATEWAY) and describe the
    # LAST HOP of whatever packet it most recently uplinked from ANY
    # originator — recording them as the node's own metric is a
    # valid-looking value with the wrong subject. Per-link RF truth lives
    # in the K1 edges table (kilo matrix), keyed by actual (receiver ←
    # sender) pairs. Removed 2026-07-05 (QA review V1.1).
}

TRANSPORT_MQTT = "mqtt"
TRANSPORT_CLAW = "claw"

# claw_last_tick.json field -> canonical metric name (K0.1 adapter).
# Dotted paths into the tick; numeric-only — a None half (unreachable
# capture) yields NOTHING, never a fabricated 0 (claw_telemetry contract).
CLAW_METRICS = {
    "device_info.heap_free_bytes": "heap_free_bytes",
    "device_info.heap_total_bytes": "heap_total_bytes",
    "device_info.uptime_s": "uptime_s",
    "device_info.wifi_rssi_dbm": "wifi_rssi_dbm",
    "ble.adv_age_s": "ble_adv_age_s",
    "ble.advs": "ble_advs",
    "ble.last_rssi_dbm": "ble_last_rssi_dbm",
}


def snapshot_readings(nodes, registry: List[KiloNode],
                      seen: Dict[Tuple[str, str], Tuple[float, float]],
                      transport: str = TRANSPORT_MQTT,
                      ) -> List[Tuple[float, str, str, Optional[str],
                                      str, float]]:
    """Pure: one subscriber snapshot -> store rows for CHANGED readings.

    ``seen`` is the in-window dedup map {(node_key, metric): (ts, value)},
    mutated here; the DB's UNIQUE constraint additionally guards across
    windows. A metric that is None is UNKNOWN and never recorded (absent
    field must not become a fabricated 0 — claw_telemetry's contract).
    """
    anchors = anchor_map(registry) if registry else {}
    rows: List[Tuple[float, str, str, Optional[str], str, float]] = []
    for node in nodes:
        key = str(getattr(node, "node_id", "") or "")
        if not key:
            continue
        # ts is the node-level last_seen — the closest observation time
        # available (MQTTNode carries no per-metric timestamps). It
        # advances on ANY packet from the node, so dedup below is by
        # VALUE, not (ts, value): a retained attribute must not re-record
        # with a fabricated fresher ts every time the node beacons
        # something else (QA review V1.2). Residual: a resident collector
        # (the planned later rung) needs true per-metric timestamps in the
        # decoder before it can be honest across hours — this window-
        # scoped model is honest because each window starts empty.
        ts = getattr(node, "last_seen", None)
        ts = ts.timestamp() if hasattr(ts, "timestamp") else time.time()
        kilo_id = anchors.get(key.lower())
        for attr, metric in NODE_METRICS.items():
            value = getattr(node, attr, None)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            prev = seen.get((key, metric))
            if prev is not None and prev[1] == value:
                continue  # unchanged since last sample this window
            seen[(key, metric)] = (ts, value)
            rows.append((ts, transport, key, kilo_id, metric, value))
    return rows


def default_claw_tick_path(home=None) -> str:
    """Where claw_metrics_push persists the PRIMARY claw's tick — same
    formula as the writer's _tick_path() (test-pinned pair; basename owned
    by claw_telemetry, the tick-shape owner). THE primary-path formula —
    claw_tick_paths derives from here, never re-builds it."""
    from pathlib import Path

    from mini_dudeai.claw_telemetry import CLAW_TICK_BASENAME
    from utils.paths import get_real_user_home
    home = Path(home) if home else get_real_user_home()
    return str(home / CLAW_TICK_BASENAME)


def claw_tick_paths(home=None) -> List[str]:
    """Every claw tick on this box: the primary tick plus any secondary
    ``claw_last_tick.<device>.json`` (multi-claw brain box, W5.1). The
    glob is the writer's own SECONDARY_TICK_GLOB (one constant, one
    owner). Sender identity comes from each tick's ``device`` field,
    never the filename."""
    from pathlib import Path

    from mini_dudeai.claw_telemetry import SECONDARY_TICK_GLOB
    from utils.paths import get_real_user_home
    home = Path(home) if home else get_real_user_home()
    paths = [Path(default_claw_tick_path(home))]
    paths += sorted(home.glob(SECONDARY_TICK_GLOB))
    return [str(p) for p in paths]


def collect_claw_all(conn, registry: List[KiloNode], home=None) -> dict:
    """Ingest EVERY claw tick present on this box (multi-claw, W5.1).

    ``ok`` is False only when some leg ERRORED — a box with no ticks at
    all is inert (no claw here; corpus shape, not failure), exactly like
    the single-file leg. Each leg keeps its own tri-state witness."""
    legs = [collect_claw(conn, registry, tick_path=p)
            for p in claw_tick_paths(home)]
    return {"ok": all(leg["ok"] for leg in legs),
            "transport": TRANSPORT_CLAW,
            "readings_written": sum(leg["readings_written"] for leg in legs),
            "legs": legs}


def collect_claw(conn, registry: List[KiloNode], tick_path: str) -> dict:
    """Ingest the WireClaw last-tick capture — ZERO new I/O to the claw:
    claw_metrics_push already polls the device every 5 min and persists
    the tick; this reads that file and lands the numeric halves.

    Tri-state summary (never error→quiet-air):
      inert  — no tick file on this box (no claw here; not a failure)
      error  — tick present but unreadable/unparseable (a witness)
      ok     — parsed; a stale/unreachable tick simply writes no fresh
               rows and the node ages toward DARK, which is the truth.
    """
    from mini_dudeai._util import READ_JSON_NOT_FOUND, read_json

    from kilo.store import record_readings

    path = tick_path
    leg = {"ok": False, "state": "inert", "transport": TRANSPORT_CLAW,
           "tick_path": path, "device": None, "tick_age_s": None,
           "readings_written": 0, "error": None}
    tick, err = read_json(path)
    if err == READ_JSON_NOT_FOUND:
        leg["ok"] = True  # absence of a claw is corpus shape, not failure
        return leg
    if err is not None or not isinstance(tick, dict):
        leg["state"] = "error"
        leg["error"] = (f"claw tick unreadable: "
                        f"{err or f'tick not an object: {type(tick).__name__}'}")
        return leg

    device = str(tick.get("device") or "")
    ts = tick.get("captured_at")
    if not device or isinstance(ts, bool) \
            or not isinstance(ts, (int, float)):
        leg["state"] = "error"
        leg["error"] = ("claw tick missing device/captured_at — "
                        "writer/reader shape drift?")
        return leg
    leg["device"] = device
    leg["tick_age_s"] = round(max(0.0, time.time() - float(ts)), 1)
    # The tick's own health verdict is a WITNESS the numeric rows can't
    # carry: a half-unreachable capture (ok=false, e.g. BLE leg errored)
    # still lands its device_info rows, and if the registry doesn't
    # expect the dead half's metrics the node would read OK with no
    # trace. Surface it here; alerting stays with the pusher's cron.
    leg["tick_ok"] = bool(tick.get("ok"))
    leg["tick_errors"] = len(tick.get("errors") or [])

    kilo_id = anchor_map(registry, kind="claw").get(device.lower()) \
        if registry else None
    rows = []
    for dotted, metric in CLAW_METRICS.items():
        cur = tick
        for part in dotted.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None or isinstance(cur, bool):
            continue
        try:
            value = float(cur)
        except (TypeError, ValueError):
            continue
        rows.append((float(ts), TRANSPORT_CLAW, device, kilo_id,
                     metric, value))
    leg["readings_written"] = record_readings(conn, rows)
    leg["state"] = "ok"
    leg["ok"] = True
    return leg


def collect_mqtt(conn, registry: List[KiloNode], seconds: float,
                 sample_every: float = 15.0,
                 config_overrides: Optional[dict] = None,
                 stop_event: Optional[threading.Event] = None,
                 subscriber=None, edges: bool = True) -> dict:
    """Run one bounded collection window; returns a witness summary.

    The summary is honest by construction: ``ok`` False means the
    subscriber never connected (nothing was observed — NOT "no traffic"),
    and counts are re-derived from what actually landed in the DB.

    With ``edges`` (default on, K1) a per-packet observer is registered on
    the SAME subscriber (no second client, #73) and (receiver ← sender)
    soundings land in the edges table. The edges leg reports its own
    honest state: a subscriber without the packet hook reads
    ``enabled: False`` with the reason — never silently no-edges.

    A caller-passed ``subscriber`` is BORROWED: its packet callback is
    removed at window end and it is never stopped or reconfigured — only
    a subscriber this function created gets stopped (#75 shared-resource
    class). Per-tick writes batch into one commit at window end (SD wear).
    """
    import math

    from kilo.store import record_edges, record_readings

    # Guard the knobs, not just the docs: a zero/negative/NaN cadence
    # would busy-loop (wait(0) returns instantly → a write-per-iteration
    # hot loop on SD), and a non-finite window never terminates a
    # "bounded" cron job. Small positive cadences stay legal (tests).
    sample_every = float(sample_every)
    if not math.isfinite(sample_every) or sample_every <= 0:
        sample_every = 15.0
    seconds = float(seconds)
    if not math.isfinite(seconds):
        seconds = 120.0

    stop = stop_event or threading.Event()
    owned = subscriber is None
    if owned:
        from monitoring.mqtt_subscriber import MQTTNodelessSubscriber
        subscriber = MQTTNodelessSubscriber()
        if config_overrides:
            # same-package knob: overrides ride on top of the box's proven
            # ~/.config/meshforge/mqtt_nodeless.json
            subscriber._config.update(config_overrides)

    summary = {
        "ok": False,
        "transport": TRANSPORT_MQTT,
        "window_s": seconds,
        "samples": 0,
        "readings_written": 0,
        "nodes_seen": 0,
        "registered_seen": [],
        "unregistered_seen": 0,
        "error": None,
    }
    edge_buf = None
    if not edges:
        summary["edges"] = {"enabled": False, "reason": "disabled by flag"}
    elif not callable(getattr(subscriber, "add_packet_callback", None)):
        summary["edges"] = {"enabled": False,
                            "reason": "subscriber has no packet hook"}
    else:
        from kilo.edges import EdgeBuffer
        edge_buf = EdgeBuffer()
        subscriber.add_packet_callback(edge_buf.on_packet)
        # rows_duplicate: offered-but-IGNOREd (packet_id already stored) —
        # the dedup swallow leaves a witness, never a silently shrinking
        # rows_written (honest_failure_modes #9).
        summary["edges"] = {"enabled": True, "rows_written": 0,
                            "rows_duplicate": 0, "packets": {}}

    def _land_edges() -> None:
        drained = edge_buf.drain()
        written = record_edges(conn, drained, commit=False)
        summary["edges"]["rows_written"] += written
        summary["edges"]["rows_duplicate"] += len(drained) - written

    if not subscriber.start():
        if not owned and edge_buf is not None:
            # the early return must not leave our observer on a borrowed
            # subscriber
            rm = getattr(subscriber, "remove_packet_callback", None)
            if callable(rm):
                rm(edge_buf.on_packet)
        summary["error"] = ("subscriber failed to connect — broker down or "
                            "misconfigured (~/.config/meshforge/"
                            "mqtt_nodeless.json); nothing was observed")
        return summary

    seen: Dict[Tuple[str, str], Tuple[float, float]] = {}
    keys_seen: set = set()
    registered: set = set()
    unregistered: set = set()
    deadline = time.monotonic() + max(1.0, seconds)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Event.wait, never time.sleep (MF010): a stop request lands
            # immediately instead of at the end of a sleep.
            if stop.wait(timeout=min(sample_every, remaining)):
                break
            nodes = subscriber.get_nodes()
            rows = snapshot_readings(nodes, registry, seen)
            summary["samples"] += 1
            summary["readings_written"] += record_readings(conn, rows,
                                                           commit=False)
            if edge_buf is not None:
                _land_edges()
            for _ts, _tr, key, kilo_id, _m, _v in rows:
                keys_seen.add(key)
                # one classification, in the same loop that stamped the
                # row — never a second join derivation at window end
                if kilo_id:
                    registered.add(kilo_id)
                else:
                    unregistered.add(key)
    finally:
        if owned:
            subscriber.stop()
        elif edge_buf is not None:
            rm = getattr(subscriber, "remove_packet_callback", None)
            if callable(rm):
                rm(edge_buf.on_packet)
        if edge_buf is not None:
            # final drain AFTER stop/detach — packets that arrived between
            # the last sample tick and disconnect still land
            _land_edges()
            summary["edges"]["packets"] = edge_buf.counts()
        conn.commit()  # one WAL commit for the whole window

    summary["ok"] = True
    summary["nodes_seen"] = len(keys_seen)
    summary["registered_seen"] = sorted(registered)
    summary["unregistered_seen"] = len(unregistered)
    return summary

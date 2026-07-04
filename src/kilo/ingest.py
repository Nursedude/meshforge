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
    "snr": "snr",
    "rssi": "rssi",
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
            if prev == (ts, value):
                continue  # unchanged since last sample this window
            seen[(key, metric)] = (ts, value)
            rows.append((ts, transport, key, kilo_id, metric, value))
    return rows


def default_claw_tick_path() -> str:
    """Where claw_metrics_push persists the last tick — same formula as
    the writer's _tick_path() (test-pinned pair; basename owned by
    claw_telemetry, the tick-shape owner)."""
    from mini_dudeai.claw_telemetry import CLAW_TICK_BASENAME
    from utils.paths import get_real_user_home
    return str(get_real_user_home() / CLAW_TICK_BASENAME)


def collect_claw(conn, registry: List[KiloNode],
                 tick_path: Optional[str] = None) -> dict:
    """Ingest the WireClaw last-tick capture — ZERO new I/O to the claw:
    claw_metrics_push already polls the device every 5 min and persists
    the tick; this reads that file and lands the numeric halves.

    Tri-state summary (never error→quiet-air):
      inert  — no tick file on this box (no claw here; not a failure)
      error  — tick present but unreadable/unparseable (a witness)
      ok     — parsed; a stale/unreachable tick simply writes no fresh
               rows and the node ages toward DARK, which is the truth.
    """
    import json as _json
    import os

    from kilo.store import record_readings

    path = tick_path or default_claw_tick_path()
    leg = {"ok": False, "state": "inert", "transport": TRANSPORT_CLAW,
           "tick_path": path, "device": None, "tick_age_s": None,
           "readings_written": 0, "error": None}
    if not os.path.exists(path):
        leg["ok"] = True  # absence of a claw is corpus shape, not failure
        return leg
    try:
        with open(path, encoding="utf-8") as f:
            tick = _json.load(f)
        if not isinstance(tick, dict):
            raise ValueError(f"tick not an object: {type(tick).__name__}")
    except (OSError, ValueError) as e:
        leg["state"] = "error"
        leg["error"] = f"claw tick unreadable: {e}"
        return leg

    device = str(tick.get("device") or "")
    ts = tick.get("captured_at")
    if not device or not isinstance(ts, (int, float)):
        leg["state"] = "error"
        leg["error"] = ("claw tick missing device/captured_at — "
                        "writer/reader shape drift?")
        return leg
    leg["device"] = device
    leg["tick_age_s"] = round(max(0.0, time.time() - float(ts)), 1)

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
    """
    from kilo.store import record_edges, record_readings

    stop = stop_event or threading.Event()
    if subscriber is None:
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
        "edges": {"enabled": False, "reason": "disabled by flag"},
        "error": None,
    }
    edge_buf = None
    if edges:
        from kilo.edges import EdgeBuffer
        add_cb = getattr(subscriber, "add_packet_callback", None)
        if callable(add_cb):
            edge_buf = EdgeBuffer()
            add_cb(edge_buf.on_packet)
            summary["edges"] = {"enabled": True, "rows_written": 0,
                                "packets": {}}
        else:
            summary["edges"] = {"enabled": False,
                                "reason": "subscriber has no packet hook"}
    if not subscriber.start():
        summary["error"] = ("subscriber failed to connect — broker down or "
                            "misconfigured (~/.config/meshforge/"
                            "mqtt_nodeless.json); nothing was observed")
        return summary

    seen: Dict[Tuple[str, str], Tuple[float, float]] = {}
    keys_seen: set = set()
    registered: set = set()
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
            summary["readings_written"] += record_readings(conn, rows)
            if edge_buf is not None:
                summary["edges"]["rows_written"] += \
                    record_edges(conn, edge_buf.drain())
            for _ts, _tr, key, kilo_id, _m, _v in rows:
                keys_seen.add(key)
                if kilo_id:
                    registered.add(kilo_id)
    finally:
        subscriber.stop()
        if edge_buf is not None:
            # final drain AFTER stop — packets that arrived between the
            # last sample tick and disconnect still land
            summary["edges"]["rows_written"] += \
                record_edges(conn, edge_buf.drain())
            summary["edges"]["packets"] = edge_buf.counts()

    summary["ok"] = True
    summary["nodes_seen"] = len(keys_seen)
    summary["registered_seen"] = sorted(registered)
    summary["unregistered_seen"] = len(
        {k for k in keys_seen
         if k.lower() not in anchor_map(registry)})
    return summary

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


def collect_mqtt(conn, registry: List[KiloNode], seconds: float,
                 sample_every: float = 15.0,
                 config_overrides: Optional[dict] = None,
                 stop_event: Optional[threading.Event] = None,
                 subscriber=None) -> dict:
    """Run one bounded collection window; returns a witness summary.

    The summary is honest by construction: ``ok`` False means the
    subscriber never connected (nothing was observed — NOT "no traffic"),
    and counts are re-derived from what actually landed in the DB.
    """
    from kilo.store import record_readings

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
        "error": None,
    }
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
            for _ts, _tr, key, kilo_id, _m, _v in rows:
                keys_seen.add(key)
                if kilo_id:
                    registered.add(kilo_id)
    finally:
        subscriber.stop()

    summary["ok"] = True
    summary["nodes_seen"] = len(keys_seen)
    summary["registered_seen"] = sorted(registered)
    summary["unregistered_seen"] = len(
        {k for k in keys_seen
         if k.lower() not in anchor_map(registry)})
    return summary

#!/usr/bin/env python3
"""Purge RETAINED MQTT messages older than N days from a local broker.

Why this exists (2026-09-18, moc3): a mosquitto bridge to the public
Meshtastic broker was disabled on 2026-05-25, but ``persistence true`` kept
every retained message the bridge had ever mirrored — 22,548 of them, all
foreign, 4.5 MB on disk — and every new wildcard subscriber (a census, a
map collector, the radio on reconnect) was handed the whole backlog as if
it were live traffic. Our own publishers (meshtasticd, the gateway) never
set the retain flag, so a retained message under ``msh/#`` on a MeshForge
box is always residue from somewhere else.

Mosquitto does not expose WHEN a retained message was stored, so age is
decoded from the payload itself:

* ``.../e/...``   → Meshtastic ``ServiceEnvelope.packet.rx_time`` (unix)
* ``.../json/...`` → the JSON envelope's ``timestamp`` field
* anything else, or a timestamp outside a sane window → **unknown age**

Unknown age is reported on its own line and NEVER purged unless
``--include-unknown-age`` is passed: a message we cannot date is not an
old message (honest_failure_modes #1 — the degraded value must not land in
the healthy domain). Wall-clock timestamps on this fleet are forgeable
(RTC-less Pis), so a timestamp in the future or before 2020 also reads
unknown rather than "recent" (#6).

Dry run by default. ``--apply`` publishes an empty retained payload to each
selected topic (the MQTT way to delete a retained message), then
RE-SUBSCRIBES and re-counts — the verdict is re-derived, never a tally. The
broker's own ``$SYS/broker/retained messages/count`` is read as a second
witness so an empty subscribe cannot be mistaken for an empty broker.

Exit codes: 0 verified (dry run, or apply re-derived to zero selected);
1 could not observe (connect/subscribe failed, or witnesses disagree);
2 apply ran but selected topics are still retained.

Cron form (weekly, wired through cron_verdict.sh like every fleet cron):
  python3 scripts/mqtt_retained_purge.py --older-than-days 7 --apply
"""

import argparse
import json
import sys
import threading
import time
from typing import Dict, Optional, Tuple

try:
    import paho.mqtt.client as mqtt
except ImportError:  # external dep — the only import allowed to be soft
    mqtt = None

try:
    from meshtastic.protobuf import mqtt_pb2
except ImportError:
    mqtt_pb2 = None

SYS_RETAINED = "$SYS/broker/retained messages/count"
# A timestamp outside this window is not evidence of anything (hfm #6).
_EARLIEST_SANE_TS = 1577836800  # 2020-01-01
_FUTURE_SLACK_S = 86400

AGE_OLD = "old"
AGE_RECENT = "recent"
AGE_UNKNOWN = "unknown"


def extract_timestamp(topic: str, payload: bytes) -> Optional[int]:
    """Best-effort publish time of a Meshtastic MQTT message, or None."""
    parts = topic.split("/")
    if "/e/" in topic and mqtt_pb2 is not None:
        try:
            env = mqtt_pb2.ServiceEnvelope()
            env.ParseFromString(payload)
            ts = int(env.packet.rx_time)
            return ts or None
        except Exception:  # malformed protobuf is "unknown", not "recent"
            return None
    if "json" in parts:
        try:
            doc = json.loads(payload.decode("utf-8", errors="replace"))
        except ValueError:
            return None
        if isinstance(doc, dict):
            ts = doc.get("timestamp")
            if isinstance(ts, (int, float)):
                return int(ts)
    return None


def classify_age(topic: str, payload: bytes, now: float,
                 older_than_s: float) -> Tuple[str, Optional[int]]:
    """Return (AGE_OLD | AGE_RECENT | AGE_UNKNOWN, timestamp)."""
    ts = extract_timestamp(topic, payload)
    if ts is None or ts < _EARLIEST_SANE_TS or ts > now + _FUTURE_SLACK_S:
        return AGE_UNKNOWN, ts
    return (AGE_OLD if now - ts > older_than_s else AGE_RECENT), ts


class _Collector:
    """Subscribe once, keep every RETAINED message, stop when the flood settles."""

    def __init__(self, host: str, port: int, topic: str, settle_s: float,
                 timeout_s: float):
        self.host, self.port, self.topic = host, port, topic
        self.settle_s, self.timeout_s = settle_s, timeout_s
        self.retained: Dict[str, bytes] = {}
        self.sys_count: Optional[int] = None
        self.error: Optional[str] = None
        self._last_msg = time.monotonic()
        self._connected = threading.Event()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False) or reason_code not in (0, "Success"):
            rc = getattr(reason_code, "value", reason_code)
            if rc != 0:
                self.error = f"connect refused: {reason_code}"
                return
        client.subscribe([(self.topic, 0), (SYS_RETAINED, 0)])
        self._connected.set()

    def _on_message(self, client, userdata, msg):
        self._last_msg = time.monotonic()
        if msg.topic == SYS_RETAINED:
            try:
                self.sys_count = int(msg.payload.decode().strip())
            except ValueError:
                pass
            return
        if msg.retain:
            self.retained[msg.topic] = bytes(msg.payload)

    def run(self) -> bool:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id="", protocol=mqtt.MQTTv311)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        try:
            client.connect(self.host, self.port, keepalive=30)
        except (OSError, ValueError) as e:
            self.error = f"connect failed: {e}"
            return False
        client.loop_start()
        try:
            deadline = time.monotonic() + self.timeout_s
            if not self._connected.wait(timeout=min(10.0, self.timeout_s)):
                self.error = self.error or "no CONNACK within 10s"
                return False
            self._last_msg = time.monotonic()
            while time.monotonic() < deadline:
                time.sleep(0.2)
                quiet = time.monotonic() - self._last_msg
                if quiet >= self.settle_s and self.sys_count is not None:
                    return True
            # Deadline hit: what we have is a partial observation, say so.
            self.error = (f"collection did not settle within {self.timeout_s}s "
                          f"(last message {time.monotonic() - self._last_msg:.1f}s ago)")
            return False
        finally:
            client.loop_stop()
            client.disconnect()


def _purge(host: str, port: int, topics, timeout_s: float) -> int:
    """Publish an empty retained payload to each topic. Returns count sent."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="", protocol=mqtt.MQTTv311)
    client.connect(host, port, keepalive=30)
    client.loop_start()
    sent = 0
    try:
        infos = []
        for t in topics:
            infos.append(client.publish(t, payload=b"", qos=1, retain=True))
        deadline = time.monotonic() + timeout_s
        for info in infos:
            remaining = max(0.1, deadline - time.monotonic())
            info.wait_for_publish(timeout=remaining)
            if info.is_published():
                sent += 1
    finally:
        client.loop_stop()
        client.disconnect()
    return sent


def _family(topic: str) -> str:
    return "/".join(topic.split("/")[:4])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--topic", default="msh/#",
                    help="subscription filter to inspect (default msh/#)")
    ap.add_argument("--older-than-days", type=float, required=True,
                    help="purge retained messages older than this many days")
    ap.add_argument("--include-unknown-age", action="store_true",
                    help="also purge messages whose age cannot be decoded")
    ap.add_argument("--apply", action="store_true",
                    help="actually purge; default is a dry run")
    ap.add_argument("--settle", type=float, default=5.0,
                    help="seconds of silence that end the retained flood (default 5)")
    ap.add_argument("--timeout", type=float, default=120.0,
                    help="hard cap on the whole observation (default 120s)")
    args = ap.parse_args(argv)

    if mqtt is None:
        print("UNKNOWN: paho-mqtt not importable (requirements/mqtt.txt)")
        return 1
    if mqtt_pb2 is None:
        print("WARN: meshtastic protobufs not importable — /e/ ages will read unknown")

    now = time.time()
    older_than_s = args.older_than_days * 86400

    col = _Collector(args.host, args.port, args.topic, args.settle, args.timeout)
    if not col.run():
        print(f"UNKNOWN: could not observe the broker — {col.error}")
        return 1

    buckets = {AGE_OLD: [], AGE_RECENT: [], AGE_UNKNOWN: []}
    for topic, payload in col.retained.items():
        cls, _ = classify_age(topic, payload, now, older_than_s)
        buckets[cls].append(topic)

    seen = len(col.retained)
    print(f"retained under {args.topic!r} on {args.host}:{args.port}: {seen} "
          f"(broker $SYS says {col.sys_count} retained in total)")
    if col.sys_count is not None and seen > col.sys_count:
        print("UNKNOWN: subscribe saw more retained than the broker reports — "
              "witnesses disagree, refusing to act")
        return 1
    print(f"  older than {args.older_than_days:g} d : {len(buckets[AGE_OLD])}")
    print(f"  more recent                : {len(buckets[AGE_RECENT])}")
    print(f"  unknown age                : {len(buckets[AGE_UNKNOWN])}"
          + ("  (WILL be purged: --include-unknown-age)" if args.include_unknown_age
             else "  (kept — cannot be dated; --include-unknown-age to purge)"))
    fams: Dict[str, int] = {}
    for t in col.retained:
        fams[_family(t)] = fams.get(_family(t), 0) + 1
    for fam, n in sorted(fams.items(), key=lambda kv: -kv[1])[:6]:
        print(f"    {n:6d}  {fam}/...")

    selected = list(buckets[AGE_OLD])
    if args.include_unknown_age:
        selected += buckets[AGE_UNKNOWN]
    if not selected:
        print("nothing selected — exit 0")
        return 0
    if not args.apply:
        print(f"DRY RUN: {len(selected)} would be purged (re-run with --apply)")
        return 0

    sent = _purge(args.host, args.port, selected, args.timeout)
    print(f"purge published for {sent}/{len(selected)} topics; re-deriving…")

    # Re-observe: the verdict comes from a fresh subscribe, not from `sent`.
    col2 = _Collector(args.host, args.port, args.topic, args.settle, args.timeout)
    if not col2.run():
        print(f"UNKNOWN: purge sent but re-observation failed — {col2.error}")
        return 1
    still = [t for t in selected if t in col2.retained]
    print(f"after purge: {len(col2.retained)} retained under {args.topic!r} "
          f"(broker $SYS says {col2.sys_count}); {len(still)} of the selected "
          f"{len(selected)} still retained")
    if still:
        for t in still[:5]:
            print(f"    still retained: {t}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

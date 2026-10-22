"""Oracle-leg deferred finding (1), 2026-07-05 review, fixed 2026-10-22.

`mqtt_content_dedup_key` — the FALLBACK dedup key for a JSON packet with no
`id` — omitted the packet's `timestamp`, so an id-less GENUINE repeat (the same
text sent again inside the 60 s window) was dropped as if it were a QoS-1
redelivery, with no witness. The same publish delivered twice (redelivery,
retained, overlapping subscriptions) carries the SAME timestamp and must still
collapse; a later send carries a new one and must not. The id path is
unchanged. Every fallback-key drop is now counted.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway.base_handler import mqtt_content_dedup_key  # noqa: E402
from gateway.mesh_bridge import MQTTMeshInterface  # noqa: E402
from gateway.mqtt_bridge_handler import MQTTBridgeHandler  # noqa: E402

_TOPIC = "msh/2/json/meshforge/!aabb0042"


def _pkt(text="hello fleet", ts=None, with_id=False):
    d = {"type": "text", "from": 0xAABB0042, "to": 0xFFFFFFFF,
         "channel": 2, "payload": {"text": text}}
    if ts is not None:
        d["timestamp"] = ts
    if with_id:
        d["id"] = 12345678
    return d


class TestKey:
    def test_timestamp_separates_genuine_repeats(self):
        assert (mqtt_content_dedup_key(_pkt(ts=1792700000))
                != mqtt_content_dedup_key(_pkt(ts=1792700005)))

    def test_same_publish_same_key(self):
        assert (mqtt_content_dedup_key(_pkt(ts=1792700000))
                == mqtt_content_dedup_key(_pkt(ts=1792700000)))

    def test_timestamp_alone_is_not_usable_content(self):
        assert mqtt_content_dedup_key({"timestamp": 1792700000}) == ""


def _handler():
    h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
    h.config = MagicMock()
    h.stats = {}
    h._recent_ids = {}
    h._dedup_window = 60
    h._mqtt_lock = threading.Lock()
    h._last_uplink_at = 0.0
    h._bridge_text_message = MagicMock()
    h._update_node_from_mqtt = MagicMock()
    h._update_telemetry = MagicMock()
    h._update_position = MagicMock()
    h._update_nodeinfo = MagicMock()
    return h


class TestMQTTBridgeHandler:
    def test_idless_genuine_repeat_bridges_twice(self):
        h = _handler()
        h._handle_json_message(_TOPIC, json.dumps(_pkt(ts=1792700000)).encode())
        h._handle_json_message(_TOPIC, json.dumps(_pkt(ts=1792700007)).encode())
        assert h._bridge_text_message.call_count == 2
        assert h.stats.get("mqtt_fallback_dedup_dropped", 0) == 0

    def test_idless_redelivery_dropped_and_counted(self):
        h = _handler()
        raw = json.dumps(_pkt(ts=1792700000)).encode()
        h._handle_json_message(_TOPIC, raw)
        h._handle_json_message(_TOPIC, raw)
        assert h._bridge_text_message.call_count == 1
        assert h.stats["mqtt_fallback_dedup_dropped"] == 1

    def test_id_path_drop_is_not_counted_as_fallback(self):
        h = _handler()
        raw = json.dumps(_pkt(ts=1792700000, with_id=True)).encode()
        h._handle_json_message(_TOPIC, raw)
        h._handle_json_message(_TOPIC, raw)
        assert h._bridge_text_message.call_count == 1
        assert h.stats.get("mqtt_fallback_dedup_dropped", 0) == 0


class TestMQTTMeshInterface:
    def _iface(self):
        cb = MagicMock()
        i = MQTTMeshInterface(config=MagicMock(), name="t",
                              message_callback=cb,
                              stop_event=threading.Event())
        return i, cb

    def test_idless_genuine_repeat_forwarded_twice(self):
        i, cb = self._iface()
        for ts in (1792700000, 1792700007):
            i._on_message(None, None, SimpleNamespace(
                topic=_TOPIC, payload=json.dumps(_pkt(ts=ts)).encode()))
        assert cb.call_count == 2
        assert i.fallback_dedup_dropped == 0

    def test_idless_redelivery_dropped_and_counted(self):
        i, cb = self._iface()
        msg = SimpleNamespace(topic=_TOPIC,
                              payload=json.dumps(_pkt(ts=1792700000)).encode())
        i._on_message(None, None, msg)
        i._on_message(None, None, msg)
        assert cb.call_count == 1
        assert i.fallback_dedup_dropped == 1

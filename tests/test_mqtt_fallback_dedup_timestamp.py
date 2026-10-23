"""Oracle-leg deferred finding (1), 2026-07-05 review — OBSERVE FIRST (2026-10-22).

`mqtt_content_dedup_key` — the FALLBACK dedup key for a JSON packet with no
`id` — omits the packet's `timestamp`, so an id-less GENUINE repeat inside the
60 s window is dropped as if it were a redelivery. Adding the timestamp would
fix that but un-collapse one id-less packet uplinked by two radios (each stamps
its own rx time) — the #34 case the key exists for (non-author pass,
2026-10-22). How often either happens is unmeasured, so the key is unchanged
and every fallback-key drop is now COUNTED, readable in the status surfaces.
These tests pin the witness and the unchanged key.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway.base_handler import FallbackDropWitness, mqtt_content_dedup_key  # noqa: E402
from gateway.mesh_bridge import MeshtasticPresetBridge, MQTTMeshInterface  # noqa: E402
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


class TestKeyUnchanged:
    def test_two_uplinkers_of_one_packet_still_collapse(self):
        # The #34 case: same packet, two radios, two rx timestamps.
        assert (mqtt_content_dedup_key(_pkt(ts=1792700000))
                == mqtt_content_dedup_key(_pkt(ts=1792700001)))


def _handler():
    h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
    h.config = MagicMock()
    h.stats = {}
    h._stats_lock = threading.Lock()
    h._fb_witness = FallbackDropWitness()
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


class TestMQTTBridgeHandlerWitness:
    def test_idless_drop_is_counted(self):
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


class TestMQTTMeshInterfaceWitness:
    def _iface(self):
        cb = MagicMock()
        i = MQTTMeshInterface(config=MagicMock(), name="t",
                              message_callback=cb,
                              stop_event=threading.Event())
        return i, cb

    def test_idless_drop_is_counted(self):
        i, cb = self._iface()
        msg = SimpleNamespace(topic=_TOPIC,
                              payload=json.dumps(_pkt(ts=1792700000)).encode())
        i._on_message(None, None, msg)
        i._on_message(None, None, msg)
        assert cb.call_count == 1
        assert i._fb_witness.count == 1

    def test_counter_reaches_bridge_status(self):
        # A counter nobody reads is no witness (non-author pass, 2026-10-22).
        i, _ = self._iface()
        i._fb_witness.count = 3
        b = MeshtasticPresetBridge.__new__(MeshtasticPresetBridge)
        b.stats = {"start_time": None}
        b.bridge_config = MagicMock()
        b._running = True
        b._primary_connected = b._secondary_connected = True
        b._primary_interface, b._secondary_interface = i, object()
        b._handoff = MagicMock(snapshot=MagicMock(return_value={}))
        b._primary_to_secondary = MagicMock(get_queue_depth=MagicMock(return_value=0))
        b._secondary_to_primary = MagicMock(get_queue_depth=MagicMock(return_value=0))
        assert b.get_status()["statistics"]["mqtt_fallback_dedup_dropped"] == 3


class TestJournalWitness:
    """The counters above live only in the gateway process — nothing out of
    process could read them (measured 2026-10-22 on moc/moc3). The witness an
    operator can actually reach is the journal: one INFO line on the first
    fallback-key drop, then at most one per interval, carrying the count."""

    def test_handler_first_drop_logs_info_with_count(self, caplog):
        import logging
        h = _handler()
        raw = json.dumps(_pkt(ts=1792700000)).encode()
        with caplog.at_level(logging.INFO, logger="gateway.base_handler"):
            h._handle_json_message(_TOPIC, raw)
            h._handle_json_message(_TOPIC, raw)
        lines = [r for r in caplog.records if "fallback key" in r.getMessage()]
        assert len(lines) == 1 and lines[0].levelno == logging.INFO
        assert "1 this process" in lines[0].getMessage()

    def test_rate_limited_then_logs_again_with_running_count(self, caplog, monkeypatch):
        import logging
        import gateway.base_handler as bh
        clock = [1000.0]
        monkeypatch.setattr(bh.time, "monotonic", lambda: clock[0])
        w = bh.FallbackDropWitness()
        with caplog.at_level(logging.INFO, logger="gateway.base_handler"):
            w.note()
            clock[0] += 10
            w.note()                                     # inside the window: silent
            clock[0] += bh.FALLBACK_DROP_LOG_INTERVAL_S
            n = w.note()                                 # window passed: logs, count 3
        msgs = [r.getMessage() for r in caplog.records if "fallback key" in r.getMessage()]
        assert n == 3 and w.count == 3
        assert len(msgs) == 2 and "3 this process" in msgs[1]


    def test_log_is_written_outside_the_witness_lock(self, monkeypatch):
        # A slow journald must not stall other callers (non-author pass).
        import gateway.base_handler as bh
        w = bh.FallbackDropWitness()
        held = []
        monkeypatch.setattr(bh.logger, "info", lambda *a, **k: held.append(w._lock.locked()))
        w.note()
        assert held == [False]

    def test_interface_drop_logs_with_its_name(self, caplog):
        import logging
        cb = MagicMock()
        i = MQTTMeshInterface(config=MagicMock(), name="leg-st",
                              message_callback=cb, stop_event=threading.Event())
        msg = SimpleNamespace(topic=_TOPIC, payload=json.dumps(_pkt(ts=1792700000)).encode())
        with caplog.at_level(logging.INFO, logger="gateway.base_handler"):
            i._on_message(None, None, msg)
            i._on_message(None, None, msg)
        msgs = [r.getMessage() for r in caplog.records if "fallback key" in r.getMessage()]
        assert i._fb_witness.count == 1
        assert len(msgs) == 1 and "[leg-st]" in msgs[0]
